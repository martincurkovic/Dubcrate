import atexit
import os
import shutil
import tempfile
from pathlib import Path

# Forced, not setdefault: a desktop that exports QT_QPA_PLATFORM would otherwise run the tests on a real display.
os.environ["QT_QPA_PLATFORM"] = "offscreen"

# No test may touch the user's real app data or settings: the theme writes generated icons, and QSettings stores prefs.
_TMP = Path(tempfile.mkdtemp(prefix="dubcrate-tests-"))
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)
os.environ["DUBCRATE_DATA_DIR"] = str(_TMP / "data")
os.environ["DUBCRATE_LOG_DIR"] = str(_TMP / "logs")

from PySide6.QtCore import QSettings

QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(
    QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(_TMP / "settings")
)

import pytest


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    from dubcrate.ui import theme

    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("DubcrateTests")
    app.setApplicationName("DubcrateTests")
    # Closing the last window must not quit the app: once it has, every new QEventLoop returns at once
    # (the windows are deleted after each test, see _no_windows_left_behind)
    app.setQuitOnLastWindowClosed(False)
    theme.apply_to_app(app, "dark")
    return app


@pytest.fixture(autouse=True)
def _fresh_settings(qapp):
    QSettings().clear()
    yield


@pytest.fixture
def wait_for(qapp):
    """Spin the event loop until `predicate()` is true (worker results arrive through it)."""
    import time

    def wait(predicate, timeout: float = 20.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            qapp.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        return False

    return wait


def _destroy_widgets(rounds: int = 1) -> None:
    """Close and delete every top-level widget, running the deletions while the event loop is whole."""
    import gc

    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    for _ in range(rounds):
        for widget in app.topLevelWidgets():
            widget.close()
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        gc.collect()


@pytest.fixture(autouse=True)
def _no_windows_left_behind(qapp):
    """Delete the windows a test leaves behind, as the test ends. Left alone they pile up (over a
    thousand top-level widgets by the end of the suite), and every theme change restyles all of them:
    a `setStyleSheet` that takes 0.04 s early on took 11 s late, which on a slow CI machine ran past the
    job's time limit. Declared before any test fixture, so it is torn down after them (a fixture that
    closes its own window has already done so)."""
    yield
    _destroy_widgets()


@pytest.fixture(scope="session", autouse=True)
def _destroy_qt_widgets_before_exit():
    """Windows the tests leave behind are torn down by PySide at interpreter exit, and that order can
    segfault inside Shiboken after every test passed. Delete them while the event loop is still whole."""
    yield
    _destroy_widgets(rounds=3)


class FakeAudioDevice:
    """Stands in for a sound card: records what is asked of it and lets a test pull audio blocks."""

    def __init__(self, rate=44_100, msec=23, device_id=None):
        self.args = (rate, msec, device_id)
        self.gen = None
        self.stopped = self.closed = False

    def start(self, gen):
        self.gen = gen

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True

    def pull(self, frames):
        return self.gen.send(frames)


@pytest.fixture(autouse=True)
def _no_real_audio(monkeypatch):
    """No test may open the machine's real audio output."""
    from dubcrate.audio import player

    monkeypatch.setattr(
        player,
        "_miniaudio_device",
        lambda rate, msec, device_id: FakeAudioDevice(rate, msec, device_id),
    )


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """No test may reach GitHub: an update check must be given a fake `urlopen`."""

    def refuse(*_a, **_k):
        raise AssertionError("a test tried to use the network; pass a fake urlopen")

    monkeypatch.setattr("urllib.request.urlopen", refuse)


@pytest.fixture(autouse=True)
def _no_modal_question(monkeypatch):
    """A question box blocks the test for ever: a test that expects one patches it itself."""
    from PySide6.QtWidgets import QMessageBox

    def refuse(*_a, **_k):
        raise AssertionError("a test opened a question box; patch QMessageBox.question")

    monkeypatch.setattr(QMessageBox, "question", staticmethod(refuse))

    def refuse_overlap(*_a, **_k):
        raise AssertionError(
            "a test opened the already-used box; patch disc_view.ask_overlap"
        )

    monkeypatch.setattr("dubcrate.ui.disc_view.ask_overlap", refuse_overlap)


@pytest.fixture(autouse=True)
def _no_real_burner(monkeypatch):
    """No test may reach a real drive: a burn window must be given a fake burner."""

    def refuse():
        raise AssertionError("a test tried to use the real burner; pass burner_factory")

    monkeypatch.setattr("dubcrate.ui.burn_dialog._backend", refuse)


@pytest.fixture(autouse=True)
def _no_real_printer(monkeypatch):
    """No test may open the system's print panel or a file chooser: patch what the label window asks."""

    def refuse(*_a, **_k):
        raise AssertionError(
            "a test opened the print panel or a file chooser; patch label_dialog.ask_printer / ask_pdf_path"
        )

    monkeypatch.setattr("dubcrate.ui.label_dialog.ask_printer", refuse)
    monkeypatch.setattr("dubcrate.ui.label_dialog.ask_pdf_path", refuse)


@pytest.fixture(autouse=True)
def _no_real_burn_check(monkeypatch):
    """A burn must not ask a real drive for its disc's table of contents or open its tray: the check
    is off by default (a test that wants it patches burn_dialog.can_read_toc and check_burn)."""
    monkeypatch.setattr("dubcrate.ui.burn_dialog.can_read_toc", lambda: False)
    monkeypatch.setattr("dubcrate.ui.burn_dialog.check_burn", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _no_real_card_search(monkeypatch):
    """No test may look at the real drives for a CDJ card or open the card folder chooser."""

    def refuse(*_a, **_k):
        raise AssertionError(
            "a test opened the card folder chooser; patch sync_view.ask_card_folder"
        )

    monkeypatch.setattr("dubcrate.ui.sync_view.ask_card_folder", refuse)

    def refuse_write(*_a, **_k):
        raise AssertionError(
            "a test opened a sync question or warning; patch sync_view.ask_sync_confirmation / show_problem"
        )

    def refuse_eject(*_a, **_k):
        raise AssertionError("a test ejected a card; patch sync_view.eject_card")

    monkeypatch.setattr("dubcrate.ui.sync_view.eject_card", refuse_eject)
    monkeypatch.setattr("dubcrate.ui.sync_view.ask_sync_confirmation", refuse_write)
    monkeypatch.setattr("dubcrate.ui.sync_view.show_problem", refuse_write)
    monkeypatch.setattr("dubcrate.ui.sync_view.mounted_volumes", list)


@pytest.fixture(autouse=True)
def _no_real_export_chooser(monkeypatch):
    """No test may open the folder chooser of the burn window's Export: patch burn_dialog.ask_export_folder."""

    def refuse(*_a, **_k):
        raise AssertionError(
            "a test opened the export folder chooser; patch burn_dialog.ask_export_folder"
        )

    monkeypatch.setattr("dubcrate.ui.burn_dialog.ask_export_folder", refuse)
