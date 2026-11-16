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
        self.loop_calls: list[dict] = []  # the loops of each call
        self.fail = fail

    def __call__(self, db, edits, backup_dir, *, keep_backup=True, loops=None, **_):
        self.calls.append(({k: list(v) for k, v in edits.items()}, keep_backup))
        self.loop_calls.append({k: list(v) for k, v in (loops or {}).items()})
        if self.fail:
            raise rw.WriteError(self.fail)
        keys = list(dict.fromkeys([*edits, *(loops or {})]))
        plan = rw.WritePlan(changes=[SimpleNamespace(key=k) for k in keys])
        return rw.WriteOutcome(plan, Path("/bk") if keep_backup else None, written=keys)


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
    assert seen.messages == []  # (a good save is silent; only a problem is said)
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


def waiting_editor(lib, write=None):
    editor, cache, seen = make(lib, write)
    editor.database = lambda: DB  # (the one imported rekordbox library)
    return editor, cache, seen


def test_waiting_edits_are_written_in_one_go_and_the_result_is_said(lib, wait_for):
    settings.set_rekordbox_editing(True)
    editor, _, seen = waiting_editor(lib)
    lib.set_memory_cues("/m/a.mp3", [(1000, "")])
    lib.set_memory_cues("/m/c.mp3", [(2000, "x")])
    assert editor.waiting() == 2
    assert editor.write_waiting() is True
    assert wait_for(lambda: lib.memory_cue_sets() == {})
    assert len(editor._write.calls) == 1 and len(editor._write.calls[0][0]) == 2
    assert wait_for(lambda: seen.messages[-1].startswith("Saved the memory cues of 2"))
    assert seen.messages[0] == "Saving waiting memory cues to rekordbox…"


def test_waiting_edits_of_files_not_in_rekordbox_stay_and_are_counted(lib, wait_for):
    settings.set_rekordbox_editing(True)

    def write(db, edits, backup_dir, *, keep_backup=True, **_):
        keys = list(edits)
        plan = rw.WritePlan(
            changes=[SimpleNamespace(key=keys[0])],
            skipped=[(k, "not in rekordbox") for k in keys[1:]],
        )
        return rw.WriteOutcome(plan, None, written=keys[:1])

    editor, _, seen = waiting_editor(lib, write)
    lib.set_memory_cues("/m/a.mp3", [(1000, "")])
    lib.set_memory_cues("/m/b.mp3", [(2000, "")])
    editor.write_waiting()
    assert wait_for(
        lambda: seen.messages[-1].endswith("(1 not in rekordbox stay in Dubcrate)")
    )
    assert seen.messages[-1].startswith("Saved the memory cues of 1 track ")
    assert len(lib.memory_cue_sets()) == 1


def test_waiting_edits_that_rekordbox_already_has_say_so(lib, wait_for):
    settings.set_rekordbox_editing(True)

    def write(db, edits, backup_dir, *, keep_backup=True, **_):
        return rw.WriteOutcome(rw.WritePlan(unchanged=list(edits)), None)

    editor, _, seen = waiting_editor(lib, write)
    lib.set_memory_cues("/m/a.mp3", [(1000, "")])
    editor.write_waiting()
    assert wait_for(lambda: seen.messages[-1] == "rekordbox already has these memory cues")  # fmt: skip
    assert lib.memory_cue_sets() == {}


def test_waiting_edits_that_fail_keep_their_failure_message(lib, wait_for):
    settings.set_rekordbox_editing(True)
    editor, _, seen = waiting_editor(lib, FakeWrite(fail="rekordbox is running: close it first"))  # fmt: skip
    lib.set_memory_cues("/m/a.mp3", [(1000, "")])
    editor.write_waiting()
    assert wait_for(lambda: len(seen.messages) == 2)
    assert "Kept in Dubcrate only" in seen.messages[-1] and "running" in seen.messages[-1]  # fmt: skip
    assert len(lib.memory_cue_sets()) == 1


def test_write_waiting_with_nothing_waiting_or_no_library_writes_nothing(lib, qapp):
    settings.set_rekordbox_editing(True)
    editor, _, seen = make(lib)  # (no rekordbox library imported)
    lib.set_memory_cues("/m/a.mp3", [(1000, "")])
    assert editor.write_waiting() is False
    assert seen.messages == ["No rekordbox library has been imported"]
    editor.database = lambda: DB
    lib.clear_memory_cue_key(path_key("/m/a.mp3"))
    assert editor.write_waiting() is False
    assert seen.messages[-1] == "No memory cues are waiting in Dubcrate"
    assert editor._write.calls == []


def test_write_waiting_asks_again_when_editing_was_turned_off(lib, monkeypatch, qapp, wait_for):  # fmt: skip
    asked = []
    monkeypatch.setattr(editor_module, "ask_enable_editing", lambda: asked.append(1) or False)  # fmt: skip
    settings.set_rekordbox_editing(False)
    editor, _, _ = waiting_editor(lib)
    lib.set_memory_cues("/m/a.mp3", [(1000, "")])
    assert editor.write_waiting() is False
    assert asked == [1] and settings.get_rekordbox_editing() is False
    assert editor._write.calls == []
    monkeypatch.setattr(editor_module, "ask_enable_editing", lambda: True)
    assert editor.write_waiting() is True
    assert settings.get_rekordbox_editing() is True
    assert wait_for(lambda: lib.memory_cue_sets() == {})


# -- loops saved in Dubcrate (`WRITE_LOOPS`: off until the command line has been tried live) --------------------


def test_loops_are_not_sent_to_rekordbox_while_the_switch_is_off(lib, qapp):
    assert RekordboxEditor.WRITE_LOOPS is False  # (the shipped default)
    settings.set_rekordbox_editing(True)
    editor, _, _ = make(lib)
    lib.set_memory_loops(IN_RB.path, [(1000, 3000, "")])
    editor.saved(IN_RB)
    qapp.processEvents()
    assert editor._write.calls == [] and editor.waiting() == 0
    assert lib.memory_loops(IN_RB.path) == [(1000, 3000, "")]


def test_a_saved_loop_is_written_then_dropped_from_dubcrate(lib, wait_for, monkeypatch):
    monkeypatch.setattr(RekordboxEditor, "WRITE_LOOPS", True)
    settings.set_rekordbox_editing(True)
    editor, cache, seen = make(lib)
    lib.set_memory_loops(IN_RB.path, [(1000, 3000, "x")])
    editor.saved(IN_RB)
    assert wait_for(lambda: seen.settled == 1)
    assert editor._write.calls == [({}, True)]  # (no memory cue edits)
    assert editor._write.loop_calls == [{path_key(IN_RB.path): [(1000, 3000, "x")]}]
    assert (
        lib.memory_loops(IN_RB.path) == []
    )  # rekordbox has it: Dubcrate's copy is gone
    assert seen.messages == [] and cache.refreshed == 1


def test_cue_edits_and_loops_go_in_one_write_and_both_are_dropped(
    lib, wait_for, monkeypatch
):
    monkeypatch.setattr(RekordboxEditor, "WRITE_LOOPS", True)
    settings.set_rekordbox_editing(True)
    editor, _, seen = make(lib)
    lib.set_memory_cues(IN_RB.path, [(500, "")])
    lib.set_memory_loops(IN_RB.path, [(1000, 3000, "")])
    editor.saved(IN_RB)
    assert wait_for(lambda: seen.settled == 1)
    assert len(editor._write.calls) == 1
    assert editor._write.calls[0][0] == {path_key(IN_RB.path): [(500, "")]}
    assert editor._write.loop_calls[0] == {path_key(IN_RB.path): [(1000, 3000, "")]}
    assert lib.memory_cues(IN_RB.path) is None and lib.memory_loops(IN_RB.path) == []


def test_a_loop_saved_while_writing_is_kept_and_written_next(
    lib, wait_for, monkeypatch
):
    monkeypatch.setattr(RekordboxEditor, "WRITE_LOOPS", True)
    settings.set_rekordbox_editing(True)
    editor, _, seen = make(lib)
    lib.set_memory_loops(IN_RB.path, [(1000, 3000, "")])
    editor.saved(IN_RB)  # the worker is now busy
    lib.set_memory_loops(IN_RB.path, [(1000, 3000, ""), (5000, 7000, "")])
    editor.saved(IN_RB)
    assert wait_for(lambda: len(editor._write.calls) == 2 and seen.settled == 2)
    assert editor._write.loop_calls[-1] == {
        path_key(IN_RB.path): [(1000, 3000, ""), (5000, 7000, "")]
    }
    assert lib.memory_loops(IN_RB.path) == []


def test_a_failed_loop_write_keeps_the_loop_in_dubcrate_and_says_why(
    lib, wait_for, monkeypatch
):
    monkeypatch.setattr(RekordboxEditor, "WRITE_LOOPS", True)
    settings.set_rekordbox_editing(True)
    editor, _, seen = make(lib, FakeWrite(fail="rekordbox is running: close it first"))
    lib.set_memory_loops(IN_RB.path, [(1000, 3000, "")])
    editor.saved(IN_RB)
    assert wait_for(lambda: bool(seen.messages))
    assert "Kept in Dubcrate only" in seen.messages[0]
    assert lib.memory_loops(IN_RB.path) == [(1000, 3000, "")] and seen.settled == 0


def test_waiting_loops_count_and_are_written_with_the_waiting_edits(
    lib, wait_for, monkeypatch
):
    monkeypatch.setattr(RekordboxEditor, "WRITE_LOOPS", True)
    settings.set_rekordbox_editing(True)
    editor, _, seen = waiting_editor(lib)
    lib.set_memory_cues("/m/a.mp3", [(1000, "")])
    lib.set_memory_loops("/m/c.mp3", [(2000, 4000, "")])
    assert editor.waiting() == 2
    assert editor.write_waiting() is True
    assert wait_for(
        lambda: lib.memory_loop_sets() == {} and lib.memory_cue_sets() == {}
    )
    assert len(editor._write.calls) == 1
    assert set(editor._write.loop_calls[0]) == {path_key("/m/c.mp3")}
    assert wait_for(lambda: seen.messages[-1].startswith("Saved the memory cues of 2"))


def test_the_first_edit_question_names_loops_only_when_they_are_written(monkeypatch):
    heading, explanation = editor_module.enable_editing_text()
    assert heading == "Save memory cue changes into your rekordbox library?"
    assert "loops" not in heading + explanation
    monkeypatch.setattr(RekordboxEditor, "WRITE_LOOPS", True)
    heading, explanation = editor_module.enable_editing_text()
    assert heading == "Save memory cue and loop changes into your rekordbox library?"
    assert "memory cues and loops" in explanation


def test_the_setting_is_a_checkbox_in_the_settings_window(qapp):
    from dubcrate.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog()
    assert dialog.rekordbox_edit_check.isChecked() is False
    dialog.rekordbox_edit_check.setChecked(True)
    assert settings.get_rekordbox_editing() is True
    dialog.rekordbox_edit_check.setChecked(False)
    assert settings.get_rekordbox_editing() is False
