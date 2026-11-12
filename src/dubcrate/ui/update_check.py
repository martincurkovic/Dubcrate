"""Run an update check and react to the answer: one policy for the Help menu, About, and start-up.

The check itself (`dubcrate.updates`) is plain Python; it runs on a worker (`run_async`) and the
answer arrives here on the GUI thread. A check you ask for says what it found, including "you're
up to date" and failures. One at start-up is quiet unless there is something new, at most once a
day, and it stays quiet about a version you skipped.
"""

import logging
import time
from collections.abc import Callable

from PySide6.QtWidgets import QMessageBox, QWidget

from dubcrate import settings
from dubcrate.updates import UpdateCheckError, UpdateInfo, check_for_update

from .tasks import TaskError, run_async
from .update_dialog import UpdateAvailableDialog
from .version import app_version

log = logging.getLogger(__name__)

UPDATE_CHECK_INTERVAL_SECONDS = 24 * 60 * 60


def _check() -> UpdateInfo | None:
    """`check_for_update` for a worker: a failed check is an expected outcome (offline, a private
    repository), so it is logged as a one-line warning, not as a bug with a traceback."""
    try:
        return check_for_update()
    except UpdateCheckError as e:
        raise TaskError(str(e)) from e


def due(now: float | None = None) -> bool:
    """Whether the start-up check should run: it is switched on, and the last one was over a day ago."""
    now = time.time() if now is None else now
    return (
        settings.get_auto_update_check()
        and now - settings.get_last_update_check() > UPDATE_CHECK_INTERVAL_SECONDS
    )


class UpdateChecker:
    """Owns at most one check at a time for a window. `parent` is the window the answers appear over."""

    def __init__(self, parent: QWidget):
        self._parent = parent
        self._running = False
        self._closed = False
        self.dialog: UpdateAvailableDialog | None = None  # kept alive while it is shown
        # the newest release found (None: up to date, or not checked yet), and who to tell when
        # that changes: the status bar's "Update available" button. It stays after "Skip This
        # Version" and "Remind Me Later", which only quiet the pop-up.
        self.available: UpdateInfo | None = None
        self.on_available: Callable[[UpdateInfo | None], None] | None = None

    @property
    def running(self) -> bool:
        return self._running

    def close(self) -> None:
        """The window is closing: an answer that arrives later is dropped, not shown."""
        self._closed = True

    def start(
        self,
        manual: bool,
        on_finished: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Ask GitHub. `manual` (you asked) reports every outcome; otherwise only news."""
        if self._running or self._closed:
            return
        self._running = True
        over = parent or self._parent
        log.info("checking for updates (%s)", "asked for" if manual else "at start-up")

        def finish() -> None:
            self._running = False
            if on_finished is not None and not self._closed:
                on_finished()

        def answered(info: UpdateInfo | None) -> None:
            finish()
            if not self._closed:
                self._show(info, manual, over)

        def failed(message: str) -> None:
            finish()
            if manual and not self._closed:
                QMessageBox.warning(
                    over,
                    "Update Check Failed",
                    f"Could not check for updates:\n{message}",
                )
            # silent at start-up: no need to nag about a flaky connection

        run_async(_check, answered, failed)

    def show_available(self, parent: QWidget | None = None) -> None:
        """Open the notice for the update that was found (the button in the status bar)."""
        if self.available is not None and not self._closed:
            self.dialog = UpdateAvailableDialog(self.available, parent or self._parent)
            self.dialog.show()

    def _show(self, info: UpdateInfo | None, manual: bool, over: QWidget) -> None:
        settings.set_last_update_check(time.time())
        self.available = info
        if self.on_available is not None:
            self.on_available(info)
        log.info(
            "update check: %s",
            f"{info.version} is available" if info else "up to date",
        )
        if info is None:
            if manual:
                QMessageBox.information(
                    over,
                    "No Updates Available",
                    f"You're up to date (version {app_version()}).",
                )
            return
        if not manual and info.version == settings.get_skipped_update():
            return
        self.dialog = UpdateAvailableDialog(info, over)
        self.dialog.show()
