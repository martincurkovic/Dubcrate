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
    assert browser.rows == []  # (no tracks table: the explanation is what is shown)


# -- the Sync button -------------------------------------------------------------------------------


def tick(view_):
    view_._disc_items()[0].setCheckState(COL_NAME, Qt.CheckState.Checked)


def test_the_sync_button_writes_the_discs_waveforms_after_asking(view, lib, tmp_path):
    card = make_card(tmp_path, lib)
    view.look(card)
    tick(view.view)
    before = (card / "PIONEER" / "MPJ" / "MPJINDX0.PDJ").read_bytes()
    view.view.sync_button.click()
    assert view.finished()
    assert (
        len(view.asked) == 1 and "to add to MPJ00001.PDJ: 4 waveforms" in view.asked[0]
    )
    assert "copied to" in view.asked[0] and "Not tried on a player yet" in view.asked[0]
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
    assert view.finished() and len(view.asked) == 1
    view.view._disc_items()[0].setCheckState(COL_NAME, Qt.CheckState.Checked)
    view.view.sync_button.click()
    assert view.finished()
    assert len(view.asked) == 1  # (not asked again)
    assert any("already has everything" in m for m in view.said)
    assert "In sync" in view.view._disc_items()[0].toolTip(COL_NAME)


def test_declining_writes_nothing(view, lib, tmp_path):
    card = make_card(tmp_path, lib)
    view.look(card)
    tick(view.view)
    view.answer["yes"] = False
    before = sorted(p.name for p in (card / "PIONEER" / "MPJ").iterdir())
    view.view.sync_button.click()
    assert view.finished()
    assert (
        sorted(p.name for p in (card / "PIONEER" / "MPJ").iterdir()) == before
    )  # no file made, nothing changed
    assert (
        any("Nothing was written" in m for m in view.said)
        and not (tmp_path / "bk").exists()
    )


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


def test_a_second_disc_is_synced_in_the_same_run_with_one_question(view, lib, tmp_path):
    ids = {t.title: t.id for t in lib.tracks()}
    second = lib.create_disc("Second", kind="mp3")
    lib.add_to_disc(second, [ids["One"], ids["Four"]])
    card = make_card(tmp_path, lib)  # (an entry for the first disc only)
    view.look(card)
    view.view.sync_mp3(card, [lib.disc_id, second])
    assert view.finished()
    assert len(view.asked) == 1  # one question for both
    text = view.asked[0]
    assert "“Warm-up”: to add to MPJ00001.PDJ" in text  # the first is written ...
    assert (
        "“Second”" in text and "Play the disc in the player once" in text
    )  # ... the second is explained in it
    assert view.problems == []  # (no second dialog)
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
