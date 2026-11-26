"""MP3 discs in the Sync tab: listed beside the audio discs (the disc, not its folders), ticked, and synced with the
Sync button or the card's right-click, on real rendered MP3s and a card made here."""

import shutil
import subprocess
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from dubcrate import card_mp3
from dubcrate import cdj_card as cc
from dubcrate import cdj_card_mp3 as m3
from dubcrate.library import Library
from dubcrate.library.folder import scan_folder
from dubcrate.ui import sync_view
from dubcrate.ui.sync_view import COL_NAME, CardPanel, SyncView

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="no ffmpeg"
)


def tone(path: Path, seconds: float, title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-v", "error", "-f", "lavfi", "-i", f"sine=duration={seconds}",
         "-c:a", "libmp3lame", "-b:a", "128k", "-ar", "44100", "-metadata", f"title={title}", "-metadata", "artist=Me", str(path)],
        check=True,
    )  # fmt: skip


@pytest.fixture
def lib(tmp_path):
    music = tmp_path / "music"
    for title, seconds in (("One", 6), ("Two", 7), ("Three", 8), ("Four", 9)):
        tone(music / f"{title}.mp3", seconds, title)
    scan = scan_folder(music)
    library = Library.open(":memory:")
    library.replace_source("folder", str(music), "music", scan.tracks, scan.nodes)
    ids = {t.title: t.id for t in library.tracks()}
    disc = library.create_disc("Warm-up", kind="mp3")
    folder = library.create_disc_dir(disc, "Folder A")
    library.add_to_disc(disc, [ids["One"], ids["Two"]])
    library.add_to_disc(disc, [ids["Three"], ids["Four"]], dir_id=folder)
    library.disc_id = disc
    yield library
    library.close()


def layout_of(lib, tmp_path):
    """The three numbers the card's entry for the disc must have: what the disc's files make."""
    request = card_mp3.collect_request(lib, lib.disc_id, card_mp3.RekordboxCues(lib))
    work = tmp_path / "layout"
    work.mkdir()
    return card_mp3.prepare_disc(
        request, work, analyse=lambda _p: [5] * 100, waves=False
    ).disc


def make_card(tmp_path, lib=None, entry=True, mpj=True, wrong=False):
    """A card with the audio files' folder and, if asked, the player's MP3 files with an entry for the disc."""
    card = tmp_path / "card"
    cdj = card / "PIONEER" / "CDJ"
    cdj.mkdir(parents=True)
    (cdj / "CDJINDX0.PDJ").write_bytes(cc.IndexFile().to_bytes())
    if mpj:
        mp = card / "PIONEER" / "MPJ"
        mp.mkdir()
        entries = []
        if entry:
            d = layout_of(lib, tmp_path)
            sectors = d.image_sectors + (1 if wrong else 0)
            raw = bytes([5, 0x2C, 6, d.file_count, 0x33, 0x87, 0x45, 0xE7]) + d.last_file_start.to_bytes(3, "big") + sectors.to_bytes(3, "big") + b"\0\0"  # fmt: skip
            entries.append(m3.Mp3IndexEntry("MPJ00001", m3.Mp3DiscId(raw)))
        (mp / "MPJINDX0.PDJ").write_bytes(m3.Mp3Index(entries).to_bytes())
    return card


@pytest.fixture
def view(qapp, lib, tmp_path, monkeypatch, wait_for):
    monkeypatch.setattr(sync_view, "read_cues", lambda _path: {})
    v = SyncView(lib, backup_dir=tmp_path / "bk")
    asked, problems, said = [], [], []
    answer = {"yes": True}
    monkeypatch.setattr(
        sync_view,
        "ask_sync_confirmation",
        lambda _p, text: asked.append(text) or answer["yes"],
    )
    monkeypatch.setattr(
        sync_view,
        "show_problem",
        lambda _p, title, text: problems.append((title, text)),
    )
    v.message.connect(said.append)

    def look(card):
        v.set_card(card)
        assert wait_for(lambda: bool(v._mp3_rows))
        assert wait_for(lambda: not v._measuring)

    def finished():
        return wait_for(lambda: not v._writing and not v._analysing)

    from types import SimpleNamespace

    ns = SimpleNamespace(
        view=v,
        look=look,
        asked=asked,
        problems=problems,
        said=said,
        answer=answer,
        finished=finished,
    )
    yield ns
    v.deleteLater()


def rows(v):
    return [
        (i.text(COL_NAME), i.text(1), i.toolTip(COL_NAME).splitlines()[0])
        for i in v._disc_items()
    ]


# -- the list --------------------------------------------------------------------------------------


def test_an_mp3_disc_is_listed_as_one_row_with_no_folders_inside(view, lib, tmp_path):
    view.look(make_card(tmp_path, lib))
    items = view.view._disc_items()
    assert [(i.text(0), i.text(1)) for i in items] == [
        ("Warm-up", "4")
    ]  # the disc and its track count
    assert (
        view.view.tree.topLevelItemCount() == 1 and items[0].childCount() == 0
    )  # (Folder A is not shown)
    assert "Folder A" not in " ".join(i.text(0) for i in view.view._walk())


def test_the_row_can_be_ticked_when_the_card_has_the_players_mp3_files_and_says_it_is_checked_at_sync(
    view, lib, tmp_path
):
    view.look(make_card(tmp_path, lib))
    item = view.view._disc_items()[0]
    assert item.checkState(COL_NAME) == Qt.CheckState.Unchecked  # (it has a box)
    assert "checked when you sync" in item.toolTip(COL_NAME)
    item.setCheckState(COL_NAME, Qt.CheckState.Checked)
    assert view.view.ticked_mp3_ids() == [lib.disc_id] and view.view.ticked_ids() == []
    assert view.view.subtitle.text() == "1 of 1 disc selected"
    assert view.view.sync_button.isEnabled()


def test_a_card_with_no_mp3_files_says_to_play_a_disc_first_and_the_row_has_no_box(
    view, lib, tmp_path
):
    view.look(make_card(tmp_path, lib, mpj=False))
    item = view.view._disc_items()[0]
    assert not view.view._tickable(item)
    assert "Play an MP3 disc in the player first" in item.toolTip(COL_NAME)
    assert view.view.ticked_mp3_ids() == []


def test_no_card_chosen_says_so(view, lib):
    view.look(None)
    assert "Choose a card" in view.view._disc_items()[0].toolTip(COL_NAME)


def test_picking_the_row_explains_it_in_the_middle_pane(view, lib, tmp_path):
    view.look(make_card(tmp_path, lib))
    view.view.tree.setCurrentItem(view.view._disc_items()[0])
    browser = view.view.browser
    assert browser.title.text() == "Warm-up"
    assert "The player must have played this disc once" in browser.message.text()
    assert "worked out when you press Sync" in browser.message.text()
    assert [r.number for r in browser.rows] == [
        1,
        2,
        3,
        4,
    ]  # (the tracks, as for an audio disc)
    assert all(r.state == "pending" or r.values for r in browser.rows) and browser.table.topLevelItemCount() == 4  # fmt: skip


def test_the_tracks_table_shows_each_track_with_its_folder_and_waveform_state(
    view, lib, tmp_path
):
    ids = {t.title: t.id for t in lib.tracks()}
    lib.set_waveform(ids["One"], [3] * 100)
    rows = card_mp3.mp3_wave_rows(lib, lib.disc_id)
    assert [r.number for r in rows] == [1, 2, 3, 4]
    assert [r.name for r in rows] == [
        "One",
        "Two",
        "[Folder A] Three",
        "[Folder A] Four",
    ]
    assert all(r.seconds > 0 for r in rows)
    assert (rows[0].state, len(rows[0].values)) == (
        "write",
        100,
    )  # (the one the library has)
    pending = rows[1:]
    assert all(r.state == "pending" and r.note == "worked out when you sync" and not r.values for r in pending)  # fmt: skip


# -- the Sync button -------------------------------------------------------------------------------


def tick(view_):
    view_._disc_items()[0].setCheckState(COL_NAME, Qt.CheckState.Checked)


def test_the_sync_button_writes_the_discs_waveforms_without_asking(view, lib, tmp_path):
    card = make_card(tmp_path, lib)
    view.look(card)
    tick(view.view)
    before = (card / "PIONEER" / "MPJ" / "MPJINDX0.PDJ").read_bytes()
    view.view.sync_button.click()
    assert view.finished()
    assert (
        view.asked == []
    )  # (no question: the card's files are copied aside, nothing is changed)
    written = m3.read_mp3_card(card)[1]["MPJ00001"]
    assert [w.key for w in written.coarse] == [
        (0, 1),
        (0, 2),
        (1, 1),
        (1, 2),
    ] and written.cues == []
    assert (
        card / "PIONEER" / "MPJ" / "MPJINDX0.PDJ"
    ).read_bytes() == before  # the index is not touched
    assert list((tmp_path / "bk").glob("card-*-mpj")) and not view.problems
    assert any("Wrote 1 MP3 disc file" in m for m in view.said)
    assert "Synced: 4 waveforms, 0 cues added" in view.view._disc_items()[0].toolTip(
        COL_NAME
    )  # (the list says so)
    assert all(
        t.waveform for t in lib.tracks()
    )  # (what was worked out is cached for next time)


def test_a_second_sync_finds_nothing_to_do_and_does_not_ask(view, lib, tmp_path):
    card = make_card(tmp_path, lib)
    view.look(card)
    tick(view.view)
    view.view.sync_button.click()
    assert view.finished()
    view.view._disc_items()[0].setCheckState(COL_NAME, Qt.CheckState.Checked)
    view.view.sync_button.click()
    assert view.finished()
    assert view.asked == []
    assert any("already has everything" in m for m in view.said)
    assert "In sync" in view.view._disc_items()[0].toolTip(COL_NAME)


def test_the_sync_ejects_the_card_when_the_box_says_so(
    view, lib, tmp_path, monkeypatch
):
    ejected = []
    monkeypatch.setattr(sync_view, "eject_card", ejected.append)
    card = make_card(tmp_path, lib)
    view.look(card)
    view.view._can_eject = lambda _p: True
    view.view._update_buttons()
    view.view.eject_check.setChecked(True)
    tick(view.view)
    view.view.sync_button.click()
    assert view.finished()
    assert ejected == [card]
    assert any("Ejected" in m for m in view.said)


def test_the_sync_leaves_the_card_in_when_the_box_is_not_ticked(
    view, lib, tmp_path, monkeypatch
):
    ejected = []
    monkeypatch.setattr(sync_view, "eject_card", ejected.append)
    card = make_card(tmp_path, lib)
    view.look(card)
    view.view._can_eject = lambda _p: True
    view.view._update_buttons()
    view.view.eject_check.setChecked(False)
    tick(view.view)
    view.view.sync_button.click()
    assert view.finished() and ejected == []


def test_the_progress_bar_shows_while_a_sync_works_and_goes_when_it_is_done(
    view, lib, tmp_path
):
    card = make_card(tmp_path, lib)
    view.look(card)
    panel = view.view.disc_panel
    assert not view.view.progress.isVisibleTo(panel)
    tick(view.view)
    seen = []
    view.view.busy_changed.connect(
        lambda _b: seen.append(view.view.progress.isVisibleTo(panel))
    )
    view.view.sync_button.click()
    assert view.finished()
    assert seen and seen[0] is True  # (it showed when the work began)
    assert not view.view.progress.isVisibleTo(panel)


def test_a_disc_the_player_has_not_played_is_explained_and_nothing_is_written(
    view, lib, tmp_path
):
    card = make_card(
        tmp_path, lib, wrong=True
    )  # an entry that is not this disc's image
    view.look(card)
    tick(view.view)
    view.view.sync_button.click()
    assert view.finished()
    assert view.asked == []
    ((title, text),) = view.problems
    assert (
        title == "Couldn't Sync to the Card"
        and "Play the disc in the player once" in text
    )
    assert not (card / "PIONEER" / "MPJ" / "MPJ00001.PDJ").exists()


def test_the_sync_button_does_the_mp3_discs_first_and_then_calls_what_comes_next(
    view, lib, tmp_path
):
    card = make_card(tmp_path, lib)
    view.look(card)
    tick(view.view)
    order = []
    view.view.sync_mp3(card, [lib.disc_id], then=lambda: order.append("then"))
    assert view.finished()
    assert order == ["then"]  # (and it did not eject the card: the next sync would)
    assert m3.read_mp3_card(card)[1]["MPJ00001"].coarse


def test_a_second_disc_is_synced_in_the_same_run_and_the_one_that_cannot_be_is_explained(
    view, lib, tmp_path
):
    ids = {t.title: t.id for t in lib.tracks()}
    second = lib.create_disc("Second", kind="mp3")
    lib.add_to_disc(second, [ids["One"], ids["Four"]])
    card = make_card(tmp_path, lib)  # (an entry for the first disc only)
    view.look(card)
    view.view.sync_mp3(card, [lib.disc_id, second])
    assert view.finished()
    assert view.asked == []
    ((title, text),) = (
        view.problems
    )  # (one box, for the disc that could not be written)
    assert title == "Couldn't Sync Every Disc"
    assert "“Second”" in text and "Play the disc in the player once" in text
    assert m3.read_mp3_card(card)[1]["MPJ00001"].coarse


# -- the card's right-click ------------------------------------------------------------------------


def test_the_card_menu_offers_each_mp3_disc_and_asks_for_it(qapp, tmp_path):
    card = tmp_path / "CARD"
    (card / "PIONEER" / "CDJ").mkdir(parents=True)
    (card / "PIONEER" / "CDJ" / "CDJINDX0.PDJ").write_bytes(cc.IndexFile().to_bytes())
    panel = CardPanel(
        volumes=lambda: [card], mp3_discs=lambda: [(7, "Warm-up"), (9, "Peak Hour")]
    )
    panel.refresh()
    asked = []
    panel.mp3_sync_requested.connect(lambda path, disc: asked.append((path, disc)))
    menu = panel.card_menu(card)
    chooser = next(
        a.menu()
        for a in menu.actions()
        if a.menu() is not None and "MP3 Disc" in a.text()
    )
    assert [a.text() for a in chooser.actions()] == ["Warm-up", "Peak Hour"]
    chooser.actions()[1].trigger()
    assert asked == [(card, 9)]
    # no MP3 discs: no entry in the menu
    bare = CardPanel(volumes=lambda: [card])
    bare.refresh()
    assert not any(a.menu() is not None for a in bare.card_menu(card).actions())


# -- what a code review of the first version found --------------------------------------------------


def test_the_tick_on_an_mp3_disc_survives_the_list_being_rebuilt(view, lib, tmp_path):
    view.look(make_card(tmp_path, lib))
    tick(view.view)
    view.view._look_at_discs(
        view.view._loaded
    )  # what a refresh, a measuring or a rekordbox re-read does
    assert view.view.ticked_mp3_ids() == [lib.disc_id]
    assert view.view._disc_items()[0].checkState(COL_NAME) == Qt.CheckState.Checked


def test_the_card_row_counts_the_mp3_discs_with_the_audio_ones_as_the_player_does(
    qapp, lib, tmp_path
):
    card = make_card(tmp_path, lib)  # no audio discs, one MP3 disc
    panel = CardPanel(volumes=lambda: [card])
    assert "1 disc" in panel._detail(card, False) and "1 discs" not in panel._detail(
        card, False
    )
    index = cc.IndexFile.parse((card / "PIONEER" / "CDJ" / "CDJINDX0.PDJ").read_bytes())
    for number in range(25):  # the owner's card: 25 audio discs and the player says 26
        index.entries.append(
            cc.IndexEntry(
                f"CDJ{number + 1:05d}",
                cc.DiscId(3 + number, 0, 9000 + number, 200, 8000),
            )
        )
    (card / "PIONEER" / "CDJ" / "CDJINDX0.PDJ").write_bytes(index.to_bytes())
    assert "26 discs" in panel._detail(card, False)
    assert m3.mp3_disc_count(card) == 1 and m3.mp3_disc_count(tmp_path / "nothing") == 0


def test_a_right_click_sync_on_another_card_makes_it_the_shown_card_first(
    view, lib, tmp_path
):
    first = make_card(tmp_path, lib)
    second = tmp_path / "second"
    shutil.copytree(first, second)
    view.look(first)
    view.view.sync_mp3_disc(second, lib.disc_id)
    assert view.finished()
    assert (
        view.view._card_path == second
    )  # (the list, the eject box and the row states are for the card written)
    assert m3.read_mp3_card(second)[1]["MPJ00001"].coarse
    assert not (
        first / "PIONEER" / "MPJ" / "MPJ00001.PDJ"
    ).exists()  # the one shown before was left alone
    assert view.asked == []


def test_a_write_that_reads_back_differently_is_explained_with_the_backup_not_a_crash(
    view, lib, tmp_path, monkeypatch
):
    view.look(make_card(tmp_path, lib))
    tick(view.view)

    def broken(*_args):
        raise cc.CardFormatError(
            "MPJ00001.PDJ read back differently from what was written."
        )

    monkeypatch.setattr(sync_view, "apply_mp3_plan", broken)
    view.view.sync_button.click()
    assert view.finished()
    ((title, text),) = view.problems
    assert (
        title == "Couldn't Sync to the Card"
        and "read back differently" in text
        and "copied to" in text
    )
    assert not view.view._writing


def test_the_audio_discs_ticked_with_a_blocked_mp3_disc_still_go_ahead(
    view, lib, tmp_path
):
    card = make_card(tmp_path, lib, wrong=True)  # the player does not know this disc
    view.look(card)
    after = []
    view.view.sync_mp3(card, [lib.disc_id], then=lambda: after.append("audio"))
    assert view.finished()
    assert (
        len(view.problems) == 1
        and "Play the disc in the player once" in view.problems[0][1]
    )
    assert after == ["audio"]  # (it was not dropped silently)


def test_from_drive_is_refused_before_any_audio_disc_is_written(lib, tmp_path, capsys):
    import argparse

    from dubcrate import cli_card

    audio = lib.create_disc("Audio")
    lib.add_to_disc(audio, [lib.tracks()[0].id])
    card = make_card(tmp_path, lib)
    before = sorted(p.name for p in (card / "PIONEER").rglob("*"))
    args = argparse.Namespace(disc=[audio, lib.disc_id], card=str(card), from_drive=True, yes=True, backup_dir=None, no_waveforms=True)  # fmt: skip
    assert cli_card.cmd_sync(lib, args) == 1
    assert "--from-drive is for an audio disc" in capsys.readouterr().err
    assert (
        sorted(p.name for p in (card / "PIONEER").rglob("*")) == before
        and not (tmp_path / "bk").exists()
    )


# -- Import Cues from the card ---------------------------------------------------------------------


def test_import_cues_brings_in_what_was_set_on_the_player_for_an_mp3_disc(
    view, lib, tmp_path, monkeypatch, wait_for
):
    """The player wrote a cue for the second file of "Folder A" (the disc's fourth track): Import Cues names the
    track and gives the time in audio time. Needs the image's record, which is how the card's entry is found."""
    from dubcrate.library.models import DiscImage

    card = make_card(tmp_path, lib)
    again = tmp_path / "again"  # (`make_card` used `tmp_path / "layout"` already)
    again.mkdir()
    layout = layout_of(lib, again)
    lib.set_disc_image(
        lib.disc_id,
        DiscImage(
            "2026-11-24T10:00:00+00:00",
            "Warm-up",
            layout.file_count,
            1,
            layout.last_file_start,
            layout.image_sectors,
        ),
    )
    entries = lib.disc_entries(lib.disc_id)
    fourth = entries[3].track  # "Four", 9 s: file 2 of folder 1 ("Folder A")
    length = round(fourth.duration * 75) + 2
    mpj = card / "PIONEER" / "MPJ"
    index = m3.Mp3Index.parse((mpj / "MPJINDX0.PDJ").read_bytes())
    file = m3.Mp3TrackFile.new(index.entries[0].disc_id)
    file.cues = [m3.Mp3Entry(m3.KIND_CUE, 1, 2, 300, 0, length, 0, 0, 0, 0)]
    file.cue_indexes = [0]
    (mpj / "MPJ00001.PDJ").write_bytes(file.to_bytes())

    view.look(card)
    shown = []
    monkeypatch.setattr(sync_view, "ask_import", lambda _p, found: shown.extend(found))
    assert view.view.import_button.isEnabled()
    view.view.import_from_card()
    assert wait_for(lambda: shown)  # (the MP3 files are read on a worker first)
    [finding] = shown
    [only] = finding.tracks
    assert (only.number, only.title) == (4, "Four")
    lead = card_mp3.rendered_lead_ms(fourth.path, None)
    assert [(r.kind, r.ms) for r in only.rows] == [("add", 4000 - lead)]


def test_import_cues_says_so_when_an_mp3_disc_has_no_record_of_its_image(
    view, lib, tmp_path, wait_for
):
    view.look(make_card(tmp_path, lib))
    view.view.import_from_card()
    assert wait_for(lambda: "1 MP3 disc couldn't be checked" in " ".join(view.said))


def test_the_tracks_table_shows_what_the_card_has_for_a_disc_with_a_recorded_image(
    view, lib, tmp_path, wait_for
):
    """With the disc's image recorded the card's file is found without rendering: the table shows the player's own
    waveform for a track and marks the cue it set."""
    from dubcrate.library.models import DiscImage

    card = make_card(tmp_path, lib)
    again = tmp_path / "again"
    again.mkdir()
    layout = layout_of(lib, again)
    lib.set_disc_image(
        lib.disc_id,
        DiscImage(
            "2026-11-24T10:00:00+00:00",
            "Warm-up",
            layout.file_count,
            1,
            layout.last_file_start,
            layout.image_sectors,
        ),
    )
    mpj = card / "PIONEER" / "MPJ"
    index = m3.Mp3Index.parse((mpj / "MPJINDX0.PDJ").read_bytes())
    file = m3.Mp3TrackFile.new(index.entries[0].disc_id)
    file.coarse = [m3.Mp3Wave(0, 2, bytes([7] * 50))]
    file.fine = [m3.Mp3Wave(0, 2, bytes([0x54] * 50))]
    length = round(lib.disc_entries(lib.disc_id)[1].track.duration * 75) + 2
    file.cues = [m3.Mp3Entry(m3.KIND_CUE, 0, 2, 300, 0, length, 0, 0, 0, 0)]
    file.cue_indexes = [0]
    (mpj / "MPJ00001.PDJ").write_bytes(file.to_bytes())

    view.look(card)
    view.view.tree.setCurrentItem(view.view._disc_items()[0])
    browser = view.view.browser
    # (the files are read on a worker first: the plain table shows now, then the one with the card's records)
    assert wait_for(lambda: [r.state for r in browser.rows][1:2] == ["card"])
    rows = browser.rows
    assert [r.state for r in rows][:2] == ["pending", "card"]
    assert rows[1].values[:2] == (4, 5) and len(rows[1].marks) == 1
    assert "already on the card" in rows[1].note


def test_a_sync_records_a_baseline_and_a_cue_deleted_on_the_player_is_offered_for_deletion(
    view, lib, tmp_path, monkeypatch, wait_for
):
    """The whole way round: Dubcrate has a memory cue on "One", a sync puts it on the card and records what the card
    holds, the player deletes it, and Import Cues offers to delete it in Dubcrate too (it cannot tell without the
    baseline)."""
    from dubcrate import mp3_sync
    from dubcrate.library.models import DiscImage

    card = make_card(tmp_path, lib)
    again = tmp_path / "again"
    again.mkdir()
    layout = layout_of(lib, again)
    lib.set_disc_image(
        lib.disc_id,
        DiscImage(
            "2026-11-24T10:00:00+00:00",
            "Warm-up",
            layout.file_count,
            1,
            layout.last_file_start,
            layout.image_sectors,
        ),
    )
    key = mp3_sync.baseline_key(
        layout.file_count, layout.last_file_start, layout.image_sectors
    )
    one = lib.disc_entries(lib.disc_id)[0].track
    lib.set_memory_cues(one.path, [(2000, "mine")])
    view.look(card)
    assert lib.card_baseline(key) is None  # (no sync recorded yet)
    tick(view.view)
    view.view.sync_button.click()
    assert view.finished()
    [(code, position, end)] = lib.card_baseline(key)
    assert mp3_sync.split_code(code) == (0, 1) and end == 0
    lead = card_mp3.rendered_lead_ms(one.path, None)
    assert abs(position - round((2.0 + lead / 1000) * 75)) <= 1
    # the player deletes the cue
    mpj = card / "PIONEER" / "MPJ"
    on_card = m3.Mp3TrackFile.parse((mpj / "MPJ00001.PDJ").read_bytes())
    assert len(on_card.cues) == 1
    on_card.cues, on_card.cue_indexes = [], []
    (mpj / "MPJ00001.PDJ").write_bytes(on_card.to_bytes())
    view.look(card)
    shown = []
    monkeypatch.setattr(sync_view, "ask_import", lambda _p, found: shown.extend(found))
    view.view.import_from_card()
    assert wait_for(lambda: shown)
    [finding] = shown
    assert finding.has_baseline
    [only] = finding.tracks
    assert [(r.kind, r.ms) for r in only.rows] == [("remove", 2000)]


def test_a_second_sync_that_has_nothing_to_write_still_records_the_baseline(
    view, lib, tmp_path
):
    from dubcrate import mp3_sync

    card = make_card(tmp_path, lib)
    work = tmp_path / "x"
    work.mkdir()
    layout = layout_of(lib, work)
    key = mp3_sync.baseline_key(
        layout.file_count, layout.last_file_start, layout.image_sectors
    )
    view.look(card)
    tick(view.view)
    view.view.sync_button.click()
    assert view.finished()
    assert lib.card_baseline(key) == []  # (a disc with no cues: recorded, and empty)
    lib.set_card_baseline(key, [(mp3_sync.file_code(0, 1), 5, 0)])  # (a stale one)
    tick(view.view)
    view.view.sync_button.click()
    assert view.finished()
    assert any("already has everything" in m for m in view.said)
    assert (
        lib.card_baseline(key) == []
    )  # (the card has all of it: that is the baseline now)


def test_the_command_line_sync_records_the_baseline_only_when_it_writes(
    lib, tmp_path, capsys
):
    import argparse

    from dubcrate import cli_card, mp3_sync

    card = make_card(tmp_path, lib)
    work = tmp_path / "x"
    work.mkdir()
    layout = layout_of(lib, work)
    key = mp3_sync.baseline_key(
        layout.file_count, layout.last_file_start, layout.image_sectors
    )
    one = lib.disc_entries(lib.disc_id)[0].track
    lib.set_memory_cues(one.path, [(2000, "mine")])

    def sync(yes):
        args = argparse.Namespace(disc=[lib.disc_id], card=str(card), from_drive=False, yes=yes, backup_dir=str(tmp_path / "bk"), no_waveforms=False)  # fmt: skip
        return cli_card.cmd_sync(lib, args)

    assert (
        sync(False) == 0 and lib.card_baseline(key) is None
    )  # (a dry run records nothing)
    assert sync(True) == 0
    [(code, _position, end)] = lib.card_baseline(key)
    assert mp3_sync.split_code(code) == (0, 1) and end == 0
    lib.set_card_baseline(key, [])
    assert sync(True) == 0  # (nothing left to write: the card has it all)
    assert len(lib.card_baseline(key)) == 1
    assert "error" not in capsys.readouterr().err
