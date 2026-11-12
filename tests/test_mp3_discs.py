"""MP3 CDs: sizes, the library's disc kind, staging the files, the burn window and the offer."""

import shutil
import subprocess
import sys

import pytest
from PySide6.QtWidgets import QMessageBox

from dubcrate import mp3
from dubcrate.build import (
    BuildError,
    build_burn_disc,
    disc_problems,
    plan_burn,
    render_plan,
)
from dubcrate.burn import BurnError, DataDisc, MediaInfo
from dubcrate.cd import DISC_74_SECTORS, DISC_80_SECTORS
from dubcrate.library import Library
from dubcrate.library.folder import scan_folder
from dubcrate.library.store import DiscError
from dubcrate.render import RenderError, probe, render_mp3
from dubcrate.ui.burn_dialog import BurnDialog
from dubcrate.ui.main_window import MainWindow

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg not installed",
)


def make_audio(path, seconds=2.0, **tags):
    """A sine tone; the extension picks the format (.flac, .mp3 ...)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = [arg for k, v in tags.items() for arg in ("-metadata", f"{k}={v}")]
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
         f"sine=duration={seconds}", *meta, str(path)],
        check=True,
    )  # fmt: skip
    return path


# -- the pure maths -----------------------------------------------------------


def test_a_sector_is_2048_bytes_and_every_file_costs_its_directory_entries():
    assert mp3.data_sectors(2048) == 1 + mp3.FILE_OVERHEAD_SECTORS
    assert mp3.data_sectors(2049) == 2 + mp3.FILE_OVERHEAD_SECTORS


def test_an_mp3_takes_its_files_size_and_anything_else_the_encoded_size():
    kw = {"duration": 100.0, "source_bitrate": 128, "disc_bitrate": 320}
    mp3_cost = mp3.track_sectors("a.MP3", file_size=4_000_000, **kw)
    assert mp3_cost == mp3.data_sectors(4_000_000 + mp3.TAG_BYTES)
    flac_cost = mp3.track_sectors("a.flac", file_size=40_000_000, **kw)
    assert flac_cost == mp3.data_sectors(mp3.encoded_bytes(100.0, 320))
    assert flac_cost > 100 * 320 * 125 / 2048  # never less than the bitrate says
    # an MP3 whose size isn't known is estimated from its own bitrate, not the disc's
    unknown = mp3.track_sectors("a.mp3", file_size=None, **kw)
    assert unknown == mp3.data_sectors(mp3.encoded_bytes(100.0, 128))


def test_file_names_are_numbered_clean_and_short_enough_for_joliet():
    assert (
        mp3.file_name(1, "Daft Punk", "One More Time")
        == "01 Daft Punk - One More Time.mp3"
    )
    assert mp3.file_name(7, "", "Solo") == "07 Solo.mp3"
    assert (
        mp3.file_name(3, "AC/DC", 'Who: "Made" Who?') == "03 AC DC - Who Made Who.mp3"
    )
    assert mp3.file_name(2, "", "") == "02 Track.mp3"
    long = mp3.file_name(12, "A" * 50, "B" * 50)
    assert (
        len(long) <= mp3.MAX_NAME_CHARS
        and long.startswith("12 A")
        and long.endswith(".mp3")
    )
    assert mp3.file_name(105, "x", "y").startswith(
        "105 "
    )  # numbers keep sorting by name


def test_sizes_read_like_the_box_and_the_volume_name_fits_joliet():
    assert mp3.format_size(DISC_80_SECTORS) == "703 MB"
    assert mp3.format_size(DISC_74_SECTORS) == "650 MB"
    assert mp3.format_amount(75 * 60, "audio") == "01:00:00"
    assert mp3.format_amount(DISC_74_SECTORS, "mp3") == "650 MB"
    assert mp3.volume_name("Friday Night Warm-up Set") == "Friday Night War"
    assert mp3.volume_name("///") == "MP3 DISC"


# -- the library --------------------------------------------------------------


@pytest.fixture
def lib(tmp_path):
    music = tmp_path / "music"
    # a few files whose size and duration the store knows from the scan
    make_audio(music / "a.flac", 2.0, title="Alpha", artist="Same")
    make_audio(music / "b.flac", 3.0, title="Beta", artist="Same")
    make_audio(music / "c.mp3", 2.0, title="Gamma", artist="Other")
    scan = scan_folder(music)
    with Library.open(":memory:") as library:
        library.replace_source("folder", str(music), "music", scan.tracks, scan.nodes)
        yield library


def track_ids(lib):
    return [t.id for t in lib.tracks(sort="title")]


@needs_ffmpeg
def test_an_mp3_disc_is_sized_in_data_sectors_and_has_no_gaps_or_99_track_limit(lib):
    disc = lib.create_disc("M", kind="mp3", pregap_sectors=150)
    summary = lib.disc(disc)
    assert (summary.kind, summary.pregap_sectors, summary.mp3_bitrate) == (
        "mp3",
        0,
        320,
    )
    ids = track_ids(lib)
    lib.add_to_disc(disc, ids * 40)  # 120 tracks: more than a CD can hold
    summary = lib.disc(disc)
    assert summary.track_count == 120 and not summary.over
    entries = lib.disc_entries(disc)
    assert all(e.kind == "mp3" for e in entries)
    # the mp3 is its file's size; a flac is the encoded size at the disc's bitrate
    by_title = {e.title: e for e in entries}
    mp3_track = by_title["Gamma"].track
    assert by_title["Gamma"].sectors == mp3.data_sectors(
        (mp3_track.file_size or 0) + mp3.TAG_BYTES
    )
    assert by_title["Gamma"].exact and not by_title["Alpha"].exact
    audio = lib.create_disc("A")
    with pytest.raises(DiscError, match="at most 99"):
        lib.add_to_disc(audio, ids * 40)


@needs_ffmpeg
def test_bitrate_changes_what_a_non_mp3_costs(lib):
    low = lib.create_disc("L", kind="mp3", mp3_bitrate=128)
    high = lib.create_disc("H", kind="mp3", mp3_bitrate=320)
    for disc in (low, high):
        lib.add_to_disc(disc, track_ids(lib))
    assert lib.disc(low).used_sectors < lib.disc(high).used_sectors
    lib.update_disc(low, mp3_bitrate=320)
    assert lib.disc(low).used_sectors == lib.disc(high).used_sectors


@needs_ffmpeg
def test_fit_and_split_use_the_discs_own_unit(lib):
    ids = track_ids(lib)
    tiny = (
        2 * mp3.FILE_OVERHEAD_SECTORS + 100
    )  # room for little more than one small file
    disc = lib.create_disc("Small", kind="mp3", capacity_sectors=tiny)
    result = lib.add_to_disc(disc, ids, fit=True)
    assert len(result.added) < len(ids) and result.rejected
    split = lib.split_into_discs(
        ids, "Set", capacity_sectors=DISC_80_SECTORS, kind="mp3"
    )
    assert len(split.disc_ids) == 1 and lib.disc(split.disc_ids[0]).kind == "mp3"


@needs_ffmpeg
def test_a_duplicate_keeps_the_kind_and_the_bitrate(lib):
    disc = lib.create_disc("M", kind="mp3", mp3_bitrate=192)
    lib.add_to_disc(disc, track_ids(lib))
    copy = lib.disc(lib.duplicate_disc(disc))
    assert (copy.kind, copy.mp3_bitrate, copy.track_count) == ("mp3", 192, 3)


@needs_ffmpeg
def test_converting_a_disc_remeasures_it_and_keeps_its_tracks_and_text(lib):
    disc = lib.create_disc("Mix", capacity_sectors=300)  # an audio CD far too small
    lib.add_to_disc(disc, track_ids(lib))
    lib.set_entry_text(disc, 0, "title", "Edited")
    assert lib.disc(disc).over
    lib.set_disc_kind(disc, "mp3", 256)
    summary = lib.disc(disc)
    assert (summary.kind, summary.mp3_bitrate, summary.track_count) == ("mp3", 256, 3)
    assert lib.disc_entries(disc)[0].title == "Edited"
    lib.set_disc_kind(disc, "audio")
    summary = lib.disc(disc)
    assert (summary.kind, summary.mp3_bitrate, summary.pregap_sectors) == (
        "audio",
        None,
        0,
    )
    with pytest.raises(DiscError):
        lib.set_disc_kind(disc, "wax")


def test_an_older_library_gets_the_new_columns(tmp_path):
    from dubcrate.library import store

    path = tmp_path / "old.db"
    with Library.open(path) as library:  # make it, then wind it back to schema 6
        library._conn.execute("ALTER TABLE disc DROP COLUMN kind")
        library._conn.execute("ALTER TABLE disc DROP COLUMN mp3_bitrate")
        library._conn.execute("INSERT INTO disc (name, capacity_sectors, created_at, updated_at) VALUES ('Old', 1, '', '')")  # fmt: skip
        library._conn.execute("PRAGMA user_version = 6")
        library._conn.commit()
    with Library.open(path) as library:
        [disc] = library.discs()
        assert (disc.kind, disc.mp3_bitrate) == ("audio", None)
        assert (
            library._conn.execute("PRAGMA user_version").fetchone()[0]
            == store.SCHEMA_VERSION
        )


# -- encoding and staging -----------------------------------------------------


@needs_ffmpeg
def test_a_non_mp3_is_encoded_at_the_chosen_bitrate_with_the_discs_own_tags(tmp_path):
    src = make_audio(tmp_path / "a.flac", 2.0, title="Original", artist="Someone")
    dst = tmp_path / "out" / "01.mp3"
    size = render_mp3(
        src, dst, title="Edited", artist="Them", album="Mix", number=1, bitrate=128
    )
    assert size == dst.stat().st_size > 0
    info = probe(dst)
    assert (info.codec, info.sample_rate, info.title, info.artist, info.album) == ("mp3", 44100, "Edited", "Them", "Mix")  # fmt: skip
    assert 120 <= (info.bitrate or 0) <= 136
    assert src.read_bytes()  # only read


@needs_ffmpeg
def test_an_mp3_is_copied_not_reencoded(tmp_path):
    src = make_audio(tmp_path / "a.mp3", 2.0, title="Original")
    before = src.read_bytes()
    dst = tmp_path / "out.mp3"
    render_mp3(src, dst, title="Edited", artist="Them", bitrate=128)
    assert src.read_bytes() == before
    assert probe(dst).title == "Edited"
    assert probe(dst).bitrate == probe(src).bitrate  # not 128: it wasn't encoded again


@needs_ffmpeg
def test_a_file_that_cant_be_read_is_a_render_error(tmp_path):
    bad = tmp_path / "bad.flac"
    bad.write_bytes(b"not audio")
    with pytest.raises(RenderError):
        render_mp3(bad, tmp_path / "x.mp3", title="t", artist="a")
    assert not (tmp_path / "x.mp3").exists()


@needs_ffmpeg
def test_the_disc_is_staged_as_numbered_files_in_a_folder_named_for_it(lib, tmp_path):
    disc = lib.create_disc("Friday Night Warm-up", kind="mp3", mp3_bitrate=128)
    lib.add_to_disc(disc, track_ids(lib))
    lib.set_entry_text(disc, 0, "title", "Opener")
    plan = plan_burn(lib, disc)
    assert (plan.kind, plan.mp3_bitrate) == ("mp3", 128)
    seen = []
    rendered = render_plan(plan, tmp_path / "work", seen.append)
    data = rendered.disc
    assert (
        isinstance(data, DataDisc)
        and rendered.lengths == {}
        and rendered.pregap_sectors == 0
    )
    assert data.folder.name == "Friday Night War" and data.title == data.folder.name
    names = sorted(p.name for p in data.folder.iterdir())
    assert names == [p.name for p in data.files]
    assert (
        names[0] == "01 Same - Opener.mp3"
        and names[1].startswith("02 ")
        and names[2].startswith("03 ")
    )
    real = sum(mp3.data_sectors(p.stat().st_size) for p in data.files)
    assert (
        data.sectors == real + mp3.FILESYSTEM_OVERHEAD_SECTORS == rendered.used_sectors
    )
    assert any("preparing 1/3" in line for line in seen)


@needs_ffmpeg
def test_staging_refuses_what_turns_out_to_be_too_big(lib, tmp_path):
    disc = lib.create_disc("Tight", kind="mp3")
    lib.add_to_disc(disc, track_ids(lib))
    plan = plan_burn(lib, disc)
    from dataclasses import replace

    with pytest.raises(BuildError, match="over the"):
        render_plan(replace(plan, capacity_sectors=100), tmp_path / "work")


@needs_ffmpeg
def test_problems_for_an_mp3_disc_speak_in_megabytes_and_skip_the_track_limit(lib):
    disc = lib.create_disc("Big", kind="mp3", capacity_sectors=50)
    lib.add_to_disc(disc, track_ids(lib) * 40)
    problems = disc_problems(lib, disc)
    assert any("MB" in p and "over capacity" in p for p in problems)
    assert not any("at most 99" in p for p in problems)


@needs_ffmpeg
def test_build_burn_disc_hands_back_a_data_disc_and_never_measures_it(lib, tmp_path):
    disc = lib.create_disc("D", kind="mp3")
    lib.add_to_disc(disc, track_ids(lib))
    data, pregap = build_burn_disc(lib, disc, tmp_path / "work")
    assert isinstance(data, DataDisc) and pregap == 0
    assert all(t.cd_sectors is None for t in lib.tracks())  # audio lengths untouched


# -- the burn window ----------------------------------------------------------

ROOMY = MediaInfo(drive="Test Drive", present=True, blank=True, free_sectors=359_849)


class FakeBurner:
    def __init__(self, media=ROOMY):
        self.media = media
        self.audio, self.data = [], []

    def media_info(self, device):
        return self.media

    def burn(self, disc, options, progress):
        self.audio.append(disc)

    def burn_data(self, disc, options, progress):
        progress("fake: writing the data disc")
        assert disc.folder.is_dir() and all(
            f.exists() for f in disc.files
        )  # still staged
        self.data.append((disc, options))


@pytest.fixture
def dialog_for(qapp, lib, wait_for):
    made = []

    def make(disc_id, burner):
        dialog = BurnDialog(lib, disc_id, burner_factory=lambda: burner)
        made.append(dialog)
        assert wait_for(lambda: not dialog._busy)
        return dialog

    yield make
    for dialog in made:
        dialog.close()
        dialog.deleteLater()


@needs_ffmpeg
def test_an_mp3_disc_is_burned_as_data_and_marked_burned(lib, dialog_for, wait_for):
    disc = lib.create_disc("Warm-up", kind="mp3")
    lib.add_to_disc(disc, track_ids(lib))
    burner = FakeBurner()
    dialog = dialog_for(disc, burner)
    assert (
        "MP3 CD" in dialog.disc_label.text()
        and "320 kbit/s" in dialog.disc_label.text()
    )
    assert "MB" in dialog.drive_label.text()
    dialog.confirm_check.setChecked(True)
    dialog.burn_button.click()
    assert wait_for(lambda: not dialog._busy)
    [(data, options)] = burner.data
    assert burner.audio == [] and isinstance(data, DataDisc) and len(data.files) == 3
    assert options.pregap_sectors == 0
    assert lib.disc(disc).last_burned_at is not None
    assert "MP3 CD" in dialog.log.toPlainText().splitlines()[0]


@needs_ffmpeg
def test_a_disc_in_the_drive_too_small_for_the_files_stops_the_burn(
    lib, dialog_for, wait_for
):
    disc = lib.create_disc("Warm-up", kind="mp3")
    lib.add_to_disc(disc, track_ids(lib))
    small = MediaInfo(drive="Test Drive", present=True, blank=True, free_sectors=mp3.FILESYSTEM_OVERHEAD_SECTORS + 5)  # fmt: skip
    burner = FakeBurner(small)
    dialog = dialog_for(disc, burner)
    assert "MB" in dialog.problems_label.text() or not dialog.burn_button.isEnabled()
    assert burner.data == [] and lib.disc(disc).last_burned_at is None


# -- the window ---------------------------------------------------------------


@pytest.fixture
def window(qapp, lib, monkeypatch):
    for kind in ("information", "warning"):
        monkeypatch.setattr(QMessageBox, kind, staticmethod(lambda *a, **k: None))
    for track in lib.tracks():
        lib.set_cd_sectors(track.id, 400)  # measured already, so adding is immediate
    win = MainWindow(lib)
    yield win, lib
    win.close()
    win.deleteLater()


def ask_with(monkeypatch, answer):
    asked = []

    def question(_parent, title, text, *a, **k):
        asked.append((title, text))
        return answer

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    return asked


def test_going_over_length_offers_an_mp3_cd_and_yes_converts_it(window, monkeypatch):
    win, lib = window
    asked = ask_with(monkeypatch, QMessageBox.StandardButton.Yes)
    disc = lib.create_disc("Mix", capacity_sectors=500)
    win.disc_view.set_disc(disc)
    win.disc_view.add_tracks(track_ids(lib), disc_id=disc)
    [(title, text)] = asked
    assert title == "Make this an MP3 CD?" and "“Mix”" in text and "too long" in text
    summary = lib.disc(disc)
    assert summary.kind == "mp3" and summary.track_count == 3 and not summary.over


def test_saying_no_leaves_it_an_audio_cd_and_does_not_ask_again(window, monkeypatch):
    win, lib = window
    asked = ask_with(monkeypatch, QMessageBox.StandardButton.No)
    disc = lib.create_disc("Mix", capacity_sectors=500)
    win.disc_view.set_disc(disc)
    ids = track_ids(lib)
    win.disc_view.add_tracks(ids[:2], disc_id=disc)
    win.disc_view.add_tracks(ids[2:], disc_id=disc)
    assert len(asked) == 1  # the second add didn't nag
    assert (
        lib.disc(disc).kind == "audio" and lib.disc(disc).over
    )  # still flagged as over


def test_nothing_is_asked_while_the_disc_fits_or_when_it_is_already_mp3(
    window, monkeypatch
):
    win, lib = window
    asked = ask_with(monkeypatch, QMessageBox.StandardButton.Yes)
    fits = lib.create_disc("Fits")
    win.disc_view.add_tracks(track_ids(lib), disc_id=fits)
    big = lib.create_disc("Already", capacity_sectors=500, kind="mp3")
    win.disc_view.add_tracks(track_ids(lib), disc_id=big)
    # (the "already used" question on the second add is the existing overlap check)
    assert [t for t, _text in asked if t == "Make this an MP3 CD?"] == []


def test_more_than_99_tracks_also_offers_it(window, monkeypatch):
    win, lib = window
    asked = ask_with(monkeypatch, QMessageBox.StandardButton.No)
    disc = lib.create_disc("Many", capacity_sectors=10_000_000)
    lib._write_disc_tracks(
        disc, track_ids(lib) * 34
    )  # 102 tracks, already past what a CD allows
    win.disc_view._offer_mp3(disc)
    assert "at most 99" in asked[0][1]


def test_the_menus_make_and_convert_mp3_discs(window):
    win, lib = window
    win.new_disc(None, kind="mp3")
    [disc] = lib.discs()
    assert disc.kind == "mp3" and disc.name.startswith("MP3 Disc")
    win.refresh_discs()
    from dubcrate.ui.models import find_selection

    def texts():
        menu = win.disc_tree_menu(
            find_selection(win.disc_tree.model(), ("disc", disc.id))
        )
        return [a.text() for a in menu.actions()]

    assert "Make Audio CD" in texts() and "Make MP3 CD" not in texts()
    win.disc_view.set_disc_kind(disc.id, "audio")
    assert lib.disc(disc.id).kind == "audio" and "Make MP3 CD" in texts()
    assert win.act_new_mp3_disc.shortcut().toString() in (
        "Ctrl+Shift+N",
        "Meta+Shift+N",
    )


def test_a_new_mp3_disc_uses_the_bitrate_from_settings(window):
    from dubcrate import settings

    win, lib = window
    settings.set_mp3_bitrate(192)
    disc = win.disc_view.new_disc(None, "mp3")
    assert lib.disc(disc).mp3_bitrate == 192
    settings.set_mp3_bitrate(999)  # not one we offer: back to the default
    assert settings.get_mp3_bitrate() == mp3.DEFAULT_BITRATE


def test_the_capacity_bar_says_megabytes_for_an_mp3_disc(window):
    win, lib = window
    disc = lib.create_disc("M", kind="mp3")
    lib.add_to_disc(disc, track_ids(lib))
    win.disc_view.set_disc(disc)
    text = win.disc_view.summary.label.text()
    assert "MB" in text and "MP3 CD" in text and "of 703 MB" in text


# -- DiscRecording, without a drive -------------------------------------------


@pytest.mark.skipif(sys.platform != "darwin", reason="DiscRecording is macOS only")
def test_discrecording_makes_a_data_track_of_the_staged_folder(tmp_path):
    from dubcrate.burn.discrecording import build_data_track

    folder = tmp_path / "VOLUME"
    folder.mkdir()
    (folder / "01 A - B.mp3").write_bytes(b"x" * 5000)
    track = build_data_track(DataDisc("VOLUME", folder, [folder / "01 A - B.mp3"], 0))
    estimate = int(track.estimateLength())
    assert (
        mp3.FILESYSTEM_OVERHEAD_SECTORS
        <= estimate
        < mp3.FILESYSTEM_OVERHEAD_SECTORS + 20
    )


def test_the_cdrdao_backend_says_it_cannot_burn_mp3_discs(tmp_path):
    from dubcrate.burn.cdrdao import CdrdaoBurner

    with pytest.raises(BurnError, match="audio CDs"):
        CdrdaoBurner().burn_data(DataDisc("V", tmp_path), None, print)


# -- the command line ---------------------------------------------------------


@needs_ffmpeg
def test_the_command_line_makes_and_shows_an_mp3_disc(lib, capsys):
    from dubcrate import cli_discs
    from dubcrate.cli import build_parser

    def run(*argv):
        return cli_discs.run(lib, build_parser().parse_args(argv))

    assert run("disc", "new", "Warm-up", "--mp3", "--bitrate", "192") == 0
    [disc] = lib.discs()
    assert (disc.kind, disc.mp3_bitrate) == ("mp3", 192)
    assert run("disc", "add", str(disc.id), "--track", *map(str, track_ids(lib))) == 0
    assert run("disc", "list") == 0
    out = capsys.readouterr().out
    assert (
        "created MP3 disc" in out
        and "MP3 CD" in out
        and "192 kbit/s" in out
        and " MB " in out
    )
    assert "MP3" in out.splitlines()[-1]
