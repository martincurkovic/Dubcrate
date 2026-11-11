"""The right-hand pane: your discs as playlists, in folders, opened in the middle."""

import json

import pytest
from PySide6.QtCore import QEvent, QMimeData, QModelIndex, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QInputDialog, QMessageBox

from dubcrate import settings
from dubcrate.library import Library, NodeRecord, TrackRecord
from dubcrate.ui.main_window import MainWindow
from dubcrate.ui.models import (
    DISC_ITEM_MIME,
    SELECTION_ROLE,
    TRACK_MIME,
    find_selection,
)

YES = QMessageBox.StandardButton.Yes


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    shown = []
    for name in ("information", "warning"):
        monkeypatch.setattr(
            QMessageBox,
            name,
            staticmethod(lambda _p, t, x, _n=name: shown.append((_n, t, x))),
        )
    lib = Library.open(":memory:")
    records = []
    for i in range(6):
        file = tmp_path / f"t{i}.mp3"
        file.write_bytes(b"x")
        records.append(TrackRecord(ext_id=f"t{i}", path=str(file), title=f"Song {i}", artist=f"Artist {i}", duration=66.0))  # fmt: skip
    nodes = [NodeRecord("set", None, "Set", False, tuple(r.ext_id for r in records))]
    lib.replace_source("folder", str(tmp_path), "Crate", records, nodes)
    for track in lib.tracks():
        lib.set_cd_sectors(track.id, 5000)
    win = MainWindow(lib)
    win.shown = shown
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


def rows(win, parent=None):
    parent = QModelIndex() if parent is None else parent
    model = win.disc_tree.model()
    return [
        (model.index(r, 0, parent).data(Qt.ItemDataRole.DisplayRole),
         model.index(r, 0, parent).data(SELECTION_ROLE))
        for r in range(model.rowCount(parent))
    ]  # fmt: skip


def mime(kind, payload):
    data = QMimeData()
    data.setData(kind, json.dumps(payload).encode())
    return data


# -- the layout -----------------------------------------------------------------


def test_three_panes_the_file_tree_the_middle_and_your_discs(window):
    win, _lib = window
    assert win.splitter.count() == 3
    assert win.splitter.widget(0).isAncestorOf(win.tree)
    assert win.splitter.widget(1) is win.center
    assert win.splitter.widget(2).isAncestorOf(win.disc_tree)
    assert (
        win.new_disc_button.text() == "New Disc"
        and win.new_folder_button.text() == "New Folder"
    )


def test_the_left_tree_has_only_the_library_not_the_discs(window):
    win, lib = window
    lib.create_disc("Night")
    win.refresh_discs()
    model = win.tree.model()
    left = [
        model.index(r, 0).data(Qt.ItemDataRole.DisplayRole)
        for r in range(model.rowCount())
    ]
    assert left == ["All Tracks", "Crate"]
    assert [k for _n, k in rows(win)] == [
        ("disc", lib.discs()[0].id)
    ]  # it is on the right


def test_the_disc_tree_lists_folders_first_then_discs_each_a_to_z(window):
    win, lib = window
    lib.create_disc_folder("zebra")
    lib.create_disc_folder("Apple")
    lib.create_disc("banana")
    lib.create_disc("Cherry")
    win.refresh_discs()
    assert [n for n, _k in rows(win)] == ["Apple", "zebra", "banana", "Cherry"]


def test_discs_sit_inside_their_folders_and_folders_inside_folders(window):
    win, lib = window
    gigs = lib.create_disc_folder("Gigs")
    sets = lib.create_disc_folder("Sets", parent_id=gigs)
    lib.create_disc("In Sets", folder_id=sets)
    lib.create_disc("In Gigs", folder_id=gigs)
    lib.create_disc("Top")
    win.refresh_discs()
    model = win.disc_tree.model()
    top = rows(win)
    assert [n for n, _k in top] == ["Gigs", "Top"]
    gigs_index = find_selection(model, ("disc_folder", gigs))
    assert [n for n, _k in rows(win, gigs_index)] == ["Sets", "In Gigs"]
    sets_index = find_selection(model, ("disc_folder", sets))
    assert [n for n, _k in rows(win, sets_index)] == ["In Sets"]


def test_a_discs_row_shows_its_track_count_and_a_folder_shows_none(window):
    win, lib = window
    disc = lib.create_disc("Night")
    lib.add_to_disc(disc, ids(lib)[:3])
    lib.create_disc_folder("F")
    win.refresh_discs()
    model = win.disc_tree.model()
    disc_row = find_selection(model, ("disc", disc))
    assert model.index(disc_row.row(), 1).data() == "3"
    folder_row = find_selection(model, ("disc_folder", lib.disc_folders()[0].id))
    assert model.index(folder_row.row(), 1).data() == ""


def test_discs_have_their_own_icon_unlike_playlists_and_folders(window):
    win, lib = window
    disc = lib.create_disc("Night")
    folder = lib.create_disc_folder("F")
    win.refresh_discs()
    model = win.disc_tree.model()

    def image(index):
        return index.data(Qt.ItemDataRole.DecorationRole).pixmap(16, 16).toImage()

    disc_icon = image(find_selection(model, ("disc", disc)))
    folder_icon = image(find_selection(model, ("disc_folder", folder)))
    library_playlist = win.tree.model().index(0, 0)  # "All Tracks" has none; use a node
    set_index = find_selection(
        win.tree.model(),
        ("node", lib.nodes(lib.sources()[0].id)[0].id, lib.sources()[0].id),
    )
    assert not disc_icon.isNull() and disc_icon != folder_icon
    assert disc_icon != image(set_index)
    del library_playlist


def test_the_tooltip_says_how_full_a_disc_is_and_when_it_was_burned(window):
    win, lib = window
    disc = lib.create_disc("Night")
    lib.add_to_disc(disc, ids(lib)[:2])
    lib.mark_burned(disc, when="2026-11-01T20:00:00+00:00")
    win.refresh_discs()
    tip = find_selection(win.disc_tree.model(), ("disc", disc)).data(
        Qt.ItemDataRole.ToolTipRole
    )
    assert "Night" in tip and "2 tracks" in tip and "of 80:00" in tip
    assert "Last burned 2026-11-01" in tip


# -- opening a disc ---------------------------------------------------------------


def test_clicking_a_disc_opens_it_in_the_middle_and_makes_it_the_target(window):
    win, lib = window
    disc = lib.create_disc("Night")
    lib.add_to_disc(disc, ids(lib)[:2])
    win.refresh_discs()
    assert win.center.currentIndex() == 0
    pick(win, ("disc", disc))
    assert win.center.currentIndex() == 1
    assert win.disc_view.current_disc_id() == disc
    assert win.disc_view.title.text() == "Night"
    assert win.disc_view.model.rowCount() == 2
    assert "Adding to “Night”" in win.target_label.text()


def test_the_target_disc_is_bold_in_the_tree(window):
    win, lib = window
    one, two = lib.create_disc("One"), lib.create_disc("Two")
    win.refresh_discs()
    pick(win, ("disc", two))
    win.refresh_discs()
    model = win.disc_tree.model()
    assert find_selection(model, ("disc", two)).data(Qt.ItemDataRole.FontRole).bold()
    one_font = find_selection(model, ("disc", one)).data(Qt.ItemDataRole.FontRole)
    assert one_font is None or not one_font.bold()


def test_clicking_a_folder_changes_nothing_in_the_middle(window):
    win, lib = window
    disc = lib.create_disc("Night")
    folder = lib.create_disc_folder("F")
    win.refresh_discs()
    pick(win, ("disc", disc))
    pick(win, ("disc_folder", folder))
    assert win.center.currentIndex() == 1 and win.disc_view.current_disc_id() == disc


def test_going_back_to_the_library_keeps_the_target_so_tracks_can_be_added(window):
    win, lib = window
    disc = lib.create_disc("Night")
    win.refresh_discs()
    pick(win, ("disc", disc))
    win.tree.setCurrentIndex(find_selection(win.tree.model(), ("all",)))
    win.tree.selectionModel().currentChanged.emit(
        win.tree.currentIndex(), QModelIndex()
    )
    assert win.center.currentIndex() == 0
    assert win.disc_view.current_disc_id() == disc  # still the target
    win.track_table.selectRow(0)
    win.add_selected_to_disc()
    assert lib.disc(disc).track_count == 1
    assert (
        win.center.currentIndex() == 0
    )  # and adding didn't pull you away from the library


def test_the_library_stays_where_it_was_when_a_disc_is_picked_in_the_other_tree(window):
    win, lib = window
    disc = lib.create_disc("Night")
    win.refresh_discs()
    before = win._selection
    pick(win, ("disc", disc))
    assert win._selection == before  # the library tree's own selection is untouched


def test_deleting_the_open_disc_returns_to_the_library(window, monkeypatch):
    win, lib = window
    disc = lib.create_disc("Night")
    win.refresh_discs()
    pick(win, ("disc", disc))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: YES))
    win.disc_view.delete_disc()
    assert lib.discs() == [] and win.center.currentIndex() == 0
    assert rows(win) == []


def test_find_tracks_from_an_open_disc_goes_to_the_library_search(window):
    win, lib = window
    disc = lib.create_disc("Night")
    win.refresh_discs()
    pick(win, ("disc", disc))
    win.act_find.trigger()
    assert win.center.currentIndex() == 0 and win.focusWidget() is win.search


def test_previous_and_next_step_through_the_open_disc_in_its_order(window):
    win, lib = window
    disc = lib.create_disc("Night")
    lib.add_to_disc(disc, ids(lib)[2:5])
    win.refresh_discs()
    pick(win, ("disc", disc))
    loaded = []
    win.preview.load = lambda track, **k: loaded.append(track.title)
    win._step_track(1)
    win._step_track(1)
    assert loaded[0] == "Song 2"  # the first one of the DISC, not of the library


def test_double_clicking_a_disc_rows_number_listens_and_the_title_edits(window):
    win, lib = window
    disc = lib.create_disc("Night")
    lib.add_to_disc(disc, ids(lib)[:2])
    win.refresh_discs()
    pick(win, ("disc", disc))
    heard = []
    win.disc_view.preview_requested.connect(lambda t: heard.append(t.title))
    win.disc_view._on_double_clicked(win.disc_view.model.index(1, 0))  # the number
    win.disc_view._on_double_clicked(
        win.disc_view.model.index(1, 3)
    )  # the title: edits
    assert heard == ["Song 1"]


# -- making discs and folders -----------------------------------------------------


def named(monkeypatch, text):
    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: (text, True))
    )


def test_new_disc_makes_one_opens_it_and_selects_it_in_the_tree(window, monkeypatch):
    win, lib = window
    named(monkeypatch, "Friday")
    win.new_disc_button.click()
    [disc] = lib.discs()
    assert disc.name == "Friday"
    assert win.center.currentIndex() == 1 and win.disc_view.current_disc_id() == disc.id
    assert win._disc_pick == ("disc", disc.id)


def test_new_folder_makes_a_folder_in_the_tree(window, monkeypatch):
    win, lib = window
    named(monkeypatch, "Gigs")
    win.new_folder_button.click()
    [folder] = lib.disc_folders()
    assert folder.name == "Gigs" and folder.parent_id is None
    assert [n for n, _k in rows(win)] == ["Gigs"]


def test_a_new_disc_goes_in_the_folder_that_is_picked(window, monkeypatch):
    win, lib = window
    folder = lib.create_disc_folder("Gigs")
    win.refresh_discs()
    pick(win, ("disc_folder", folder))
    named(monkeypatch, "In Gigs")
    win.new_disc_button.click()
    assert lib.discs()[0].folder_id == folder


def test_a_new_disc_goes_beside_the_picked_disc_in_its_folder(window, monkeypatch):
    win, lib = window
    folder = lib.create_disc_folder("Gigs")
    first = lib.create_disc("First", folder_id=folder)
    win.refresh_discs()
    pick(win, ("disc", first))
    named(monkeypatch, "Second")
    win.new_disc_button.click()
    second = next(d for d in lib.discs() if d.name == "Second")
    assert second.folder_id == folder


def test_folders_that_were_open_stay_open_after_the_tree_is_rebuilt(window):
    win, lib = window
    folder = lib.create_disc_folder("Gigs")
    lib.create_disc("In Gigs", folder_id=folder)
    win.refresh_discs()
    win.disc_tree.setExpanded(
        find_selection(win.disc_tree.model(), ("disc_folder", folder)), True
    )
    win.refresh_discs()
    assert win.disc_tree.isExpanded(
        find_selection(win.disc_tree.model(), ("disc_folder", folder))
    )


def test_the_pick_survives_a_rebuild(window):
    win, lib = window
    disc = lib.create_disc("Night")
    win.refresh_discs()
    pick(win, ("disc", disc))
    win.refresh_discs()
    assert win.disc_tree.currentIndex().data(SELECTION_ROLE) == ("disc", disc)


# -- right-click --------------------------------------------------------------------


def menu_texts(menu):
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def test_a_discs_menu_opens_renames_duplicates_burns_and_deletes(window):
    win, lib = window
    disc = lib.create_disc("Night")
    win.refresh_discs()
    menu = win.disc_tree_menu(find_selection(win.disc_tree.model(), ("disc", disc)))
    assert menu_texts(menu) == ["Open", "Rename…", "Duplicate", "Burn…", "Delete…"]


def test_a_folders_menu_makes_things_inside_it_renames_and_deletes(window):
    win, lib = window
    folder = lib.create_disc_folder("F")
    win.refresh_discs()
    menu = win.disc_tree_menu(
        find_selection(win.disc_tree.model(), ("disc_folder", folder))
    )
    assert menu_texts(menu) == [
        "New Disc Here…",
        "New Folder Here…",
        "Rename…",
        "Delete Folder…",
    ]


def test_empty_space_offers_a_new_disc_or_folder_at_the_top_level(window, monkeypatch):
    win, lib = window
    folder = lib.create_disc_folder("F")
    win.refresh_discs()
    pick(
        win, ("disc_folder", folder)
    )  # something is picked, but the menu is for empty space
    menu = win.disc_tree_menu(QModelIndex())
    assert menu_texts(menu) == ["New Disc…", "New Folder…"]
    named(monkeypatch, "Top Level")
    menu.actions()[0].trigger()
    assert lib.discs()[0].folder_id is None  # the top level, not the picked folder


def test_the_menu_actions_do_what_they_say(window, monkeypatch):
    win, lib = window
    disc = lib.create_disc("Night")
    lib.add_to_disc(disc, ids(lib)[:2])
    win.refresh_discs()
    index = find_selection(win.disc_tree.model(), ("disc", disc))
    actions = {
        a.text(): a for a in win.disc_tree_menu(index).actions() if not a.isSeparator()
    }
    named(monkeypatch, "Renamed")
    actions["Rename…"].trigger()
    assert lib.disc(disc).name == "Renamed"
    actions["Duplicate"].trigger()
    assert {d.name for d in lib.discs()} == {"Renamed", "Renamed (copy)"}
    assert win.center.currentIndex() == 1  # the copy was opened
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: YES))
    actions["Delete…"].trigger()
    assert {d.name for d in lib.discs()} == {"Renamed (copy)"}


def test_folder_menu_rename_and_delete_keep_the_discs(window, monkeypatch):
    win, lib = window
    folder = lib.create_disc_folder("Old")
    disc = lib.create_disc("D", folder_id=folder)
    win.refresh_discs()
    menu = win.disc_tree_menu(
        find_selection(win.disc_tree.model(), ("disc_folder", folder))
    )
    actions = {a.text(): a for a in menu.actions() if not a.isSeparator()}
    named(monkeypatch, "New Name")
    actions["Rename…"].trigger()
    assert lib.disc_folders()[0].name == "New Name"
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: YES))
    actions["Delete Folder…"].trigger()
    assert (
        lib.disc_folders() == [] and lib.disc(disc).folder_id is None
    )  # the disc survives


# -- dragging -----------------------------------------------------------------------


def test_dragging_tracks_onto_a_disc_adds_them_to_that_disc_only(window):
    win, lib = window
    target, other = lib.create_disc("Target"), lib.create_disc("Other")
    win.refresh_discs()
    pick(win, ("disc", target))  # the open disc is "Target"
    model = win.disc_tree.model()
    onto = find_selection(model, ("disc", other))
    assert model.dropMimeData(
        mime(TRACK_MIME, ids(lib)[:2]), Qt.DropAction.CopyAction, -1, -1, onto
    )
    assert lib.disc(other).track_count == 2 and lib.disc(target).track_count == 0
    assert win.disc_view.current_disc_id() == target  # the open disc is unchanged


def test_tracks_can_only_be_dropped_on_a_disc(window):
    win, lib = window
    folder = lib.create_disc_folder("F")
    win.refresh_discs()
    model = win.disc_tree.model()
    data = mime(TRACK_MIME, ids(lib)[:1])
    on_folder = find_selection(model, ("disc_folder", folder))
    assert not model.canDropMimeData(data, Qt.DropAction.CopyAction, -1, -1, on_folder)
    assert not model.canDropMimeData(
        data, Qt.DropAction.CopyAction, -1, -1, QModelIndex()
    )


def test_dragging_a_disc_into_a_folder_files_it(window):
    win, lib = window
    folder = lib.create_disc_folder("F")
    disc = lib.create_disc("D")
    win.refresh_discs()
    model = win.disc_tree.model()
    target = find_selection(model, ("disc_folder", folder))
    assert model.dropMimeData(
        mime(DISC_ITEM_MIME, [["disc", disc]]), Qt.DropAction.MoveAction, -1, -1, target
    )
    assert lib.disc(disc).folder_id == folder


def test_dropping_on_empty_space_files_it_at_the_top_level(window):
    win, lib = window
    folder = lib.create_disc_folder("F")
    disc = lib.create_disc("D", folder_id=folder)
    win.refresh_discs()
    model = win.disc_tree.model()
    assert model.dropMimeData(
        mime(DISC_ITEM_MIME, [["disc", disc]]),
        Qt.DropAction.MoveAction,
        -1,
        -1,
        QModelIndex(),
    )
    assert lib.disc(disc).folder_id is None


def test_a_folder_can_be_dragged_into_another_but_not_into_its_own_child(window):
    win, lib = window
    outer = lib.create_disc_folder("Outer")
    inner = lib.create_disc_folder("Inner", parent_id=outer)
    other = lib.create_disc_folder("Other")
    win.refresh_discs()
    model = win.disc_tree.model()
    assert model.dropMimeData(
        mime(DISC_ITEM_MIME, [["disc_folder", other]]),
        Qt.DropAction.MoveAction,
        -1,
        -1,
        find_selection(model, ("disc_folder", outer)),
    )
    assert {f.id: f.parent_id for f in lib.disc_folders()}[other] == outer
    win.refresh_discs()
    model = win.disc_tree.model()
    model.dropMimeData(
        mime(DISC_ITEM_MIME, [["disc_folder", outer]]),
        Qt.DropAction.MoveAction,
        -1,
        -1,
        find_selection(model, ("disc_folder", inner)),
    )
    assert {f.id: f.parent_id for f in lib.disc_folders()}[outer] is None  # refused
    assert any("inside itself" in text for _kind, _title, text in win.shown)


def test_nothing_can_be_dropped_inside_a_disc(window):
    win, lib = window
    disc = lib.create_disc("D")
    other = lib.create_disc("Other")
    win.refresh_discs()
    model = win.disc_tree.model()
    onto = find_selection(model, ("disc", disc))
    assert not model.canDropMimeData(
        mime(DISC_ITEM_MIME, [["disc", other]]), Qt.DropAction.MoveAction, -1, -1, onto
    )


def test_dragging_a_disc_out_of_the_tree_carries_it_and_its_folders(window):
    win, lib = window
    disc = lib.create_disc("D")
    folder = lib.create_disc_folder("F")
    win.refresh_discs()
    model = win.disc_tree.model()
    indexes = [
        find_selection(model, ("disc", disc)),
        find_selection(model, ("disc_folder", folder)),
    ]
    items = json.loads(bytes(model.mimeData(indexes).data(DISC_ITEM_MIME)).decode())
    assert sorted(items) == [["disc", disc], ["disc_folder", folder]]


def test_the_tree_never_deletes_rows_itself(window):
    win, lib = window
    lib.create_disc("D")
    win.refresh_discs()
    model = win.disc_tree.model()
    assert model.removeRows(0, 1) is False and model.rowCount() == 1


# -- keys on the disc list ----------------------------------------------------------


def test_the_disc_list_has_the_same_transport_keys_as_the_track_list(window):
    win, _lib = window
    table = win.disc_view.table
    seen = []
    table.play_pause_requested.connect(lambda: seen.append("space"))
    table.beat_jump_requested.connect(seen.append)

    def press(key, modifiers=Qt.KeyboardModifier.NoModifier):
        table.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, modifiers))

    press(Qt.Key.Key_Space)
    press(Qt.Key.Key_Left, Qt.KeyboardModifier.KeypadModifier)  # (what macOS reports)
    press(Qt.Key.Key_Right, Qt.KeyboardModifier.KeypadModifier)
    press(
        Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier
    )  # a real modifier: not a jump
    assert seen == ["space", -1, 1]


def test_the_disc_pane_remembers_the_last_open_disc_next_time(window):
    win, lib = window
    disc = lib.create_disc("Night")
    win.refresh_discs()
    pick(win, ("disc", disc))
    assert settings.get_last_disc() == disc
    again = MainWindow(lib)
    try:
        assert again.disc_view.current_disc_id() == disc
        assert (
            again.center.currentIndex() == 0
        )  # it starts on the library, with the target ready
        assert again._disc_pick == ("disc", disc)
    finally:
        again.close()
        again.deleteLater()


# -- the + button on the picked row, and the "too long" cue ---------------------------


def plus_point(win, selection):
    """Where the + sits on a row, in the tree viewport's coordinates."""
    index = find_selection(win.disc_tree.model(), selection)
    return win.disc_tree.plus_rect(index).center(), index


def click_at(win, point):
    from PySide6.QtTest import QTest

    QTest.mouseClick(win.disc_tree.viewport(), Qt.MouseButton.LeftButton, pos=point)


def ask(monkeypatch, name="Friday", ok=True):
    asked = []
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        staticmethod(lambda *a, **k: asked.append(1) or (name, ok)),
    )
    return asked


def test_a_folders_plus_makes_a_disc_inside_it(window, monkeypatch):
    win, lib = window
    folder = lib.create_disc_folder("Gigs")
    win.refresh_discs()
    win.show()
    ask(monkeypatch)
    point, index = plus_point(win, ("disc_folder", folder))
    win.disc_tree.setCurrentIndex(index)
    click_at(win, point)
    disc = next(d for d in lib.discs() if d.name == "Friday")
    assert disc.folder_id == folder
    assert win.disc_view.current_disc_id() == disc.id  # and it is open, ready to fill
    assert win.center.currentIndex() == 1


def test_a_discs_plus_makes_another_disc_beside_it(window, monkeypatch):
    win, lib = window
    folder = lib.create_disc_folder("Gigs")
    inside = lib.create_disc("Inside", folder_id=folder)
    top = lib.create_disc("Top")
    win.refresh_discs()
    win.show()
    win.disc_tree.expandAll()
    ask(monkeypatch, "Second")
    point, index = plus_point(win, ("disc", inside))
    win.disc_tree.setCurrentIndex(index)
    click_at(win, point)
    assert next(d for d in lib.discs() if d.name == "Second").folder_id == folder
    ask(monkeypatch, "Third")
    point, index = plus_point(win, ("disc", top))
    win.disc_tree.setCurrentIndex(index)
    click_at(win, point)
    assert next(d for d in lib.discs() if d.name == "Third").folder_id is None


def test_every_row_has_a_plus_but_only_the_picked_one_shows_it(window):
    win, lib = window
    folder = lib.create_disc_folder("Gigs")
    disc = lib.create_disc("Night")
    inside = lib.create_disc("Inside", folder_id=folder)
    win.refresh_discs()
    win.show()
    win.disc_tree.expandAll()
    tree, model = win.disc_tree, win.disc_tree.model()
    index = {
        key: find_selection(model, key)
        for key in (("disc_folder", folder), ("disc", disc), ("disc", inside))
    }
    tree.clearSelection()
    assert not any(tree.plus_visible(i) for i in index.values())
    for picked in index.values():
        tree.setCurrentIndex(picked)
        shown = [i for i in index.values() if tree.plus_visible(i)]
        assert shown == [picked]


def test_hovering_a_row_does_not_show_its_plus(window):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    win, lib = window
    one, two = lib.create_disc_folder("One"), lib.create_disc_folder("Two")
    win.refresh_discs()
    win.show()
    tree = win.disc_tree
    pick(win, ("disc_folder", one))
    second = find_selection(tree.model(), ("disc_folder", two))
    point = tree.visualRect(second).center()
    QApplication.sendEvent(
        tree.viewport(),
        QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(point),
            QPointF(tree.viewport().mapToGlobal(point)),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )
    assert not tree.plus_visible(second)


def test_clicking_the_plus_of_an_unpicked_row_just_picks_the_row(window, monkeypatch):
    win, lib = window
    one, two = lib.create_disc_folder("One"), lib.create_disc_folder("Two")
    win.refresh_discs()
    win.show()
    asked = ask(monkeypatch)
    pick(win, ("disc_folder", one))
    point, index = plus_point(win, ("disc_folder", two))  # where it would be
    click_at(win, point)
    assert not asked and lib.discs() == []
    assert win.disc_tree.currentIndex().siblingAtColumn(0) == index


def test_cancelling_the_name_makes_nothing(window, monkeypatch):
    win, lib = window
    folder = lib.create_disc_folder("Gigs")
    win.refresh_discs()
    win.show()
    ask(monkeypatch, "", ok=False)
    point, index = plus_point(win, ("disc_folder", folder))
    win.disc_tree.setCurrentIndex(index)
    click_at(win, point)
    assert lib.discs() == [] and win.center.currentIndex() == 0


def test_clicking_elsewhere_on_a_picked_row_does_not_make_a_disc(window, monkeypatch):
    win, lib = window
    folder = lib.create_disc_folder("Gigs")
    win.refresh_discs()
    win.show()
    asked = ask(monkeypatch)
    _point, index = plus_point(win, ("disc_folder", folder))
    win.disc_tree.setCurrentIndex(index)
    click_at(win, win.disc_tree.visualRect(index).center())
    assert not asked and lib.discs() == []


def test_a_disc_over_its_length_is_starred_and_explained(window):
    win, lib = window
    ok, long = lib.create_disc("Fits"), lib.create_disc("Too long")
    lib.add_to_disc(ok, ids(lib)[:1])
    lib.update_disc(long, capacity_sectors=8000)  # two 5,000-sector tracks do not fit
    lib.add_to_disc(long, ids(lib)[:2])
    win.refresh_discs()
    model = win.disc_tree.model()

    def cells(disc_id):
        index = find_selection(model, ("disc", disc_id))
        return index, index.siblingAtColumn(1)

    fits_name, fits_count = cells(ok)
    long_name, long_count = cells(long)
    assert fits_count.data(Qt.ItemDataRole.DisplayRole) == "1"
    assert "*" not in fits_name.data(Qt.ItemDataRole.ToolTipRole)
    assert long_count.data(Qt.ItemDataRole.DisplayRole) == "* 2"
    assert long_name.data(Qt.ItemDataRole.DisplayRole) == "Too long"  # the name is left alone  # fmt: skip
    for cell in (long_name, long_count):
        tip = cell.data(Qt.ItemDataRole.ToolTipRole)
        assert "Too long" in tip and "over" in tip.lower() and "0:" in tip
    assert long_count.data(Qt.ItemDataRole.ForegroundRole) != fits_count.data(
        Qt.ItemDataRole.ForegroundRole
    )
