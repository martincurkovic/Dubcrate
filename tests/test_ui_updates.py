"""Checking for a new version: the policy, the dialogs, and where they sit in the app."""

import threading
import time

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QMessageBox

from dubcrate import settings
from dubcrate.library import Library
from dubcrate.ui import about_dialog, update_check, update_dialog
from dubcrate.ui.about_dialog import AboutDialog, NoticesDialog
from dubcrate.ui.main_window import MainWindow
from dubcrate.ui.update_check import UpdateChecker, due
from dubcrate.ui.update_dialog import UpdateAvailableDialog
from dubcrate.updates import UpdateInfo

INFO = UpdateInfo("0.2.0", "v0.2.0", "Dubcrate 0.2.0", "https://example/v0.2.0", "## What's new\n\nFaster **burns**. [More](https://example/more)")  # fmt: skip


@pytest.fixture
def boxes(monkeypatch):
    """Records the message boxes instead of opening them."""
    shown = []
    for name in ("information", "warning"):
        monkeypatch.setattr(
            QMessageBox,
            name,
            staticmethod(
                lambda _p, title, text, _n=name: shown.append((_n, title, text))
            ),
        )
    return shown


@pytest.fixture
def opened(monkeypatch):
    urls = []
    monkeypatch.setattr(update_dialog, "open_url", urls.append)
    return urls


def fake_check(monkeypatch, result=None, error=None, threads=None):
    def check():
        if threads is not None:
            threads.append(threading.current_thread())
        if error:
            raise error
        return result

    monkeypatch.setattr(update_check, "check_for_update", check)


@pytest.fixture
def checker(qapp):
    return UpdateChecker(None)


def run(checker, wait_for, manual, **kw):
    done = []
    checker.start(manual, on_finished=lambda: done.append(1), **kw)
    assert wait_for(lambda: done), "the check never finished"


# -- the settings --------------------------------------------------------------------


def test_the_update_settings_default_and_remember():
    assert settings.get_auto_update_check() is True
    assert settings.get_last_update_check() == 0.0
    assert settings.get_skipped_update() is None
    settings.set_auto_update_check(False)
    settings.set_last_update_check(1234.5)
    settings.set_skipped_update("0.3.0")
    assert settings.get_auto_update_check() is False
    assert settings.get_last_update_check() == 1234.5
    assert settings.get_skipped_update() == "0.3.0"


def test_a_switch_stored_as_text_is_understood():
    from PySide6.QtCore import QSettings

    QSettings().setValue("updates/auto_check", "false")
    assert settings.get_auto_update_check() is False
    QSettings().setValue("updates/last_check", "garbage")
    assert settings.get_last_update_check() == 0.0


def test_the_startup_check_is_due_once_a_day_and_only_if_switched_on():
    assert due(now=1_000_000)  # never checked
    settings.set_last_update_check(1_000_000)
    assert not due(now=1_000_000 + 3600)
    assert due(now=1_000_000 + 25 * 3600)
    settings.set_auto_update_check(False)
    assert not due(now=1_000_000 + 25 * 3600)


# -- what a check does -----------------------------------------------------------------


def test_a_new_version_opens_the_notice_and_notes_the_time(
    checker, wait_for, boxes, monkeypatch
):
    fake_check(monkeypatch, INFO)
    run(checker, wait_for, manual=False)
    assert (
        isinstance(checker.dialog, UpdateAvailableDialog) and checker.dialog.isVisible()
    )
    assert settings.get_last_update_check() > 0
    assert boxes == []
    checker.dialog.close()


def test_the_check_runs_off_the_gui_thread(checker, wait_for, monkeypatch):
    threads = []
    fake_check(monkeypatch, None, threads=threads)
    run(checker, wait_for, manual=False)
    assert threads and threads[0] is not threading.main_thread()


def test_being_up_to_date_is_only_said_when_you_asked(
    checker, wait_for, boxes, monkeypatch
):
    fake_check(monkeypatch, None)
    run(checker, wait_for, manual=False)
    assert boxes == []
    run(checker, wait_for, manual=True)
    [(kind, title, text)] = boxes
    assert kind == "information" and title == "No Updates Available"
    assert "up to date" in text


def test_a_failure_is_only_reported_when_you_asked_and_is_not_counted_as_a_check(
    checker, wait_for, boxes, monkeypatch
):
    fake_check(monkeypatch, error=OSError("no route to host"))
    run(checker, wait_for, manual=False)
    assert boxes == [] and settings.get_last_update_check() == 0.0
    run(checker, wait_for, manual=True)
    [(kind, _title, text)] = boxes
    assert kind == "warning" and "no route to host" in text


def test_a_skipped_version_stays_quiet_at_startup_but_not_when_asked(
    checker, wait_for, boxes, monkeypatch
):
    settings.set_skipped_update("0.2.0")
    fake_check(monkeypatch, INFO)
    run(checker, wait_for, manual=False)
    assert checker.dialog is None
    run(checker, wait_for, manual=True)
    assert checker.dialog is not None
    checker.dialog.close()
    fake_check(monkeypatch, UpdateInfo("0.3.0", "v0.3.0", "x", "https://example"))
    checker.dialog = None
    run(
        checker, wait_for, manual=False
    )  # a version newer than the skipped one is news again
    assert checker.dialog is not None
    checker.dialog.close()


def test_only_one_check_runs_at_a_time(checker, wait_for, monkeypatch):
    calls = []
    release = threading.Event()

    def slow():
        calls.append(1)
        release.wait(5)

    monkeypatch.setattr(update_check, "check_for_update", slow)
    done = []
    checker.start(False, on_finished=lambda: done.append(1))
    assert checker.running
    checker.start(False)  # ignored
    release.set()
    assert wait_for(lambda: done)
    assert calls == [1] and not checker.running


def test_an_answer_that_arrives_after_the_window_closed_is_dropped(
    checker, wait_for, boxes, monkeypatch
):
    release = threading.Event()
    seen = []

    def slow():
        release.wait(5)
        return INFO

    monkeypatch.setattr(update_check, "check_for_update", slow)
    checker.start(True, on_finished=lambda: seen.append("finished"))
    checker.close()
    release.set()
    assert wait_for(lambda: not checker.running)
    time.sleep(0.05)
    assert checker.dialog is None and boxes == [] and seen == []
    checker.start(True)  # and a closed window starts nothing new
    assert not checker.running


# -- the dialog ------------------------------------------------------------------------


def test_the_dialog_says_what_is_available_and_shows_the_notes(qapp):
    dialog = UpdateAvailableDialog(INFO)
    assert "0.2.0" in dialog.heading.text()
    assert "https://example/v0.2.0" in dialog.link_label.text()
    assert dialog.notes_view is not None and "Faster" in dialog.notes_view.toPlainText()


def test_a_release_with_no_notes_has_no_empty_box(qapp):
    dialog = UpdateAvailableDialog(
        UpdateInfo("0.2.0", "v0.2.0", "x", "https://example")
    )
    assert dialog.notes_view is None


def test_view_release_opens_the_page_and_closes(qapp, opened):
    dialog = UpdateAvailableDialog(INFO)
    dialog.show()
    dialog.view_button.click()
    assert opened == ["https://example/v0.2.0"] and not dialog.isVisible()


def test_skip_remembers_the_version(qapp, opened):
    dialog = UpdateAvailableDialog(INFO)
    dialog.show()
    dialog.skip_button.click()
    assert settings.get_skipped_update() == "0.2.0" and not dialog.isVisible()
    assert opened == []


def test_remind_me_later_remembers_nothing(qapp, opened):
    dialog = UpdateAvailableDialog(INFO)
    dialog.show()
    dialog.later_button.click()
    assert settings.get_skipped_update() is None and not dialog.isVisible()


def test_only_web_links_in_the_notes_are_opened(qapp, opened):
    dialog = UpdateAvailableDialog(INFO)
    for url in ("https://example/more", "http://example/plain", "file:///etc/passwd", "javascript:alert(1)", "dubcrate://x"):  # fmt: skip
        dialog._open_notes_link(QUrl(url))
    assert opened == ["https://example/more", "http://example/plain"]


# -- About and the notices --------------------------------------------------------------


def test_about_says_what_the_app_is_and_who_it_is_not(qapp):
    dialog = AboutDialog(None, UpdateChecker(None), None)
    text = dialog.findChildren(type(dialog.name_label))
    joined = " ".join(label.text() for label in text)
    assert "General Public License" in joined and "not affiliated" in joined
    assert "ChiKareGo2" in joined and "Third-Party Notices" in joined


def test_about_opens_the_notices(qapp):
    dialog = AboutDialog(None, UpdateChecker(None), None)
    dialog.notices_button.click()
    assert isinstance(dialog.notices, NoticesDialog) and dialog.notices.isVisible()
    assert "PySide6" in dialog.notices.viewer.toPlainText()
    dialog.notices.close()


def test_the_notices_load_from_the_file_that_ships_with_the_app(qapp):
    dialog = NoticesDialog()
    text = dialog.viewer.toPlainText()
    assert about_dialog.NOTICES_PATH.name == "THIRD_PARTY_NOTICES.md"
    assert "ChiKareGo2" in text and "LGPL" in text and "SQLCipher" in text


def test_the_notices_say_so_if_the_file_is_missing(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(about_dialog, "NOTICES_PATH", tmp_path / "gone.md")
    assert "Couldn't load the notices" in NoticesDialog().viewer.toPlainText()


def test_about_checks_for_updates_over_itself_and_enables_the_button_again(
    qapp, wait_for, boxes, monkeypatch
):
    fake_check(monkeypatch, None)
    dialog = AboutDialog(None, UpdateChecker(None), None)
    dialog.update_button.click()
    assert not dialog.update_button.isEnabled()
    assert wait_for(lambda: dialog.update_button.isEnabled())
    assert boxes and boxes[0][1] == "No Updates Available"


# -- in the window --------------------------------------------------------------------


@pytest.fixture
def lib():
    with Library.open(":memory:") as library:
        yield library


def test_the_help_menu_offers_both(qapp, lib):
    window = MainWindow(lib)
    assert window.act_notices.text().replace("&", "") == "Third-Party Notices…"
    assert window.act_updates.text().replace("&", "") == "Check for Updates…"
    window.close()


def test_the_menu_item_checks_and_says_what_it_found(
    qapp, lib, wait_for, boxes, monkeypatch
):
    fake_check(monkeypatch, None)
    window = MainWindow(lib)
    window.act_updates.trigger()
    assert wait_for(lambda: boxes)
    assert boxes[0][1] == "No Updates Available"
    window.close()


def test_the_window_checks_at_startup_only_when_the_app_asks_and_it_is_due(
    qapp, lib, monkeypatch
):
    started = []
    monkeypatch.setattr(
        UpdateChecker, "start", lambda self, manual, **k: started.append(manual)
    )
    monkeypatch.setattr(
        "dubcrate.ui.main_window.QTimer.singleShot", staticmethod(lambda ms, fn: fn())
    )
    MainWindow(lib).close()  # (tests, and anything that doesn't ask: no network)
    assert started == []
    MainWindow(lib, update_check=True).close()
    assert started == [False]  # quietly: only news is shown
    settings.set_last_update_check(time.time())
    MainWindow(lib, update_check=True).close()
    assert started == [False]  # checked a moment ago
    settings.set_last_update_check(0)
    settings.set_auto_update_check(False)
    MainWindow(lib, update_check=True).close()
    assert started == [False]  # switched off


def test_closing_the_window_stops_listening_for_the_answer(qapp, lib):
    window = MainWindow(lib)
    window.close()
    window.updates.start(True)
    assert not window.updates.running


def test_the_settings_switch_turns_the_startup_check_off(qapp):
    from dubcrate.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog()
    assert dialog.auto_update_check.isChecked()
    dialog.auto_update_check.setChecked(False)
    assert settings.get_auto_update_check() is False
