"""Memory cue edits saved into rekordbox as they are made: the question, the worker, and what happens after."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal

from dubcrate import rekordbox_write as rw
from dubcrate import settings
from dubcrate.library import Library
from dubcrate.library.store import path_key
from dubcrate.ui import rekordbox_editor as editor_module
from dubcrate.ui.rekordbox_editor import RekordboxEditor

DB = Path("/rb/master.db")
IN_RB = SimpleNamespace(path="/m/a.mp3")
NOT_IN_RB = SimpleNamespace(path="/m/b.mp3")


class FakeCache(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.refreshed = 0

    def database_for(self, track):
        return DB if track is IN_RB else None

    def is_current(self, database):
        return True

    def refresh(self, database):
        self.refreshed += 1
        self.changed.emit()


class FakeWrite:
    """Stands in for `apply_write`: records the calls, and can fail."""

    def __init__(self, fail: str | None = None):
        self.calls: list[tuple[dict, bool]] = []
        self.fail = fail

    def __call__(self, db, edits, backup_dir, *, keep_backup=True, **_):
        self.calls.append(({k: list(v) for k, v in edits.items()}, keep_backup))
        if self.fail:
            raise rw.WriteError(self.fail)
        plan = rw.WritePlan(changes=[SimpleNamespace(key=k) for k in edits])
        return rw.WriteOutcome(
            plan, Path("/bk") if keep_backup else None, written=list(edits)
        )


@pytest.fixture
def lib(tmp_path):
    return Library.open(tmp_path / "lib.db")


def make(lib, write=None):
    cache = FakeCache()
    editor = RekordboxEditor(
        lib, cache, write=write or FakeWrite(), backup_dir=lambda: Path("/bk")
    )
    seen = SimpleNamespace(messages=[], settled=0)
    editor.message.connect(seen.messages.append)
    editor.settled.connect(lambda: setattr(seen, "settled", seen.settled + 1))
    return editor, cache, seen


def test_a_track_outside_rekordbox_is_never_asked_about_or_written(lib):
    editor, _, _ = make(lib)
    assert editor.allow(NOT_IN_RB) is False
    lib.set_memory_cues(NOT_IN_RB.path, [(1000, "")])
    settings.set_rekordbox_editing(True)
    editor.saved(NOT_IN_RB)
    assert editor._write.calls == []


def test_the_first_edit_asks_once_and_remembers_yes(lib, monkeypatch):
    asked = []
    monkeypatch.setattr(
        editor_module, "ask_enable_editing", lambda: asked.append(1) or True
    )
    editor, _, _ = make(lib)
    assert settings.get_rekordbox_editing() is None
    assert editor.allow(IN_RB) is True
    assert editor.allow(IN_RB) is True
    assert asked == [1]
    assert settings.get_rekordbox_editing() is True


def test_declining_is_remembered_and_nothing_is_written(lib, monkeypatch, qapp):
    monkeypatch.setattr(editor_module, "ask_enable_editing", lambda: False)
    editor, _, _ = make(lib)
    assert editor.allow(IN_RB) is False
    assert settings.get_rekordbox_editing() is False
    lib.set_memory_cues(IN_RB.path, [(1000, "")])
    editor.saved(IN_RB)
    qapp.processEvents()
    assert editor._write.calls == []
    assert lib.memory_cues(IN_RB.path) == [(1000, "")]


def test_an_edit_is_written_then_dropped_from_dubcrate(lib, wait_for):
    settings.set_rekordbox_editing(True)
    editor, cache, seen = make(lib)
    lib.set_memory_cues(IN_RB.path, [(1000, "a"), (5000, "")])
    editor.saved(IN_RB)
    assert wait_for(lambda: seen.settled == 1)
    assert editor._write.calls == [
        ({path_key(IN_RB.path): [(1000, "a"), (5000, "")]}, True)
    ]
    assert (
        lib.memory_cues(IN_RB.path) is None
    )  # rekordbox has them: Dubcrate's copy is gone
    assert seen.messages == ["Memory cues saved to rekordbox"]
    assert cache.refreshed == 1


def test_only_the_first_write_of_a_session_keeps_a_backup(lib, wait_for):
    settings.set_rekordbox_editing(True)
    editor, _, seen = make(lib)
    for n in (1, 2):
        lib.set_memory_cues(IN_RB.path, [(n * 1000, "")])
        editor.saved(IN_RB)
        assert wait_for(lambda n=n: seen.settled == n)
    assert [keep for _, keep in editor._write.calls] == [True, False]


def test_a_failed_write_keeps_the_edit_and_says_why(lib, wait_for):
    settings.set_rekordbox_editing(True)
    editor, _, seen = make(
        lib, FakeWrite(fail="rekordbox is running: close it first, then try again")
    )
    lib.set_memory_cues(IN_RB.path, [(1000, "")])
    editor.saved(IN_RB)
    assert wait_for(lambda: bool(seen.messages))
    assert "running" in seen.messages[0] and "Kept in Dubcrate" in seen.messages[0]
    assert lib.memory_cues(IN_RB.path) == [(1000, "")]
    assert seen.settled == 0


def test_an_edit_made_while_writing_is_written_too(lib, wait_for):
    settings.set_rekordbox_editing(True)
    editor, _cache, seen = make(lib)
    lib.set_memory_cues(IN_RB.path, [(1000, "")])
    editor.saved(IN_RB)  # the worker is now busy
    lib.set_memory_cues(IN_RB.path, [(1000, ""), (2000, "")])
    editor.saved(IN_RB)
    assert wait_for(lambda: len(editor._write.calls) == 2 and seen.settled == 2)
    assert editor._write.calls[-1][0] == {
        path_key(IN_RB.path): [(1000, ""), (2000, "")]
    }
    assert lib.memory_cues(IN_RB.path) is None


def test_the_setting_is_a_checkbox_in_the_settings_window(qapp):
    from dubcrate.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog()
    assert dialog.rekordbox_edit_check.isChecked() is False
    dialog.rekordbox_edit_check.setChecked(True)
    assert settings.get_rekordbox_editing() is True
    dialog.rekordbox_edit_check.setChecked(False)
    assert settings.get_rekordbox_editing() is False
