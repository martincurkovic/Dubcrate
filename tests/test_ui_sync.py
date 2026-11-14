"""The Sync tab: the card panel, the disc list with its dry run, the fixed bottom tab (offscreen)."""

import shutil
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from dubcrate import cdj_card as cc
from dubcrate.analysis import Cue
from dubcrate.library import Library, TrackRecord
from dubcrate.ui import sync_view
from dubcrate.ui.main_window import MainWindow
from dubcrate.ui.source_tabs import SourceTabBar, sd_card_glyph
from dubcrate.ui.sync_view import COL_STATUS, COL_TICK, CardPanel, SyncView

DATA = Path(__file__).parent / "data" / "cdj_card"


def cd2_lengths():
    starts, lead_out = cc.toc_from_drutil(
        (DATA / "CD2_TOC.txt").read_text(encoding="utf-8")
    )
    return [b - a for a, b in zip(starts, [*starts[1:], lead_out], strict=True)]


def card_folder(parent: Path, name="CARD") -> Path:
    """A card as it is mounted: its root, with the index (and nothing else) in PIONEER/CDJ."""
    root = parent / name
    folder = root / "PIONEER" / "CDJ"
    folder.mkdir(parents=True)
    shutil.copy(DATA / "index_one_disc.PDJ", folder / cc.INDEX_NAME)
    return root


@pytest.fixture
def lib():
    with Library.open(":memory:") as library:
        yield library


def add_disc(lib, name, *, source="rekordbox", folder=None, measured=True):
    """An audio disc of CD 2's twelve tracks, each in the rekordbox source (so cues can be looked up)."""
    n = len(lib.sources())
    lib.replace_source(
        source, f"/{source}{n}/master.db", f"S{n}",
        [TrackRecord(ext_id=f"{name}-{i}", path=f"/nonexistent/{name}-{i}.flac", title=f"{name}{i}", duration=60.0) for i in range(12)],
    )  # fmt: skip
    ids = [t.id for t in lib.tracks(sort="path") if t.ext_id.startswith(f"{name}-")]
    if measured:
        for track_id, sectors in zip(ids, cd2_lengths(), strict=True):
            lib.set_cd_sectors(track_id, sectors)
    disc_id = lib.create_disc(name, folder_id=folder)
    lib.add_to_disc(disc_id, ids)
    return disc_id


def shown(view: SyncView) -> list[tuple[str, str]]:
    """(name, status) of each disc row in the order shown."""
    return [(i.text(0), i.text(COL_STATUS)) for i in view._disc_items()]


@pytest.fixture
def cue_view(qapp, lib, monkeypatch, wait_for):
    """A Sync view over a library with rekordbox's cues stood in for."""
    cues = {}
    monkeypatch.setattr(sync_view, "read_cues", lambda _path: cues)
    view = SyncView(lib)
    view.cues = cues

    def look(card=None):
        view.set_card(card)
        assert wait_for(lambda: bool(view._surveys) or not lib.discs())

    view.look = look
    return view


# -- the card panel -----------------------------------------------------------------------------


def test_the_panel_lists_the_cards_that_are_plugged_in_and_picks_the_first(
    qapp, tmp_path
):
    card = card_folder(tmp_path)
    plain = tmp_path / "Backup"
    plain.mkdir()
    panel = CardPanel(volumes=lambda: [plain, card])
    assert panel.current_card() is None and panel.subtitle.text() == "No card found"
    told = []
    panel.card_changed.connect(told.append)
    panel.refresh()
    assert panel.current_card() == card
    assert panel.subtitle.text() == "1 card found" and not panel.hint.isVisibleTo(panel)
    assert told == [card]
    panel.refresh()
    assert told == [card]  # nothing changed: nothing said


def test_a_card_that_is_taken_out_is_gone_after_a_refresh(qapp, tmp_path):
    card = card_folder(tmp_path)
    here = [card]
    panel = CardPanel(volumes=lambda: here)
    panel.refresh()
    told = []
    panel.card_changed.connect(told.append)
    here.clear()
    panel.refresh()
    assert panel.current_card() is None and told == [None]
    assert panel.hint.isVisibleTo(panel)


def test_a_folder_chosen_by_hand_is_listed_and_picked(qapp, tmp_path, monkeypatch):
    copy = card_folder(tmp_path, "copy-of-card")
    panel = CardPanel(volumes=list)
    told = []
    panel.card_changed.connect(told.append)
    monkeypatch.setattr(sync_view, "ask_card_folder", lambda _parent: str(copy))
    panel.choose_folder()
    assert panel.current_card() == copy and told[-1] == copy
    assert panel.cards.item(0).text().endswith("(folder)")
    panel.refresh()  # (kept through a refresh)
    assert panel.current_card() == copy


def test_a_folder_that_is_not_a_card_is_refused_in_plain_words(
    qapp, tmp_path, monkeypatch
):
    panel = CardPanel(volumes=list)
    monkeypatch.setattr(sync_view, "ask_card_folder", lambda _parent: str(tmp_path))
    panel.choose_folder()
    assert panel.current_card() is None
    assert "is this the player's card" in panel.subtitle.text()
    monkeypatch.setattr(sync_view, "ask_card_folder", lambda _parent: "")
    panel.choose_folder()  # (cancelled: nothing changes)
    assert panel.current_card() is None


# -- the disc list ------------------------------------------------------------------------------


def test_discs_are_listed_in_their_folders_with_a_status_each(cue_view, lib, tmp_path):
    inside = lib.create_disc_folder("Sets")
    add_disc(lib, "Friday", folder=inside)
    add_disc(lib, "Alpha")
    cue_view.cues.update({"Friday-0": [Cue("memory", 15.0), Cue("hot", 40.0, slot=1)]})
    cue_view.look(card_folder(tmp_path))
    top = [
        cue_view.tree.topLevelItem(r).text(0)
        for r in range(cue_view.tree.topLevelItemCount())
    ]
    assert top == ["Sets", "Alpha"]  # a folder first, then discs, as in the Discs list
    assert shown(cue_view) == [
        ("Friday", "Not on the card: 2 cues to add"),
        ("Alpha", "No rekordbox cues"),
    ]


def test_with_no_card_the_discs_are_listed_and_the_footer_says_to_choose_one(
    cue_view, lib
):
    add_disc(lib, "One")
    cue_view.look(None)
    assert shown(cue_view) == [("One", "Choose a card")]
    assert cue_view.footer_title.text() == "No card"
    assert "Choose a card" in cue_view.footer_note.text()


def test_a_folder_that_is_not_a_card_is_said_in_the_footer(cue_view, lib, tmp_path):
    add_disc(lib, "One")
    cue_view.look(tmp_path)
    assert "is this the player's card" in cue_view.footer_note.text()


def test_only_discs_with_something_to_write_can_be_ticked_and_a_folder_ticks_them_all(
    cue_view, lib, tmp_path
):
    inside = lib.create_disc_folder("Sets")
    add_disc(lib, "A", folder=inside)
    add_disc(lib, "B", folder=inside)
    add_disc(lib, "Plain")
    cue_view.cues.update({"A-0": [Cue("memory", 5.0)], "B-3": [Cue("memory", 9.0)]})
    cue_view.look(card_folder(tmp_path))
    items = {i.text(0): i for i in cue_view._disc_items()}
    assert cue_view._tickable(items["A"]) and cue_view._tickable(items["B"])
    assert not cue_view._tickable(items["Plain"])  # nothing to write
    folder = cue_view.tree.topLevelItem(0)
    folder.setCheckState(COL_TICK, Qt.CheckState.Checked)
    assert sorted(cue_view.ticked_ids()) == sorted(
        d.id for d in lib.discs() if d.name in ("A", "B")
    )
    assert cue_view.subtitle.text() == "2 of 2 discs selected"
    assert cue_view.footer_title.text() == "2 discs → CARD"
    assert cue_view.footer_note.text() == "2 cues to add."
    items["A"].setCheckState(COL_TICK, Qt.CheckState.Unchecked)
    assert folder.checkState(COL_TICK) == Qt.CheckState.PartiallyChecked
    assert cue_view.subtitle.text() == "1 of 2 discs selected"
    cue_view.clear_button.click()
    assert (
        cue_view.ticked_ids() == []
        and folder.checkState(COL_TICK) == Qt.CheckState.Unchecked
    )


def test_a_disc_with_nothing_to_write_never_counts_as_ticked(cue_view, lib, tmp_path):
    add_disc(lib, "Plain")
    cue_view.look(card_folder(tmp_path))
    item = cue_view._disc_items()[0]
    item.setCheckState(
        COL_TICK, Qt.CheckState.Checked
    )  # (not something a click can do)
    assert cue_view.ticked_ids() == []


def test_ticks_survive_a_refresh(cue_view, lib, tmp_path):
    add_disc(lib, "A")
    cue_view.cues["A-0"] = [Cue("memory", 5.0)]
    card = card_folder(tmp_path)
    cue_view.look(card)
    cue_view._disc_items()[0].setCheckState(COL_TICK, Qt.CheckState.Checked)
    cue_view.look(card)
    assert len(cue_view.ticked_ids()) == 1


def test_picking_a_disc_shows_its_dry_run_and_picking_nothing_explains_the_rules(
    cue_view, lib, tmp_path
):
    add_disc(lib, "A")
    cue_view.cues["A-0"] = [Cue("memory", 15.0)]
    cue_view.look(card_folder(tmp_path))
    assert "Pick a disc" in cue_view.detail.toPlainText()
    assert "never touched" in cue_view.detail.toPlainText()
    cue_view.tree.setCurrentItem(cue_view._disc_items()[0])
    text = cue_view.detail.toPlainText()
    assert "rekordbox cues found on 1 of 12 tracks" in text and "+ cue  track 1" in text


def test_a_search_hides_the_discs_that_do_not_match_and_the_folders_that_empty(
    cue_view, lib, tmp_path
):
    inside = lib.create_disc_folder("Sets")
    add_disc(lib, "Friday", folder=inside)
    add_disc(lib, "Saturday")
    cue_view.look(card_folder(tmp_path))
    cue_view.search.setText("sat")
    assert [i.isHidden() for i in cue_view._disc_items()] == [True, False]
    assert cue_view.tree.topLevelItem(
        0
    ).isHidden()  # the folder holds nothing that matches
    cue_view.search.setText("")
    assert not any(i.isHidden() for i in cue_view._walk())


def test_the_sync_button_is_there_but_off_and_says_why(
    cue_view, lib, tmp_path, monkeypatch
):
    add_disc(lib, "A")
    cue_view.cues["A-0"] = [Cue("memory", 5.0)]
    cue_view.look(card_folder(tmp_path))
    cue_view._disc_items()[0].setCheckState(COL_TICK, Qt.CheckState.Checked)
    assert not cue_view.sync_button.isEnabled()
    assert (
        "no card file written by Dubcrate has been loaded"
        in cue_view.sync_button.toolTip()
    )
    asked = []
    cue_view.sync_requested.connect(asked.append)
    cue_view._on_sync()
    assert asked == []  # off means off, even if something calls it
    monkeypatch.setattr(
        sync_view, "WRITING_ENABLED", True
    )  # (the day it is switched on)
    cue_view._update_buttons()
    assert cue_view.sync_button.isEnabled()
    cue_view._on_sync()
    assert asked == [cue_view.ticked_ids()]


def test_a_rekordbox_database_that_cant_be_read_is_said_per_disc(
    qapp, lib, monkeypatch, wait_for
):
    def broken(_path):
        raise sync_view.RekordboxError("it is locked")

    monkeypatch.setattr(sync_view, "read_cues", broken)
    add_disc(lib, "A")
    view = SyncView(lib)
    view.set_card(None)
    assert wait_for(lambda: bool(view._surveys))
    assert shown(view) == [("A", "Can't sync")]
    view.tree.setCurrentItem(view._disc_items()[0])
    assert "Couldn't read rekordbox's cues: it is locked" in view.detail.toPlainText()


def test_the_lengths_of_unmeasured_tracks_can_be_measured_from_the_view(
    cue_view, lib, tmp_path, monkeypatch, wait_for
):
    add_disc(lib, "A", measured=False)
    cue_view.cues["A-0"] = [Cue("memory", 5.0)]
    card = card_folder(tmp_path)
    monkeypatch.setattr(sync_view, "needs_measuring", lambda t: t.cd_sectors is None)
    cue_view.look(card)
    assert shown(cue_view) == [("A", "Can't sync")]
    assert cue_view.measure_button.isEnabled()
    probed = []

    def fake_probe(tracks, report=None):
        probed.append(len(list(tracks)))
        by_title = {t.title: t.id for t in tracks}
        return {by_title[f"A{i}"]: s for i, s in enumerate(cd2_lengths())}, []

    monkeypatch.setattr(sync_view, "probe_lengths", fake_probe)
    cue_view.measure_button.click()
    assert wait_for(lambda: cue_view._surveys and shown(cue_view)[0][1] != "Can't sync")
    assert probed == [12] and shown(cue_view) == [
        ("A", "Not on the card: 1 cue to add")
    ]
    assert not cue_view.measure_button.isEnabled()


# -- the tab ------------------------------------------------------------------------------------


def test_a_bottom_tab_is_fixed_below_the_scrolling_ones_and_leaves_their_keys_alone(
    qapp,
):
    bar = SourceTabBar()
    bar.add_tab("library", "Library", sd_card_glyph)
    bar.add_tab("sync", "Sync", sd_card_glyph, bottom=True)
    bar.add_tab("pin:/x", "x", sd_card_glyph, movable=True)
    assert bar.keys() == ["library", "pin:/x"] and bar.bottom_keys() == ["sync"]
    picked = []
    bar.current_changed.connect(picked.append)
    bar.set_current("sync")
    assert bar.current() == "sync" and picked == ["sync"]
    bar.set_current("library")
    assert bar.current() == "library"
    bar.move_tab(
        "pin:/x", -5
    )  # as far up as the scrolling tabs go, never into Sync's place
    assert bar.keys() == ["library", "pin:/x"]
    bar.remove_tab("sync")
    assert bar.bottom_keys() == []


def test_the_sync_tab_shows_the_card_panel_and_the_sync_view_and_leaving_it_restores_the_library(
    qapp, tmp_path, monkeypatch
):
    card = card_folder(tmp_path)
    monkeypatch.setattr(sync_view, "mounted_volumes", lambda: [card])
    lib = Library.open(":memory:")
    win = MainWindow(lib)
    assert win.source_tabs.bottom_keys() == ["sync"]
    assert (
        win.card_panel.current_card() is None
    )  # (the drives are only looked at on opening the tab)
    win.source_tabs.set_current("sync")
    assert win.source_pages.currentWidget() is win.card_panel
    assert win.center.currentWidget() is win.sync_view
    assert win.card_panel.current_card() == card
    assert win.sync_view._card_path == card
    win.source_tabs.set_current("library")
    assert win.source_pages.currentIndex() == 0 and win.center.currentIndex() == 0
    win.close()
    win.deleteLater()
    lib.close()


def test_the_sync_tab_is_not_the_one_the_app_comes_back_to(qapp, tmp_path):
    from dubcrate import settings

    settings.set_left_tab("sync")
    lib = Library.open(":memory:")
    win = MainWindow(lib, persist=True)
    assert (
        win.source_tabs.current() == "library"
    )  # (Sync reads a card: not at start-up)
    win.close()
    win.deleteLater()
    lib.close()
