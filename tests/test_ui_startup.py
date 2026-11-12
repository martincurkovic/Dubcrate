"""Bringing the rekordbox library up to date while the splash screen is showing."""

import threading

import pytest

from dubcrate import settings
from dubcrate.library import Library, NodeRecord, TrackRecord
from dubcrate.library.rekordbox import RekordboxError, RekordboxLibrary
from dubcrate.ui import startup
from dubcrate.ui.startup import StartupImport, maybe_refresh, refresh_rekordbox_sources


def rec(ext_id, title=None) -> TrackRecord:
    return TrackRecord(
        ext_id=ext_id,
        path=f"/nonexistent/{ext_id}.mp3",
        title=title or f"Song {ext_id}",
    )


@pytest.fixture
def db_file(tmp_path):
    path = tmp_path / "master.db"
    path.write_bytes(b"not a real database: reading it is faked")
    return path


@pytest.fixture
def lib(db_file):
    with Library.open(":memory:") as library:
        library.replace_source(
            "rekordbox", str(db_file), "rekordbox", [rec("a"), rec("b")]
        )
        yield library


@pytest.fixture
def fake_read(monkeypatch):
    """Replace the rekordbox reader; the test says what it returns (or raises)."""
    state = {
        "tracks": [rec("a"), rec("b"), rec("c")],
        "error": None,
        "calls": [],
        "threads": [],
    }

    def read(path, progress=None):
        state["calls"].append(path)
        state["threads"].append(threading.current_thread())
        if state["error"]:
            raise state["error"]
        return RekordboxLibrary(
            source=path,
            tracks=state["tracks"],
            nodes=[NodeRecord("p", None, "Playlist", False, ("a",))],
        )

    monkeypatch.setattr(startup, "read_master_db", read)
    return state


def titles(lib):
    return sorted(t.title for t in lib.tracks())


# -- what it does -----------------------------------------------------------------


def test_new_tracks_are_brought_in_and_old_ones_keep_their_ids(qapp, lib, fake_read):
    before = {t.ext_id: t.id for t in lib.tracks()}
    result = refresh_rekordbox_sources(lib, lambda _s: None)
    assert titles(lib) == ["Song a", "Song b", "Song c"]
    assert {
        t.ext_id: t.id for t in lib.tracks() if t.ext_id in before
    } == before  # stable ids
    assert (result.added, result.removed, result.notes) == (1, 0, [])


def test_tracks_deleted_in_rekordbox_are_dropped_unless_a_disc_uses_them(
    qapp, lib, fake_read
):
    disc = lib.create_disc("Night")
    on_disc = next(t.id for t in lib.tracks() if t.ext_id == "b")
    lib.add_to_disc(disc, [on_disc])
    fake_read["tracks"] = [rec("a")]  # b is gone from rekordbox, but a disc has it
    result = refresh_rekordbox_sources(lib, lambda _s: None)
    assert lib.disc(disc).track_count == 1  # the disc keeps its track
    assert next(t for t in lib.tracks(include_stale=True) if t.ext_id == "b").stale
    assert any("no longer in the source" in n or "kept" in n for n in result.notes)


def test_the_status_line_follows_the_steps(qapp, lib, fake_read):
    said = []
    refresh_rekordbox_sources(lib, said.append)
    assert said == ["Reading the rekordbox library…", "Updating your library…"]


def test_the_reading_is_done_off_the_gui_thread(qapp, lib, fake_read):
    refresh_rekordbox_sources(lib, lambda _s: None)
    assert (
        fake_read["threads"] and fake_read["threads"][0] is not threading.main_thread()
    )


def test_the_playlists_are_brought_up_to_date_too(qapp, lib, fake_read):
    refresh_rekordbox_sources(lib, lambda _s: None)
    [source] = lib.sources()
    assert [n.name for n in lib.nodes(source.id)] == ["Playlist"]


def test_every_rekordbox_source_is_updated(qapp, lib, fake_read, tmp_path):
    other = tmp_path / "other" / "master.db"
    other.parent.mkdir()
    other.write_bytes(b"x")
    lib.replace_source("rekordbox", str(other), "second", [rec("z")])
    refresh_rekordbox_sources(lib, lambda _s: None)
    assert len(fake_read["calls"]) == 2


# -- what it leaves alone ----------------------------------------------------------


def test_nothing_is_read_when_nothing_was_imported_yet(qapp, fake_read):
    with Library.open(":memory:") as empty:
        result = refresh_rekordbox_sources(empty, lambda _s: None)
        assert fake_read["calls"] == [] and result.message() == ""
        assert empty.sources() == []  # it never imports a library on its own


def test_folder_sources_are_not_rescanned(qapp, fake_read, db_file):
    # (the folder source's path is a real file here, so that only its kind can stop it being read)
    with Library.open(":memory:") as library:
        library.replace_source("folder", str(db_file), "Music", [rec("m")])
        refresh_rekordbox_sources(library, lambda _s: None)
        assert fake_read["calls"] == []
        assert titles(library) == ["Song m"]


# -- when it can't -------------------------------------------------------------------


def test_a_missing_library_file_is_skipped_with_a_note_and_nothing_is_lost(
    qapp, lib, fake_read, db_file
):
    db_file.unlink()
    result = refresh_rekordbox_sources(lib, lambda _s: None)
    assert fake_read["calls"] == []
    assert titles(lib) == ["Song a", "Song b"]
    assert len(result.notes) == 1 and "wasn't found" in result.notes[0]


def test_a_library_that_cant_be_read_is_skipped_with_the_reason(qapp, lib, fake_read):
    fake_read["error"] = RekordboxError("could not open it: file is locked")
    result = refresh_rekordbox_sources(lib, lambda _s: None)
    assert titles(lib) == ["Song a", "Song b"]
    assert (
        "file is locked" in result.notes[0] and "couldn't be updated" in result.notes[0]
    )


def test_an_unexpected_failure_is_reported_not_raised(qapp, lib, fake_read):
    fake_read["error"] = ValueError("something odd")
    result = refresh_rekordbox_sources(lib, lambda _s: None)
    assert titles(lib) == ["Song a", "Song b"] and "something odd" in result.notes[0]


def test_an_empty_answer_never_empties_your_library(qapp, lib, fake_read):
    fake_read[
        "tracks"
    ] = []  # rekordbox "returned" nothing: far more likely a problem than intent
    result = refresh_rekordbox_sources(lib, lambda _s: None)
    assert titles(lib) == ["Song a", "Song b"]
    assert "no tracks" in result.notes[0] and "kept" in result.notes[0]


def test_an_empty_library_staying_empty_is_fine(qapp, fake_read, db_file):
    with Library.open(":memory:") as library:
        library.replace_source("rekordbox", str(db_file), "rekordbox", [])
        fake_read["tracks"] = []
        result = refresh_rekordbox_sources(library, lambda _s: None)
        assert result.notes == []


def test_one_source_failing_does_not_stop_the_next(qapp, lib, fake_read, tmp_path):
    bad = tmp_path / "bad" / "master.db"
    bad.parent.mkdir()
    bad.write_bytes(b"x")
    lib.replace_source("rekordbox", str(bad), "bad one", [rec("z")])

    def flaky(path, progress=None):
        if "bad" in str(path):
            raise RekordboxError("broken")
        return RekordboxLibrary(
            source=path, tracks=[rec("a"), rec("b"), rec("c")], nodes=[]
        )

    startup.read_master_db = flaky
    result = refresh_rekordbox_sources(lib, lambda _s: None)
    assert "Song c" in titles(lib) and len(result.notes) == 1


# -- the message ----------------------------------------------------------------------


def test_nothing_changed_means_nothing_to_say():
    assert StartupImport().message() == ""


def test_the_message_says_what_came_in_and_what_went():
    assert StartupImport(added=3).message() == "Brought in 3 new tracks from rekordbox"
    assert StartupImport(added=1).message() == "Brought in 1 new track from rekordbox"
    assert (
        StartupImport(removed=2).message()
        == "Removed 2 tracks that are gone from rekordbox"
    )
    both = StartupImport(added=4, removed=1).message()
    assert (
        both == "Brought in 4 new tracks from rekordbox; removed 1 track that is gone"
    )


def test_problems_are_in_the_message_too():
    msg = StartupImport(
        added=2, notes=["“rekordbox” couldn't be updated: locked"]
    ).message()
    assert (
        msg.startswith("Brought in 2 new tracks")
        and "couldn't be updated: locked" in msg
    )
    assert StartupImport(notes=["a", "b"]).message() == "a; b"


# -- whether it runs -------------------------------------------------------------------


def test_it_runs_when_asked_to_and_the_setting_is_on(qapp, lib, fake_read):
    assert maybe_refresh(lib, lambda _s: None) is not None
    assert len(fake_read["calls"]) == 1


def test_it_does_not_run_when_the_setting_is_off(qapp, lib, fake_read):
    settings.set_auto_import(False)
    assert maybe_refresh(lib, lambda _s: None) is None
    assert fake_read["calls"] == []


def test_it_does_not_run_when_told_not_to_on_the_command_line(qapp, lib, fake_read):
    assert maybe_refresh(lib, lambda _s: None, skip=True) is None
    assert fake_read["calls"] == []


# -- the whole thing, as the app starts --------------------------------------------


def run_app(tmp_path, *extra):
    """Start the app (a quick run) against tmp_path/lib.db, with rekordbox's reader faked;
    returns the text it printed from inside the running app."""
    import os
    import subprocess
    import sys

    # (macOS keeps QSettings in its preferences, whatever HOME says: send them to the temp folder
    # so the run never reads the real app's saved tab or pins, or writes to them)
    code = (
        "import sys\n"
        "from PySide6.QtCore import QSettings, QTimer\n"
        "QSettings.setDefaultFormat(QSettings.Format.IniFormat)\n"
        "QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,\n"
        f"                  {str(tmp_path / 'settings')!r})\n"
        "from PySide6.QtWidgets import QApplication\n"
        "import dubcrate.ui.app as app\n"
        "import dubcrate.ui.startup as startup\n"
        "from dubcrate.library import TrackRecord\n"
        "from dubcrate.library.rekordbox import RekordboxLibrary\n"
        "startup.read_master_db = lambda path, progress=None: RekordboxLibrary(\n"
        "    source=path, tracks=[TrackRecord(ext_id='1', path='/x/1.mp3', title='Old One'),\n"
        "                         TrackRecord(ext_id='2', path='/x/2.mp3', title='Added At Startup')])\n"
        "def show():\n"
        "    names = {type(w).__name__: w for w in QApplication.topLevelWidgets()}\n"
        "    print('STATUS:', names['MainWindow'].statusBar().currentMessage())\n"
        "    print('SPLASH VISIBLE:', any(w.isVisible() for n, w in names.items() if n == 'StartupSplash'))\n"
        "    QApplication.quit()\n"
        "run = QApplication.exec\n"
        "QApplication.exec = staticmethod(lambda: (QTimer.singleShot(200, show), run())[1])\n"
        f"sys.exit(app.main(['--library', {str(tmp_path / 'lib.db')!r}, *{list(extra)!r}]))\n"
    )
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": "offscreen",
        "DUBCRATE_DATA_DIR": str(tmp_path / "data"),
        "HOME": str(tmp_path),
    }
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def app_library(tmp_path):
    master = tmp_path / "master.db"
    master.write_bytes(b"x")
    with Library.open(tmp_path / "lib.db") as library:
        library.replace_source(
            "rekordbox",
            str(master),
            "rekordbox",
            [TrackRecord(ext_id="1", path="/x/1.mp3", title="Old One")],
        )
    return tmp_path


def stored_titles(tmp_path):
    with Library.open(tmp_path / "lib.db") as library:
        return sorted(t.title for t in library.tracks())


def test_starting_the_app_brings_in_new_rekordbox_tracks_and_says_so(app_library):
    out = run_app(app_library)
    assert stored_titles(app_library) == ["Added At Startup", "Old One"]
    assert "STATUS: Brought in 1 new track from rekordbox" in out
    assert (
        "SPLASH VISIBLE: False" in out
    )  # and the splash is gone once the window is up


def test_no_import_on_the_command_line_starts_without_updating(app_library):
    out = run_app(app_library, "--no-import")
    assert stored_titles(app_library) == ["Old One"]
    assert "STATUS:" in out and "Brought in" not in out


def test_a_first_run_with_nothing_imported_reads_nothing(tmp_path):
    out = run_app(tmp_path)
    assert stored_titles(tmp_path) == []  # it never imports a library on its own
    assert "Brought in" not in out
