import shutil
import subprocess
import wave

import pytest

from dubcrate import cli
from dubcrate.build import (
    BuildError,
    build_burn_disc,
    disc_problems,
    media_notes,
    media_problems,
    plan_burn,
)
from dubcrate.burn import BurnError, MediaInfo
from dubcrate.cd import SAMPLES_PER_SECTOR
from dubcrate.library import Library
from dubcrate.library.folder import scan_folder
from dubcrate.library.lengths import measure_tracks

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg not installed",
)


def make_flac(path, seconds=2.0, **tags):
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = [arg for k, v in tags.items() for arg in ("-metadata", f"{k}={v}")]
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
         f"sine=duration={seconds}", *meta, str(path)],
        check=True,
    )  # fmt: skip
    return path


@pytest.fixture
def lib(tmp_path):
    music = tmp_path / "music"
    make_flac(music / "a.flac", 2.0, title="Alpha", artist="Same")
    make_flac(music / "b.flac", 3.0, title="Beta", artist="Same")
    make_flac(music / "c.flac", 1.5, title="Gamma", artist="Other")
    scan = scan_folder(music)
    with Library.open(":memory:") as library:
        library.replace_source("folder", str(music), "music", scan.tracks, scan.nodes)
        yield library


def all_ids(lib):
    return [t.id for t in lib.tracks(sort="title")]


def test_measure_fills_in_exact_lengths_once(lib):
    ids = all_ids(lib)
    assert all(t.cd_sectors is None for t in lib.tracks())
    assert measure_tracks(lib, ids) == []
    got = {t.title: t.cd_sectors for t in lib.tracks()}
    assert got == {"Alpha": 150, "Beta": 225, "Gamma": 113}  # 2.0 s, 3.0 s, 1.5 s
    lib.set_cd_sectors(ids[0], 999)  # already measured: left alone
    measure_tracks(lib, ids)
    assert lib.track(ids[0]).cd_sectors == 999


def test_measure_reports_unreadable_files_and_leaves_them_unmeasured(lib, tmp_path):
    [alpha] = lib.tracks(search="Alpha")
    (tmp_path / "music" / "a.flac").write_bytes(b"not audio")
    problems = measure_tracks(lib, [alpha.id])
    assert len(problems) == 1 and "a.flac" in problems[0]
    assert lib.track(alpha.id).cd_sectors is None


def test_measure_skips_tracks_whose_file_is_missing(lib, tmp_path):
    music = tmp_path / "music"
    scan = scan_folder(music)
    (music / "b.flac").unlink()
    lib.replace_source("folder", str(music), "music", scan.tracks, scan.nodes)
    [beta] = lib.tracks(search="Beta")
    assert beta.missing
    assert measure_tracks(lib, [beta.id]) == []
    assert lib.track(beta.id).cd_sectors is None  # keeps its conservative estimate


def test_build_renders_sector_aligned_wavs_and_records_exact_lengths(lib, tmp_path):
    disc_id = lib.create_disc("Night")
    lib.add_to_disc(disc_id, all_ids(lib))
    messages = []
    burn_disc, pregap = build_burn_disc(
        lib, disc_id, tmp_path / "work", progress=messages.append
    )
    assert pregap == 0 and burn_disc.title == "Night"
    assert [t.title for t in burn_disc.tracks] == ["Alpha", "Beta", "Gamma"]
    assert len(messages) == 3
    for t in burn_disc.tracks:
        with wave.open(str(t.wav)) as w:
            assert w.getnframes() == t.sectors * SAMPLES_PER_SECTOR
    # Rendered lengths are saved back as the authoritative ones.
    assert [e.track.cd_sectors for e in lib.disc_entries(disc_id)] == [
        t.sectors for t in burn_disc.tracks
    ]


def test_performer_is_the_common_artist_or_various(lib, tmp_path):
    same = lib.create_disc("Same")
    lib.add_to_disc(same, [t.id for t in lib.tracks(search="Same")])
    disc, _ = build_burn_disc(lib, same, tmp_path / "w1")
    assert disc.performer == "Same"
    mixed = lib.create_disc("Mixed")
    lib.add_to_disc(mixed, all_ids(lib))
    disc, _ = build_burn_disc(lib, mixed, tmp_path / "w2")
    assert disc.performer == "Various Artists"


def test_pregap_is_passed_through(lib, tmp_path):
    disc_id = lib.create_disc("Gaps", pregap_sectors=150)
    lib.add_to_disc(disc_id, all_ids(lib)[:2])
    _, pregap = build_burn_disc(lib, disc_id, tmp_path / "w")
    assert pregap == 150


def test_an_empty_or_overfull_disc_is_refused_before_rendering(lib, tmp_path):
    empty = lib.create_disc("Empty")
    assert disc_problems(lib, empty) == ["the disc is empty"]
    with pytest.raises(BuildError, match="empty"):
        build_burn_disc(lib, empty, tmp_path / "w")
    tiny = lib.create_disc("Tiny", capacity_sectors=200)
    lib.add_to_disc(tiny, all_ids(lib))
    with pytest.raises(BuildError, match="over capacity"):
        build_burn_disc(lib, tiny, tmp_path / "w")
    assert not (tmp_path / "w").exists()


def test_a_missing_file_is_a_problem(lib, tmp_path):
    disc_id = lib.create_disc("D")
    ids = all_ids(lib)
    lib.add_to_disc(disc_id, ids)
    [source] = lib.sources()
    (tmp_path / "music" / "b.flac").unlink()
    scan = scan_folder(tmp_path / "music")
    lib.replace_source("folder", source.path, "music", scan.tracks, scan.nodes)
    problems = disc_problems(lib, disc_id)
    assert any("no longer in its source" in p for p in problems)


# -- CLI wiring ---------------------------------------------------------------


def run_cli(tmp_path, *args):
    return cli.main(["--library", str(tmp_path / "lib.db"), *args])


def test_cli_plan_and_burn_dry_run(tmp_path, capsys, monkeypatch):
    music = tmp_path / "music"
    for name, secs in [("one", 2), ("two", 2)]:
        make_flac(music / f"{name}.flac", secs, title=name, artist="X")
    assert run_cli(tmp_path, "import-folder", str(music)) == 0
    assert run_cli(tmp_path, "disc", "new", "CLI disc", "--minutes", "74") == 0
    assert run_cli(tmp_path, "disc", "add", "1", "--node", "1") == 0
    out = capsys.readouterr().out
    assert 'Disc 1 "CLI disc": 74 min, gapless' in out and "2 tracks" in out

    def explode(_name):
        raise AssertionError("a dry run must never construct a burner")

    monkeypatch.setattr("dubcrate.cli_discs.get_burner", explode)
    assert run_cli(tmp_path, "burn", "1") == 0
    assert "dry run" in capsys.readouterr().out


def test_cli_burn_refuses_an_overfull_disc(tmp_path, capsys):
    music = tmp_path / "music"
    make_flac(music / "one.flac", 2, title="one")
    run_cli(tmp_path, "import-folder", str(music))
    run_cli(tmp_path, "disc", "new", "D")
    run_cli(tmp_path, "disc", "add", "1", "--node", "1")
    run_cli(tmp_path, "disc", "set", "1", "--minutes", "74")
    # Shrink it past what the track needs via the library directly.
    with Library.open(tmp_path / "lib.db") as lib:
        lib.update_disc(1, capacity_sectors=10)
    capsys.readouterr()
    assert run_cli(tmp_path, "burn", "1", "--yes") == 2
    assert "over capacity" in capsys.readouterr().err


class FakeBurner:
    def __init__(self, fail: bool = False, media: MediaInfo | None = None):
        self.fail = fail
        self.media = media or MediaInfo()
        self.burned = []

    def media_info(self, device):
        return self.media

    def burn(self, disc, options, progress=None):
        if self.fail:
            raise BurnError("drive on fire")
        self.burned.append((disc, options))


def _planned_cli_disc(tmp_path):
    music = tmp_path / "music"
    for name in ("one", "two"):
        make_flac(music / f"{name}.flac", 2, title=name, artist="X")
    run_cli(tmp_path, "import-folder", str(music))
    run_cli(tmp_path, "disc", "new", "D")
    run_cli(tmp_path, "disc", "add", "1", "--node", "1")


def test_cli_burn_saves_a_record_of_a_real_burn(tmp_path, monkeypatch):
    _planned_cli_disc(tmp_path)
    burner = FakeBurner()
    monkeypatch.setattr("dubcrate.cli_discs.get_burner", lambda _name: burner)
    assert run_cli(tmp_path, "burn", "1", "--yes", "--speed", "12") == 0
    assert len(burner.burned) == 1
    with Library.open(tmp_path / "lib.db") as lib:
        [record] = lib.burns()
        assert (record.disc_id, record.speed, record.simulated) == (1, 12, False)
        assert [t.title for t in record.tracks] == ["one", "two"]
        assert all(t.sectors > 0 for t in record.tracks)


def test_cli_simulated_or_failed_burns_leave_no_record(tmp_path, monkeypatch):
    _planned_cli_disc(tmp_path)
    monkeypatch.setattr("dubcrate.cli_discs.get_burner", lambda _name: FakeBurner())
    assert run_cli(tmp_path, "burn", "1", "--yes", "--simulate") == 0
    monkeypatch.setattr(
        "dubcrate.cli_discs.get_burner", lambda _name: FakeBurner(fail=True)
    )
    assert run_cli(tmp_path, "burn", "1", "--yes") == 1
    with Library.open(tmp_path / "lib.db") as lib:
        assert lib.burns() == []


# -- the disc in the drive ----------------------------------------------------


def test_a_disc_that_holds_enough_or_that_the_drive_cant_describe_is_fine():
    assert media_problems(300_000, MediaInfo(present=True, free_sectors=359_849)) == []
    assert media_problems(300_000, MediaInfo()) == []
    # a free space of 0 means "didn't say", not "full"
    assert media_problems(300_000, MediaInfo(present=True, free_sectors=0)) == []


def test_a_disc_too_small_for_the_audio_is_refused_with_both_lengths():
    [problem] = media_problems(
        350_000, MediaInfo(present=True, blank=True, free_sectors=333_000)
    )
    assert "holds 74:00:00" in problem and "needs 77:46:50" in problem


def test_no_disc_in_the_drive_is_a_problem_but_unknown_is_not():
    assert media_problems(1, MediaInfo(present=False)) == ["no disc in the drive"]
    assert media_problems(1, MediaInfo(present=None)) == []


def test_notes_mention_a_disc_that_isnt_blank_and_how_much_it_holds():
    assert media_notes(MediaInfo()) == []
    notes = media_notes(MediaInfo(present=True, blank=False, free_sectors=333_000))
    assert "doesn't look blank" in notes[0] and "74:00:00" in notes[1]


def test_cli_refuses_to_burn_onto_a_disc_that_is_too_small(
    tmp_path, monkeypatch, capsys
):
    _planned_cli_disc(tmp_path)
    burner = FakeBurner(media=MediaInfo(present=True, blank=True, free_sectors=100))
    monkeypatch.setattr("dubcrate.cli_discs.get_burner", lambda _name: burner)
    assert run_cli(tmp_path, "burn", "1", "--yes") == 1
    err = capsys.readouterr().err
    assert "burn failed" in err and "the disc in the drive holds" in err
    assert burner.burned == []
    with Library.open(tmp_path / "lib.db") as lib:
        assert lib.burns() == []


def test_cli_burns_when_the_disc_is_big_enough_and_says_what_it_holds(
    tmp_path, monkeypatch, capsys
):
    _planned_cli_disc(tmp_path)
    burner = FakeBurner(media=MediaInfo(present=True, blank=True, free_sectors=359_849))
    monkeypatch.setattr("dubcrate.cli_discs.get_burner", lambda _name: burner)
    assert run_cli(tmp_path, "burn", "1", "--yes") == 0
    assert "the disc in the drive holds 79:57:74" in capsys.readouterr().out
    assert len(burner.burned) == 1


def test_cli_stops_when_the_drive_says_there_is_no_disc(tmp_path, monkeypatch, capsys):
    _planned_cli_disc(tmp_path)
    burner = FakeBurner(media=MediaInfo(present=False))
    monkeypatch.setattr("dubcrate.cli_discs.get_burner", lambda _name: burner)
    assert run_cli(tmp_path, "burn", "1", "--yes") == 1
    assert "no disc in the drive" in capsys.readouterr().err
    assert burner.burned == []


# -- what is written on the CD: the disc's own text, not the library's ---------


def test_the_burn_uses_the_text_edited_for_the_disc_not_the_librarys(lib, tmp_path):
    ids = all_ids(lib)
    disc_id = lib.create_disc("My Mix")
    lib.add_to_disc(disc_id, ids)
    lib.set_entry_text(disc_id, 0, "title", "Cleaner Title")
    lib.set_entry_text(disc_id, 1, "artist", "Better Artist")
    plan = plan_burn(lib, disc_id)
    assert [(t.title, t.artist) for t in plan.tracks] == [
        ("Cleaner Title", "Same"), ("Beta", "Better Artist"), ("Gamma", "Other"),
    ]  # fmt: skip
    burn_disc, _ = build_burn_disc(lib, disc_id, tmp_path / "w")
    assert [(t.title, t.performer) for t in burn_disc.tracks] == [
        ("Cleaner Title", "Same"), ("Beta", "Better Artist"), ("Gamma", "Other"),
    ]  # fmt: skip
    assert burn_disc.title == "My Mix"  # the CD's own title is the disc's name


def test_the_files_and_the_library_are_untouched_by_editing_for_a_burn(lib, tmp_path):
    before = {t.title: t.artist for t in lib.tracks()}
    music = tmp_path / "music"
    sizes = {p.name: p.stat().st_size for p in music.glob("*.flac")}
    mtimes = {p.name: p.stat().st_mtime for p in music.glob("*.flac")}
    disc_id = lib.create_disc("D")
    lib.add_to_disc(disc_id, all_ids(lib))
    lib.set_entry_text(disc_id, 0, "title", "Renamed")
    build_burn_disc(lib, disc_id, tmp_path / "w")
    assert {t.title: t.artist for t in lib.tracks()} == before
    assert {p.name: p.stat().st_size for p in music.glob("*.flac")} == sizes
    assert {p.name: p.stat().st_mtime for p in music.glob("*.flac")} == mtimes


def test_the_discs_performer_follows_the_edited_artists(lib):
    disc_id = lib.create_disc("D")
    lib.add_to_disc(disc_id, all_ids(lib))
    for position in range(3):
        lib.set_entry_text(disc_id, position, "artist", "One Artist For All")
    assert plan_burn(lib, disc_id).performer == "One Artist For All"


def test_a_problem_names_the_track_as_it_is_written_on_the_disc(lib, tmp_path):
    disc_id = lib.create_disc("D")
    lib.add_to_disc(disc_id, all_ids(lib))
    lib.set_entry_text(disc_id, 0, "title", "Edited Name")
    (tmp_path / "music" / "a.flac").unlink()
    scan = scan_folder(tmp_path / "music")
    [source] = lib.sources()
    lib.replace_source("folder", source.path, "music", scan.tracks, scan.nodes)
    problems = disc_problems(lib, disc_id)
    assert any("Edited Name" in p for p in problems)


def test_cli_text_edits_one_discs_cd_text_and_shows_it_marked(tmp_path, capsys):
    music = tmp_path / "music"
    make_flac(music / "one.flac", 2, title="Original Title", artist="Original Artist")
    run_cli(tmp_path, "import-folder", str(music))
    run_cli(tmp_path, "disc", "new", "D")
    run_cli(tmp_path, "disc", "add", "1", "--node", "1")
    capsys.readouterr()
    assert (
        run_cli(
            tmp_path,
            "disc",
            "text",
            "1",
            "1",
            "--title",
            "On The CD",
            "--artist",
            "CD Artist",
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "On The CD" in out and "CD Artist" in out and "Original Title" not in out
    assert "* = artist or title edited for this disc only" in out
    with Library.open(tmp_path / "lib.db") as lib:
        assert (lib.tracks()[0].title, lib.tracks()[0].artist) == (
            "Original Title",
            "Original Artist",
        )
        assert lib.disc_entries(1)[0].title == "On The CD"
    assert (
        run_cli(tmp_path, "disc", "text", "1", "1", "--title", "", "--artist", "") == 0
    )
    assert (
        "Original Title" in capsys.readouterr().out
    )  # blank goes back to the original


def test_cli_text_needs_something_to_change_and_a_real_position(tmp_path, capsys):
    music = tmp_path / "music"
    make_flac(music / "one.flac", 2, title="T")
    run_cli(tmp_path, "import-folder", str(music))
    run_cli(tmp_path, "disc", "new", "D")
    run_cli(tmp_path, "disc", "add", "1", "--node", "1")
    capsys.readouterr()
    assert run_cli(tmp_path, "disc", "text", "1", "1") == 2
    assert "give --title and/or --artist" in capsys.readouterr().err
