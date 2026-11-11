"""Every message box the app shows ends up in the log, with the button that answered it."""

import logging

import pytest
from PySide6.QtWidgets import QMessageBox

from dubcrate.ui import dialog_log


@pytest.fixture
def installed(monkeypatch):
    """Install over fake boxes, and undo it afterwards: other tests patch QMessageBox themselves."""
    for kind in ("information", "question", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, kind, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))  # fmt: skip
    monkeypatch.setattr(dialog_log, "_installed", False)
    dialog_log.install()


def test_a_warning_box_is_logged_at_warning_level_with_its_answer(installed, caplog):
    caplog.set_level("INFO")
    QMessageBox.warning(None, "Import failed", "Couldn't read\nthe file")
    levels = [(r.levelname, r.getMessage()) for r in caplog.records]
    assert levels[0][0] == "WARNING" and "Import failed" in levels[0][1]
    assert "Couldn't read the file" in levels[0][1]  # on one line
    assert levels[1] == ("INFO", "dialog [warning] 'Import failed' -> Ok")


def test_installing_twice_logs_once(installed, caplog):
    dialog_log.install()
    caplog.set_level(logging.INFO)
    QMessageBox.information(None, "Hello", "text")
    assert len([r for r in caplog.records if "dialog [information]" in r.getMessage() and "->" not in r.getMessage()]) == 1  # fmt: skip
