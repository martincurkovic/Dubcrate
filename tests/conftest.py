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


@pytest.fixture(scope="session", autouse=True)
def _destroy_qt_widgets_before_exit():
    """Windows the tests leave behind are torn down by PySide at interpreter exit, and that order can
    segfault inside Shiboken after every test passed. Delete them while the event loop is still whole."""
    yield
    import gc

    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    for _ in range(3):
        for widget in app.topLevelWidgets():
            widget.close()
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        gc.collect()


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
def _no_real_burner(monkeypatch):
    """No test may reach a real drive: a burn window must be given a fake burner."""

    def refuse():
        raise AssertionError("a test tried to use the real burner; pass burner_factory")

    monkeypatch.setattr("dubcrate.ui.burn_dialog._backend", refuse)
