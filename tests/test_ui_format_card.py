"""The Format a Card window and its button on the card panel (no disk is listed or erased: both are stood in for)."""

from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from dubcrate import card_format as cf
from dubcrate import cdj_card as cc
from dubcrate.ui import sync_view
from dubcrate.ui.format_card_dialog import FormatCardDialog
from dubcrate.ui.sync_view import CardPanel

GB = 1000**3


def disk(device="disk4", name="Generic STORAGE DEVICE", size=2_013_265_920, **kw):
    values = {
        "device": device, "name": name, "size": size, "protocol": "USB", "whole": True,
        "removable": True, "virtual": False, "system": False, "writable": True,
    }  # fmt: skip
    values.update(kw)
    return cf.CardDisk(**values)


@pytest.fixture
def make(qapp, wait_for, monkeypatch):
    made = []
    announced = []
    monkeypatch.setattr(
        "dubcrate.ui.format_card_dialog.announce_ready",
        lambda parent, name: announced.append(name),
    )

    def build(disks=None, formatter=None, error=None):
        def lister():
            if error:
                raise cf.FormatError(error)
            return list(disks if disks is not None else [disk()])

        calls = []

        def default_format(device, expect):
            calls.append((device, expect))
            return cf.Formatted(
                expect, Path("/Volumes/DISK_IMG"), Path("/Volumes/DISK_IMG/PIONEER/CDJ")
            )

        dialog = FormatCardDialog(
            None, list_disks=lister, formatter=formatter or default_format
        )
        dialog.calls = calls
        dialog.announced = announced
        made.append(dialog)
        assert wait_for(lambda: dialog.status.text() != "Looking for cards…")
        return dialog

    yield build
    for dialog in made:
        dialog.close()
        dialog.deleteLater()


def test_a_card_is_listed_with_its_name_and_size_and_picked(make):
    dialog = make()
    assert dialog.list.count() == 1
    text = dialog.list.item(0).text()
    assert "Generic STORAGE DEVICE" in text and "2.0 GB" in text and "disk4" in text
    assert "Ready to format" in text
    assert dialog.chosen().device == "disk4"
    assert dialog.status.text() == "1 card can be formatted."


def test_a_card_that_cannot_be_used_is_greyed_out_with_the_reason_and_cannot_be_picked(
    make,
):
    dialog = make([disk(device="disk5", name="Big card", size=32 * GB), disk()])
    big = dialog.list.item(0)
    assert "SDHC" in big.text() and "plain SD" in big.text()
    assert not (big.flags() & Qt.ItemFlag.ItemIsEnabled)
    assert dialog.chosen().device == "disk4"  # the usable one is picked
    dialog.list.setCurrentRow(0)
    assert dialog.chosen() is None


def test_nothing_can_be_erased_until_a_card_is_picked_and_the_tick_is_in(make):
    dialog = make()
    assert not dialog.format_button.isEnabled() and not dialog.understand.isChecked()
    dialog.start()  # (a stray call does nothing)
    assert dialog.calls == []
    dialog.understand.setChecked(True)
    assert dialog.format_button.isEnabled()
    assert (
        "will be erased" in dialog.warning.text() and "disk4" in dialog.warning.text()
    )
    dialog.understand.setChecked(False)
    assert not dialog.format_button.isEnabled()


def test_formatting_runs_the_formatter_on_the_picked_card_with_the_disk_as_listed(
    make, wait_for
):
    dialog = make()
    told = []
    closed = []
    dialog.formatted.connect(told.append)
    dialog.accepted.connect(lambda: closed.append(True))
    dialog.understand.setChecked(True)
    dialog.format_button.click()
    assert wait_for(lambda: bool(told))
    ((device, expect),) = dialog.calls
    assert (
        device == "disk4" and expect == disk()
    )  # what was listed, so a swapped card is caught
    assert told == [Path("/Volumes/DISK_IMG")]
    # it closes itself and a box says so, rather than leaving a quiet line in an open window
    assert closed == [True] and dialog.announced == [
        "DISK_IMG"
    ]  # (the label, not the mount's name)
    assert dialog.result.text() == ""
    assert not dialog.format_button.isVisibleTo(dialog)
    assert (
        not dialog.understand.isChecked()
    )  # (a second erase needs a new tick and a new list)


def test_a_failure_is_said_in_words_and_the_tick_must_be_given_again(make, wait_for):
    def refuse(device, expect):
        raise cf.FormatError(
            "disk4 is not the card that was picked any more: nothing was erased."
        )

    dialog = make(formatter=refuse)
    told = []
    dialog.formatted.connect(told.append)
    dialog.understand.setChecked(True)
    dialog.format_button.click()
    assert wait_for(lambda: "Couldn't format" in dialog.result.text())
    assert "nothing was erased" in dialog.result.text() and told == []
    assert (
        dialog.announced == []
    )  # (no "Card Ready" box for a failure; the window stays open)
    assert not dialog.understand.isChecked() and not dialog.format_button.isEnabled()
    assert dialog.close_button.text() == "Cancel"  # (nothing is done: it can try again)


def test_no_disks_and_a_listing_failure_are_said_with_what_to_do(make):
    assert "No removable disks found" in make([]).status.text()
    assert (
        "isn't done on this kind"
        in make(
            error="Formatting a card isn't done on this kind of computer yet."
        ).status.text()
    )


def test_the_window_is_busy_while_formatting_and_will_not_start_twice(make, wait_for):
    gate = []

    def slow(device, expect):
        while not gate:
            import time

            time.sleep(0.01)
        return cf.Formatted(
            expect, Path("/Volumes/DISK_IMG"), Path("/Volumes/DISK_IMG/PIONEER/CDJ")
        )

    dialog = make(formatter=slow)
    dialog.understand.setChecked(True)
    dialog.start()
    assert not dialog.list.isEnabled() and not dialog.refresh_button.isEnabled()
    assert (
        not dialog.close_button.isEnabled()
        and "Erasing and formatting" in dialog.status.text()
    )
    assert dialog.progress.isVisibleTo(dialog)  # (something moves while it works)
    dialog.start()  # (ignored)
    gate.append(1)
    assert wait_for(lambda: dialog._done)
    assert not dialog.progress.isVisibleTo(dialog)


# -- the card panel -----------------------------------------------------------------------------


def test_the_panel_opens_the_window_and_picks_the_card_it_made(
    qapp, tmp_path, monkeypatch
):
    card = tmp_path / "DISK_IMG"
    (card / "PIONEER" / "CDJ").mkdir(
        parents=True
    )  # as the formatter leaves it: no index yet
    panel = CardPanel(volumes=lambda: [card])
    told, said = [], []
    panel.card_changed.connect(told.append)
    panel.message.connect(said.append)

    class Fake:
        def __init__(self, parent):
            self.formatted = type(
                "S", (), {"connect": lambda _s, f: setattr(self, "cb", f)}
            )()

        def exec(self):
            self.cb(card)

    monkeypatch.setattr(sync_view, "FormatCardDialog", Fake)
    menu_action(panel.card_menu(None), "Format a Card…").trigger()
    assert panel.current_card() == card and told[-1] == card
    assert "Formatted “DISK_IMG”" in said[-1]
    assert panel.subtitle.text() == "1 card found"


def menu_action(menu, text):
    return next(a for a in menu.actions() if a.text() == text)


def test_the_format_menu_item_waits_while_a_sync_writes(qapp, monkeypatch):
    panel = CardPanel(volumes=list)
    opened = []
    monkeypatch.setattr(
        sync_view, "FormatCardDialog", lambda parent: opened.append(parent)
    )
    panel.set_busy(True)
    assert not menu_action(panel.card_menu(None), "Format a Card…").isEnabled()
    panel.format_card()
    assert opened == []
    panel.set_busy(False)
    assert menu_action(panel.card_menu(None), "Format a Card…").isEnabled()


def test_right_click_on_the_empty_list_offers_only_format(qapp):
    panel = CardPanel(volumes=list)
    assert [a.text() for a in panel.card_menu(None).actions() if a.text()] == ["Format a Card…"]  # fmt: skip
    assert not hasattr(panel, "format_button")


def test_right_click_on_a_card_offers_eject_and_format(qapp, tmp_path, monkeypatch):
    card = tmp_path / "DISK_IMG"
    cc.make_card_folders(card)
    panel = CardPanel(volumes=lambda: [card])
    panel.refresh(announce=False)
    menu = panel.card_menu(card)
    assert [a.text() for a in menu.actions() if a.text()] == ["Sync to “DISK_IMG”…", "Import Cues from “DISK_IMG”…", "Eject “DISK_IMG”", "Format a Card…"]  # fmt: skip
    ejected = []
    monkeypatch.setattr(panel, "_eject", ejected.append)
    menu_action(menu, "Eject “DISK_IMG”").trigger()
    assert ejected == [card]
    panel.set_busy(True)
    assert not menu_action(panel.card_menu(card), "Eject “DISK_IMG”").isEnabled()


def test_right_click_on_a_folder_chosen_by_hand_offers_to_take_it_off_the_list(qapp, tmp_path, monkeypatch):  # fmt: skip
    folder = tmp_path / "copy"
    cc.make_card_folders(folder)
    panel = CardPanel(volumes=list)
    panel._chosen.append(folder)
    panel.refresh(announce=False)
    menu = panel.card_menu(folder)
    assert [a.text() for a in menu.actions() if a.text()] == ["Sync to “copy”…", "Import Cues from “copy”…", "Take This Folder Off the List", "Format a Card…"]  # fmt: skip
    menu_action(menu, "Take This Folder Off the List").trigger()
    assert panel.cards.count() == 0


def test_sync_in_the_menu_asks_for_a_sync_to_that_card_and_waits_while_one_writes(qapp, tmp_path):  # fmt: skip
    first, second = tmp_path / "A", tmp_path / "B"
    for card in (first, second):
        cc.make_card_folders(card)
    panel = CardPanel(volumes=lambda: [first, second])
    panel.refresh(announce=False)
    asked, told = [], []
    panel.sync_requested.connect(asked.append)
    panel.card_changed.connect(told.append)
    assert panel.current_card() == first
    menu_action(panel.card_menu(second), "Sync to “B”…").trigger()
    assert asked == [second] and panel.current_card() == second
    assert told == []  # (the Sync view is told once, by the request)
    panel.set_busy(True)
    assert not menu_action(panel.card_menu(second), "Sync to “B”…").isEnabled()


def test_import_cues_in_the_menu_asks_for_that_card_and_waits_while_one_writes(qapp, tmp_path):  # fmt: skip
    first, second = tmp_path / "A", tmp_path / "B"
    for card in (first, second):
        cc.make_card_folders(card)
    panel = CardPanel(volumes=lambda: [first, second])
    panel.refresh(announce=False)
    asked, told = [], []
    panel.import_requested.connect(asked.append)
    panel.card_changed.connect(told.append)
    menu_action(panel.card_menu(second), "Import Cues from “B”…").trigger()
    assert asked == [second] and panel.current_card() == second
    assert told == []  # (the Sync view is told once, by the request)
    panel.set_busy(True)
    assert not menu_action(panel.card_menu(second), "Import Cues from “B”…").isEnabled()  # fmt: skip


# -- cards that are plugged in but not set up -----------------------------------------------------


def unset(device="disk4", has_files=False, **kw):
    return cf.UnsetCard(disk(device=device, **kw), has_files)


def test_a_card_not_set_up_is_listed_after_the_cards_and_cannot_be_picked(
    qapp, tmp_path, wait_for
):
    card = tmp_path / "DISK_IMG"
    cc.make_card_folders(card)
    panel = CardPanel(volumes=lambda: [card], unset=lambda: [unset("disk5")])
    panel.refresh(announce=False)
    assert wait_for(lambda: panel.cards.count() == 2)
    assert panel.current_card() == card
    row = panel.cards.itemWidget(panel.cards.item(1))
    assert row.unset.disk.device == "disk5"
    assert "Not set up for the CDJ" in row.detail.text()
    assert not (panel.cards.item(1).flags() & Qt.ItemFlag.ItemIsSelectable)
    assert panel.subtitle.text() == "1 card found · 1 to set up"
    assert panel.is_volume(card) and not panel.is_volume(Path("/dev/disk5"))


def test_with_only_a_card_not_set_up_nothing_is_picked_and_sync_stays_off(
    qapp, wait_for
):
    panel = CardPanel(volumes=list, unset=lambda: [unset()])
    panel.refresh(announce=False)
    assert wait_for(lambda: panel.cards.count() == 1)
    assert panel.current_card() is None
    assert not panel.sync_button.isEnabled()
    assert panel.subtitle.text() == "1 to set up"


def test_a_card_with_other_files_warns_that_formatting_erases_them(qapp, wait_for):
    panel = CardPanel(volumes=list, unset=lambda: [unset(has_files=True)])
    panel.refresh(announce=False)
    assert wait_for(lambda: panel.cards.count() == 1)
    row = panel.cards.itemWidget(panel.cards.item(0))
    assert "Has other files: formatting erases them" in row.detail.text()


def test_the_format_button_opens_the_window_with_that_card_picked(
    qapp, monkeypatch, wait_for
):
    panel = CardPanel(volumes=list, unset=lambda: [unset("disk5")])
    panel.refresh(announce=False)
    assert wait_for(lambda: panel.cards.count() == 1)
    opened = []

    class Fake:
        def __init__(self, parent, device=None):
            opened.append(device)
            self.formatted = type("S", (), {"connect": lambda _s, f: None})()

        def exec(self):
            pass

    monkeypatch.setattr(sync_view, "FormatCardDialog", Fake)
    panel.cards.itemWidget(panel.cards.item(0)).button.click()
    assert opened == ["disk5"]
    # the right-click menu of the empty list is still there, and opens it with no card picked
    menu_action(panel.card_menu(None), "Format a Card…").trigger()
    assert opened == ["disk5", None]


def test_a_card_listed_as_not_set_up_has_no_sync_or_eject_in_its_menu(
    qapp, monkeypatch, wait_for
):
    panel = CardPanel(volumes=list, unset=lambda: [unset()])
    panel.refresh(announce=False)
    assert wait_for(lambda: panel.cards.count() == 1)
    asked = []

    class Menu:
        def exec(self, *_a):
            pass

    monkeypatch.setattr(panel, "card_menu", lambda path: asked.append(path) or Menu())
    panel._on_context_menu(panel.cards.visualItemRect(panel.cards.item(0)).center())
    assert asked == [
        None
    ]  # the empty list's menu: Format a Card… and nothing to sync or eject


def test_a_failed_look_for_unset_cards_leaves_the_list_as_it_was(qapp, tmp_path, wait_for):  # fmt: skip
    card = tmp_path / "DISK_IMG"
    cc.make_card_folders(card)

    asked = []

    def broken():
        asked.append(1)
        raise cf.FormatError("diskutil failed")

    panel = CardPanel(volumes=lambda: [card], unset=broken)
    panel.refresh(announce=False)
    assert wait_for(lambda: asked)
    wait_for(lambda: False, timeout=0.2)  # (let the failure be delivered)
    assert panel.cards.count() == 1 and panel.subtitle.text() == "1 card found"


def test_a_card_just_formatted_is_not_listed_twice(qapp, tmp_path, monkeypatch, wait_for):  # fmt: skip
    card = tmp_path / "DISK_IMG"
    cc.make_card_folders(card)
    panel = CardPanel(volumes=list, unset=lambda: [unset()])
    panel.refresh(announce=False)
    assert wait_for(lambda: panel.cards.count() == 1)
    panel._find_unset = list  # (the system now sees it as a card)
    panel._volumes = lambda: [card]
    panel._formatted(card)
    assert wait_for(lambda: panel.cards.count() == 1)
    assert panel.current_card() == card and panel.subtitle.text() == "1 card found"


def test_the_card_ready_box_names_the_card_and_says_to_eject(qapp, monkeypatch):
    import dubcrate.ui.format_card_dialog as fcd

    shown = []
    monkeypatch.undo()  # (conftest refuses the real one: this test is of the real one)
    monkeypatch.setattr(
        "dubcrate.ui.format_card_dialog.QMessageBox.information",
        lambda parent, title, text: shown.append((title, text)),
    )
    fcd.announce_ready(None, "DISK_IMG")
    ((title, text),) = shown
    assert title == "Card Ready"
    assert (
        text == "“DISK_IMG” is formatted and ready for the CDJ-1000MK3."
    )  # (short: nothing more to say)
