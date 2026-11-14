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


def add_disc(lib, name, *, source="rekordbox", folder=None, measured=True, longer=0):
    """An audio disc of CD 2's twelve tracks, each in the rekordbox source (so cues can be looked up)."""
    n = len(lib.sources())
    lib.replace_source(
        source, f"/{source}{n}/master.db", f"S{n}",
        [TrackRecord(ext_id=f"{name}-{i}", path=f"/nonexistent/{name}-{i}.flac", title=f"{name}{i}", duration=60.0) for i in range(12)],
    )  # fmt: skip
    ids = [t.id for t in lib.tracks(sort="path") if t.ext_id.startswith(f"{name}-")]
    if measured:
        lengths = cd2_lengths()
        lengths[0] += (
            longer  # (discs with the same lengths share a card ID, and its cues)
        )
        for track_id, sectors in zip(ids, lengths, strict=True):
            lib.set_cd_sectors(track_id, sectors)
    disc_id = lib.create_disc(name, folder_id=folder)
    lib.add_to_disc(disc_id, ids)
    return disc_id


def shown(view: SyncView) -> list[tuple[str, str]]:
    """(name, status) of each disc row in the order shown."""
    return [(i.text(0), i.text(COL_STATUS)) for i in view._disc_items()]


@pytest.fixture
def cue_view(qapp, lib, tmp_path, monkeypatch, wait_for):
    """A Sync view over a library with rekordbox's cues stood in for."""
    cues = {}
    monkeypatch.setattr(sync_view, "read_cues", lambda _path: cues)
    view = SyncView(lib, backup_dir=tmp_path / "bk")
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
    assert (
        panel.row_for(copy).by_hand
        and panel.row_for(copy).title.text() == "copy-of-card"
    )
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


def test_with_the_switch_off_the_sync_button_is_off_and_says_why(
    cue_view, lib, tmp_path, monkeypatch
):
    monkeypatch.setattr(sync_view, "WRITING_ENABLED", False)
    add_disc(lib, "A")
    cue_view.cues["A-0"] = [Cue("memory", 5.0)]
    card = card_folder(tmp_path)
    cue_view.look(card)
    cue_view._disc_items()[0].setCheckState(COL_TICK, Qt.CheckState.Checked)
    assert not cue_view.sync_button.isEnabled()
    assert (
        "no card file written by Dubcrate has been loaded"
        in cue_view.sync_button.toolTip()
    )
    before = {p.name: p.read_bytes() for p in (card / "PIONEER" / "CDJ").iterdir()}
    cue_view._on_sync()  # off means off, even if something calls it: no question, no write
    assert {
        p.name: p.read_bytes() for p in (card / "PIONEER" / "CDJ").iterdir()
    } == before
    assert not (tmp_path / "bk").exists()


def test_the_switch_is_on(cue_view, lib, tmp_path):
    assert (
        sync_view.WRITING_ENABLED is True
    )  # (the player showed the cues of a file we wrote)


# -- writing (with the switch on) -------------------------------------------------------------------


@pytest.fixture
def writing(cue_view, lib, tmp_path, monkeypatch, wait_for):
    """A card, a disc with cues ticked, the switch on, and the question answered `yes`."""
    monkeypatch.setattr(sync_view, "WRITING_ENABLED", True)
    add_disc(lib, "A")
    add_disc(lib, "B", longer=75)
    cue_view.cues.update(
        {
            "A-0": [Cue("memory", 15.0), Cue("hot", 40.0, slot=1)],
            "B-2": [Cue("loop", 20.0, end=24.0)],
        }
    )
    card = card_folder(tmp_path)
    cue_view.look(card)
    cue_view._tick_all(Qt.CheckState.Checked)
    asked = []
    answer = {"yes": True}
    monkeypatch.setattr(
        sync_view,
        "ask_sync_confirmation",
        lambda _parent, text: asked.append(text) or answer["yes"],
    )
    problems, said = [], []
    monkeypatch.setattr(
        sync_view,
        "show_problem",
        lambda _p, title, text: problems.append((title, text)),
    )
    cue_view.message.connect(said.append)

    def finished():
        return wait_for(lambda: not cue_view._writing)

    from types import SimpleNamespace

    return SimpleNamespace(
        view=cue_view, card=card, asked=asked, answer=answer, problems=problems, said=said,
        backup=tmp_path / "bk", finished=finished, lib=lib,
    )  # fmt: skip


def card_files(card):
    return {
        p.name: p.read_bytes() for p in sorted((card / "PIONEER" / "CDJ").iterdir())
    }


def test_the_sync_button_is_on_when_the_switch_is_and_discs_are_ticked(writing):
    assert (
        writing.view.sync_button.isEnabled()
        and writing.view.sync_button.toolTip() == ""
    )


def test_the_question_names_each_disc_what_is_kept_and_where_the_backup_goes(writing):
    writing.view.sync_button.click()
    (text,) = writing.asked
    assert "Write rekordbox cues to “CARD”?" in text
    assert (
        "A: not on the card: 2 cues to add" in text
        and "B: not on the card: 1 cue to add" in text
    )
    assert "3 cues will be added as memory cues" in text
    assert "hot cues and its waveforms are kept" in text
    assert str(writing.backup) in text
    assert writing.finished()


def test_yes_copies_the_card_aside_writes_and_the_list_then_says_in_sync(writing):
    before = card_files(writing.card)
    writing.view.sync_button.click()
    assert (
        not writing.view.sync_button.isEnabled()
        and not writing.view.measure_button.isEnabled()
    )
    assert writing.finished()
    (backup,) = list(writing.backup.iterdir())
    assert {p.name: p.read_bytes() for p in backup.iterdir()} == before
    _, files = cc.read_card(writing.card)
    assert sorted(len(f.cues) for f in files.values() if f is not None and f.cues) == [
        1,
        2,
    ]
    assert (
        "Wrote 2 disc files to “CARD”" in writing.said[-1]
        and str(backup) in writing.said[-1]
    )
    assert wait_until(
        writing.view, lambda: all(s == "In sync" for _n, s in shown(writing.view))
    )
    assert writing.view.ticked_ids() == [] and not writing.view.sync_button.isEnabled()


def wait_until(view, predicate, timeout=20.0):
    import time

    from PySide6.QtWidgets import QApplication

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_no_changes_nothing_is_written_and_nothing_is_copied(writing):
    before = card_files(writing.card)
    writing.answer["yes"] = False
    writing.view.sync_button.click()
    assert not writing.view._writing
    assert card_files(writing.card) == before and not writing.backup.exists()
    assert writing.view.sync_button.isEnabled()  # (and the ticks are still there)


def test_a_write_that_fails_is_said_in_a_box_and_the_status_bar_and_the_view_recovers(
    writing, monkeypatch
):
    def full(*_a, **_k):
        raise cc.CardFormatError("The card couldn't be read or written: no space")

    monkeypatch.setattr(sync_view, "apply_sync", full)
    writing.view.sync_button.click()
    assert writing.finished()
    ((title, text),) = writing.problems
    assert title == "Couldn't Sync to the Card" and "no space" in text
    assert str(writing.backup) in text  # where the old files are put back from
    assert "no space" in writing.said[-1]
    assert not writing.view._writing
    # (once the card has been read again)
    assert wait_until(writing.view, writing.view.sync_button.isEnabled)


def test_a_second_click_while_writing_does_nothing(writing):
    writing.view.sync_button.click()
    writing.view._on_sync()  # (as a double click could)
    assert len(writing.asked) == 1
    assert writing.finished()


def test_the_question_says_when_cues_wont_fit_and_when_a_disc_has_a_warning(
    cue_view, lib, tmp_path
):
    from dubcrate.card_discs import DiscInput, DiscSurvey
    from dubcrate.card_sync import DiscPlan, Status, WantedCue

    summary = lib.disc(add_disc(lib, "A"))
    plan = DiscPlan(
        Status.NEW_DISC,
        cc.DiscId(2, 0, 9, 5, 7),
        "CDJ00002",
        None,
        [],
        0,
        0,
        [WantedCue(1, 5)],
        [],
    )
    found = DiscSurvey(
        summary, "A", DiscInput(summary.id, "A", warnings=["not burned"]), plan
    )
    text = sync_view.confirmation_text([found], "CARD", tmp_path / "bk")
    assert "1 cue won't fit (the player holds 100 per disc)" in text
    assert "1 disc has a warning in the list" in text


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


def test_clicking_the_tab_you_are_on_brings_its_middle_pane_back_from_an_open_disc(
    qapp, tmp_path
):
    lib = Library.open(":memory:")
    disc_id = lib.create_disc("D")
    win = MainWindow(lib)
    win.source_tabs.set_current("sync")
    assert win.center.currentWidget() is win.sync_view
    win.open_disc(disc_id)
    assert win.center.currentWidget() is win.disc_view
    win.source_tabs._bottom["sync"].click()
    assert win.center.currentWidget() is win.sync_view
    win.source_tabs.set_current("library")
    win.open_disc(disc_id)
    assert win.center.currentIndex() == 1
    win.source_tabs._buttons["library"].click()  # (the same for the tabs that scroll)
    assert win.center.currentIndex() == 0
    win.close()
    win.deleteLater()
    lib.close()


# -- ejecting the card after a sync ------------------------------------------------------------


@pytest.fixture
def ejecting(writing, monkeypatch):
    """The writing rig with a card that can be ejected, and the eject stood in for."""
    from types import SimpleNamespace

    writing.view._can_eject = lambda _path: True
    writing.view._update_buttons()
    ejected, gone = [], []
    monkeypatch.setattr(sync_view, "eject_card", lambda path: ejected.append(path))
    writing.view.card_ejected.connect(lambda: gone.append(1))
    return SimpleNamespace(**vars(writing), ejected=ejected, gone=gone)


def test_ejecting_after_syncing_is_on_by_default_and_remembered(qapp, lib):
    from dubcrate import settings

    assert SyncView(lib).eject_check.isChecked()
    settings.set_sync_eject(False)
    view = SyncView(lib)
    assert not view.eject_check.isChecked()
    view.eject_check.setChecked(True)
    assert settings.get_sync_eject() is True


def test_a_folder_that_is_not_a_plugged_in_card_cannot_be_ejected(writing):
    view = writing.view  # (no `can_eject`: as for a folder chosen by hand)
    assert not view.eject_check.isEnabled()
    assert "not a folder you chose" in view.eject_check.toolTip()


def test_a_card_that_is_plugged_in_can_be_ejected(ejecting):
    assert ejecting.view.eject_check.isEnabled()


def test_the_question_says_the_card_will_be_ejected(ejecting):
    ejecting.view.sync_button.click()
    assert "will be ejected afterwards" in ejecting.asked[0]
    assert ejecting.finished()


def test_after_writing_the_card_is_ejected_and_the_view_says_it_is_safe_to_take_out(
    ejecting,
):
    ejecting.view.sync_button.click()
    assert ejecting.finished()
    assert ejecting.ejected == [ejecting.card]
    assert ejecting.gone == [1]
    assert "Ejected “CARD”: it is safe to take out." in ejecting.said[-1]
    assert not ejecting.view._writing


def test_the_buttons_stay_off_until_the_eject_has_finished(ejecting):
    ejecting.view.sync_button.click()
    assert not ejecting.view.sync_button.isEnabled()
    assert not ejecting.view.eject_check.isEnabled()
    assert ejecting.finished()


def test_an_eject_that_fails_says_to_eject_by_hand_and_the_card_is_read_again(
    ejecting, monkeypatch
):
    from dubcrate.eject import EjectError

    def busy(_path):
        raise EjectError("The system couldn't eject “CARD”: disk is busy")

    monkeypatch.setattr(sync_view, "eject_card", busy)
    ejecting.view.sync_button.click()
    assert ejecting.finished()
    assert "disk is busy" in ejecting.said[-1]
    assert "Eject it in Finder before taking it out." in ejecting.said[-1]
    assert ejecting.gone == []
    assert wait_until(ejecting.view, lambda: shown(ejecting.view)[0][1] == "In sync")


def test_with_eject_unticked_the_card_is_not_ejected_and_the_view_says_to_do_it(
    ejecting,
):
    ejecting.view.eject_check.setChecked(False)
    ejecting.view.sync_button.click()
    assert ejecting.finished()
    assert ejecting.ejected == [] and ejecting.gone == []
    assert "Eject the card before taking it out." in ejecting.said[-1]
    assert "will be ejected" not in ejecting.asked[0]


def test_the_main_window_lets_go_of_a_card_that_was_ejected(
    qapp, tmp_path, monkeypatch
):
    card = card_folder(tmp_path)
    here = [card]
    monkeypatch.setattr(sync_view, "mounted_volumes", lambda: here)
    lib = Library.open(":memory:")
    win = MainWindow(lib)
    win.source_tabs.set_current("sync")
    assert win.card_panel.is_volume(card) and win.sync_view._card_path == card
    here.clear()  # (the system has ejected it)
    win.sync_view.card_ejected.emit()
    assert win.card_panel.current_card() is None and win.sync_view._card_path is None
    win.close()
    win.deleteLater()
    lib.close()


# -- the card list: rows, eject, drives coming and going -----------------------------------------


@pytest.fixture
def plugged(tmp_path, monkeypatch):
    """A folder standing for /Volumes, with a `volumes()` that lists the cards in it, and a quick settle."""
    monkeypatch.setattr(sync_view, "CARD_SETTLE_MS", 20)
    monkeypatch.setattr(sync_view, "card_capacity", lambda _path: 2_013_265_920)
    root = tmp_path / "Volumes"
    root.mkdir()

    class Plugged:
        folder = root

        @staticmethod
        def volumes():
            return sorted(p for p in root.iterdir() if p.is_dir())

        @staticmethod
        def insert(name="CARD"):
            return card_folder(root, name)

    return Plugged


def test_a_card_row_has_an_icon_its_name_its_size_and_discs_and_an_eject_button(
    qapp, plugged
):
    card = plugged.insert("DISK_IMG")
    panel = CardPanel(volumes=plugged.volumes)
    panel.refresh()
    row = panel.row_for(card)
    assert row.title.text() == "DISK_IMG"
    assert row.detail.text() == "2.0 GB · 1 disc"  # (index_one_disc: one disc)
    assert row.icon is not None and not row.by_hand
    assert row.button.toolTip() == "Eject “DISK_IMG”" and row.button.isEnabled()
    assert panel.cards.item(0).sizeHint().height() == sync_view.ROW_HEIGHT


def test_a_card_that_is_plugged_in_appears_by_itself_and_one_taken_out_goes(
    qapp, plugged, wait_for
):
    panel = CardPanel(volumes=plugged.volumes, watch=[plugged.folder])
    told = []
    panel.card_changed.connect(told.append)
    card = plugged.insert("CARD")
    assert wait_for(lambda: panel.row_for(card) is not None)  # (no Refresh)
    assert panel.current_card() == card and told == [card]
    assert panel.subtitle.text() == "1 card found"
    import shutil

    shutil.rmtree(card)
    assert wait_for(lambda: panel.current_card() is None)
    assert told == [card, None] and panel.hint.isVisibleTo(panel)


def test_a_second_card_joins_the_list_and_the_picked_one_stays_picked(
    qapp, plugged, wait_for
):
    first = plugged.insert("A")
    panel = CardPanel(volumes=plugged.volumes, watch=[plugged.folder])
    panel.refresh()
    second = plugged.insert("B")
    assert wait_for(lambda: panel.row_for(second) is not None)
    assert panel.current_card() == first and panel.subtitle.text() == "2 cards found"


def test_the_picked_row_is_drawn_as_picked_and_the_others_are_not(qapp, plugged):
    first, second = plugged.insert("A"), plugged.insert("B")
    panel = CardPanel(volumes=plugged.volumes)
    panel.refresh()
    assert panel.row_for(first)._selected and not panel.row_for(second)._selected
    panel.cards.setCurrentRow(1)
    assert not panel.row_for(first)._selected and panel.row_for(second)._selected


def test_the_eject_button_ejects_that_card_says_so_and_the_list_lets_go_of_it(
    qapp, plugged, monkeypatch, wait_for
):
    card = plugged.insert("CARD")
    panel = CardPanel(volumes=plugged.volumes)
    panel.refresh()
    ejected, said, told = [], [], []
    panel.message.connect(said.append)
    panel.card_changed.connect(told.append)

    def eject(path):
        ejected.append(path)
        import shutil

        shutil.rmtree(path)  # (the system takes it away)

    monkeypatch.setattr(sync_view, "eject_card", eject)
    panel.row_for(card).button.click()
    assert not panel.row_for(card).button.isEnabled()  # (while it works)
    assert wait_for(lambda: panel.current_card() is None)
    assert ejected == [card] and told == [None]
    assert said == ["Ejected “CARD”: it is safe to take out."]


def test_an_eject_that_fails_says_to_do_it_in_finder_and_the_card_stays(
    qapp, plugged, monkeypatch, wait_for
):
    from dubcrate.eject import EjectError

    card = plugged.insert("CARD")
    panel = CardPanel(volumes=plugged.volumes)
    panel.refresh()
    said = []
    panel.message.connect(said.append)

    def busy(_path):
        raise EjectError("The system couldn't eject “CARD”: disk is busy")

    monkeypatch.setattr(sync_view, "eject_card", busy)
    panel.row_for(card).button.click()
    assert wait_for(lambda: said)
    assert "disk is busy" in said[0] and "Eject it in Finder" in said[0]
    assert panel.current_card() == card and panel.row_for(card).button.isEnabled()


def test_nothing_is_ejected_while_a_sync_is_writing(qapp, plugged, monkeypatch):
    card = plugged.insert("CARD")
    panel = CardPanel(volumes=plugged.volumes)
    panel.refresh()
    ejected = []
    monkeypatch.setattr(sync_view, "eject_card", ejected.append)
    panel.set_busy(True)
    assert not panel.row_for(card).button.isEnabled()
    panel._eject(card)  # (even if something asks)
    assert ejected == []
    panel.set_busy(False)
    assert panel.row_for(card).button.isEnabled()


def test_a_folder_chosen_by_hand_has_a_button_that_takes_it_off_the_list_and_nothing_else(
    qapp, tmp_path, monkeypatch
):
    copy = card_folder(tmp_path, "copy-of-card")
    panel = CardPanel(volumes=list)
    monkeypatch.setattr(sync_view, "ask_card_folder", lambda _parent: str(copy))
    told = []
    panel.card_changed.connect(told.append)
    panel.choose_folder()
    row = panel.row_for(copy)
    assert row.button.toolTip() == "Take this folder off the list"
    assert "Folder ·" in row.detail.text()
    before = {p.name: p.read_bytes() for p in (copy / "PIONEER" / "CDJ").iterdir()}
    row.button.click()
    assert (
        panel.row_for(copy) is None
        and panel.current_card() is None
        and told[-1] is None
    )
    assert {
        p.name: p.read_bytes() for p in (copy / "PIONEER" / "CDJ").iterdir()
    } == before


def test_the_sizes_are_in_gigabytes_or_megabytes():
    assert sync_view._size_text(2_013_265_920) == "2.0 GB"
    assert sync_view._size_text(64_000_000) == "64 MB"


def test_the_sync_view_says_when_it_is_writing_so_the_card_is_left_alone(writing):
    states = []
    writing.view.busy_changed.connect(states.append)
    writing.view.sync_button.click()
    assert states[0] is True
    assert writing.finished()
    assert states[-1] is False


def test_the_main_window_keeps_the_card_list_still_while_a_sync_writes(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(sync_view, "mounted_volumes", lambda: [card_folder(tmp_path)])
    lib = Library.open(":memory:")
    win = MainWindow(lib)
    win.source_tabs.set_current("sync")
    win.sync_view.busy_changed.emit(True)
    assert win.card_panel._busy
    win.sync_view.busy_changed.emit(False)
    assert not win.card_panel._busy
    win.close()
    win.deleteLater()
    lib.close()


def test_a_card_rows_text_is_shortened_with_an_ellipsis_not_cut_off(qapp, plugged):
    card = plugged.insert("A very long volume name for a card")
    panel = CardPanel(volumes=plugged.volumes)
    panel.resize(200, 400)
    panel.show()
    panel.refresh()
    qapp.processEvents()
    row = panel.row_for(card)
    assert (
        row.title.text().endswith("…")
        and "very long volume name" in row.title.toolTip()
    )
    panel.resize(600, 400)
    qapp.processEvents()
    assert row.title.text() == "A very long volume name for a card"
