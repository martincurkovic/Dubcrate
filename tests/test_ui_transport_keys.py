"""Space plays or pauses and Left / Right beat jump, wherever focus is in the main window,
except where those keys already mean something else (typing, sliders, tree branches)."""

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QSpinBox

from dubcrate.library import Library, NodeRecord, TrackRecord
from dubcrate.ui.main_window import MainWindow
from dubcrate.ui.models import find_selection


@pytest.fixture
def window(qapp, tmp_path):
    lib = Library.open(":memory:")
    records = []
    for i in range(3):
        file = tmp_path / f"t{i}.mp3"
        file.write_bytes(b"x")
        records.append(TrackRecord(ext_id=f"t{i}", path=str(file), title=f"Song {i}", artist="A", duration=66.0))  # fmt: skip
    lib.replace_source("folder", str(tmp_path), "Crate", records, [NodeRecord("set", None, "Set", False, tuple(r.ext_id for r in records))])  # fmt: skip
    for track in lib.tracks():
        lib.set_cd_sectors(track.id, 5000)
    win = MainWindow(lib)
    win.seen = []
    win.preview.toggle = lambda: win.seen.append("play")
    win.preview.beat_jump = lambda d: win.seen.append(d)
    win.show()
    win.activateWindow()
    qapp.processEvents()
    yield win
    win.close()
    win.deleteLater()
    lib.close()


def press(widget, key, modifiers=Qt.KeyboardModifier.NoModifier):
    widget.setFocus()
    assert QApplication.focusWidget() is widget
    QTest.keyClick(widget, key, modifiers)


@pytest.mark.parametrize(
    "where", ["track_table", "player_strip", "center", "new_disc_button", "tree"]
)
def test_space_and_the_arrows_work_wherever_focus_is(window, where):
    win = window
    target = getattr(win, where)
    target.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    press(target, Qt.Key.Key_Space)
    press(target, Qt.Key.Key_Left)
    press(target, Qt.Key.Key_Right)
    expected = ["play"] + ([] if where == "tree" else [-1, 1])
    assert win.seen == expected


def test_the_open_discs_table_and_the_disc_tree_work_too(window):
    win = window
    disc = win._lib.create_disc("Night")
    win.refresh_discs()
    win.open_disc(disc)
    press(win.disc_view.table, Qt.Key.Key_Space)
    press(win.disc_view.table, Qt.Key.Key_Right)
    press(win.disc_tree, Qt.Key.Key_Space)
    assert win.seen == ["play", 1, "play"]


def test_a_tree_keeps_left_and_right_for_opening_and_closing_folders(window):
    win = window
    folder = win._lib.create_disc_folder("Gigs")
    win._lib.create_disc("Friday", folder_id=folder)
    win.refresh_discs()
    index = find_selection(win.disc_tree.model(), ("disc_folder", folder))
    win.disc_tree.setCurrentIndex(index)
    press(win.disc_tree, Qt.Key.Key_Right)
    assert win.disc_tree.isExpanded(index) and win.seen == []
    press(win.disc_tree, Qt.Key.Key_Left)
    assert not win.disc_tree.isExpanded(index) and win.seen == []


def test_typing_in_the_search_box_is_left_alone(window):
    win = window
    press(win.search, Qt.Key.Key_A)
    press(win.search, Qt.Key.Key_Space)
    press(win.search, Qt.Key.Key_B)
    press(win.search, Qt.Key.Key_Left)
    press(win.search, Qt.Key.Key_Left)
    press(win.search, Qt.Key.Key_X)
    assert win.search.text() == "ax b" and win.seen == []


def test_editing_a_cd_text_cell_is_left_alone(window):
    win = window
    disc = win._lib.create_disc("Night")
    win._lib.add_to_disc(disc, [t.id for t in win._lib.tracks()][:1])
    win.refresh_discs()
    win.open_disc(disc)
    table = win.disc_view.table
    table.setFocus()
    table.edit(win.disc_view.model.index(0, 3))  # the title
    editor = QApplication.focusWidget()
    assert editor is not table and editor is not None  # the cell's own text editor
    QTest.keyClick(editor, Qt.Key.Key_Space)
    QTest.keyClick(editor, Qt.Key.Key_Left)
    assert win.seen == []


def test_a_slider_keeps_its_arrows_but_space_still_plays(window):
    win = window
    slider = win.player_strip.volume
    slider.setValue(50)
    slider.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    press(slider, Qt.Key.Key_Left)
    assert slider.value() < 50 and win.seen == []
    press(slider, Qt.Key.Key_Space)
    assert win.seen == ["play"]


@pytest.mark.parametrize("widget_type", [QSpinBox, QComboBox])
def test_form_controls_inside_the_window_keep_their_keys(window, widget_type):
    win = window
    box = widget_type(win.centralWidget())
    box.show()
    press(box, Qt.Key.Key_Left)
    press(box, Qt.Key.Key_Space)
    assert win.seen == []


def test_modifiers_make_it_a_different_shortcut(window):
    win = window
    press(win.track_table, Qt.Key.Key_Left, Qt.KeyboardModifier.ControlModifier)
    press(win.track_table, Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier)
    assert win.seen == []


def test_holding_space_does_not_flicker_play_and_pause(window):
    win = window
    win.track_table.setFocus()
    for repeat in (False, True, True):
        event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Space,
            Qt.KeyboardModifier.NoModifier,
            " ",
            repeat,
        )
        QApplication.sendEvent(win.track_table, event)
    assert win.seen == ["play"]


def test_a_dialog_in_front_gets_its_own_keys(window):
    win = window
    dialog = QDialog(win)
    dialog.show()
    dialog.activateWindow()
    QApplication.processEvents()
    if QApplication.activeWindow() is not dialog:
        pytest.skip("this platform does not activate the dialog offscreen")
    QTest.keyClick(dialog, Qt.Key.Key_Space)
    QTest.keyClick(dialog, Qt.Key.Key_Left)
    assert win.seen == []
    dialog.close()


def test_the_window_stops_listening_once_closed(window, qapp):
    win = window
    win.close()
    assert win.seen == []
    QTest.keyClick(qapp.focusWidget() or win, Qt.Key.Key_Space)
    assert win.seen == []
