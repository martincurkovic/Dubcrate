"""A message for an unexpected error: the hook's listener, the throttle, and the log button."""

import sys
import threading

import pytest

from dubcrate import logsetup
from dubcrate.ui import crash_notice
from dubcrate.ui.crash_notice import MIN_GAP_SECONDS, CrashNotice, describe


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def notice(qapp):
    asked, opened = [], []
    clock = Clock()
    answer = {"show_log": False}

    def ask(_parent, text):
        asked.append(text)
        return answer["show_log"]

    n = CrashNotice(lambda: opened.append(True), ask=ask, clock=clock)
    n.asked, n.opened, n.clock, n.answer = asked, opened, clock, answer
    yield n
    logsetup.set_uncaught_listener(None)


def test_an_error_is_described_in_one_line():
    assert describe(ValueError, ValueError("bad\n  thing")) == "ValueError: bad thing"
    assert describe(KeyError, "") == "KeyError"
    long = describe(RuntimeError, "x" * 1000)
    assert (
        long.startswith("RuntimeError: xxx") and len(long) <= crash_notice.MAX_TEXT + 1
    )


def test_a_reported_error_opens_the_message_once_the_event_loop_runs(notice, qapp):
    notice.report(ValueError, ValueError("boom"))
    assert (
        notice.asked == []
    )  # (not inside the slot that failed: later, from the event loop)
    qapp.processEvents()
    assert notice.asked == ["ValueError: boom"] and notice.opened == []


def test_show_log_file_opens_the_log_folder(notice, qapp):
    notice.answer["show_log"] = True
    notice.report(OSError, OSError("disk"))
    qapp.processEvents()
    assert notice.opened == [True]


def test_a_second_error_right_after_is_logged_but_not_shown_again(notice, qapp):
    notice.report(ValueError, ValueError("one"))
    notice.report(ValueError, ValueError("two"))
    qapp.processEvents()
    assert notice.asked == ["ValueError: one"] and notice.suppressed == 1
    notice.clock.now += MIN_GAP_SECONDS + 1  # (a while later, a new one is shown)
    notice.report(KeyError, KeyError("three"))
    qapp.processEvents()
    assert notice.asked == ["ValueError: one", "KeyError: 'three'"]


def test_an_error_while_the_message_is_open_is_not_stacked(notice, qapp):
    def ask(_parent, text):
        notice.asked.append(text)
        notice.report(
            ValueError, ValueError("while open")
        )  # (what a repeating paint error would do)
        qapp.processEvents()
        return False

    notice._ask = ask
    notice.report(ValueError, ValueError("first"))
    qapp.processEvents()
    qapp.processEvents()
    assert notice.asked == ["ValueError: first"]


# -- the hook ------------------------------------------------------------------------------


def test_the_hook_tells_the_listener_on_the_main_thread_and_in_a_thread(qapp):
    heard = []
    saved = sys.excepthook, threading.excepthook
    try:
        sys.excepthook = lambda *args: (
            None
        )  # (the hooks pass on to these: keep stderr and pytest quiet)
        threading.excepthook = lambda args: None
        logsetup.install_exception_hooks()
        logsetup.set_uncaught_listener(
            lambda kind, value: heard.append((kind.__name__, str(value)))
        )
        try:
            raise ValueError("main")
        except ValueError:
            exc = sys.exc_info()
        sys.excepthook(*exc)

        def boom():
            raise KeyError("in a thread")

        t = threading.Thread(target=boom)
        t.start()
        t.join()
    finally:
        logsetup.set_uncaught_listener(None)
        sys.excepthook, threading.excepthook = saved
    assert ("ValueError", "main") in heard
    assert ("KeyError", "'in a thread'") in heard


def test_ctrl_c_and_exit_are_not_reported(qapp):
    heard = []
    logsetup.set_uncaught_listener(lambda kind, value: heard.append(kind))
    try:
        logsetup._tell_listener(KeyboardInterrupt, KeyboardInterrupt())
        logsetup._tell_listener(SystemExit, SystemExit(0))
        logsetup._tell_listener(ValueError, ValueError("x"))
    finally:
        logsetup.set_uncaught_listener(None)
    assert heard == [ValueError]


def test_a_listener_that_fails_never_hides_the_error(qapp, caplog):
    def broken(kind, value):
        raise RuntimeError("the listener is broken")

    logsetup.set_uncaught_listener(broken)
    try:
        logsetup._tell_listener(ValueError, ValueError("x"))  # (must not raise)
    finally:
        logsetup.set_uncaught_listener(None)
    assert "listener failed" in caplog.text


def test_install_wires_the_notice_to_the_hook_and_uninstall_stops_it(qapp):
    from PySide6.QtWidgets import QWidget

    class Window(QWidget):
        def show_log_file(self):
            pass

    window = Window()
    notice = crash_notice.install(window)
    try:
        assert logsetup._uncaught_listener == notice.report
        assert notice.parent() is window
    finally:
        crash_notice.uninstall()
    assert logsetup._uncaught_listener is None
