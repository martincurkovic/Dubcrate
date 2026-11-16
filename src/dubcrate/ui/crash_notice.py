"""A message for an unexpected error: something broke that nobody planned for.

`logsetup.install_exception_hooks` writes every uncaught exception (on any thread, or from a Qt slot) to the log with
its traceback. Until now that was all: the user saw nothing, and a button that did nothing looked broken. This tells
them too, in plain words, once (not once per repeat of a failing paint): what happened in a line, that it is in the
log, and a button for the log folder. **Expected errors are not this**: a missing drive or an unreadable file is a
`TaskError` with its own message, and never reaches here.

`CrashNotice.report` may be called on any thread; the message is shown on the GUI thread, after the slot that failed
has returned. A new message waits while one is open and for `MIN_GAP_SECONDS` after, so a repeating error can't bury
the window (every one is still logged).
"""

import time
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QMessageBox, QWidget

from dubcrate import logsetup

MIN_GAP_SECONDS = 5.0
MAX_TEXT = 300


def describe(kind: object, value: object) -> str:
    """The error in one line, as `ValueError: what it said`, trimmed."""
    name = getattr(kind, "__name__", str(kind))
    said = " ".join(str(value).split())
    line = f"{name}: {said}" if said else name
    return line if len(line) <= MAX_TEXT else line[:MAX_TEXT] + "…"


def ask_about_error(parent: QWidget | None, text: str) -> bool:
    """The real message. True: the user chose Show Log File."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Something went wrong")
    box.setText("Dubcrate ran into an unexpected error.")
    box.setInformativeText(
        f"{text}\n\nIt has been written to the log. Dubcrate should keep working, but if something"
        " doesn't look right, restart it."
    )
    show = box.addButton("Show Log File", QMessageBox.ButtonRole.ActionRole)
    ok = box.addButton("OK", QMessageBox.ButtonRole.AcceptRole)
    box.setDefaultButton(ok)
    box.exec()
    return box.clickedButton() is show


class CrashNotice(QObject):
    _raised = Signal(str)

    def __init__(
        self,
        show_log: Callable[[], None],
        parent: QWidget | None = None,
        *,
        ask: Callable[[QWidget | None, str], bool] = ask_about_error,
        clock: Callable[[], float] = time.monotonic,
    ):
        super().__init__(parent)
        self._window = parent
        self._show_log = show_log
        self._ask = ask
        self._clock = clock
        self._open = False
        self._last: float | None = None
        self.suppressed = (
            0  # messages not shown (one was open or just closed): they are in the log
        )
        # queued even on the GUI thread: the message must not open inside the slot that failed
        self._raised.connect(self._show, Qt.ConnectionType.QueuedConnection)

    def report(self, kind: object, value: object) -> None:
        """An uncaught exception (any thread): say so, unless a message was just shown."""
        self._raised.emit(describe(kind, value))

    def _show(self, text: str) -> None:
        now = self._clock()
        if self._open or (
            self._last is not None and now - self._last < MIN_GAP_SECONDS
        ):
            self.suppressed += 1
            return
        self._open = True
        try:
            if self._ask(self._window, text):
                self._show_log()
        finally:
            self._open = False
            self._last = self._clock()


def install(window) -> CrashNotice:
    """Show a message for every uncaught exception from now on (until `uninstall`)."""
    notice = CrashNotice(window.show_log_file, window)
    logsetup.set_uncaught_listener(notice.report)
    return notice


def uninstall() -> None:
    logsetup.set_uncaught_listener(None)
