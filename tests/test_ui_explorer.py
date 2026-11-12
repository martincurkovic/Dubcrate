"""The Explorer tab: the folder tree, the tab strip, and the window with both."""

import os
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QItemSelectionModel

from dubcrate import settings
from dubcrate.library import Library, folder
from dubcrate.render import SourceInfo
from dubcrate.ui import explorer, main_window
from dubcrate.ui.explorer import ExplorerTree, explorer_roots, subfolders
from dubcrate.ui.main_window import MainWindow
from dubcrate.ui.source_tabs import SourceTabBar, explorer_glyph, library_glyph

ROWS = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows


def make_tree(root: Path) -> Path:
    """music/ with House/ (Disco/), Dub/ and a hidden folder, plus a file."""
    for sub in ("House/Disco", "Dub", ".hidden", "Zed"):
        (root / sub).mkdir(parents=True)
    (root / "readme.txt").write_text("not a folder")
    return root


# -- listing folders ---------------------------------------------------------------------


def test_subfolders_are_the_folders_inside_by_name_without_hidden_ones(tmp_path):
    make_tree(tmp_path)
    assert [p.name for p in subfolders(tmp_path)] == ["Dub", "House", "Zed"]
    assert subfolders(tmp_path / "House") == [tmp_path / "House" / "Disco"]
    assert subfolders(tmp_path / "Dub") == []


def test_a_folder_that_cannot_be_listed_has_no_subfolders(tmp_path):
    assert subfolders(tmp_path / "missing") == []
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("this account can read anything")
        assert subfolders(locked) == []
    finally:
        locked.chmod(0o755)


def test_the_roots_are_music_home_and_the_drives_and_not_the_system_disk(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "me")
    (tmp_path / "me" / "Music").mkdir(parents=True)
    names = [name for name, _path, _drive in explorer_roots()]
    assert names[:2] == ["Music", "me"]
    for _name, path, drive in explorer_roots():
        assert path.resolve() != Path("/")  # "Macintosh HD" is never offered
        assert drive == (path.parent == Path("/Volumes"))


def test_without_a_music_folder_the_roots_start_at_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "me")
    (tmp_path / "me").mkdir()
    assert explorer_roots()[0][:2] == ("me", tmp_path / "me")


def test_drives_are_only_looked_for_on_a_mac(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert explorer.mounted_volumes() == []  # (Linux and Windows are not handled yet)


@pytest.mark.skipif(sys.platform != "darwin", reason="/Volumes is a Mac's")
def test_the_boot_disk_link_in_volumes_is_left_out():
    for volume in explorer.mounted_volumes():
        assert volume.resolve() != Path("/") and not volume.name.startswith(".")


# -- the tree ------------------------------------------------------------------------------


@pytest.fixture
def tree(qapp, tmp_path):
    make_tree(tmp_path / "music")
    widget = ExplorerTree(roots=lambda: [("Music", tmp_path / "music", False)])
    widget.resize(300, 400)
    widget.show()
    yield widget, tmp_path / "music"
    widget.close()


def child_names(item):
    return [item.child(r).text() for r in range(item.rowCount())]


def test_a_folder_is_read_only_when_it_is_opened(tree):
    widget, _music = tree
    root = widget._model.item(0)
    assert (
        root.text() == "Music" and root.rowCount() == 1
    )  # a placeholder: it has an arrow
    assert not root.data(explorer.LOADED_ROLE)
    widget.expand(root.index())
    assert child_names(root) == ["Dub", "House", "Zed"]
    house = root.child(1)
    assert not house.data(explorer.LOADED_ROLE)  # its own folders are not read yet
    widget.expand(house.index())
    assert child_names(house) == ["Disco"]


def test_a_folder_with_nothing_inside_loses_its_arrow_once_opened(tree):
    widget, _music = tree
    root = widget._model.item(0)
    widget.expand(root.index())
    dub = root.child(0)
    assert dub.rowCount() == 1
    widget.expand(dub.index())
    assert dub.rowCount() == 0


def test_picking_a_folder_says_which(tree):
    widget, music = tree
    picked = []
    widget.folder_selected.connect(picked.append)
    root = widget._model.item(0)
    widget.expand(root.index())
    widget.setCurrentIndex(root.child(1).index())
    assert picked == [str(music / "House")]
    assert widget.current_path() == str(music / "House")


def test_select_path_opens_the_tree_down_to_a_folder_and_picks_it(tree):
    widget, music = tree
    picked = []
    widget.folder_selected.connect(picked.append)
    assert widget.select_path(str(music / "House" / "Disco")) is True
    assert picked == [str(music / "House" / "Disco")]
    assert widget.current_path() == str(music / "House" / "Disco")
    assert widget.isExpanded(widget._model.item(0).index())


def test_select_path_for_a_root_itself_and_for_a_place_outside(tree, tmp_path):
    widget, music = tree
    assert widget.select_path(str(music)) is True
    assert widget.current_path() == str(music)
    assert widget.select_path(str(tmp_path / "elsewhere")) is False
    assert widget.select_path(str(music / "Nope")) is False


def test_the_deepest_root_wins_when_roots_overlap(qapp, tmp_path):
    make_tree(tmp_path / "home" / "Music")
    widget = ExplorerTree(
        roots=lambda: [
            ("home", tmp_path / "home", False),
            ("Music", tmp_path / "home" / "Music", False),
        ]
    )
    widget.select_path(str(tmp_path / "home" / "Music" / "Dub"))
    assert widget.currentIndex().parent().data() == "Music"  # not buried under "home"


def test_the_roots_can_be_read_again_for_drives_that_came_and_went(tree, tmp_path):
    widget, _music = tree
    widget._roots = lambda: [
        ("Music", tmp_path / "music", False),
        ("USB", tmp_path, True),
    ]
    widget.reload_roots()
    assert [widget._model.item(r).text() for r in range(2)] == ["Music", "USB"]


# -- the tab strip -------------------------------------------------------------------------


def test_the_first_tab_is_picked_and_the_others_can_be(qapp):
    bar = SourceTabBar()
    bar.add_tab("library", "Library", library_glyph)
    bar.add_tab("explorer", "Explorer", explorer_glyph)
    assert bar.keys() == ["library", "explorer"] and bar.current() == "library"
    seen = []
    bar.current_changed.connect(lambda key: seen.append((key, bar.current())))
    bar.set_current("explorer")
    assert seen == [("explorer", "explorer")]  # `current()` is already right when told
    bar.set_current("explorer")
    bar.set_current("nope")
    assert seen == [("explorer", "explorer")]  # no change, no signal
    bar.set_current("library")
    assert bar.current() == "library" and seen[-1] == ("library", "library")


def test_the_tabs_paint_in_every_look(qapp):
    from dubcrate.ui import theme

    bar = SourceTabBar()
    bar.add_tab("library", "Library", library_glyph)
    bar.add_tab("explorer", "Explorer", explorer_glyph)
    try:
        for look in ("dark", "light", "system6"):
            theme.apply_to_app(qapp, look)
            bar.resize(bar.sizeHint().width(), 300)
            bar.show()
            image = bar.grab().toImage()
            assert image.width() == bar.width() and not image.isNull()
            bar.hide()
    finally:
        theme.apply_to_app(qapp, "dark")


# -- the window ----------------------------------------------------------------------------


@pytest.fixture
def probed(monkeypatch):
    """A stand-in for ffprobe: each file's title is its name (no ffmpeg needed)."""

    def fake(path: Path):
        return SourceInfo(
            path=path,
            duration=61.0,
            sample_rate=44100,
            bits=16,
            title=path.stem,
            artist="Artist",
            album="",
        )

    monkeypatch.setattr(folder, "probe", fake)


@pytest.fixture
def music(tmp_path):
    root = tmp_path / "music"
    for name in (
        "dub/a.mp3",
        "dub/b.mp3",
        "dub/c.flac",
        "house/x.mp3",
        "empty/readme.txt",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 10)
    return root


@pytest.fixture
def window(qapp, tmp_path, music, probed):
    lib = Library.open(":memory:")
    win = MainWindow(lib)
    win.explorer_tree._roots = lambda: [("Music", music, False)]
    win.explorer_tree.reload_roots()
    win.show()
    yield win, lib, music
    win.close()
    win.deleteLater()
    lib.close()


def titles(win):
    return [t.title for t in win.track_model.tracks()]


def open_folder(win, wait_for, path, expect):
    win.explorer_tree.select_path(str(path))
    assert wait_for(lambda: titles(win) == expect), titles(win)


def test_the_library_is_the_first_tab_and_the_explorer_the_second(window):
    win, _lib, _music = window
    assert win.source_tabs.keys() == ["library", "explorer"]
    assert win.source_tabs.current() == "library"
    assert win.source_pages.currentIndex() == 0


def test_the_explorer_tab_starts_empty_and_asks_for_a_folder(window):
    win, _lib, _music = window
    win.source_tabs.set_current("explorer")
    assert win.source_pages.currentIndex() == 1
    assert win.tracks_title.text() == "Explorer"
    assert win.track_model.rowCount() == 0
    assert win.tracks_count.text() == "Pick a folder on the left"


def test_picking_a_folder_shows_its_audio_files_in_the_track_list(window, wait_for):
    win, _lib, music = window
    win.source_tabs.set_current("explorer")
    open_folder(win, wait_for, music / "dub", ["a", "b", "c"])
    assert win.tracks_title.text() == "dub"
    assert win.tracks_count.text().startswith("3 tracks")
    open_folder(win, wait_for, music / "house", ["x"])
    open_folder(win, wait_for, music / "empty", [])  # no audio: nothing, not an error


def test_the_explorers_tracks_stay_out_of_the_library(window, wait_for):
    win, lib, music = window
    win.source_tabs.set_current("explorer")
    open_folder(win, wait_for, music / "dub", ["a", "b", "c"])
    assert lib.count_tracks() == 0 and lib.sources() == []
    win.source_tabs.set_current("library")
    assert win.tracks_title.text() == "All Tracks" and win.track_model.rowCount() == 0


def test_going_back_to_the_library_shows_what_was_picked_there(window, wait_for):
    win, lib, music = window
    lib.replace_source("folder", "/real", "Real", [])
    from dubcrate.library import TrackRecord

    lib.replace_source(
        "folder", "/real", "Real",
        [TrackRecord(ext_id="/real/r.mp3", path="/real/r.mp3", title="Real Track", duration=5)],
    )  # fmt: skip
    win.refresh_sources()
    [source] = lib.sources()
    from dubcrate.ui.models import find_selection

    win.tree.setCurrentIndex(find_selection(win.tree.model(), ("source", source.id)))
    assert titles(win) == ["Real Track"]
    win.source_tabs.set_current("explorer")
    open_folder(win, wait_for, music / "dub", ["a", "b", "c"])
    win.source_tabs.set_current("library")
    assert titles(win) == ["Real Track"] and win.tracks_title.text() == "Real"
    assert win._selection == ("source", source.id)
    win.source_tabs.set_current("explorer")
    assert titles(win) == ["a", "b", "c"] and win.tracks_title.text() == "dub"


def test_a_change_to_the_library_while_in_the_explorer_does_not_take_over_the_list(
    window, wait_for
):
    win, _lib, music = window
    win.source_tabs.set_current("explorer")
    open_folder(win, wait_for, music / "dub", ["a", "b", "c"])
    win.refresh_sources()  # (what an import finishing does)
    assert win._selection == ("explorer", str(music / "dub"))
    assert titles(win) == ["a", "b", "c"]


def test_a_folder_visited_before_shows_at_once_and_then_catches_up(window, wait_for):
    win, _lib, music = window
    win.source_tabs.set_current("explorer")
    open_folder(win, wait_for, music / "dub", ["a", "b", "c"])
    open_folder(win, wait_for, music / "house", ["x"])
    (music / "dub" / "d.mp3").write_bytes(b"x" * 10)  # a file arrives meanwhile
    (music / "dub" / "a.mp3").unlink()
    win.explorer_tree.select_path(str(music / "dub"))
    assert titles(win) == ["a", "b", "c"]  # at once: what was read last time
    assert wait_for(lambda: titles(win) == ["b", "c", "d"])  # then what is there now


def test_the_tags_of_an_unchanged_file_are_not_read_again(
    window, wait_for, monkeypatch
):
    win, _lib, music = window
    win.source_tabs.set_current("explorer")
    open_folder(win, wait_for, music / "dub", ["a", "b", "c"])
    open_folder(win, wait_for, music / "house", ["x"])
    calls = []
    real = folder.probe

    def counting(path):
        calls.append(path.name)
        return real(path)

    monkeypatch.setattr(folder, "probe", counting)
    (music / "dub" / "d.mp3").write_bytes(b"x" * 10)
    win.explorer_tree.select_path(str(music / "dub"))
    assert wait_for(lambda: titles(win) == ["a", "b", "c", "d"])
    assert calls == ["d.mp3"]


def test_a_folder_that_cannot_be_read_says_why_and_shows_nothing(
    window, wait_for, monkeypatch
):
    win, _lib, music = window

    def refuse(path, known=None):
        raise PermissionError("no")

    monkeypatch.setattr(main_window, "scan_directory", refuse)
    win.source_tabs.set_current("explorer")
    win.explorer_tree.select_path(str(music / "dub"))
    assert wait_for(lambda: "isn't allowed" in win.statusBar().currentMessage())
    assert win.track_model.rowCount() == 0


def test_a_folder_that_has_gone_says_so(window, wait_for, music):
    win, _lib, _music = window
    win.source_tabs.set_current("explorer")
    win.explorer_tree.select_path(str(music / "dub"))
    assert wait_for(lambda: titles(win) == ["a", "b", "c"])
    import shutil

    shutil.rmtree(music / "house")
    win._show_explorer_folder(str(music / "house"))
    assert wait_for(lambda: "no longer there" in win.statusBar().currentMessage())


def test_files_that_cannot_be_read_are_counted_in_the_status_bar(
    window, wait_for, monkeypatch
):
    win, _lib, music = window
    real = folder.probe

    def flaky(path):
        if path.name == "b.mp3":
            raise folder.RenderError("bad")
        return real(path)

    monkeypatch.setattr(folder, "probe", flaky)
    win.source_tabs.set_current("explorer")
    open_folder(win, wait_for, music / "dub", ["a", "c"])
    assert wait_for(lambda: "Couldn't read 1 file" in win.statusBar().currentMessage())


def test_explorer_tracks_go_on_a_disc_and_survive_the_next_start(
    window, wait_for, monkeypatch
):
    win, lib, music = window
    monkeypatch.setattr(
        "dubcrate.ui.disc_view.probe_lengths", lambda todo, report=None: ({}, [])
    )  # (the files are stand-ins: nothing to measure)
    win.source_tabs.set_current("explorer")
    open_folder(win, wait_for, music / "dub", ["a", "b", "c"])
    selection = win.track_table.selectionModel()
    selection.clearSelection()
    selection.select(win.track_table.model().index(1, 0), ROWS)
    win.add_selected_to_disc()
    assert wait_for(lambda: lib.discs() and lib.discs()[0].track_count == 1)
    [disc] = lib.discs()
    assert [e.track.title for e in lib.disc_entries(disc.id)] == ["b"]
    assert lib.prune_explorer() == 2  # the next start: a and c were only a cache
    assert [e.track.title for e in lib.disc_entries(disc.id)] == ["b"]


def test_removing_a_source_in_the_library_does_not_disturb_the_explorers_list(
    window, wait_for
):
    win, _lib, music = window
    win.source_tabs.set_current("explorer")
    open_folder(win, wait_for, music / "dub", ["a", "b", "c"])
    win._library_selection = ("source", 999)
    win.refresh_sources()
    assert win._library_selection == ("all",) and win._selection[0] == "explorer"


def test_a_search_filters_the_explorers_folder_too(window, wait_for):
    win, _lib, music = window
    win.source_tabs.set_current("explorer")
    open_folder(win, wait_for, music / "dub", ["a", "b", "c"])
    win.search.setText("b")
    win.refresh_tracks()
    assert titles(win) == ["b"]


def test_the_tab_and_folder_are_remembered_for_next_time(
    qapp, music, probed, wait_for, monkeypatch
):
    monkeypatch.setattr(explorer, "explorer_roots", lambda: [("Music", music, False)])
    settings.set_left_tab("explorer")
    settings.set_explorer_path(str(music / "dub"))
    lib = Library.open(":memory:")
    win = MainWindow(lib, persist=True)
    try:
        assert win.source_tabs.current() == "explorer"
        assert win.source_pages.currentIndex() == 1
        assert win._explorer_path == str(music / "dub")
        assert win.tracks_title.text() == "dub"
    finally:
        win.close()
        win.deleteLater()
        lib.close()


def test_a_remembered_folder_that_has_gone_is_forgotten_quietly(
    qapp, tmp_path, probed, monkeypatch
):
    monkeypatch.setattr(explorer, "explorer_roots", lambda: [("T", tmp_path, False)])
    settings.set_left_tab("explorer")
    settings.set_explorer_path(str(tmp_path / "gone"))
    lib = Library.open(":memory:")
    win = MainWindow(lib, persist=True)
    try:
        assert win.source_tabs.current() == "explorer"
        assert (
            win.tracks_title.text() == "Explorer"
        )  # nothing picked: it asks for a folder
        assert win._explorer_path is None
    finally:
        win.close()
        win.deleteLater()
        lib.close()


def test_choosing_a_tab_and_a_folder_saves_them(window, wait_for):
    win, _lib, music = window
    win._persist = (
        True  # (the fixture builds an ordinary window that remembers nothing)
    )
    win.source_tabs.set_current("explorer")
    assert settings.get_left_tab() == "explorer"
    open_folder(win, wait_for, music / "dub", ["a", "b", "c"])
    assert settings.get_explorer_path() == str(music / "dub")
    win.source_tabs.set_current("library")
    assert settings.get_left_tab() == "library"


def test_bad_settings_for_the_tab_fall_back_to_the_library(qapp):
    from PySide6.QtCore import QSettings

    QSettings().setValue("window/left_tab", "mixxx")
    assert settings.get_left_tab() == "library"
    assert settings.get_explorer_path() is None
    settings.set_explorer_path("/x")
    assert settings.get_explorer_path() == "/x"
    settings.set_explorer_path(None)
    assert settings.get_explorer_path() is None
