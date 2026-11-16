"""The log file: what it records, and that a broken log never breaks the app."""

import logging
import sys
import threading

import pytest

from dubcrate import logsetup


@pytest.fixture
def log(tmp_path):
    path = logsetup.setup(directory=tmp_path / "logs")
    yield path
    logsetup.shutdown()


def text(path):
    for handler in logging.getLogger("dubcrate").handlers:
        handler.flush()
    return path.read_text(encoding="utf-8")


def test_setup_writes_to_a_rotating_log_file(log, tmp_path):
    assert log == tmp_path / "logs" / "dubcrate.log"
    logging.getLogger("dubcrate.test").info("hello log")
    assert "INFO" in text(log) and "dubcrate.test: hello log" in text(log)
    handler = logsetup._handler
    assert (handler.maxBytes, handler.backupCount) == (logsetup.MAX_BYTES, logsetup.BACKUPS)  # fmt: skip


def test_the_log_folder_can_be_chosen_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("DUBCRATE_LOG_DIR", str(tmp_path / "elsewhere"))
    assert logsetup.log_path() == tmp_path / "elsewhere" / "dubcrate.log"


def test_detail_is_only_logged_when_verbose(tmp_path):
    try:
        path = logsetup.setup(verbose=False, directory=tmp_path)
        logging.getLogger("dubcrate.test").debug("fine detail")
        assert "fine detail" not in text(path) and not logsetup.is_verbose()
        path = logsetup.setup(verbose=True, directory=tmp_path)
        logging.getLogger("dubcrate.test").debug("more fine detail")
        assert "more fine detail" in text(path) and logsetup.is_verbose()
    finally:
        logsetup.shutdown()


def test_setting_up_twice_does_not_log_every_line_twice(tmp_path):
    try:
        logsetup.setup(directory=tmp_path)
        path = logsetup.setup(directory=tmp_path)
        logging.getLogger("dubcrate.test").info("only once")
        assert text(path).count("only once") == 1
    finally:
        logsetup.shutdown()


def test_a_log_that_cant_be_written_is_not_an_error(tmp_path):
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    try:
        assert (
            logsetup.setup(directory=blocker / "logs") is None
        )  # a folder can't be made inside a file
        logging.getLogger("dubcrate.test").info("still fine")
    finally:
        logsetup.shutdown()


def test_start_up_details_say_what_ran_on_what(log):
    logsetup.log_startup()
    t = text(log)
    assert "Dubcrate" in t and "starting" in t and "Python" in t and "PySide6" in t


def test_an_uncaught_exception_is_logged_with_its_traceback(log):
    import contextlib
    import io

    saved = sys.excepthook, threading.excepthook
    try:
        logsetup.install_exception_hooks()
        try:
            raise ValueError("boom in the app")
        except ValueError:
            exc = sys.exc_info()
        with contextlib.redirect_stderr(
            io.StringIO()
        ):  # the hook then calls the normal one, which prints
            sys.excepthook(*exc)
    finally:
        sys.excepthook, threading.excepthook = saved
    t = text(log)
    assert "CRITICAL" in t and "uncaught exception" in t
    assert "ValueError: boom in the app" in t and "Traceback" in t


def test_the_crash_log_gets_a_header_for_each_run(tmp_path):
    import faulthandler

    try:
        path = logsetup.enable_crash_log(tmp_path / "logs")
        assert path == tmp_path / "logs" / "crash.log" and faulthandler.is_enabled()
        assert "started" in path.read_text(encoding="utf-8")
    finally:
        faulthandler.disable()


def test_detail_can_be_switched_on_and_off_while_logging(tmp_path):
    try:
        path = logsetup.setup(directory=tmp_path)
        logsetup.set_verbose(True)
        logging.getLogger("dubcrate.test").debug("now in the log")
        assert "now in the log" in text(path) and logsetup.is_verbose()
        logsetup.set_verbose(False)
        logging.getLogger("dubcrate.test").debug("not in the log")
        assert "not in the log" not in text(path) and not logsetup.is_verbose()
    finally:
        logsetup.shutdown()


def test_switching_detail_without_a_log_is_harmless():
    logsetup.shutdown()
    logsetup.set_verbose(True)
    assert not logsetup.is_verbose()


def test_the_verbose_log_switch_is_in_the_settings_window_and_remembered(
    qapp, tmp_path
):
    from dubcrate import settings
    from dubcrate.ui.settings_dialog import SettingsDialog

    try:
        logsetup.setup(directory=tmp_path)
        dialog = SettingsDialog()
        assert dialog.verbose_check.isChecked() is False
        dialog.verbose_check.setChecked(True)
        assert settings.get_verbose_logging() is True and logsetup.is_verbose()
        dialog.verbose_check.setChecked(False)
        assert settings.get_verbose_logging() is False and not logsetup.is_verbose()
    finally:
        logsetup.shutdown()
