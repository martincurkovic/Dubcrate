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
