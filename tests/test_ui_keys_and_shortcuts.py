"""Delete and rename from the keyboard, the key notation setting, and the shortcuts window."""

import pytest
from PySide6.QtCore import QEvent, QModelIndex, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QMessageBox

from dubcrate import keys, settings
from dubcrate.library import Library, NodeRecord, TrackRecord
from dubcrate.ui import key_style
from dubcrate.ui.main_window import MainWindow
from dubcrate.ui.models import find_selection
from dubcrate.ui.settings_dialog import SettingsDialog
from dubcrate.ui.shortcuts_dialog import (
    EXTRA_SHORTCUTS,
    ShortcutsDialog,
    menu_shortcuts,
)

YES, NO = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
WRITTEN_KEYS = ["8A", "Abm", "Db", "n/a"]


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    for name in ("information", "warning"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    lib = Library.open(":memory:")
    records = []
    for i, key in enumerate(WRITTEN_KEYS):
        file = tmp_path / f"t{i}.mp3"
        file.write_bytes(b"x" * 1000)
        records.append(TrackRecord(ext_id=f"t{i}", path=str(file), title=f"Song {i}", artist="Art", duration=2.0, key=key))  # fmt: skip
    nodes = [NodeRecord("set", None, "Set", False, tuple(r.ext_id for r in records))]
    lib.replace_source("folder", str(tmp_path), "Crate", records, nodes)
    for track in lib.tracks():
        lib.set_cd_sectors(track.id, 5000)
    win = MainWindow(lib)
    yield win, lib
    win.close()
    win.deleteLater()
    lib.close()


def ids(lib):
    return [t.id for t in lib.tracks(sort="title")]


def pick(win, selection):
    index = find_selection(win.disc_tree.model(), selection)
    assert index.isValid(), selection
    win.disc_tree.setCurrentIndex(index)
    return index


def ask_with(monkeypatch, answer):
    """Records what a question box was shown with: (title, text, buttons, default button)."""
    asked = []

    def question(_parent, title, text, buttons=None, default=None):
        asked.append((title, text, buttons, default))
        return answer

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    return asked


# -- delete -----------------------------------------------------------------------


def test_the_delete_and_rename_shortcuts_are_the_mac_ones(window):
    win, _lib = window
    delete = {s.toString() for s in win.act_delete.shortcuts()}
    # ⌘⌫, and the forward-delete key too (Qt's short name for it is "Del")
    assert delete == {"Ctrl+Backspace", "Ctrl+Del"}
    assert win.act_rename.shortcut().toString() == "Ctrl+R"
    assert (
        win.act_reimport.shortcut().toString() == "Ctrl+Shift+R"
    )  # moved out of the way


def test_an_empty_disc_is_deleted_at_once_without_asking(window):
    win, lib = window
    disc = lib.create_disc("Blank")
    win.refresh_discs()
    pick(win, ("disc", disc))
    win.act_delete.trigger()  # (a question box here would fail the test: see conftest)
    assert lib.discs() == []


def test_a_disc_with_tracks_asks_and_yes_is_the_default(window, monkeypatch):
    win, lib = window
    disc = lib.create_disc("Full")
    lib.add_to_disc(disc, ids(lib)[:2])
    win.refresh_discs()
    pick(win, ("disc", disc))
    asked = ask_with(monkeypatch, NO)
    win.delete_picked()
    assert len(lib.discs()) == 1  # No leaves it
    [(_title, text, buttons, default)] = asked
    assert default == YES and buttons == YES | NO
    assert "tracks stay in your library" in text
    ask_with(monkeypatch, YES)
    win.delete_picked()
    assert lib.discs() == []


def test_a_folder_is_empty_only_if_it_holds_no_disc_and_no_folder(window, monkeypatch):
    win, lib = window
    empty = lib.create_disc_folder("Empty")
    holds_disc = lib.create_disc_folder("Holds a disc")
    lib.create_disc("In it", folder_id=holds_disc)
    holds_folder = lib.create_disc_folder("Holds a folder")
    lib.create_disc_folder("Inner", holds_folder)
    win.refresh_discs()
    pick(win, ("disc_folder", empty))
    win.delete_picked()  # at once
    assert empty not in {f.id for f in lib.disc_folders()}
    asked = ask_with(monkeypatch, YES)
    for folder in (holds_disc, holds_folder):
        win.refresh_discs()
        pick(win, ("disc_folder", folder))
        win.delete_picked()
    assert len(asked) == 2 and all(a[3] == YES for a in asked)
    assert "also deletes 1 disc and no folders" in asked[0][1]
    assert "also deletes no discs and 1 folder" in asked[1][1]
    assert lib.discs() == []  # the disc went with its folder


def test_a_folder_on_an_mp3_disc_asks_only_when_it_holds_tracks_or_folders(
    window, monkeypatch
):
    win, lib = window
    disc = lib.create_disc("M", kind="mp3")
    blank = lib.create_disc_dir(disc, "Blank")
    full = lib.create_disc_dir(disc, "Full")
    lib.add_to_disc(disc, ids(lib)[:1], dir_id=full)
    nested = lib.create_disc_dir(disc, "Nested")
    lib.create_disc_dir(disc, "Inside", parent_id=nested)
    win.refresh_discs()
    pick(win, ("disc_dir", disc, blank))
    win.delete_picked()
    assert blank not in {d.id for d in lib.disc_dirs(disc)}
    asked = ask_with(monkeypatch, YES)
    for folder in (full, nested):
        win.refresh_discs()
        pick(win, ("disc_dir", disc, folder))
        win.delete_picked()
    assert len(asked) == 2 and "also takes 1 track off the disc" in asked[0][1]
    assert "no folders" not in asked[1][1] and "1 folder" in asked[1][1]
    assert lib.disc(disc).track_count == 0 and lib.disc_dirs(disc) == []
    assert lib.count_tracks() == len(WRITTEN_KEYS)  # the library is untouched


def test_a_disc_that_holds_only_folders_is_not_empty(window, monkeypatch):
    win, lib = window
    disc = lib.create_disc("M", kind="mp3")
    lib.create_disc_dir(disc, "Folder")
    assert not win.disc_view.is_empty(("disc", disc))
    asked = ask_with(monkeypatch, NO)
    win.disc_view.delete_item(("disc", disc))
    assert len(asked) == 1 and len(lib.discs()) == 1


def test_with_nothing_picked_the_open_disc_is_the_target(window):
    win, lib = window
    disc = lib.create_disc("Open")
    win.disc_view.set_disc(disc)
    win.disc_tree.setCurrentIndex(QModelIndex())
    assert win.picked_disc_item() == ("disc", disc)
    win.delete_picked()
    assert lib.discs() == []


def test_command_delete_in_the_track_list_does_not_remove_tracks(window):
    win, lib = window
    disc = lib.create_disc("D")
    lib.add_to_disc(disc, ids(lib)[:2])
    win.disc_view.set_disc(disc)
    table, emitted = win.disc_view.table, []
    table.remove_requested.connect(lambda: emitted.append(1))

    def press(modifier):
        table.keyPressEvent(
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Backspace, modifier)
        )

    press(Qt.KeyboardModifier.ControlModifier)
    assert emitted == []  # ⌘⌫ is the shortcut for the disc, not for the tracks
    press(Qt.KeyboardModifier.NoModifier)
    assert emitted == [1]  # a plain Delete still takes the selected tracks off


# -- rename -----------------------------------------------------------------------


def test_command_r_renames_whatever_is_picked_in_the_discs_list(window):
    win, lib = window
    disc = lib.create_disc("D")
    folder = lib.create_disc_folder("F")
    mp3 = lib.create_disc("M", kind="mp3")
    inner = lib.create_disc_dir(mp3, "Inner")
    win.refresh_discs()
    for selection in (
        ("disc", disc),
        ("disc_folder", folder),
        ("disc_dir", mp3, inner),
    ):
        win.refresh_discs()
        index = pick(win, selection)
        win.act_rename.trigger()
        assert win.disc_tree.indexWidget(index) is not None, (
            selection
        )  # the editor is open
        win.disc_tree.closePersistentEditor(index)


# -- key notation -------------------------------------------------------------------


def key_column(win):
    from dubcrate.ui.models import TRACK_COLUMNS

    return next(i for i, c in enumerate(TRACK_COLUMNS) if c.title == "Key")


def shown_keys(win):
    model, column = win.track_table.model(), key_column(win)
    rows = sorted(range(model.rowCount()), key=lambda r: model.track_at(r).title)
    return [model.index(r, column).data() for r in rows]


def test_camelot_is_the_default_and_the_choice_is_remembered():
    assert settings.get_key_notation() == keys.CAMELOT
    settings.set_key_notation(keys.MUSICAL)
    assert settings.get_key_notation() == keys.MUSICAL
    settings.set_key_notation("nonsense")  # not one we offer: back to the default
    assert settings.get_key_notation() == keys.CAMELOT


def test_the_track_list_shows_keys_in_the_chosen_notation_and_redraws_live(window):
    win, lib = window
    win.show()
    assert shown_keys(win) == [
        "8A",
        "1A",
        "3B",
        "n/a",
    ]  # one notation, whatever the sources wrote
    changed = []
    win.track_table.model().dataChanged.connect(lambda *a: changed.append(a))
    key_style.set_notation(keys.MUSICAL)
    assert shown_keys(win) == ["A min", "G# min", "C# maj", "n/a"]
    assert changed  # the table was told to redraw, without being reloaded
    key_style.set_notation(keys.CAMELOT)
    assert shown_keys(win)[0] == "8A"
    assert {t.key for t in lib.tracks()} == set(
        WRITTEN_KEYS
    )  # the library keeps what it was given


def test_the_player_strip_follows_the_notation(window):
    win, lib = window
    track = lib.tracks(sort="title")[1]  # "Abm"
    win.player_strip.show_track(track)
    assert win.player_strip.key.text() == "1A"
    key_style.set_notation(keys.MUSICAL)
    assert win.player_strip.key.text() == "G# min"
    win.player_strip.clear()
    assert win.player_strip.key.text() == ""


def test_the_settings_window_offers_both_and_applies_at_once(window):
    _win, _lib = window
    dialog = SettingsDialog()
    assert [dialog.key_combo.itemData(i) for i in range(dialog.key_combo.count())] == [keys.CAMELOT, keys.MUSICAL]  # fmt: skip
    assert dialog.key_combo.currentData() == keys.CAMELOT
    dialog.key_combo.setCurrentIndex(1)
    dialog.key_combo.activated.emit(1)
    assert settings.get_key_notation() == keys.MUSICAL
    assert (
        SettingsDialog().key_combo.currentData() == keys.MUSICAL
    )  # shown as chosen next time
    dialog.deleteLater()


def test_sorting_by_key_goes_round_the_wheel_whatever_was_written(window):
    _win, lib = window
    order = [t.key for t in lib.tracks(sort="key")]
    assert order == ["Abm", "Db", "8A", "n/a"]  # 1A, 3B, 8A, then what is not a key


# -- the shortcuts window -----------------------------------------------------------


def native(sequence: str) -> str:
    from PySide6.QtGui import QKeySequence

    return QKeySequence(sequence).toString(QKeySequence.SequenceFormat.NativeText)


def test_every_menu_shortcut_is_listed_with_its_menu_and_the_platforms_keys(window):
    win, _lib = window
    rows = {(group, action): keys_ for group, action, keys_ in menu_shortcuts(win)}
    assert rows[("Disc", "New Disc")] == native("Ctrl+N")
    assert rows[("File", "Settings")] == native("Ctrl+,")
    assert rows[("Disc", "Rename")] == native("Ctrl+R")
    assert rows[("File", "Re-import Selected Source")] == native("Ctrl+Shift+R")
    assert rows[("Help", "Keyboard Shortcuts")] == native("Ctrl+/")
    # a name has no mnemonic ampersand and no ellipsis; an action with two shortcuts shows both
    assert not any("&" in action or action.endswith("…") for _g, action in rows)
    assert "  or  " in rows[("Disc", "Delete")]
    # and nothing with a shortcut is missing
    from PySide6.QtWidgets import QMenu

    expected = sum(
        1
        for menu in win.menuBar().findChildren(QMenu)
        for action in menu.actions()
        if action.shortcuts() and not action.menu()
    )
    assert len(rows) == expected


def test_the_window_lists_the_menu_ones_then_the_keys_that_are_not_in_a_menu(window):
    win, _lib = window
    dialog = ShortcutsDialog(win)
    shown = dialog.visible_rows()
    groups = list(dict.fromkeys(group for group, _a, _k in shown))
    assert (
        groups[:5] == ["File", "Disc", "View", "Help", "Playing"][:5]
        or "File" in groups
    )
    assert ("Playing", "Play or pause (anywhere except a text box)", "Space") in shown
    assert ("Track lists", "Add the selected tracks to the disc", "Return") in shown
    assert len(shown) == len(menu_shortcuts(win)) + len(EXTRA_SHORTCUTS)
    dialog.deleteLater()


def test_searching_matches_the_action_the_keys_and_the_menu(window):
    win, _lib = window
    dialog = ShortcutsDialog(win)
    dialog.search.setText("beat")
    assert [a for _g, a, _k in dialog.visible_rows()] == [
        "Beat jump back",
        "Beat jump forward",
    ]
    dialog.search.setText("space")
    assert [k for _g, _a, k in dialog.visible_rows()] == ["Space"]
    dialog.search.setText("burn")  # an action by name
    assert any(a == "Burn Disc" for _g, a, _k in dialog.visible_rows())
    dialog.search.setText("Track lists")  # a whole group, by its heading
    assert {g for g, _a, _k in dialog.visible_rows()} == {"Track lists"}
    dialog.search.setText("zzzz")
    assert dialog.visible_rows() == []
    assert all(
        group.isHidden() for group in dialog._groups
    )  # no empty headings left over
    dialog.search.setText("")
    assert len(dialog.visible_rows()) == len(dialog.rows)
    dialog.deleteLater()


def test_help_opens_it_beside_the_work_and_it_reflects_the_menus_as_they_are(window):
    win, _lib = window
    win.act_shortcuts.trigger()
    first = win.shortcuts_dialog
    assert first is not None and first.isVisible() and not first.isModal()
    win.act_shortcuts.trigger()  # asking again replaces it rather than stacking windows
    assert win.shortcuts_dialog is not first and not first.isVisible()
    from PySide6.QtGui import QKeySequence

    win.act_rename.setShortcut(
        QKeySequence("Ctrl+Shift+E")
    )  # a changed shortcut shows up by itself
    win.act_shortcuts.trigger()
    shown = win.shortcuts_dialog.visible_rows()
    assert ("Disc", "Rename", native("Ctrl+Shift+E")) in shown
    win.shortcuts_dialog.close()
