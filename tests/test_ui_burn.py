"""The burn window, driven with a fake burner and real rendered audio."""

import shutil
import subprocess

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QMessageBox

from dubcrate import settings
from dubcrate.burn import BurnError, MediaInfo
from dubcrate.library import Library
from dubcrate.library.folder import scan_folder
from dubcrate.ui.burn_dialog import BurnDialog, describe_media
from dubcrate.ui.main_window import MainWindow

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg not installed",
)

ROOMY = MediaInfo(drive="Test Drive", present=True, blank=True, free_sectors=359_849)


def make_flac(path, seconds, **tags):
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = [arg for k, v in tags.items() for arg in ("-metadata", f"{k}={v}")]
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
         f"sine=duration={seconds}", *meta, str(path)],
        check=True,
    )  # fmt: skip
    return path


class FakeBurner:
    """Records what it was asked to burn. Never touches a drive."""

    def __init__(self, media=ROOMY, fail=None):
        self.media = media
        self.fail = fail
        self.burned = []  # (disc, options)
        self.media_calls = 0

    def media_info(self, device):
        self.media_calls += 1
        return self.media

    def burn(self, disc, options, progress):
        progress("fake burner: starting")
        if self.fail:
            raise BurnError(self.fail)
        self.burned.append((disc, options))
        progress("fake burner: done")


@pytest.fixture
def lib(tmp_path):
    music = tmp_path / "music"
    make_flac(music / "a.flac", 2.0, title="Alpha", artist="Same")
    make_flac(music / "b.flac", 3.0, title="Beta", artist="Same")
    scan = scan_folder(music)
    with Library.open(":memory:") as library:
        library.replace_source("folder", str(music), "music", scan.tracks, scan.nodes)
        yield library


@pytest.fixture
def disc_id(lib):
    disc = lib.create_disc("Friday")
    lib.add_to_disc(disc, [t.id for t in lib.tracks(sort="title")])
    return disc


@pytest.fixture
def make_dialog(qapp, lib, disc_id, wait_for):
    made = []

    def make(burner=None, disc=None):
        burner = burner or FakeBurner()
        dialog = BurnDialog(lib, disc or disc_id, burner_factory=lambda: burner)
        made.append(dialog)
        if (disc or disc_id) == disc_id:
            assert wait_for(lambda: not dialog._busy)  # the drive check finished
        return dialog, burner

    yield make
    for dialog in made:
        dialog.close()
        dialog.deleteLater()


def burn(dialog, wait_for):
    dialog.confirm_check.setChecked(True)
    assert dialog.burn_button.isEnabled()
    dialog.burn_button.click()
    assert wait_for(lambda: not dialog._busy)


# -- before burning -----------------------------------------------------------


def test_the_drive_and_disc_are_shown_and_nothing_is_burned_yet(make_dialog):
    dialog, burner = make_dialog()
    assert "Test Drive" in dialog.drive_label.text()
    assert "blank disc" in dialog.drive_label.text()
    assert "holds 79:57:74" in dialog.drive_label.text()
    assert (
        "2 tracks" in dialog.disc_label.text() and "gapless" in dialog.disc_label.text()
    )
    assert burner.burned == []


def test_burn_waits_for_the_blank_cdr_confirmation(make_dialog):
    dialog, _burner = make_dialog()
    assert not dialog.confirm_check.isChecked()
    assert not dialog.burn_button.isEnabled()
    dialog.confirm_check.setChecked(True)
    assert dialog.burn_button.isEnabled()
    dialog.confirm_check.setChecked(False)
    assert not dialog.burn_button.isEnabled()


def test_no_button_in_the_window_is_the_return_key_default(make_dialog):
    dialog, _burner = make_dialog()
    for button in (dialog.burn_button, dialog.close_button, dialog.check_button):
        assert not button.autoDefault() and not button.isDefault()


def test_return_never_starts_a_burn(make_dialog):
    dialog, burner = make_dialog()
    dialog.confirm_check.setChecked(True)
    event = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier
    )
    dialog.keyPressEvent(event)
    assert burner.burned == [] and not dialog._busy


def test_speed_starts_at_the_settings_default_and_can_be_changed_for_one_burn(
    qapp, lib, disc_id, make_dialog, wait_for
):
    settings.set_burn_speed(16)
    dialog, burner = make_dialog()
    assert dialog.speed_combo.currentData() == 16
    dialog.speed_combo.setCurrentIndex(dialog.speed_combo.findData(8))
    burn(dialog, wait_for)
    assert burner.burned[0][1].speed == 8
    assert settings.get_burn_speed() == 16  # the default is untouched
    assert lib.burns()[0].speed == 8


def test_the_default_speed_is_10x(make_dialog):
    dialog, _burner = make_dialog()
    assert dialog.speed_combo.currentData() == 10


# -- burning ------------------------------------------------------------------


def test_a_burn_renders_burns_stores_lengths_and_saves_a_record(
    lib, disc_id, make_dialog, wait_for
):
    dialog, burner = make_dialog()
    saved = []
    dialog.burned.connect(saved.append)
    burn(dialog, wait_for)

    [(disc, options)] = burner.burned
    assert [t.title for t in disc.tracks] == ["Alpha", "Beta"]
    assert all(t.wav.name.endswith(".wav") for t in disc.tracks)
    assert (options.speed, options.simulate, options.pregap_sectors) == (10, False, 0)
    assert disc.performer == "Same"

    [record] = lib.burns()
    assert (record.disc_id, record.drive, record.speed) == (disc_id, "Test Drive", 10)
    assert [t.sectors for t in record.tracks] == [150, 225]  # 2 s and 3 s, exact
    assert saved == [record.id]
    assert all(t.cd_sectors is not None for t in lib.tracks())  # stored on this thread

    text = dialog.log.toPlainText()
    assert "rendering 1/2: Alpha" in text and "fake burner: done" in text
    assert "saved under Burned" in text


def test_after_a_real_burn_the_dialog_cannot_burn_again_and_can_close(
    make_dialog, wait_for
):
    dialog, burner = make_dialog()
    burn(dialog, wait_for)
    assert not dialog.confirm_check.isChecked()
    dialog.confirm_check.setChecked(True)
    assert not dialog.burn_button.isEnabled()  # the disc is no longer blank
    assert dialog.close_button.isEnabled()
    assert len(burner.burned) == 1


def test_a_test_burn_records_nothing_and_asks_for_confirmation_again(
    lib, make_dialog, wait_for
):
    dialog, burner = make_dialog()
    dialog.simulate_check.setChecked(True)
    assert dialog.burn_button.text() == "Test Burn"
    burn(dialog, wait_for)
    assert burner.burned[0][1].simulate is True
    assert lib.burns() == []
    assert "Nothing was recorded" in dialog.status_label.text()
    assert not dialog.confirm_check.isChecked()
    dialog.confirm_check.setChecked(True)
    assert dialog.burn_button.isEnabled()  # a test can be repeated
    assert all(t.cd_sectors is not None for t in lib.tracks())  # lengths still learned


def test_a_failed_burn_says_so_and_records_nothing(lib, make_dialog, wait_for, caplog):
    dialog, burner = make_dialog(FakeBurner(fail="drive on fire"))
    burn(dialog, wait_for)
    assert burner.burned == [] and lib.burns() == []
    assert "Failed: drive on fire" in dialog.log.toPlainText()
    assert [r for r in caplog.records if r.levelname == "ERROR"] == []
    assert "failed" in dialog.status_label.text()
    assert not dialog.confirm_check.isChecked() and dialog.close_button.isEnabled()
    dialog.confirm_check.setChecked(True)
    assert dialog.burn_button.isEnabled()  # can try again


def test_the_window_cannot_be_closed_in_the_middle_of_a_burn(make_dialog):
    dialog, _burner = make_dialog()
    dialog.show()
    dialog._busy = True
    dialog._update()
    assert not dialog.close_button.isEnabled()
    dialog.reject()
    assert dialog.isVisible()
    dialog._busy = False
    dialog.reject()
    assert not dialog.isVisible()


def test_the_burn_writes_the_text_edited_for_the_disc(
    lib, disc_id, make_dialog, wait_for
):
    lib.set_entry_text(disc_id, 0, "title", "On The CD")
    lib.set_entry_text(disc_id, 1, "artist", "CD Artist")
    dialog, burner = make_dialog()
    burn(dialog, wait_for)
    [(disc, _options)] = burner.burned
    assert [(t.title, t.performer) for t in disc.tracks] == [
        ("On The CD", "Same"),
        ("Beta", "CD Artist"),
    ]
    assert disc.title == "Friday"  # the CD's own title is the disc's name
    [record] = lib.burns()
    assert [(t.title, t.artist) for t in record.tracks] == [
        ("On The CD", "Same"),
        ("Beta", "CD Artist"),
    ]
    assert {t.title for t in lib.tracks()} == {
        "Alpha",
        "Beta",
    }  # the library is as it was


# -- when the disc can't be burned --------------------------------------------


def test_a_disc_too_small_blocks_the_burn_even_when_confirmed(make_dialog):
    small = MediaInfo(drive="D", present=True, blank=True, free_sectors=100)
    dialog, burner = make_dialog(FakeBurner(small))
    dialog.confirm_check.setChecked(True)
    assert not dialog.burn_button.isEnabled()
    assert "the disc in the drive holds" in dialog.problems_label.text()
    assert dialog.drive_label.property("level") == "critical"
    assert burner.burned == []


def test_no_disc_in_the_drive_blocks_the_burn(make_dialog):
    dialog, _burner = make_dialog(FakeBurner(MediaInfo(drive="D", present=False)))
    dialog.confirm_check.setChecked(True)
    assert not dialog.burn_button.isEnabled()
    assert "no disc in the drive" in dialog.problems_label.text()


def test_a_drive_that_cannot_say_how_much_it_holds_does_not_block_a_burn(
    make_dialog, wait_for
):
    dialog, burner = make_dialog(FakeBurner(MediaInfo(drive="D", present=True)))
    assert dialog.drive_label.property("level") == "warning"
    assert "going by the planned size" in dialog.drive_label.text()
    burn(dialog, wait_for)
    assert len(burner.burned) == 1


def test_a_drive_that_cannot_be_reached_blocks_the_burn_and_can_be_rechecked(
    qapp, lib, disc_id, wait_for, caplog
):
    class Unreachable(FakeBurner):
        def media_info(self, device):
            self.media_calls += 1
            if self.media_calls == 1:
                raise BurnError("DiscRecording found no optical drives")
            return self.media

    burner = Unreachable()
    dialog = BurnDialog(lib, disc_id, burner_factory=lambda: burner)
    assert wait_for(lambda: not dialog._busy)
    dialog.confirm_check.setChecked(True)
    assert not dialog.burn_button.isEnabled()
    assert "no optical drives" in dialog.problems_label.text()
    # a missing drive is expected: it is shown in the window, not logged as a crash
    assert [r for r in caplog.records if r.levelname == "ERROR"] == []
    dialog.check_button.click()  # plug it in, check again
    assert wait_for(lambda: not dialog._busy)
    assert dialog.burn_button.isEnabled()
    dialog.close()


def test_an_empty_disc_is_explained_and_the_drive_is_left_alone(lib, make_dialog):
    empty = lib.create_disc("Empty")
    dialog, burner = make_dialog(disc=empty)
    assert "the disc is empty" in dialog.problems_label.text()
    assert "isn't checked until" in dialog.drive_label.text()
    assert not dialog.check_button.isEnabled()
    dialog.confirm_check.setChecked(True)
    assert not dialog.burn_button.isEnabled()
    assert burner.media_calls == 0


def test_a_missing_file_blocks_the_burn(lib, disc_id, tmp_path, make_dialog):
    (tmp_path / "music" / "a.flac").unlink()
    scan = scan_folder(tmp_path / "music")
    [source] = lib.sources()
    lib.replace_source("folder", source.path, "music", scan.tracks, scan.nodes)
    dialog, burner = make_dialog()
    assert "no longer in its source" in dialog.problems_label.text()
    assert burner.media_calls == 0


# -- the words ----------------------------------------------------------------


def test_the_drive_line_describes_what_is_known():
    text, level = describe_media(ROOMY, 100)
    assert (level, "blank disc" in text) == ("normal", True)
    text, level = describe_media(
        MediaInfo(present=True, blank=False, free_sectors=5), 1
    )
    assert level == "warning" and "doesn't look blank" in text
    assert describe_media(None, 1)[0].startswith("Looking")


# -- from the main window -----------------------------------------------------


@pytest.fixture
def window(qapp, lib, disc_id, monkeypatch):
    shown = []
    monkeypatch.setattr(
        QMessageBox, "information",
        staticmethod(lambda _p, title, text: shown.append((title, text))),
    )  # fmt: skip
    burner = FakeBurner()
    win = MainWindow(lib, burner_factory=lambda: burner)
    win.shown = shown
    win.burner = burner
    yield win
    if win.burn_dialog is not None:
        win.burn_dialog.close()
    win.close()
    win.deleteLater()


def test_burn_is_in_the_disc_menu_and_the_panel(window):
    assert window.act_burn.shortcut().toString() == "Ctrl+B"
    assert window.act_burn.text().replace("&", "") == "Burn Disc…"
    assert window.disc_panel.burn_button.text() == "Burn…"
    assert window.disc_panel.burn_button.isEnabled()


def test_the_panel_button_opens_the_burn_window_for_the_current_disc(window, wait_for):
    window.disc_panel.burn_button.click()
    dialog = window.burn_dialog
    assert dialog is not None and dialog.isVisible()
    assert dialog.windowTitle() == "Burn Disc"
    assert wait_for(lambda: not dialog._busy)
    assert "Test Drive" in dialog.drive_label.text()


def test_a_finished_burn_shows_up_under_burned_in_the_panel(window, lib, wait_for):
    window.disc_panel.burn_button.click()
    dialog = window.burn_dialog
    assert wait_for(lambda: not dialog._busy)
    burn(dialog, wait_for)
    panel = window.disc_panel
    assert panel.tabs.currentIndex() == 1  # switched to Burned
    assert panel.current_burn_id() == lib.burns()[0].id
    assert [panel.burn_model.track_at(r).title for r in range(2)] == ["Alpha", "Beta"]


def test_burning_with_no_disc_just_says_to_make_one(qapp, monkeypatch):
    shown = []
    monkeypatch.setattr(
        QMessageBox, "information",
        staticmethod(lambda _p, title, text: shown.append((title, text))),
    )  # fmt: skip
    with Library.open(":memory:") as empty:
        win = MainWindow(empty, burner_factory=lambda: FakeBurner())
        win.burn_current_disc()
        assert win.burn_dialog is None
        assert shown and "Make a disc" in shown[0][1]
        assert not win.disc_panel.burn_button.isEnabled()
        win.close()
        win.deleteLater()


def test_a_genuine_bug_is_still_logged_as_an_error(
    qapp, lib, disc_id, wait_for, caplog
):
    class Broken(FakeBurner):
        def media_info(self, device):
            raise ValueError("a real bug, not a drive problem")

    dialog = BurnDialog(lib, disc_id, burner_factory=lambda: Broken())
    assert wait_for(lambda: not dialog._busy)
    assert "a real bug" in dialog.problems_label.text()  # still shown to the user
    assert any(r.levelname == "ERROR" for r in caplog.records)
    dialog.close()
