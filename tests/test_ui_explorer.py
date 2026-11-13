"""The Explorer tab: the folder tree, the tab strip, and the window with both."""

import os
import sys
from pathlib import Path, PureWindowsPath

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt

from dubcrate import settings
from dubcrate.library import Library, folder
from dubcrate.render import SourceInfo
from dubcrate.ui import explorer, main_window
from dubcrate.ui.explorer import ExplorerTree, explorer_roots, subfolders
from dubcrate.ui.main_window import MainWindow
from dubcrate.ui.source_tabs import (
    SourceTabBar,
    explorer_glyph,
    library_glyph,
    pinned_glyph,
)

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
        assert drive == (path in explorer.mounted_volumes())


def test_without_a_music_folder_the_roots_start_at_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "me")
    (tmp_path / "me").mkdir()
    assert explorer_roots()[0][:2] == ("me", tmp_path / "me")


def test_each_system_looks_for_its_own_kind_of_drive(monkeypatch):
    for platform, helper in (("darwin", "_mac_volumes"), ("linux", "_linux_volumes")):
        monkeypatch.setattr(sys, "platform", platform)
        monkeypatch.setattr(explorer, helper, lambda: [Path("/the/drive")])
        assert explorer.mounted_volumes() == [Path("/the/drive")]
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(explorer, "_windows_drives", lambda: [("C", 3), ("D", 2)])
    monkeypatch.setenv("SystemDrive", "C:")
    assert explorer.mounted_volumes() == [Path("D:/")]


def test_linux_drives_are_mount_points_under_the_media_folders(tmp_path):
    user_media, mnt = tmp_path / "media" / "me", tmp_path / "mnt"
    for path in (user_media / "USB STICK", user_media / "plain folder", user_media / ".hidden", mnt / "backup", mnt / "empty"):  # fmt: skip
        path.mkdir(parents=True)
    (mnt / "a file").write_text("x")
    mounts = {user_media / "USB STICK", user_media / ".hidden", mnt / "backup"}
    found = explorer._linux_volumes([user_media, tmp_path / "gone", mnt], lambda p: p in mounts)  # fmt: skip
    assert found == [user_media / "USB STICK", mnt / "backup"]
    # the same drive reached by two folders is listed once
    assert explorer._linux_volumes([user_media, user_media], lambda p: p in mounts) == [user_media / "USB STICK"]  # fmt: skip


def test_windows_drives_leave_out_the_system_drive_and_cd_drives():
    drives = [("C", 3), ("D", 3), ("E", 5), ("F", 2), ("G", 4), ("H", 1)]
    found = explorer._windows_volumes(drives, "C:")
    assert found == [Path("D:/"), Path("F:/"), Path("G:/")]
    assert explorer._windows_volumes([("c", 3), ("D", 3)], "c:") == [Path("D:/")]


def test_a_windows_drive_is_named_by_its_letter():
    assert explorer.volume_label(Path("/Volumes/Gig Stick")) == "Gig Stick"
    assert explorer.volume_label(PureWindowsPath("D:/")) == "D:"


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


def drive_tree(volumes):
    """A tree whose drives are the folders in `volumes`, watched as /Volumes would be."""

    def roots():
        found = sorted(
            p for p in volumes.parent.glob(volumes.name + "/*") if p.is_dir()
        )
        return [(p.name, p, True) for p in found]

    widget = ExplorerTree(roots=roots, watch=[volumes])
    widget.resize(300, 400)
    widget.show()
    return widget


def test_a_drive_plugged_in_or_taken_out_shows_up_without_reopening_the_tab(qapp, tmp_path, wait_for):  # fmt: skip
    volumes = tmp_path / "Volumes"
    (volumes / "Boot").mkdir(parents=True)
    widget = drive_tree(volumes)
    assert root_names(widget) == ["Boot"]
    (volumes / "Gig Stick").mkdir()
    assert wait_for(lambda: root_names(widget) == ["Boot", "Gig Stick"])
    (volumes / "Gig Stick").rmdir()
    assert wait_for(lambda: root_names(widget) == ["Boot"])
    widget.close()


def test_a_drive_change_keeps_what_is_picked_and_open(qapp, tmp_path, wait_for):
    volumes = tmp_path / "Volumes"
    (volumes / "Boot" / "Sets").mkdir(parents=True)
    widget = drive_tree(volumes)
    widget.select_path(str(volumes / "Boot" / "Sets"))
    picked = []
    widget.folder_selected.connect(picked.append)
    (volumes / "Gig Stick").mkdir()
    assert wait_for(lambda: root_names(widget) == ["Boot", "Gig Stick"])
    assert widget.current_path() == str(volumes / "Boot" / "Sets") and picked == []
    widget.close()


def test_taking_out_the_picked_drive_picks_nothing_else(qapp, tmp_path, wait_for):
    volumes = tmp_path / "Volumes"
    for name in ("Boot", "Gig Stick", "Zed"):
        (volumes / name).mkdir(parents=True)
    widget = drive_tree(volumes)
    widget.select_path(str(volumes / "Gig Stick"))
    picked = []
    widget.folder_selected.connect(picked.append)
    (volumes / "Gig Stick").rmdir()
    assert wait_for(lambda: root_names(widget) == ["Boot", "Zed"])
    assert picked == [] and widget.current_path() is None  # not "Zed", picked for you
    widget.close()


def test_a_watched_folder_that_appears_later_is_watched_from_then_on(qapp, tmp_path, wait_for):  # fmt: skip
    base = tmp_path / "run"
    base.mkdir()
    volumes = (
        base / "media"
    )  # not there yet, as /run/media/<user> is before the first drive
    widget = ExplorerTree(roots=list, watch=[base, volumes])
    assert widget._watcher.directories() == [str(base)]
    volumes.mkdir()
    assert wait_for(lambda: str(volumes) in widget._watcher.directories())
    widget.close()


def test_no_folders_to_watch_is_fine(qapp):
    widget = ExplorerTree(roots=list)
    assert widget._watcher.directories() == []
    widget.close()


def test_each_system_says_where_its_drives_appear(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert explorer.volume_parents() == [Path("/Volumes")]
    monkeypatch.setattr(sys, "platform", "win32")
    assert explorer.volume_parents() == []  # drive letters are not folders
    monkeypatch.setattr(sys, "platform", "linux")
    assert Path("/mnt") in explorer.volume_parents()
    assert Path("/run/media") in explorer.volume_parents()


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


def test_a_tab_picked_just_before_the_bar_goes_away_is_not_revealed_afterwards(
    qapp, capsys
):
    """Picking a tab schedules a scroll-into-view for a moment later; the bar may be gone by then,
    which must not raise (the app's exception hook would log it as an uncaught error)."""
    from PySide6.QtCore import QCoreApplication, QEvent

    bar = SourceTabBar()
    bar.add_tab("library", "Library", library_glyph)
    bar.add_tab("explorer", "Explorer", explorer_glyph)
    bar.set_current("explorer")  # schedules the reveal
    bar.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()  # the scheduled call comes due
    assert "already deleted" not in capsys.readouterr().err


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


# -- pinned folders ------------------------------------------------------------------------


def test_a_tab_can_be_removed_and_the_first_is_picked_instead(qapp):
    bar = SourceTabBar()
    bar.add_tab("library", "Library", library_glyph)
    bar.add_tab("explorer", "Explorer", explorer_glyph)
    bar.add_tab("pin:/x", "x", pinned_glyph)
    bar.set_current("pin:/x")
    bar.remove_tab("pin:/x")
    assert bar.keys() == ["library", "explorer"] and bar.current() == "library"
    bar.remove_tab("pin:/x")  # (already gone: nothing happens)


def test_the_pinned_tab_paints_in_every_look(qapp):
    from dubcrate.ui import theme

    bar = SourceTabBar()
    bar.add_tab("library", "Library", library_glyph)
    bar.add_tab("pin:/x", "x", pinned_glyph)
    try:
        for look in ("dark", "light", "system6"):
            theme.apply_to_app(qapp, look)
            bar.resize(bar.sizeHint().width(), 300)
            bar.show()
            assert not bar.grab().toImage().isNull()
            bar.hide()
    finally:
        theme.apply_to_app(qapp, "dark")


def test_the_pinned_list_is_read_back_and_bad_values_are_ignored(qapp):
    from PySide6.QtCore import QSettings

    assert settings.get_pinned_folders() == []
    settings.set_pinned_folders(["/a", "/b"])
    assert settings.get_pinned_folders() == ["/a", "/b"]
    QSettings().setValue("explorer/pinned", '["/a", 3, "", "/a", "/b"]')
    assert settings.get_pinned_folders() == ["/a", "/b"]
    QSettings().setValue("explorer/pinned", "not json")
    assert settings.get_pinned_folders() == []
    QSettings().setValue("explorer/pinned", '{"a": 1}')
    assert settings.get_pinned_folders() == []
    QSettings().setValue("window/left_tab", "pin:/a")
    assert settings.get_left_tab() == "pin:/a"


def test_pinning_a_folder_adds_a_tab_and_leaves_you_where_you_are(window, wait_for):
    win, _lib, music = window
    win.pin_folder(str(music / "dub"))
    key = "pin:" + str(music / "dub")
    assert win.source_tabs.keys() == ["library", "explorer", key]
    assert win.source_tabs.current() == "library"  # (it did not jump to the new tab)
    assert win.source_pages.currentIndex() == 0
    assert "Pinned" in win.statusBar().currentMessage()
    win.source_tabs.set_current(key)  # (opening it shows the folder)
    assert win.source_pages.currentIndex() == 2
    assert wait_for(lambda: titles(win) == ["a", "b", "c"]), titles(win)
    assert win.tracks_title.text() == "dub"


def test_pinning_from_the_explorer_stays_in_the_explorer(window, wait_for):
    win, _lib, music = window
    win.source_tabs.set_current("explorer")
    open_folder(win, wait_for, music / "dub", ["a", "b", "c"])
    win.explorer_tree._pin_menu(str(music / "dub")).actions()[0].trigger()
    assert win.source_tabs.current() == "explorer"
    assert titles(win) == ["a", "b", "c"] and win._selection == (
        "explorer",
        str(music / "dub"),
    )


def test_pinning_twice_does_not_add_a_second_tab(window):
    win, _lib, music = window
    win.pin_folder(str(music / "dub"))
    win.pin_folder(str(music / "dub"))
    assert "already pinned" in win.statusBar().currentMessage()
    assert win.source_tabs.current() == "library"
    assert win.source_tabs.keys().count("pin:" + str(music / "dub")) == 1
    assert win._pins == [str(music / "dub")]


def test_a_pinned_tab_reads_its_folder_again_each_time_it_is_opened(window, wait_for):
    win, _lib, music = window
    win.pin_folder(str(music / "dub"))
    win.source_tabs.set_current("pin:" + str(music / "dub"))
    assert wait_for(lambda: titles(win) == ["a", "b", "c"]), titles(win)
    win.source_tabs.set_current("library")
    (music / "dub" / "d.mp3").write_bytes(b"x" * 10)
    win.source_tabs.set_current("pin:" + str(music / "dub"))
    assert wait_for(lambda: titles(win) == ["a", "b", "c", "d"]), titles(win)


def test_a_pinned_tab_browses_the_folders_inside_it(window, wait_for):
    win, _lib, music = window
    win.pin_folder(str(music))
    win.source_tabs.set_current("pin:" + str(music))
    tree = win._pin_trees["pin:" + str(music)]
    assert tree.current_path() == str(music)
    tree.select_path(str(music / "house"))
    assert wait_for(lambda: titles(win) == ["x"]), titles(win)
    win.source_tabs.set_current("explorer")
    win.source_tabs.set_current("pin:" + str(music))
    assert wait_for(lambda: titles(win) == ["x"]), titles(win)  # it kept its own pick


def test_unpinning_removes_the_tab_and_goes_back_to_the_library(window):
    win, _lib, music = window
    win.pin_folder(str(music / "dub"))
    win.pin_folder(str(music / "house"))
    win.unpin_folder(str(music / "house"))
    assert win.source_tabs.keys() == [
        "library",
        "explorer",
        "pin:" + str(music / "dub"),
    ]
    assert win.source_tabs.current() == "library"
    assert win.source_pages.count() == 3 and win._pins == [str(music / "dub")]
    assert (music / "house" / "x.mp3").exists()  # (the folder is left alone)
    win.unpin_folder(str(music / "house"))  # (already gone: nothing happens)


def test_the_menu_offers_pin_or_unpin_by_whether_the_folder_is_pinned(window):
    win, _lib, music = window
    tree = win.explorer_tree
    path = str(music / "dub")
    asked = []
    tree.pin_requested.connect(asked.append)

    def offered():
        return [a.text() for a in tree._pin_menu(path).actions()]

    assert offered() == ["Pin Folder", "Refresh"]
    tree._pin_menu(path).actions()[0].trigger()
    assert asked == [path]
    win.pin_folder(path)
    assert offered() == ["Unpin Folder", "Refresh"]  # (the tree is told what is pinned)
    win.unpin_folder(path)
    assert offered() == ["Pin Folder", "Refresh"]


def test_pins_and_the_pinned_tab_are_remembered(qapp, music, probed, monkeypatch):
    monkeypatch.setattr(explorer, "explorer_roots", lambda: [("Music", music, False)])
    gone = music / "gone"
    settings.set_pinned_folders([str(music / "dub"), str(gone)])
    settings.set_left_tab("pin:" + str(music / "dub"))
    lib = Library.open(":memory:")
    win = MainWindow(lib, persist=True)
    try:
        assert win.source_tabs.keys()[2:] == [
            "pin:" + str(music / "dub"),
            "pin:" + str(gone),
        ]  # (a pin whose folder is missing keeps its tab: a drive may be unplugged)
        assert win.source_tabs.current() == "pin:" + str(music / "dub")
        win.unpin_folder(str(gone))
        assert settings.get_pinned_folders() == [str(music / "dub")]
    finally:
        win.close()
        win.deleteLater()
        lib.close()


def test_a_remembered_tab_whose_pin_has_gone_falls_back_to_the_library(
    qapp, music, probed, monkeypatch
):
    monkeypatch.setattr(explorer, "explorer_roots", lambda: [("Music", music, False)])
    settings.set_left_tab("pin:/nowhere")
    lib = Library.open(":memory:")
    win = MainWindow(lib, persist=True)
    try:
        assert win.source_tabs.current() == "library"
    finally:
        win.close()
        win.deleteLater()
        lib.close()


# -- refreshing, reordering, scrolling ------------------------------------------------------


def root_names(tree):
    return [tree._model.item(r).text() for r in range(tree._model.rowCount())]


def test_refreshing_the_drives_adds_and_removes_rows_and_keeps_the_rest(tmp_path):
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    home, stick = tmp_path / "home", tmp_path / "stick"
    for d in (home / "sub", stick):
        d.mkdir(parents=True)
    roots = [("home", home, False)]
    tree = ExplorerTree(roots=lambda: list(roots))
    tree.select_path(str(home / "sub"))
    assert root_names(tree) == ["home"]
    roots.append(("stick", stick, True))
    tree.refresh_roots()
    assert root_names(tree) == ["home", "stick"]
    assert tree.current_path() == str(home / "sub")  # (what was open and picked stays)
    roots.pop(0)
    roots.insert(0, ("aaa", tmp_path / "aaa", True))
    tree.refresh_roots()
    assert root_names(tree) == ["aaa", "stick"]  # (in order: new at the top, old gone)
    roots.pop(0)
    tree.refresh_roots()
    assert root_names(tree) == ["stick"]
    tree.refresh_roots()  # (nothing changed: nothing happens)
    assert root_names(tree) == ["stick"]


def test_refreshing_a_folder_lists_its_subfolders_again(tmp_path):
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    tree = ExplorerTree(roots=lambda: [("t", tmp_path, False)])
    tree.select_path(str(tmp_path / "b"))
    (tmp_path / "ab").mkdir()
    (tmp_path / "a").rmdir()
    tree.refresh_folder(str(tmp_path))
    root = tree._model.item(0)
    assert [root.child(r).text() for r in range(root.rowCount())] == ["ab", "b"]
    assert tree.current_path() == str(tmp_path / "b")  # (the pick survives)
    tree.refresh_folder(str(tmp_path / "nowhere"))  # (not in the tree: nothing happens)


def test_opening_the_explorer_tab_looks_for_drives_again(window):
    win, _lib, music = window
    drives = [("Music", music, False)]
    win.explorer_tree._roots = lambda: list(drives)
    drives.append(("Stick", music / "dub", True))
    win.source_tabs.set_current("explorer")
    assert root_names(win.explorer_tree) == ["Music", "Stick"]


def test_refresh_on_the_showing_folder_reads_its_files_again(window, wait_for):
    win, _lib, music = window
    win.source_tabs.set_current("explorer")
    open_folder(win, wait_for, music / "dub", ["a", "b", "c"])
    (music / "dub" / "d.mp3").write_bytes(b"x" * 10)
    win.explorer_tree._pin_menu(str(music / "dub")).actions()[-1].trigger()
    assert wait_for(lambda: titles(win) == ["a", "b", "c", "d"]), titles(win)


def pins(win, music, *names):
    for name in names:
        win.pin_folder(str(music / name))
    return [Path(p).name for p in win._pins]


def tab_names(win):
    keys = win.source_tabs.keys()
    return [Path(k[4:]).name for k in keys if k.startswith("pin:")]


def test_a_tab_moves_up_and_down_as_far_as_there_is_room(qapp):
    bar = SourceTabBar()
    for key in ("library", "explorer", "pin:/a", "pin:/b", "pin:/c"):
        bar.add_tab(key, key, pinned_glyph, movable=key.startswith("pin:"))
    bar.move_tab("pin:/c", -1)
    assert bar.keys() == ["library", "explorer", "pin:/a", "pin:/c", "pin:/b"]
    bar.move_tab("pin:/b", 5)  # (already last)
    bar.move_tab("pin:/a", 9)
    assert bar.keys()[-1] == "pin:/a"
    bar.move_tab("nope", 1)
    order = [bar._layout.itemAt(i).widget().key for i in range(bar._layout.count() - 1)]
    assert order == bar.keys()  # (the buttons really are in that order on screen)


def test_pinned_tabs_move_and_the_order_is_remembered(window):
    win, _lib, music = window
    win._persist = True
    assert pins(win, music, "dub", "house", "empty") == ["dub", "house", "empty"]
    win.move_pin(str(music / "empty"), -2)
    assert [Path(p).name for p in win._pins] == ["empty", "dub", "house"]
    assert tab_names(win) == ["empty", "dub", "house"]
    assert [Path(p).name for p in settings.get_pinned_folders()] == [
        "empty", "dub", "house"
    ]  # fmt: skip
    win.move_pin(str(music / "empty"), -1)  # (at the top: nothing happens)
    assert tab_names(win) == ["empty", "dub", "house"]
    assert win.source_tabs.keys()[:2] == [
        "library",
        "explorer",
    ]  # (pins stay below these)


def test_the_tab_menu_offers_moves_that_make_sense_and_unpin(window):
    win, _lib, music = window
    pins(win, music, "dub", "house", "empty")

    def menu(name):
        m = win._tab_menu("pin:" + str(music / name))
        return [(a.text(), a.isEnabled()) for a in m.actions() if a.text()]

    assert menu("dub") == [
        ("Move Up", False),
        ("Move Down", True),
        ("Unpin Folder", True),
    ]
    assert menu("house")[:2] == [("Move Up", True), ("Move Down", True)]
    assert menu("empty")[:2] == [("Move Up", True), ("Move Down", False)]
    assert win._tab_menu("library") is None


def test_many_tabs_scroll_instead_of_stretching_the_window(qapp):
    bar = SourceTabBar()
    bar.add_tab("library", "Library", library_glyph)
    for i in range(30):
        bar.add_tab(f"pin:/{i}", f"f{i}", pinned_glyph)
    bar.resize(bar.width(), 300)
    bar.show()
    qapp.processEvents()
    assert bar.minimumSizeHint().height() < 300  # (it does not force the window taller)
    scroll = bar._area.verticalScrollBar()
    assert scroll.maximum() > 0 and scroll.value() == 0
    bar.set_current("pin:/29")  # (picking a tab brings it into view)
    qapp.processEvents()
    assert scroll.value() > 0
    visible = bar._area.viewport().rect()
    button = bar._buttons["pin:/29"]
    assert visible.contains(button.mapTo(bar._area.viewport(), button.rect().center()))
    bar.set_current("library")
    qapp.processEvents()
    assert scroll.value() == 0
    for look in ("dark", "light", "system6"):  # (the chevrons paint in every look)
        from dubcrate.ui import theme

        theme.apply_to_app(qapp, look)
        assert not bar.grab().toImage().isNull()
    theme.apply_to_app(qapp, "dark")
    bar.hide()


def test_a_few_tabs_need_no_scrolling(qapp):
    bar = SourceTabBar()
    bar.add_tab("library", "Library", library_glyph)
    bar.add_tab("explorer", "Explorer", explorer_glyph)
    bar.resize(bar.width(), 400)
    bar.show()
    qapp.processEvents()
    assert bar._area.verticalScrollBar().maximum() == 0
    bar.hide()


# -- dragging a tab ------------------------------------------------------------------------


def drag_bar(qapp, count=4):
    bar = SourceTabBar()
    bar.add_tab("library", "Library", library_glyph)
    bar.add_tab("explorer", "Explorer", explorer_glyph)
    for i in range(count):
        bar.add_tab(f"pin:/{i}", f"f{i}", pinned_glyph, movable=True)
    bar.resize(bar.width(), 600)
    bar.show()
    qapp.processEvents()
    return bar


def drag(bar, key, to_key, *, below=False):
    """Press on one tab, move the pointer to another's middle (or just below it), let go."""
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    button, target = bar._buttons[key], bar._buttons[to_key]
    start = button.rect().center()
    goal = button.mapFromGlobal(
        target.mapToGlobal(
            QPoint(target.width() // 2, target.height() - 2 if below else 4)
        )
    )
    QTest.mousePress(
        button, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start
    )
    QTest.mouseMove(button, start + QPoint(0, 12))  # (past the drag distance)
    QTest.mouseMove(button, goal)
    QTest.mouseRelease(
        button, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, goal
    )


def test_a_pinned_tab_can_be_dragged_to_another_place(qapp):
    bar = drag_bar(qapp)
    told = []
    bar.tabs_reordered.connect(lambda: told.append(bar.keys()))
    drag(bar, "pin:/3", "pin:/0")
    expected = ["library", "explorer", "pin:/3", "pin:/0", "pin:/1", "pin:/2"]
    assert bar.keys() == expected and told == [expected]
    drag(bar, "pin:/3", "pin:/2", below=True)
    assert bar.keys()[-1] == "pin:/3"
    bar.hide()


def test_dragging_does_not_pick_the_tab_but_a_click_does(qapp):
    from PySide6.QtTest import QTest

    bar = drag_bar(qapp)
    picked = []
    bar.current_changed.connect(picked.append)
    drag(bar, "pin:/2", "pin:/0")
    assert picked == [] and bar.current() == "library"
    QTest.mouseClick(bar._buttons["pin:/1"], Qt.MouseButton.LeftButton)
    assert picked == ["pin:/1"]
    bar.hide()


def test_library_and_explorer_stay_first_and_second(qapp):
    bar = drag_bar(qapp)
    drag(bar, "pin:/1", "library")  # (dragged right up over the Library tab)
    assert bar.keys()[:2] == ["library", "explorer"]
    assert bar.keys()[2] == "pin:/1"  # (it stops at the top of the pins)
    drag(bar, "library", "pin:/3")  # (a tab that is not movable cannot be picked up)
    assert bar.keys()[:2] == ["library", "explorer"]
    bar.move_tab("explorer", 3)
    bar.move_tab("pin:/0", -9)
    assert bar.keys()[:3] == ["library", "explorer", "pin:/0"]
    bar.hide()


def test_a_drag_in_the_window_reorders_the_pins_and_saves_them(window):
    win, _lib, music = window
    win._persist = True
    pins(win, music, "dub", "house", "empty")
    win.source_tabs.set_current("library")
    win.show()
    drag(win.source_tabs, "pin:" + str(music / "empty"), "pin:" + str(music / "dub"))
    assert tab_names(win) == ["empty", "dub", "house"]
    assert [Path(p).name for p in win._pins] == ["empty", "dub", "house"]
    assert [Path(p).name for p in settings.get_pinned_folders()] == [
        "empty", "dub", "house"
    ]  # fmt: skip
    assert win.source_tabs.current() == "library"  # (dragging did not change the tab)


def test_a_tab_picked_as_soon_as_it_is_added_is_scrolled_into_view(qapp):
    bar = SourceTabBar()
    bar.add_tab("library", "Library", library_glyph)
    for i in range(12):
        bar.add_tab(f"pin:/{i}", f"f{i}", pinned_glyph, movable=True)
    bar.resize(bar.width(), 300)
    bar.show()
    qapp.processEvents()
    bar.add_tab("pin:/new", "new", pinned_glyph, movable=True)
    bar.set_current(
        "pin:/new"
    )  # (as `pin_folder` does, with no event loop turn between)
    qapp.processEvents()
    button = bar._buttons["pin:/new"]
    viewport = bar._area.viewport()
    assert viewport.rect().contains(button.mapTo(viewport, button.rect().center()))
    bar.hide()
