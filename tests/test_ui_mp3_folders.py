"""Folders on an MP3 disc in the window: the tree under the disc, opening one, filling and
emptying it, and what the player's limits do to the menus."""

import json

import pytest
from PySide6.QtCore import QMimeData, QModelIndex, Qt
from PySide6.QtWidgets import QMessageBox

from dubcrate.library import Library, NodeRecord, TrackRecord
from dubcrate.ui.main_window import MainWindow
from dubcrate.ui.models import (
    DISC_ROWS_MIME,
    SELECTION_ROLE,
    TRACK_MIME,
    find_selection,
)
from dubcrate.ui.overlaps import OverlapChoice

YES, NO = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No


def mime(kind, payload):
    data = QMimeData()
    data.setData(kind, json.dumps(payload).encode())
    return data


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
        file.write_bytes(b"x" * 3000)
        records.append(TrackRecord(ext_id=f"t{i}", path=str(file), title=f"Song {i}", artist="Art", duration=2.0, file_size=3000))  # fmt: skip
    nodes = [NodeRecord("set", None, "Set", False, tuple(r.ext_id for r in records))]
    lib.replace_source("folder", str(tmp_path), "Crate", records, nodes)
    for track in lib.tracks():
        lib.set_cd_sectors(track.id, 5000)  # measured already, so adding is immediate
    win = MainWindow(lib)
    win.shown = shown
    yield win, lib
    win.close()
    win.deleteLater()
    lib.close()


def ids(lib):
    return [t.id for t in lib.tracks(sort="title")]


def index(win, selection):
    found = find_selection(win.disc_tree.model(), selection)
    assert found.isValid(), selection
    return found


def shown_titles(win):
    return [
        win.disc_view.model.entry_at(r).title
        for r in range(win.disc_view.model.rowCount())
    ]


def ask(monkeypatch, answer):
    asked = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda _p, t, x, *a, **k: asked.append((t, x)) or answer),
    )
    return asked


@pytest.fixture
def mp3(window):
    """An MP3 disc with House (holding two tracks, and Deep inside it with one) and two in the root."""
    win, lib = window
    disc = lib.create_disc("Set", kind="mp3")
    house = lib.create_disc_dir(disc, "House")
    deep = lib.create_disc_dir(disc, "Deep", parent_id=house)
    t = ids(lib)
    lib.add_to_disc(disc, t[:2])
    lib.add_to_disc(disc, t[2:4], dir_id=house)
    lib.add_to_disc(disc, t[4:5], dir_id=deep)
    win.refresh_discs()
    return win, lib, disc, house, deep


# -- the tree -----------------------------------------------------------------


def test_an_mp3_disc_shows_its_folders_under_it_and_an_audio_disc_does_not(mp3):
    win, lib, disc, house, deep = mp3
    audio = lib.create_disc("Plain")
    win.refresh_discs()
    model = win.disc_tree.model()
    disc_index = index(win, ("disc", disc))
    assert model.rowCount(disc_index) == 1
    house_index = index(win, ("disc_dir", disc, house))
    assert house_index.parent() == disc_index and house_index.data() == "House"
    deep_index = index(win, ("disc_dir", disc, deep))
    assert deep_index.parent() == house_index
    assert model.index(house_index.row(), 1, disc_index).data() == "2"  # its own files
    assert model.rowCount(index(win, ("disc", audio))) == 0
    assert (
        model.index(disc_index.row(), 1).data() == "MP3 5"
    )  # the disc counts them all


def test_a_folder_in_the_tree_is_renamed_and_dropped_on_but_not_dragged(mp3):
    win, _lib, disc, house, _ = mp3
    model = win.disc_tree.model()
    flags = model.flags(index(win, ("disc_dir", disc, house)))
    assert flags & Qt.ItemFlag.ItemIsEditable and flags & Qt.ItemFlag.ItemIsDropEnabled
    assert not flags & Qt.ItemFlag.ItemIsDragEnabled
    assert not model.flags(index(win, ("disc_dir", disc, house)).siblingAtColumn(1)) & Qt.ItemFlag.ItemIsEditable  # fmt: skip


# -- opening one --------------------------------------------------------------


def test_clicking_a_folder_shows_its_tracks_and_the_bar_stays_the_whole_disc(mp3):
    win, _lib, disc, house, deep = mp3
    win.disc_tree.setCurrentIndex(index(win, ("disc_dir", disc, house)))
    assert win.center.currentIndex() == 1
    assert (win.disc_view.current_disc_id(), win.disc_view.current_dir_id()) == (
        disc,
        house,
    )
    assert shown_titles(win) == ["Song 2", "Song 3"]
    assert win.disc_view.model.index(1, 0).data() == "2"  # numbered within the folder
    assert win.disc_view.crumb_names() == ["Set", "House"]
    assert "5 tracks  ·  2 here" in win.disc_view.count.text()
    assert len(win.disc_view.summary.bar.spans) == 5  # the whole disc
    win.disc_tree.setCurrentIndex(index(win, ("disc_dir", disc, deep)))
    assert win.disc_view.crumb_names() == ["Set", "House", "Deep"]
    assert shown_titles(win) == ["Song 4"]
    win.disc_tree.setCurrentIndex(
        index(win, ("disc", disc))
    )  # the disc itself: its root
    assert win.disc_view.current_dir_id() is None and shown_titles(win) == [
        "Song 0",
        "Song 1",
    ]


def test_opening_the_same_disc_from_another_folder_still_switches_the_view(mp3):
    win, _lib, disc, house, _ = mp3
    win.disc_tree.setCurrentIndex(index(win, ("disc", disc)))
    win.disc_tree.setCurrentIndex(index(win, ("disc_dir", disc, house)))
    assert shown_titles(win) == ["Song 2", "Song 3"]


def test_a_deleted_folder_that_was_open_falls_back_to_its_parent(mp3, monkeypatch):
    win, _lib, disc, house, deep = mp3
    win.disc_tree.setCurrentIndex(index(win, ("disc_dir", disc, deep)))
    ask(monkeypatch, YES)
    win.disc_view.delete_dir(disc, deep)
    assert win.disc_view.current_dir_id() == house
    assert shown_titles(win) == ["Song 2", "Song 3"]  # its track went with it
    assert (
        find_selection(win.disc_tree.model(), ("disc_dir", disc, deep)) == QModelIndex()
    )


# -- adding and moving tracks ---------------------------------------------------


def test_adding_tracks_puts_them_in_the_folder_being_shown(mp3, monkeypatch):
    monkeypatch.setattr(
        "dubcrate.ui.disc_view.ask_overlap", lambda *a, **k: OverlapChoice.ADD_ALL
    )  # (some of these tracks are on the disc already: add anyway)
    win, lib, disc, house, _ = mp3
    win.disc_tree.setCurrentIndex(index(win, ("disc_dir", disc, house)))
    win.disc_view.add_tracks(ids(lib)[5:6])
    assert [e.title for e in lib.disc_entries(disc) if e.dir_id == house] == ["Song 2", "Song 3", "Song 5"]  # fmt: skip
    win.disc_view.add_tracks(ids(lib)[5:6], position=0)  # at a row of the shown list
    assert shown_titles(win)[0] == "Song 5"


def test_dropping_library_tracks_on_a_folder_or_on_the_disc(mp3, monkeypatch):
    monkeypatch.setattr(
        "dubcrate.ui.disc_view.ask_overlap", lambda *a, **k: OverlapChoice.ADD_ALL
    )  # (some of these tracks are on the disc already: add anyway)
    win, lib, disc, house, deep = mp3
    model = win.disc_tree.model()
    t = ids(lib)
    assert model.dropMimeData(mime(TRACK_MIME, t[5:6]), Qt.DropAction.CopyAction, -1, -1, index(win, ("disc_dir", disc, deep)))  # fmt: skip
    assert [e.title for e in lib.disc_entries(disc) if e.dir_id == deep] == [
        "Song 4",
        "Song 5",
    ]
    # the open disc is showing a folder; a drop on the disc's own row means its root
    win.disc_tree.setCurrentIndex(index(win, ("disc_dir", disc, house)))
    assert model.dropMimeData(mime(TRACK_MIME, t[:1]), Qt.DropAction.CopyAction, -1, -1, index(win, ("disc", disc)))  # fmt: skip
    assert [e.title for e in lib.disc_entries(disc) if e.dir_id is None] == ["Song 0", "Song 1", "Song 0"]  # fmt: skip
    # and a drop on a folder of the open disc goes there, not to the folder shown
    assert model.dropMimeData(mime(TRACK_MIME, t[1:2]), Qt.DropAction.CopyAction, -1, -1, index(win, ("disc_dir", disc, deep)))  # fmt: skip
    assert lib.dir_file_counts(disc)[deep] == 3


def test_dragging_rows_of_the_open_disc_onto_a_folder_moves_them(mp3):
    win, lib, disc, _house, deep = mp3
    win.disc_tree.setCurrentIndex(
        index(win, ("disc", disc))
    )  # the root: Song 0, Song 1
    model = win.disc_tree.model()
    assert model.dropMimeData(mime(DISC_ROWS_MIME, [1]), Qt.DropAction.MoveAction, -1, -1, index(win, ("disc_dir", disc, deep)))  # fmt: skip
    assert [e.title for e in lib.disc_entries(disc) if e.dir_id == deep] == [
        "Song 1",
        "Song 4",
    ]
    assert shown_titles(win) == ["Song 0"]
    # onto the disc row: back to the root (the rows are the shown folder's)
    win.disc_tree.setCurrentIndex(index(win, ("disc_dir", disc, deep)))
    assert model.dropMimeData(mime(DISC_ROWS_MIME, [0]), Qt.DropAction.MoveAction, -1, -1, index(win, ("disc", disc)))  # fmt: skip
    assert lib.disc_entries(disc)[1].dir_id is None


def test_rows_dragged_onto_a_folder_of_another_disc_are_ignored(mp3):
    win, lib, disc, _house, _deep = mp3
    other = lib.create_disc("Other", kind="mp3")
    folder = lib.create_disc_dir(other, "Elsewhere")
    win.refresh_discs()
    win.disc_tree.setCurrentIndex(index(win, ("disc", disc)))
    model = win.disc_tree.model()
    model.dropMimeData(mime(DISC_ROWS_MIME, [0]), Qt.DropAction.MoveAction, -1, -1, index(win, ("disc_dir", other, folder)))  # fmt: skip
    assert lib.dir_file_counts(other) == {} and lib.disc(disc).track_count == 5


def test_reordering_in_a_folder_keeps_to_the_folder(mp3):
    win, lib, disc, house, _ = mp3
    win.disc_tree.setCurrentIndex(index(win, ("disc_dir", disc, house)))
    win.disc_view._move_rows([1], 0)
    assert shown_titles(win) == ["Song 3", "Song 2"]
    assert [e.title for e in lib.disc_entries(disc) if e.dir_id is None] == [
        "Song 0",
        "Song 1",
    ]


def test_the_track_list_offers_to_move_tracks_to_a_folder(mp3):
    win, lib, disc, _house, deep = mp3
    win.disc_tree.setCurrentIndex(index(win, ("disc", disc)))
    win.disc_view.table.selectRow(0)
    menu = win.disc_view.table_menu()
    texts = [a.text() for a in menu.actions()]
    assert texts == ["Move to Folder", "Remove from Disc"]
    move = menu.actions()[0].menu()
    assert [a.text() for a in move.actions()] == [
        "House",
        "House / Deep",
    ]  # no "root": it is the root
    next(a for a in move.actions() if a.text() == "House / Deep").trigger()
    assert lib.disc_entries(disc)[0].dir_id == deep
    # an audio disc has no folders to offer
    audio = lib.create_disc("Plain")
    win.disc_view.set_disc(audio)
    assert [a.text() for a in win.disc_view.table_menu().actions()] == [
        "Remove from Disc"
    ]


def test_from_inside_a_folder_the_root_is_offered_too(mp3):
    win, lib, disc, house, _ = mp3
    win.disc_tree.setCurrentIndex(index(win, ("disc_dir", disc, house)))
    win.disc_view.table.selectRow(0)
    move = win.disc_view.table_menu().actions()[0].menu()
    assert next(a.text() for a in move.actions()) == "(The disc's root)"
    assert "House" not in [a.text() for a in move.actions()]  # not into where it is
    move.actions()[0].trigger()
    assert [e.title for e in lib.disc_entries(disc) if e.dir_id is None][-1] == "Song 2"


# -- making, renaming, deleting folders -------------------------------------------


def test_the_menus_make_folders_with_free_names_and_open_them_for_naming(mp3):
    win, lib, disc, _house, _ = mp3
    disc_menu = win.disc_tree_menu(index(win, ("disc", disc)))
    assert "New Folder on Disc" in [a.text() for a in disc_menu.actions()]
    next(a for a in disc_menu.actions() if a.text() == "New Folder on Disc").trigger()
    names = [d.name for d in lib.disc_dirs(disc) if d.parent_id is None]
    assert names == ["House", "New Folder"]
    new = lib.disc_dirs(disc)[-1]
    assert win.disc_tree.indexWidget(
        index(win, ("disc_dir", disc, new.id))
    )  # the editor is open
    win.new_disc_dir(disc)
    assert [d.name for d in lib.disc_dirs(disc) if d.parent_id is None][
        -1
    ] == "New Folder 2"


def test_the_plus_on_a_folder_makes_a_folder_beside_it_at_the_same_level(mp3):
    win, lib, disc, house, deep = mp3
    win._on_plus_clicked(("disc_dir", disc, deep))  # Deep is inside House
    beside = [d for d in lib.disc_dirs(disc) if d.parent_id == house]
    assert [d.name for d in beside] == ["Deep", "New Folder"]
    win._on_plus_clicked(("disc_dir", disc, house))  # House is at the top of the disc
    assert [d.name for d in lib.disc_dirs(disc) if d.parent_id is None] == [
        "House",
        "New Folder",
    ]
    win._on_plus_clicked(
        ("disc", disc)
    )  # on the disc itself it still makes a disc beside it
    assert len(lib.discs()) == 2


def test_the_folder_button_makes_a_folder_inside_the_disc_or_the_folder(mp3):
    win, lib, disc, _house, deep = mp3
    win._on_new_folder_clicked(("disc", disc))
    assert [d.name for d in lib.disc_dirs(disc) if d.parent_id is None] == [
        "House",
        "New Folder",
    ]
    win._on_new_folder_clicked(("disc_dir", disc, deep))
    assert [d.name for d in lib.disc_dirs(disc) if d.parent_id == deep] == [
        "New Folder"
    ]


def test_the_folder_button_shows_only_where_folders_can_go(mp3):
    win, lib, disc, house, _ = mp3
    audio = lib.create_disc("Plain")
    win.refresh_discs()
    win.show()
    tree = win.disc_tree
    for selection, expected in (
        (("disc", disc), True),
        (("disc_dir", disc, house), True),
        (("disc", audio), False),
    ):
        picked = index(win, selection)
        tree.setCurrentIndex(picked)
        assert tree.plus_visible(picked) and tree.folder_visible(picked) is expected, (
            selection
        )
    picked = index(win, ("disc", disc))
    tree.setCurrentIndex(picked)
    assert (
        tree.folder_rect(picked).right() < tree.plus_rect(picked).left()
    )  # left of the +
    assert tree.visualRect(picked.siblingAtColumn(2)).contains(tree.folder_rect(picked))
    other = index(win, ("disc_dir", disc, house))  # not picked: neither button shows
    assert not tree.folder_visible(other)


def test_clicking_the_folder_button_in_the_tree_makes_the_folder(mp3):
    from PySide6.QtTest import QTest

    win, lib, disc, _house, _ = mp3
    win.show()
    picked = index(win, ("disc", disc))
    win.disc_tree.setCurrentIndex(picked)
    QTest.mouseClick(
        win.disc_tree.viewport(),
        Qt.MouseButton.LeftButton,
        pos=win.disc_tree.folder_rect(picked).center(),
    )
    assert [d.name for d in lib.disc_dirs(disc) if d.parent_id is None] == [
        "House",
        "New Folder",
    ]
    assert (
        len(lib.discs()) == 1
    )  # and not a new disc: it was the folder button, not the +


# -- the path above the tracks ----------------------------------------------------


def test_the_path_above_the_tracks_links_every_step_but_the_last(mp3):
    win, _lib, disc, house, deep = mp3
    win.disc_tree.setCurrentIndex(index(win, ("disc_dir", disc, deep)))
    title = win.disc_view.title
    assert title.textFormat() == Qt.TextFormat.RichText
    assert f'href="dir:{house}"' in title.text() and 'href="root"' in title.text()
    assert f'href="dir:{deep}"' not in title.text()  # where you are is not a link
    win.disc_tree.setCurrentIndex(index(win, ("disc", disc)))
    assert (
        title.textFormat() == Qt.TextFormat.PlainText and title.text() == "Set"
    )  # no path, no links


def test_clicking_a_step_goes_there_and_picks_its_row_in_the_discs_list(mp3):
    win, _lib, disc, house, deep = mp3
    win.disc_tree.setCurrentIndex(index(win, ("disc_dir", disc, deep)))
    assert shown_titles(win) == ["Song 4"]
    win.disc_view.title.linkActivated.emit(f"dir:{house}")
    assert win.disc_view.current_dir_id() == house and shown_titles(win) == [
        "Song 2",
        "Song 3",
    ]
    assert win.disc_tree.currentIndex().siblingAtColumn(0).data(SELECTION_ROLE) == ("disc_dir", disc, house)  # fmt: skip
    win.disc_view.title.linkActivated.emit("root")
    assert win.disc_view.current_dir_id() is None and shown_titles(win) == [
        "Song 0",
        "Song 1",
    ]
    assert win.disc_tree.currentIndex().siblingAtColumn(0).data(SELECTION_ROLE) == (
        "disc",
        disc,
    )


def test_a_step_still_works_when_the_library_is_showing(mp3):
    win, _lib, disc, house, deep = mp3
    win.disc_tree.setCurrentIndex(index(win, ("disc_dir", disc, deep)))
    win.disc_view.title.linkActivated.emit(f"dir:{house}")
    win.center.setCurrentIndex(
        0
    )  # back to the library, the same row still picked in the tree
    win.disc_view.title.linkActivated.emit(f"dir:{house}")
    assert win.center.currentIndex() == 1


def test_names_in_the_path_cannot_inject_markup(mp3):
    win, lib, disc, house, deep = mp3
    lib.rename_disc_dir(disc, house, "<b>x</b> & y")
    win.refresh_discs()
    win.disc_tree.setCurrentIndex(index(win, ("disc_dir", disc, deep)))
    assert "&lt;b&gt;x&lt;/b&gt; &amp; y" in win.disc_view.title.text()
    assert win.disc_view.crumb_names() == ["Set", "<b>x</b> & y", "Deep"]


def test_a_folders_menu_and_what_it_does(mp3, monkeypatch):
    win, lib, disc, _house, deep = mp3
    menu = win.disc_tree_menu(index(win, ("disc_dir", disc, deep)))
    assert [a.text() for a in menu.actions() if a.text()] == ["Open", "New Folder Inside", "Rename", "Delete Folder from Disc…"]  # fmt: skip
    menu.actions()[0].trigger()
    assert win.disc_view.current_dir_id() == deep
    ask(monkeypatch, YES)
    next(a for a in menu.actions() if a.text().startswith("Delete")).trigger()
    assert [d.name for d in lib.disc_dirs(disc)] == ["House"]


def test_renaming_a_folder_in_the_tree_and_a_clash_is_told(mp3, wait_for):
    win, lib, disc, house, _deep = mp3
    model = win.disc_tree.model()
    model.setData(index(win, ("disc_dir", disc, house)), "Techno")
    assert wait_for(lambda: lib.disc_dirs(disc)[0].name == "Techno")
    lib.create_disc_dir(disc, "Other")
    win.refresh_discs()
    model = win.disc_tree.model()
    model.setData(index(win, ("disc_dir", disc, lib.disc_dirs(disc)[-1].id)), "techno")
    assert wait_for(lambda: win.shown)
    assert (
        "already a folder" in win.shown[-1][2]
        and lib.disc_dirs(disc)[-1].name == "Other"
    )


def test_deleting_a_folder_asks_and_no_changes_nothing(mp3, monkeypatch):
    win, lib, disc, house, _ = mp3
    asked = ask(monkeypatch, NO)
    win.disc_view.delete_dir(disc, house)
    assert (
        len(lib.disc_dirs(disc)) == 2
        and "also takes 0 tracks off the disc" in asked[0][1]
        or "also takes" in asked[0][1]
    )


def test_the_players_limits_are_told_not_crashed_into(mp3):
    win, lib, disc, _house, deep = mp3
    parent = deep  # House / Deep are 2 levels down; the root is the first of 8
    for _ in range(5):
        parent = win.disc_view.new_dir(disc, parent)
        assert parent is not None
    assert win.shown == []
    assert (
        win.disc_view.new_dir(disc, parent) is None
    )  # the 9th level would be too deep
    assert "levels deep" in win.shown[-1][2]
    for _ in range(100):  # and 99 folders at most, counting the root
        if win.disc_view.new_dir(disc, None) is None:
            break
    assert len(lib.disc_dirs(disc)) == 98 and "at most 99 folders" in win.shown[-1][2]


# -- converting ---------------------------------------------------------------------


def test_making_a_disc_with_folders_an_audio_cd_asks_first(mp3, monkeypatch):
    win, lib, disc, house, _ = mp3
    asked = ask(monkeypatch, NO)
    win.disc_view.set_disc_kind(disc, "audio")
    assert lib.disc(disc).kind == "mp3" and "no folders" in asked[0][1]
    ask(monkeypatch, YES)
    win.disc_view.set_disc_kind(disc, "audio")
    assert lib.disc(disc).kind == "audio" and lib.disc_dirs(disc) == []
    assert win.disc_view.current_dir_id() is None
    assert (
        find_selection(win.disc_tree.model(), ("disc_dir", disc, house))
        == QModelIndex()
    )


def test_a_disc_converted_with_too_many_files_for_its_root_says_so(window, monkeypatch):
    from dubcrate.build import disc_problems

    win, lib = window
    audio = lib.create_disc("Many")
    lib._write_disc_tracks(
        audio, ids(lib) * 170
    )  # 1,020 tracks: fine to plan, not for a folder
    ask(monkeypatch, YES)
    win.disc_view.set_disc_kind(audio, "mp3")
    win.disc_view.set_disc(audio)
    assert "at most 999 files" in win.disc_view.summary.label.text()
    assert any("1020 files in the disc's root" in p for p in disc_problems(lib, audio))
    # sorting some into a folder clears it
    folder = lib.create_disc_dir(audio, "Half")
    lib.move_to_dir(audio, range(300), folder)
    win.disc_view.set_disc(audio)
    assert "at most 999 files" not in win.disc_view.summary.label.text()
