import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QItemSelectionModel, QMimeData, QModelIndex, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from dubcrate import settings
from dubcrate.cd import DISC_74_SECTORS, DISC_80_SECTORS
from dubcrate.library import Library, NodeRecord, TrackRecord
from dubcrate.library.rekordbox import RekordboxError, RekordboxLibrary
from dubcrate.ui import main_window, theme
from dubcrate.ui.disc_view import DiscView
from dubcrate.ui.main_window import MainWindow
from dubcrate.ui.models import DISC_ROWS_MIME, TRACK_COLUMNS, TRACK_MIME, find_selection

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg not installed",
)
YES = QMessageBox.StandardButton.Yes
NO = QMessageBox.StandardButton.No
ROWS = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows


def make_flac(path, seconds=1.0, **tags):
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = [arg for k, v in tags.items() for arg in ("-metadata", f"{k}={v}")]
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
         f"sine=duration={seconds}", *meta, str(path)],
        check=True,
    )  # fmt: skip


def select_rows(view, rows):
    selection = view.selectionModel()
    selection.clearSelection()
    for row in rows:
        selection.select(view.model().index(row, 0), ROWS)


def select_node(win, lib, name):
    [source] = [s for s in lib.sources() if s.name == "Crate"]
    node = next(n for n in lib.nodes(source.id) if n.name == name)
    win.tree.setCurrentIndex(
        find_selection(win.tree.model(), ("node", node.id, source.id))
    )


def titles(win):
    return [t.title for t in win.track_model.tracks()]


def disc_titles(win):
    panel = win.disc_view
    return [panel.model.entry_at(r).track.title for r in range(panel.model.rowCount())]


def no_dialogs(monkeypatch):
    """Record the message boxes the window would have shown instead of blocking on them."""
    shown = []
    for name in ("information", "warning"):
        monkeypatch.setattr(
            QMessageBox,
            name,
            staticmethod(
                lambda _parent, title, text, _n=name: shown.append((_n, title, text))
            ),
        )
    return shown


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    shown = no_dialogs(monkeypatch)
    lib = Library.open(":memory:")
    records = []
    for i in range(6):
        file = tmp_path / "music" / f"t{i}.mp3"
        file.parent.mkdir(exist_ok=True)
        file.write_bytes(b"x")
        records.append(
            TrackRecord(ext_id=f"t{i}", path=str(file), title=f"Song {i}", artist=f"Artist {i}", bpm=120.0 + i * 3,
                        key="8A", duration=66.0, genre="House")
        )  # fmt: skip
    nodes = [
        NodeRecord(
            "set", None, "Set", False, tuple(r.ext_id for r in reversed(records))
        )
    ]
    lib.replace_source("folder", str(tmp_path / "music"), "Crate", records, nodes)
    for track in lib.tracks():
        lib.set_cd_sectors(track.id, 5000)  # already measured, so adding is immediate
    win = MainWindow(lib)
    win.shown = shown
    yield win, lib
    win.close()
    win.deleteLater()
    lib.close()


# -- browsing -----------------------------------------------------------------


def test_starts_on_all_tracks(window):
    win, _ = window
    assert win.tracks_title.text() == "All Tracks"
    assert win.track_model.rowCount() == 6
    assert win.tracks_count.text().startswith("6 tracks")
    assert not win.add_button.isEnabled() and win.fit_button.isEnabled()


def test_a_playlist_shows_its_tracks_in_playlist_order(window):
    win, lib = window
    select_node(win, lib, "Set")
    assert win.tracks_title.text() == "Set"
    assert titles(win) == [f"Song {i}" for i in (5, 4, 3, 2, 1, 0)]


def test_search_filters_the_table(window):
    win, _ = window
    win.search.setText("song 3")
    assert win._search_timer.isActive()  # debounced
    win.refresh_tracks()
    assert titles(win) == ["Song 3"]
    win.search.setText("")
    win.refresh_tracks()
    assert win.track_model.rowCount() == 6


def test_clicking_a_header_sorts_and_the_marker_column_restores_the_natural_order(
    window,
):
    win, lib = window
    select_node(win, lib, "Set")
    bpm = next(i for i, c in enumerate(TRACK_COLUMNS) if c.title == "BPM")
    win._on_sort_changed(bpm, Qt.SortOrder.DescendingOrder)
    assert titles(win)[0] == "Song 5" and win._sort == ("bpm", True)
    win._on_sort_changed(bpm, Qt.SortOrder.AscendingOrder)
    assert titles(win)[0] == "Song 0"
    win._on_sort_changed(0, Qt.SortOrder.AscendingOrder)
    assert (
        win._sort == (None, False) and titles(win)[0] == "Song 5"
    )  # playlist order again


def test_source_actions_need_a_source_selected(window):
    win, lib = window
    assert not win.act_reimport.isEnabled() and not win.act_remove_source.isEnabled()
    select_node(win, lib, "Set")
    assert win.act_reimport.isEnabled() and win.act_remove_source.isEnabled()


# -- building a disc ----------------------------------------------------------


def test_adding_selected_tracks_creates_a_disc_and_marks_them(window):
    win, lib = window
    select_rows(win.track_table, [0, 2])
    assert win.add_button.isEnabled()
    win.add_selected_to_disc()
    assert len(lib.discs()) == 1 and lib.discs()[0].name == "Disc 1"
    assert (
        win.disc_view.current_disc_id() == lib.discs()[0].id
    )  # and it is the open disc
    assert "(a new disc)" in win.statusBar().currentMessage()
    assert (
        win.center.currentIndex() == 0
    )  # adding from the library stays in the library
    assert disc_titles(win) == ["Song 0", "Song 2"]
    marks = [win.track_model.data(win.track_model.index(r, 0)) for r in range(6)]
    assert marks == ["●", "", "●", "", "", ""]
    assert "Added 2 tracks" in win.statusBar().currentMessage()


def test_command_return_adds_the_selected_tracks_to_the_disc(window):
    win, _ = window
    select_rows(win.track_table, [1, 3])
    QTest.keyClick(
        win.track_table, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier
    )
    assert disc_titles(win) == ["Song 1", "Song 3"]


def test_the_add_menu_item_has_command_return_and_command_d(window):
    win, _ = window
    keys = [s.toString() for s in win.act_add_selected.shortcuts()]
    assert keys == ["Ctrl+Return", "Ctrl+Enter", "Ctrl+D"]


def test_return_loads_the_highlighted_track_into_the_player_and_adds_nothing(
    window, monkeypatch
):
    win, _ = window
    loaded = []
    monkeypatch.setattr(
        win.preview, "load", lambda track, **k: loaded.append((track.title, k))
    )
    select_rows(win.track_table, [2])
    QTest.keyClick(win.track_table, Qt.Key.Key_Return)
    assert loaded == [("Song 2", {})]  # as a double-click does: loaded, and it plays
    assert disc_titles(win) == []  # nothing was added to a disc


def test_return_with_several_selected_loads_the_one_with_the_focus(window, monkeypatch):
    win, _ = window
    loaded = []
    monkeypatch.setattr(
        win.preview, "load", lambda track, **k: loaded.append(track.title)
    )
    select_rows(win.track_table, [1, 3, 4])
    selection = win.track_table.selectionModel()
    selection.setCurrentIndex(  # the focus moves to row 3 without changing what is selected
        win.track_table.model().index(3, 1), selection.SelectionFlag.NoUpdate
    )
    QTest.keyClick(win.track_table, Qt.Key.Key_Return)
    assert loaded == ["Song 3"]
    win.track_table.clearSelection()
    QTest.keyClick(
        win.track_table, Qt.Key.Key_Return
    )  # nothing highlighted: nothing happens
    assert loaded == ["Song 3"]


def test_fit_all_stops_when_the_disc_is_full(window):
    win, lib = window
    disc_id = lib.create_disc(
        "Tiny", capacity_sectors=12_000
    )  # room for two 5000-sector tracks
    win.disc_view.set_disc(disc_id)
    win.fit_all_to_disc()
    assert len(disc_titles(win)) == 2
    assert "4 didn't fit" in win.statusBar().currentMessage()
    assert not lib.disc(disc_id).over


def test_dropping_tracks_inserts_them_at_the_drop_row(window):
    win, lib = window
    ids = {t.title: t.id for t in lib.tracks()}
    win.disc_view.add_tracks([ids["Song 0"], ids["Song 1"]])
    mime = QMimeData()
    mime.setData(TRACK_MIME, json.dumps([ids["Song 5"]]).encode())
    win.disc_view.model.dropMimeData(
        mime, Qt.DropAction.CopyAction, 1, 0, QModelIndex()
    )
    assert disc_titles(win) == ["Song 0", "Song 5", "Song 1"]


def test_dragging_rows_reorders_the_disc(window):
    win, lib = window
    win.disc_view.add_tracks([t.id for t in lib.tracks(sort="title")][:4])
    mime = QMimeData()
    mime.setData(DISC_ROWS_MIME, json.dumps([0]).encode())
    win.disc_view.model.dropMimeData(
        mime, Qt.DropAction.MoveAction, 3, 0, QModelIndex()
    )
    assert disc_titles(win) == ["Song 1", "Song 2", "Song 0", "Song 3"]
    assert [win.disc_view.model.entry_at(r).position for r in range(4)] == [0, 1, 2, 3]


def test_remove_button_and_delete_key_take_tracks_off_the_disc(window):
    win, lib = window
    panel = win.disc_view
    panel.add_tracks([t.id for t in lib.tracks(sort="title")][:4])
    assert not panel.remove_button.isEnabled()
    select_rows(panel.table, [1])
    assert panel.remove_button.isEnabled()
    panel.remove_selected()
    assert disc_titles(win) == ["Song 0", "Song 2", "Song 3"]
    select_rows(panel.table, [0, 1])
    panel.table.keyPressEvent(
        QKeyEvent(
            QKeyEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier
        )
    )
    assert disc_titles(win) == ["Song 3"]
    assert (
        win.track_model.data(win.track_model.index(3, 0)) == "●"
    )  # still marked: it's still on the disc


def test_new_discs_are_80_minutes_and_gapless_by_default(window):
    win, lib = window
    win.disc_view.add_tracks([t.id for t in lib.tracks()][:2])
    disc = lib.disc(win.disc_view.current_disc_id())
    assert (disc.capacity_sectors, disc.pregap_sectors) == (DISC_80_SECTORS, 0)
    assert "of 80:00" in win.disc_view.summary.label.text()


def test_new_discs_follow_the_size_and_gap_chosen_in_settings(window):
    win, lib = window
    settings.set_disc_minutes(74)
    settings.set_pregap_seconds(2)
    ids = [t.id for t in lib.tracks()]
    win.disc_view.add_tracks(ids[:2])
    disc = lib.disc(win.disc_view.current_disc_id())
    assert (disc.capacity_sectors, disc.pregap_sectors) == (DISC_74_SECTORS, 150)
    assert disc.used_sectors == 2 * 5000 + 150
    assert "of 74:00" in win.disc_view.summary.label.text()


def test_existing_discs_keep_their_own_size_when_settings_change(window):
    win, lib = window
    win.disc_view.add_tracks([t.id for t in lib.tracks()][:1])
    disc_id = win.disc_view.current_disc_id()
    settings.set_disc_minutes(74)
    settings.set_pregap_seconds(3)
    win.disc_view.reload()
    assert lib.disc(disc_id).capacity_sectors == DISC_80_SECTORS
    assert lib.disc(disc_id).pregap_sectors == 0


def test_the_disc_view_no_longer_has_size_or_gap_controls(window):
    win, _lib = window
    assert not hasattr(win.disc_view, "size_combo")
    assert not hasattr(win.disc_view, "gap_combo")


def test_new_rename_and_delete_a_disc(window, monkeypatch):
    win, lib = window
    panel = win.disc_view
    assert panel.stack.currentIndex() == 0  # nothing yet: the empty state
    disc_id = panel.new_disc()
    assert lib.disc(disc_id).name == "Disc 1" and panel.stack.currentIndex() == 1
    assert win.center.currentIndex() == 1  # a new disc opens in the middle
    panel.rename_disc(disc_id, "Renamed")
    assert lib.disc(disc_id).name == "Renamed" and panel.title.text() == "Renamed"
    panel.rename_disc(disc_id, "  ")  # nothing to rename it to
    assert lib.disc(disc_id).name == "Renamed"
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: YES))
    panel.delete_disc()
    assert lib.discs() == [] and panel.stack.currentIndex() == 0


def test_split_cuts_the_shown_tracks_across_discs(window, monkeypatch):
    win, lib = window
    for track in lib.tracks():
        lib.set_cd_sectors(track.id, 150_000)  # two per 80-minute disc
    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: ("Set", True))
    )
    win.split_shown()
    assert [d.name for d in lib.discs()] == ["Set 1", "Set 2", "Set 3"]
    assert win.disc_view.title.text() == "Set 1" and win.center.currentIndex() == 1
    assert "Created 3 discs" in win.statusBar().currentMessage()


def test_the_last_disc_used_is_reselected_next_time(window):
    win, lib = window
    first, second = lib.create_disc("One"), lib.create_disc("Two")
    win.disc_view.set_disc(second)
    assert settings.get_last_disc() == second
    assert DiscView(lib).current_disc_id() == second
    lib.delete_disc(second)
    assert DiscView(lib).current_disc_id() is None  # that one is gone: nothing is open
    assert first in {d.id for d in lib.discs()}


# -- importing ----------------------------------------------------------------


@needs_ffmpeg
def test_adding_unmeasured_tracks_measures_them_on_a_worker_then_adds(
    qapp, tmp_path, monkeypatch, wait_for
):
    no_dialogs(monkeypatch)
    make_flac(tmp_path / "a.flac", 2.0, title="Alpha")
    make_flac(tmp_path / "b.flac", 3.0, title="Beta")
    lib = Library.open(":memory:")
    records = [
        TrackRecord(ext_id=n, path=str(tmp_path / f"{n}.flac"), title=n, duration=1)
        for n in ("a", "b")
    ]
    lib.replace_source("folder", str(tmp_path), "Crate", records)
    win = MainWindow(lib)
    busy = []
    win.disc_view.busy.connect(lambda b, _m: busy.append(b))
    win.disc_view.add_tracks([t.id for t in lib.tracks()])
    assert win._busy and busy[0] is True  # working in the background
    assert wait_for(lambda: disc_titles(win) == ["a", "b"] and not win._busy)
    assert busy == [True, False]
    assert [t.cd_sectors for t in lib.tracks(sort="title")] == [150, 225]
    assert win.disc_view.summary.label.text().startswith(
        "0:05 of 80:00"
    )  # 2 s + 3 s, measured
    win.close()
    lib.close()


@needs_ffmpeg
def test_importing_a_folder_scans_in_the_background_then_shows_it(
    window, tmp_path, monkeypatch, wait_for
):
    win, lib = window
    music = tmp_path / "new crate"
    make_flac(music / "x.flac", title="Ex", artist="Why")
    make_flac(music / "Sub" / "y.flac", title="Why")
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(music))
    )
    win.import_folder()
    assert win._busy and not win.act_import_folder.isEnabled()
    assert wait_for(lambda: not win._busy)
    assert win.act_import_folder.isEnabled()
    source = next(s for s in lib.sources() if s.name == "new crate")
    assert source.track_count == 2 and win._selection == ("source", source.id)
    assert win.tracks_title.text() == "new crate" and win.track_model.rowCount() == 2
    assert "Imported 2 tracks" in win.statusBar().currentMessage()


@needs_ffmpeg
def test_unreadable_files_are_listed_after_an_import(
    window, tmp_path, monkeypatch, wait_for
):
    win, _ = window
    music = tmp_path / "mixed"
    make_flac(music / "good.flac")
    (music / "corrupt.flac").write_bytes(b"x")
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(music))
    )
    win.import_folder()
    assert wait_for(lambda: not win._busy)
    [(kind, title, text)] = win.shown
    assert (kind, title) == ("information", "Import notes") and "corrupt.flac" in text


def fake_rekordbox(tmp_path):
    record = TrackRecord(
        ext_id="9",
        path=str(tmp_path / "rb.mp3"),
        title="From rekordbox",
        duration=200,
        bpm=128.0,
    )
    return RekordboxLibrary(
        source=tmp_path / "master.db",
        tracks=[record],
        nodes=[NodeRecord("p", None, "My Playlist", False, ("9",))],
        notes=["skipped 1 smart playlist(s): members are rule-based"],
    )


def test_importing_rekordbox_reads_it_in_the_background(
    window, tmp_path, monkeypatch, wait_for
):
    win, lib = window
    monkeypatch.setattr(main_window, "default_master_db", lambda: None)
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **k: (str(tmp_path / "master.db"), "")),
    )
    monkeypatch.setattr(
        main_window, "read_master_db", lambda path: fake_rekordbox(tmp_path)
    )
    win.import_rekordbox()
    assert wait_for(lambda: not win._busy)
    source = next(s for s in lib.sources() if s.kind == "rekordbox")
    assert source.name == "rekordbox" and source.track_count == 1
    assert titles(win) == ["From rekordbox"]
    assert win.shown and "smart playlist" in win.shown[0][2]


def test_a_failed_import_is_reported_and_leaves_the_window_usable(
    window, tmp_path, monkeypatch, wait_for
):
    win, lib = window

    def boom(path):
        raise RekordboxError("could not open the database")

    monkeypatch.setattr(main_window, "default_master_db", lambda: None)
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **k: (str(tmp_path / "master.db"), "")),
    )
    monkeypatch.setattr(main_window, "read_master_db", boom)
    win.import_rekordbox()
    assert wait_for(lambda: not win._busy)
    assert win.shown == [("warning", "Import failed", "could not open the database")]
    assert [s.name for s in lib.sources()] == [
        "Crate"
    ] and win.act_import_folder.isEnabled()


def test_cancelling_the_file_dialog_imports_nothing(window, monkeypatch):
    win, _ = window
    monkeypatch.setattr(main_window, "default_master_db", lambda: None)
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", ""))
    )
    win.import_rekordbox()
    assert not win._busy


@needs_ffmpeg
def test_reimporting_a_source_syncs_it_in_place(
    window, tmp_path, monkeypatch, wait_for
):
    win, lib = window
    music = tmp_path / "again"
    make_flac(music / "one.flac", title="One")
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(music))
    )
    win.import_folder()
    assert wait_for(lambda: not win._busy)
    first_ids = {t.title: t.id for t in lib.tracks(source_id=win._selection[1])}
    make_flac(music / "two.flac", title="Two")
    win.reimport_selected()
    assert wait_for(lambda: not win._busy)
    after = {t.title: t.id for t in lib.tracks(source_id=win._selection[1])}
    assert set(after) == {"One", "Two"} and after["One"] == first_ids["One"]


def test_reimport_of_a_vanished_folder_explains_itself(window, tmp_path):
    win, lib = window
    shutil.rmtree(tmp_path / "music")
    select_node(win, lib, "Set")
    win.reimport_selected()
    assert (
        not win._busy
        and win.shown[0][0] == "warning"
        and "no longer there" in win.shown[0][2]
    )


def test_removing_a_source_that_discs_use_asks_before_taking_them_off(
    window, monkeypatch
):
    win, lib = window
    win.disc_view.add_tracks([t.id for t in lib.tracks()][:2])
    select_node(win, lib, "Set")
    answers = iter([YES, YES])  # remove it? ... then: remove them from the discs too?
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: next(answers))
    )
    win.remove_selected_source()
    assert lib.sources() == [] and win._selection == ("all",)
    assert lib.discs()[0].track_count == 0 and win.disc_view.model.rowCount() == 0


def test_declining_the_second_question_keeps_everything(window, monkeypatch):
    win, lib = window
    win.disc_view.add_tracks([t.id for t in lib.tracks()][:2])
    select_node(win, lib, "Set")
    answers = iter([YES, QMessageBox.StandardButton.No])
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: next(answers))
    )
    win.remove_selected_source()
    assert len(lib.sources()) == 1 and lib.discs()[0].track_count == 2


# -- theme, settings, startup ---------------------------------------------------


def test_the_theme_menu_applies_and_remembers_the_choice(window):
    win, _ = window
    try:
        win.set_theme("light")
        assert settings.get_theme() == "light" and theme.current_preference() == "light"
        assert (
            win._theme_actions["light"].isChecked()
            and not win._theme_actions["dark"].isChecked()
        )
        win.set_theme("system6")
        assert theme.current_palette().get("mono_icons")
        assert (
            win.tree.model().rowCount() == 2
        )  # the tree was rebuilt for the new icons (All Tracks, the source)
    finally:
        win.set_theme("dark")


def test_window_and_splitter_layout_are_remembered(window):
    _win, lib = window
    first = MainWindow(lib, persist=True)
    first.splitter.setSizes([250, 700, 350])
    first.close()
    assert settings.get_bytes("window/geometry") is not None
    assert settings.get_bytes("window/splitter3") is not None
    second = MainWindow(lib, persist=True)
    assert second.splitter.saveState() == first.splitter.saveState()
    second.deleteLater()
    first.deleteLater()


def test_the_app_starts_and_quits(tmp_path):
    code = (
        "import sys\n"
        "from PySide6.QtCore import QTimer\n"
        "from PySide6.QtWidgets import QApplication\n"
        "import dubcrate.ui.app as app\n"
        "run = QApplication.exec\n"
        "QApplication.exec = staticmethod(lambda: (QTimer.singleShot(300, QApplication.quit), run())[1])\n"
        f"sys.exit(app.main(['--library', {str(tmp_path / 'lib.db')!r}]))\n"
    )
    env = {
        **__import__("os").environ,
        "QT_QPA_PLATFORM": "offscreen",
        "DUBCRATE_DATA_DIR": str(tmp_path / "data"),
        "HOME": str(tmp_path),  # a throwaway QSettings location too
    }
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert Path(tmp_path / "lib.db").is_file()


# -- already-used warning -----------------------------------------------------


def ask_overlap(monkeypatch, answer):
    asked = []

    def question(_parent, title, text, *a, **k):
        asked.append(text)
        return answer

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    return asked


def test_adding_a_track_already_on_the_disc_asks_first(window, monkeypatch):
    win, lib = window
    ids = [t.id for t in lib.tracks(sort="title")]
    win.disc_view.add_tracks(ids[:2])
    asked = ask_overlap(monkeypatch, NO)
    win.disc_view.add_tracks(ids[1:3])
    assert len(asked) == 1 and "already on this disc" in asked[0]
    assert disc_titles(win) == ["Song 0", "Song 1"]  # declined: nothing added


def test_agreeing_adds_them_anyway(window, monkeypatch):
    win, lib = window
    ids = [t.id for t in lib.tracks(sort="title")]
    win.disc_view.add_tracks(ids[:1])
    ask_overlap(monkeypatch, YES)
    win.disc_view.add_tracks(ids[:1])
    assert disc_titles(win) == ["Song 0", "Song 0"]


def test_a_track_on_a_burned_disc_is_flagged_on_a_new_one_with_when_it_was_burned(
    window, monkeypatch
):
    win, lib = window
    ids = [t.id for t in lib.tracks(sort="title")]
    old = lib.create_disc("Friday")
    lib.add_to_disc(old, ids[:1])
    lib.mark_burned(old, when="2026-11-01T20:00:00+00:00")
    new = lib.create_disc("Saturday")
    win.disc_view.set_disc(new)
    asked = ask_overlap(monkeypatch, YES)
    win.disc_view.add_tracks(ids[:2])
    assert len(asked) == 1
    assert "on “Friday” (burned 2026-11-01)" in asked[0] and "Song 1" not in asked[0]


def test_fresh_tracks_are_added_without_asking(window, monkeypatch):
    win, lib = window
    ids = [t.id for t in lib.tracks(sort="title")]
    asked = ask_overlap(monkeypatch, NO)
    win.disc_view.add_tracks(ids[:2])
    assert asked == []
    assert disc_titles(win) == ["Song 0", "Song 1"]


# -- the capacity bar ---------------------------------------------------------


def test_the_capacity_bar_has_no_seam_between_neighbouring_tracks(qapp):
    from PySide6.QtGui import QColor

    from dubcrate.ui.capacity_bar import CapacityBar, Span

    bar = CapacityBar()
    bar.resize(401, bar.HEIGHT)
    bar.set_spans(
        [Span(1000, "track", 0), Span(1000, "track", 1), Span(1000, "track", 2)],
        capacity=9000,
    )
    bar.show()
    image = bar.grab().toImage()
    y = image.height() // 2
    track_colours = {
        theme.current_palette()["bg_disabled"],  # the empty part of the bar
    }
    row = [QColor(image.pixelColor(x, y)).name() for x in range(image.width())]
    inside = row[2 : round(image.width() * 3 / 9) - 2]  # between the bar's own ends
    # Each pixel is a track colour, never a divider: at most 3 distinct colours.
    assert len(set(inside) - track_colours) == 3, set(inside)
    bar.close()


# -- find --------------------------------------------------------------------


def test_the_find_shortcut_is_the_platform_one_and_focuses_the_search_box(window):
    from PySide6.QtGui import QKeySequence

    win, _lib = window
    assert win.act_find.text().replace("&", "") == "Find Tracks"
    assert win.act_find.shortcut() == QKeySequence(QKeySequence.StandardKey.Find)
    assert (
        win.act_find.shortcut().toString(QKeySequence.SequenceFormat.PortableText)
        == "Ctrl+F"
    )
    win.show()
    win.track_table.setFocus()
    win.search.setText("kettama")
    win.act_find.trigger()
    assert win.focusWidget() is win.search
    assert win.search.selectedText() == "kettama"  # ready to type over


# -- reordering columns -------------------------------------------------------


def column_titles(win):
    header = win.track_table.horizontalHeader()
    return [
        main_window.TRACK_COLUMNS[header.logicalIndex(v)].title
        for v in range(header.count())
        if not header.isSectionHidden(header.logicalIndex(v))
    ]


def test_the_columns_can_be_dragged_but_the_marker_stays_first(window):
    win, _lib = window
    header = win.track_table.horizontalHeader()
    assert header.sectionsMovable() and not header.isFirstSectionMovable()


def test_moving_a_column_is_remembered_and_restored(window):
    win, lib = window
    header = win.track_table.horizontalHeader()
    assert column_titles(win)[:3] == ["", "Title", "Artist"]
    header.moveSection(header.visualIndex(7), 1)  # Genre to the front
    assert column_titles(win)[:3] == ["", "Genre", "Title"]
    assert settings.get_column_order()[:2] == ["Genre", "Title"]
    again = MainWindow(lib)
    try:
        assert column_titles(again)[:3] == ["", "Genre", "Title"]
    finally:
        again.close()
        again.deleteLater()


def test_a_saved_order_with_unknown_or_missing_columns_still_works(window):
    _win, lib = window
    settings.set_column_order(["Genre", "Nope", "Title", "Genre"])
    again = MainWindow(lib)
    try:
        assert column_titles(again) == [
            "", "Genre", "Title", "Artist", "BPM", "Key", "Time", "Preview",
        ]  # fmt: skip
    finally:
        again.close()
        again.deleteLater()


def test_sorting_still_follows_the_column_after_it_moved(window):
    win, _lib = window
    header = win.track_table.horizontalHeader()
    header.moveSection(header.visualIndex(2), 1)  # Artist before Title
    header.sectionClicked.emit(1)  # Title is still logical column 1
    header.setSortIndicator(1, Qt.SortOrder.AscendingOrder)
    assert titles(win) == sorted(titles(win))


def test_reset_columns_restores_the_default_order_and_columns(window):
    win, _lib = window
    header = win.track_table.horizontalHeader()
    header.moveSection(header.visualIndex(7), 1)
    win.set_column_visible(4, False)
    win.set_column_visible(col("Year"), True)
    win.reset_columns()
    assert column_titles(win) == [
        "", "Title", "Artist", "BPM", "Key", "Time", "Preview", "Genre",
    ]  # fmt: skip
    assert settings.get_column_order() == []


# -- more columns, off by default ----------------------------------------------

ESSENTIAL = ["", "Title", "Artist", "BPM", "Key", "Time", "Preview", "Genre"]


def col(title):
    return next(i for i, c in enumerate(main_window.TRACK_COLUMNS) if c.title == title)


def test_only_the_essential_columns_show_until_more_are_switched_on(window):
    win, _lib = window
    assert column_titles(win) == ESSENTIAL
    assert win.track_table.isColumnHidden(col("Year"))
    assert win.track_table.isColumnHidden(col("My Tags"))


def test_switching_an_extra_column_on_shows_it_and_it_is_remembered(window):
    win, lib = window
    win.set_column_visible(col("Rating"), True)
    win.set_column_visible(col("My Tags"), True)
    assert {"Rating", "My Tags"} <= set(column_titles(win))
    assert {"Rating", "My Tags"} <= set(settings.get_visible_columns())
    again = MainWindow(lib)
    try:
        assert {"Rating", "My Tags"} <= set(column_titles(again))
        assert not again.track_table.isColumnHidden(col("Rating"))
        assert again.track_table.isColumnHidden(col("Year"))  # the rest still off
    finally:
        again.close()
        again.deleteLater()


def test_the_marker_and_title_are_never_offered_in_the_column_menu(window):
    from PySide6.QtWidgets import QMenu

    win, _lib = window
    menu = win.column_menu()
    every = [a.text() for a in menu.actions() if not a.isSeparator()]
    for submenu in menu.findChildren(QMenu):
        every += [a.text() for a in submenu.actions() if not a.isSeparator()]
    assert "Title" not in every and "" not in every
    assert "Artist" in every and "Year" in every  # but the rest are


def test_a_layout_saved_before_the_extra_columns_existed_keeps_what_it_hid(window):
    _win, lib = window
    settings._qs().setValue(
        "tracks/hidden_columns", '["Genre", "Preview"]'
    )  # the old setting
    again = MainWindow(lib)
    try:
        assert column_titles(again) == ["", "Title", "Artist", "BPM", "Key", "Time"]
        assert again.track_table.isColumnHidden(col("Year"))  # and the new ones are off
        again.set_column_visible(
            col("Genre"), True
        )  # changing anything saves the new kind
        assert "Genre" in settings.get_visible_columns()
        assert "Year" not in settings.get_visible_columns()
        assert "Preview" not in settings.get_visible_columns()
    finally:
        again.close()
        again.deleteLater()


def test_reset_brings_back_what_an_old_layout_had_hidden(window):
    _win, lib = window
    settings._qs().setValue("tracks/hidden_columns", '["Genre"]')
    again = MainWindow(lib)
    try:
        assert again.track_table.isColumnHidden(col("Genre"))
        again.reset_columns()
        assert column_titles(again) == ESSENTIAL
    finally:
        again.close()
        again.deleteLater()


def test_the_column_menu_keeps_the_extras_in_a_submenu_of_their_own(window):
    from PySide6.QtWidgets import QMenu

    win, _lib = window
    menu = win.column_menu()
    top = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert top == [
        "Artist",
        "BPM",
        "Key",
        "Time",
        "Preview",
        "Genre",
        "More Columns",
        "Reset Columns",
    ]
    [more] = [a.menu() for a in menu.actions() if a.text() == "More Columns"]
    assert isinstance(more, QMenu)
    assert [a.text() for a in more.actions()] == [
        c.title for c in main_window.TRACK_COLUMNS if not c.shown
    ]
    year = next(a for a in more.actions() if a.text() == "Year")
    assert year.isCheckable() and not year.isChecked()
    year.trigger()
    assert not win.track_table.isColumnHidden(col("Year"))
    assert next(
        a
        for a in win.column_menu().findChildren(QMenu)[0].actions()
        if a.text() == "Year"
    ).isChecked()


def test_each_column_opens_at_its_own_width(window):
    win, _lib = window
    for index, spec in enumerate(main_window.TRACK_COLUMNS):
        win.track_table.setColumnHidden(
            index, False
        )  # (Qt reports a hidden column as 0 wide)
        assert win.track_table.columnWidth(index) == spec.width, spec.title


def test_every_sortable_column_sorts_the_list(window):
    win, _lib = window
    header = win.track_table.horizontalHeader()
    count = len(titles(win))
    for index, spec in enumerate(main_window.TRACK_COLUMNS):
        if spec.sort_key is None:
            continue
        header.setSortIndicator(index, Qt.SortOrder.AscendingOrder)
        assert win._sort == (spec.sort_key, False), spec.title
        assert len(titles(win)) == count, spec.title


# -- editing the artist and track name for the CD ------------------------------


def disc_cell(win, row, column):
    return win.disc_view.model.data(win.disc_view.model.index(row, column))


def edit_cell(win, qapp, row, column, text):
    win.disc_view.model.setData(win.disc_view.model.index(row, column), text)
    qapp.processEvents()  # the panel applies it a moment later, not inside the editor's commit


def test_editing_the_disc_list_changes_the_cd_text_and_not_the_library(window, qapp):
    win, lib = window
    ids = [t.id for t in lib.tracks(sort="title")]
    win.disc_view.add_tracks(ids[:3])
    edit_cell(win, qapp, 1, 3, "Clean Name")
    edit_cell(win, qapp, 1, 2, "Clean Artist")
    assert (
        disc_cell(win, 1, 3) == "Clean Name" and disc_cell(win, 1, 2) == "Clean Artist"
    )
    entry = lib.disc_entries(win.disc_view.current_disc_id())[1]
    assert (entry.title_override, entry.artist_override) == (
        "Clean Name",
        "Clean Artist",
    )
    assert (
        lib.track(ids[1]).title == "Song 1" and lib.track(ids[1]).artist == "Artist 1"
    )
    assert "Clean Name" not in titles(win)  # the browser still shows the library's text


def test_clearing_an_edited_cell_in_the_list_restores_the_original(window, qapp):
    win, _lib = window
    win.disc_view.add_tracks([t.id for t in _lib.tracks(sort="title")][:2])
    edit_cell(win, qapp, 0, 3, "Temporary")
    edit_cell(win, qapp, 0, 3, "")
    assert disc_cell(win, 0, 3) == "Song 0"


def test_edits_stay_with_their_tracks_when_the_list_is_reordered(window, qapp):
    win, lib = window
    ids = [t.id for t in lib.tracks(sort="title")]
    win.disc_view.add_tracks(ids[:3])
    edit_cell(win, qapp, 0, 3, "First, Edited")
    disc_id = win.disc_view.current_disc_id()
    lib.move_in_disc(disc_id, 0, 2)
    win.disc_view.set_disc(disc_id)
    assert [disc_cell(win, r, 3) for r in range(3)] == [
        "Song 1",
        "Song 2",
        "First, Edited",
    ]


def test_the_disc_list_is_edited_by_double_clicking_or_the_edit_key(window):
    from PySide6.QtWidgets import QAbstractItemView

    win, _lib = window
    triggers = win.disc_view.table.editTriggers()
    assert triggers & QAbstractItemView.EditTrigger.DoubleClicked
    assert triggers & QAbstractItemView.EditTrigger.EditKeyPressed


def test_the_disc_view_no_longer_lists_bpm_or_key(window):
    win, _lib = window
    headers = [
        win.disc_view.model.headerData(c, Qt.Orientation.Horizontal)
        for c in range(win.disc_view.model.columnCount())
    ]
    assert headers == ["#", "Time", "Artist", "Title"]


# -- fixed-width columns -------------------------------------------------------


def test_columns_whose_content_has_a_known_size_are_fixed_width(window):
    from PySide6.QtWidgets import QHeaderView

    win, _lib = window
    header = win.track_table.horizontalHeader()
    for index, spec in enumerate(main_window.TRACK_COLUMNS):
        mode = header.sectionResizeMode(index)
        if spec.fixed:
            assert mode == QHeaderView.ResizeMode.Fixed, spec.title
        else:
            assert mode == QHeaderView.ResizeMode.Interactive, spec.title


def test_the_marker_and_the_preview_waveform_cannot_be_resized(window):
    _win, _lib = window
    fixed = {c.title for c in main_window.TRACK_COLUMNS if c.fixed}
    assert {"", "Preview"} <= fixed
    # the text columns, which want room, stay resizable
    assert not {"Title", "Artist", "Genre", "Album", "Comment", "Path"} & fixed


def test_a_fixed_column_keeps_its_width_when_the_window_is_resized(window, qapp):
    win, _lib = window
    win.show()
    preview = main_window.PREVIEW_COLUMN
    before = win.track_table.columnWidth(preview)
    win.resize(1700, 700)
    qapp.processEvents()
    win.resize(1100, 700)
    qapp.processEvents()
    assert (
        win.track_table.columnWidth(preview)
        == before
        == main_window.TRACK_COLUMNS[preview].width
    )
    assert win.track_table.columnWidth(0) == main_window.TRACK_COLUMNS[0].width


def test_help_menu_opens_the_log_folder(window, monkeypatch, tmp_path):
    win, _lib = window
    monkeypatch.setenv("DUBCRATE_LOG_DIR", str(tmp_path / "logs"))
    opened = []
    monkeypatch.setattr(
        main_window.QDesktopServices, "openUrl", lambda url: opened.append(url)
    )
    win.act_show_log.trigger()
    assert (tmp_path / "logs").is_dir()  # made, so there is something to open
    assert [u.toLocalFile() for u in opened] == [str(tmp_path / "logs")]


def dots(win):
    model = win.track_model
    return [model.data(model.index(r, 0)) for r in range(model.rowCount())]


def test_dots_say_open_disc_other_disc_or_no_disc(window):
    win, lib = window
    ids = [t.id for t in lib.tracks(sort="title")]  # the table is in this order
    first, second = lib.create_disc("First"), lib.create_disc("Second")
    lib.add_to_disc(first, ids[:2])
    lib.add_to_disc(second, ids[1:4])
    win.refresh_discs()
    win.open_disc(first)
    assert dots(win) == [
        "●",
        "●",
        "○",
        "○",
        "",
        "",
    ]  # on the open one; only on the other; on none
    win.open_disc(second)
    assert dots(win) == [
        "○",
        "●",
        "●",
        "●",
        "",
        "",
    ]  # (track 1 is on both: the open one wins)


def test_dots_follow_changes_to_the_open_disc_and_to_other_discs(window):
    win, lib = window
    ids = [t.id for t in lib.tracks(sort="title")]
    first, second = lib.create_disc("First"), lib.create_disc("Second")
    win.refresh_discs()
    win.open_disc(first)
    assert dots(win) == [""] * 6
    win.disc_view.add_tracks(
        ids[:1], disc_id=second
    )  # added to a disc that is not open
    assert dots(win) == ["○", "", "", "", "", ""]
    win.disc_view.add_tracks(ids[2:3])  # to the open one
    assert dots(win) == ["○", "", "●", "", "", ""]
    win.disc_view.delete_disc(
        second, ask=False
    )  # the other disc goes: its outline with it
    assert dots(win) == ["", "", "●", "", "", ""]
    lib.remove_from_disc(first, [0])
    win.disc_view.reload()
    assert dots(win) == [""] * 6


def test_with_no_disc_open_every_track_on_a_disc_is_an_outline(window):
    win, lib = window
    ids = [t.id for t in lib.tracks(sort="title")]
    disc = lib.create_disc("Only")
    lib.add_to_disc(disc, ids[:2])
    win.disc_view.set_disc(None)
    win._sync_disc_markers()
    assert dots(win) == ["○", "○", "", "", "", ""]
