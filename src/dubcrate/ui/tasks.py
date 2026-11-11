"""Run blocking work on a worker thread and get the result back on the GUI thread.

Adapted from akaiutil-GUI's tasks.py. Callbacks are delivered through slots of a
QObject that lives in the GUI thread, which is what makes Qt queue them across
threads (a bare lambda connected to a signal would run on the worker thread).

A task must never touch the SQLite library: that connection belongs to the GUI
thread. Tasks compute (scan, probe, read a rekordbox snapshot); the GUI thread
stores the result.
"""

import logging
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

log = logging.getLogger(__name__)
_live: set["_Relay"] = set()  # keeps relays alive until their result arrives


class TaskError(RuntimeError):
    """Raise from a task for a failure whose message is already written for the user."""


class _Relay(QObject):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)

    def __init__(self, on_done, on_error, on_progress):
        super().__init__()
        self._on_done = on_done
        self._on_error = on_error
        self._on_progress = on_progress
        self.done.connect(self._finish_ok)
        self.failed.connect(self._finish_err)
        self.progress.connect(self._report)

    @Slot(object)
    def _finish_ok(self, result):
        _live.discard(self)
        if self._on_done is not None:
            self._on_done(result)

    @Slot(str)
    def _finish_err(self, message):
        _live.discard(self)
        if self._on_error is not None:
            self._on_error(message)

    @Slot(int, int)
    def _report(self, done, total):
        if self._on_progress is not None:
            self._on_progress(done, total)


class _Task(QRunnable):
    def __init__(self, fn: Callable[..., Any], relay: _Relay, with_progress: bool):
        super().__init__()
        self._fn = fn
        self._relay = relay
        self._with_progress = with_progress

    def run(self):
        try:
            if self._with_progress:
                result = self._fn(self._relay.progress.emit)
            else:
                result = self._fn()
        except Exception as e:  # noqa: BLE001 - reported to the UI, never allowed to kill the pool thread
            if isinstance(e, TaskError):
                log.warning("task failed: %s", e)
            else:
                log.error(
                    "task failed with an unexpected %s:\n%s",
                    e.__class__.__name__,
                    traceback.format_exc(),
                )
            self._relay.failed.emit(str(e) or e.__class__.__name__)
        else:
            self._relay.done.emit(result)


def run_async(
    fn: Callable[..., Any],
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> None:
    """Call from the GUI thread. The callbacks also run on the GUI thread.

    With `on_progress`, `fn` is called with one argument, `report(done, total)`.
    """
    relay = _Relay(on_done, on_error, on_progress)
    _live.add(relay)
    QThreadPool.globalInstance().start(_Task(fn, relay, on_progress is not None))
