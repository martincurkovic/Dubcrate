"""Saving memory cue edits into rekordbox's library as they are made (the player's MEMORY, ✕ and Move).

The edit is always kept in Dubcrate's own library first (`library.cue_edits`: instant, and never lost), so the
player answers at once. If the user has allowed it (`settings.get_rekordbox_editing`: asked the first time a
rekordbox track is edited, and remembered for good; Settings > Library changes it), `RekordboxEditor` then
writes the track's memory cues into `master.db` on a worker (`rekordbox_write.apply_write`: rekordbox must be
closed, the database is copied aside first (once a session: `keep_backup`), written in one transaction, read
back, and put back if wrong). When rekordbox has been re-read and has the cues, Dubcrate's copy of the edit is
dropped, so the track shows rekordbox's own cues again. If the write can't be done (rekordbox is open, say),
the edit simply stays in Dubcrate with a message, and the track's next edit tries again.
**Tried on synthetic databases, and the command line on the owner's library; this live path is not tried.**
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from dubcrate import rekordbox_write as rw
from dubcrate import settings
from dubcrate.library import Library
from dubcrate.library.models import Track
from dubcrate.library.store import path_key

from .cue_cache import RekordboxCueCache
from .tasks import TaskError, run_async

log = logging.getLogger(__name__)

Write = Callable[..., rw.WriteOutcome]


def ask_enable_editing() -> bool:
    """The first-edit question. True: save edits into rekordbox's library from now on (remembered)."""
    box = QMessageBox(QApplication.activeWindow())
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("Edit your rekordbox library?")
    box.setText("Save memory cue changes into your rekordbox library?")
    box.setInformativeText(
        "Dubcrate can write the memory cues you change here straight into rekordbox's library, so they show up"
        " in rekordbox and on your USB exports. Only memory cues are written, nothing else.\n\n"
        "rekordbox has to be closed when Dubcrate saves (otherwise the change waits in Dubcrate). Before the first"
        " change of each session Dubcrate copies rekordbox's database files aside, and it checks what it wrote and"
        " puts the copy back if anything is wrong.\n\n"
        "“Keep in Dubcrate” leaves rekordbox alone: changes stay in Dubcrate. You can change this later in"
        " Settings > Library."
    )
    enable = box.addButton("Enable Editing", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Keep in Dubcrate", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(enable)
    box.exec()
    return box.clickedButton() is enable


class RekordboxEditor(QObject):
    message = Signal(str)  # for the status bar
    settled = Signal()  # an edit was handed over to rekordbox: what is drawn from the cues can be asked again

    def __init__(
        self,
        lib: Library,
        cues: RekordboxCueCache,
        parent: QObject | None = None,
        *,
        write: Write = rw.apply_write,
        backup_dir: Callable[[], Path] = rw.default_backup_dir,
    ):
        super().__init__(parent)
        self._lib = lib
        self._cues = cues
        self._write = write
        self._backup_dir = backup_dir
        self._pending: dict[
            str, Path
        ] = {}  # path_key -> its database, edits not yet in rekordbox
        self._sent: dict[
            str, list[tuple[int, str]]
        ] = {}  # in the write that just finished
        self._sent_db: Path | None = None
        self._busy = False
        self._backed_up = False  # this session's backup exists
        cues.changed.connect(self._on_cues_changed)

    def allow(self, track: Track) -> bool:
        """Before an edit: whether it will go into rekordbox. Asks once (a rekordbox track, nothing chosen yet)."""
        if self._cues.database_for(track) is None:
            return False
        choice = settings.get_rekordbox_editing()
        if choice is None:
            choice = ask_enable_editing()
            settings.set_rekordbox_editing(choice)
        return choice

    def saved(self, track: Track) -> None:
        """After the edit is in Dubcrate's library: queue it for rekordbox if that is allowed."""
        database = self._cues.database_for(track)
        if database is None or not settings.get_rekordbox_editing():
            return
        self._pending[path_key(track.path)] = database
        self._start()

    def _start(self) -> None:
        if self._busy or not self._pending:
            return
        database = next(iter(self._pending.values()))
        batch = [k for k, d in self._pending.items() if d == database]
        have = self._lib.memory_cue_sets()
        edits = {k: have[k] for k in batch if k in have}
        for k in batch:
            if k not in edits:  # reset since: nothing to write
                del self._pending[k]
        if not edits:
            return self._start()
        keep = not self._backed_up
        self._busy = True
        write, backup_dir = self._write, self._backup_dir()

        def job() -> rw.WriteOutcome:
            try:
                return write(database, edits, backup_dir, keep_backup=keep)
            except rw.WriteError as exc:
                raise TaskError(str(exc)) from exc

        run_async(
            job,
            lambda outcome: self._done(database, edits, outcome, keep),
            lambda text: self._failed(edits, text),
        )

    def _done(
        self,
        database: Path,
        edits: Mapping[str, Sequence[tuple[int, str]]],
        outcome: rw.WriteOutcome,
        kept: bool,
    ) -> None:
        self._busy = False
        if kept and outcome.backup is not None:
            self._backed_up = True
        for key, why in outcome.plan.skipped:
            self._pending.pop(key, None)  # not in rekordbox: stays an edit in Dubcrate
            log.info("rekordbox edit of %s stays in Dubcrate: %s", key, why)
        done = set(outcome.done_keys)
        self._sent = {k: list(edits[k]) for k in edits if k in done}
        self._sent_db = database
        if not self._sent:
            return self._start()
        if outcome.written:
            self.message.emit("Memory cues saved to rekordbox")
        self._cues.refresh(database)

    def _failed(
        self, edits: Mapping[str, Sequence[tuple[int, str]]], text: str
    ) -> None:
        self._busy = False
        for key in edits:
            self._pending.pop(key, None)
        log.warning("memory cues not saved to rekordbox: %s", text)
        self.message.emit(f"Kept in Dubcrate only, not saved to rekordbox: {text}")
        self._start()

    def _on_cues_changed(self) -> None:
        """rekordbox's cues were read again: once that is after our write, drop Dubcrate's copies."""
        if not self._sent or self._sent_db is None:
            return
        if not self._cues.is_current(self._sent_db):
            self._cues.refresh(
                self._sent_db
            )  # that read began before the write finished
            return
        sent, self._sent = self._sent, {}
        have = self._lib.memory_cue_sets()
        for key, cues in sent.items():
            if have.get(key) == cues:  # not edited again meanwhile
                self._lib.clear_memory_cue_key(key)
                self._pending.pop(key, None)
        self.settled.emit()
        self._start()
