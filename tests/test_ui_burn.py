"""The burn window, driven with a fake burner and real rendered audio."""

import shutil
import subprocess

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QMessageBox

from dubcrate import cdj_card as cc
from dubcrate import loudness, settings
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

    def __init__(self, media=ROOMY, fail=None, drives=()):
        self.media = media
        self.fail = fail
        self.burned = []  # (disc, options)
        self.media_calls = 0
        self.devices_asked = []  # the `device` each media_info was asked about
        self._drives = list(drives)

    def drives(self):
        return list(self._drives)

    def media_info(self, device):
        self.media_calls += 1
        self.devices_asked.append(device)
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

    def make(burner=None, disc=None, export_only=False):
        burner = burner or FakeBurner()
        dialog = BurnDialog(
            lib, disc or disc_id, burner_factory=lambda: burner, export_only=export_only
        )
        made.append(dialog)
        if (disc or disc_id) == disc_id and not export_only:
            assert wait_for(lambda: not dialog._busy)  # the drive check finished
        return dialog, burner

    yield make
    for dialog in made:
        dialog.close()
        dialog.deleteLater()


def burn(dialog, wait_for):
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


def test_burn_is_ready_once_the_drive_has_a_disc_and_there_are_no_checkboxes(
    make_dialog,
):
    dialog, _burner = make_dialog()
    assert dialog.burn_button.isEnabled()
    assert not hasattr(dialog, "confirm_check") and not hasattr(
        dialog, "simulate_check"
    )
    assert not dialog.check_button.isVisibleTo(dialog)  # (nothing to look at again)
    assert not dialog.log.isVisibleTo(dialog)  # (nothing to say until a burn starts)


def test_no_button_in_the_window_is_the_return_key_default(make_dialog):
    dialog, _burner = make_dialog()
    for button in (dialog.burn_button, dialog.close_button, dialog.check_button):
        assert not button.autoDefault() and not button.isDefault()


def test_return_never_starts_a_burn(make_dialog):
    dialog, burner = make_dialog()
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


def test_the_default_speed_is_10x(make_dialog):
    dialog, _burner = make_dialog()
    assert dialog.speed_combo.currentData() == 10


# -- burning ------------------------------------------------------------------


def test_by_default_a_burn_ejects_the_disc_itself_and_checks_nothing(
    make_dialog, wait_for
):
    dialog, burner = make_dialog()
    burn(dialog, wait_for)
    [(_disc, options)] = burner.burned
    assert options.eject is True


def test_with_a_readable_drive_an_audio_burn_keeps_the_disc_in_and_checks_its_toc(
    make_dialog, wait_for, monkeypatch
):
    checks = []
    monkeypatch.setattr("dubcrate.ui.burn_dialog.can_read_toc", lambda: True)
    monkeypatch.setattr(
        "dubcrate.ui.burn_dialog.check_burn",
        lambda sectors, gap, say, **_kw: checks.append((list(sectors), gap)),
    )
    dialog, burner = make_dialog()
    burn(dialog, wait_for)
    [(_disc, options)] = burner.burned
    assert options.eject is False  # (the check opens the tray itself)
    assert checks == [([150, 225], 0)]  # the burned tracks' lengths, and the gap


def test_the_toc_the_drive_read_back_is_kept_for_the_disc(
    lib, disc_id, make_dialog, wait_for, monkeypatch
):
    monkeypatch.setattr("dubcrate.ui.burn_dialog.can_read_toc", lambda: True)

    def check(sectors, gap, say, record):
        planned = cc.toc_from_lengths(sectors, gap)
        starts, lead_out = planned
        record(planned, ([starts[0], starts[1] + 1], lead_out + 1))

    monkeypatch.setattr("dubcrate.ui.burn_dialog.check_burn", check)
    dialog, _burner = make_dialog()
    burn(dialog, wait_for)
    [known] = lib.disc_tocs(disc_id)
    assert known.source == "burn"
    assert known.actual == ([150, known.planned[0][1] + 1], known.planned[1] + 1)


def test_a_burn_that_could_not_be_read_back_leaves_the_known_tocs_alone(
    lib, disc_id, make_dialog, wait_for
):
    lib.add_disc_toc(disc_id, ([150, 300], 500), ([150, 301], 501))
    dialog, _burner = make_dialog()  # (conftest's check_burn reads nothing)
    burn(dialog, wait_for)
    assert len(lib.disc_tocs(disc_id)) == 1


def test_a_check_that_says_something_puts_it_in_the_burn_log(
    make_dialog, wait_for, monkeypatch
):
    monkeypatch.setattr("dubcrate.ui.burn_dialog.can_read_toc", lambda: True)
    monkeypatch.setattr(
        "dubcrate.ui.burn_dialog.check_burn",
        lambda sectors, gap, say, **_kw: say("Checked: as planned."),
    )
    dialog, _burner = make_dialog()
    burn(dialog, wait_for)
    assert "Checked: as planned." in dialog.log.toPlainText()
    assert "marked as burned" in dialog.log.toPlainText()


def test_a_burn_renders_burns_stores_lengths_and_marks_the_disc_burned(
    lib, disc_id, make_dialog, wait_for
):
    dialog, burner = make_dialog()
    saved = []
    dialog.burned.connect(saved.append)
    assert lib.disc(disc_id).last_burned_at is None
    burn(dialog, wait_for)

    [(disc, options)] = burner.burned
    assert [t.title for t in disc.tracks] == ["Alpha", "Beta"]
    assert all(t.wav.name.endswith(".wav") for t in disc.tracks)
    assert (options.speed, options.simulate, options.pregap_sectors) == (10, False, 0)
    assert disc.performer == "Same"

    assert lib.disc(disc_id).last_burned_at is not None  # a note, not a copy
    assert saved == [disc_id]
    assert [e.track.cd_sectors for e in lib.disc_entries(disc_id)] == [
        150,
        225,
    ]  # exact
    assert all(t.cd_sectors is not None for t in lib.tracks())  # stored on this thread
    assert all(
        t.waveform is not None and len(t.waveform) == 100 for t in lib.tracks()
    )  # and the waveforms Sync would write: nothing left for it to analyse

    text = dialog.log.toPlainText()
    assert "rendering 1/2: Alpha" in text and "fake burner: done" in text
    assert "marked as burned" in text


def test_print_labels_appears_only_after_a_real_burn_and_asks_for_that_disc(
    disc_id, make_dialog, wait_for
):
    dialog, _burner = make_dialog()
    assert not dialog.labels_button.isVisibleTo(dialog)  # (nothing burned yet)
    asked = []
    dialog.labels_requested.connect(asked.append)
    burn(dialog, wait_for)
    assert dialog.labels_button.isVisibleTo(dialog) and dialog.labels_button.isEnabled()
    dialog.labels_button.click()
    assert asked == [disc_id]
    assert (
        not dialog.isVisible()
    )  # (the burn window closed itself: no label window over a modal one)


def test_print_labels_after_a_burn_opens_the_label_window_with_that_disc_ticked(
    window, disc_id, wait_for
):
    window.disc_view.burn_button.click()
    dialog = window.burn_dialog
    assert wait_for(lambda: not dialog._busy)
    burn(dialog, wait_for)
    dialog.labels_button.click()
    labels = window.label_dialog
    assert labels.isVisible()
    assert [d.id for d in labels.ticked_discs()] == [disc_id]
    labels.close()


# -- more than one drive ---------------------------------------------------------------------------------------

from dubcrate.burn import Drive

TWO = (Drive("disk3", "LITE-ON eSAU108"), Drive("disk4", "ASUS SDRW-08D2S"))


def test_one_drive_or_none_shows_no_drive_choice(qapp, lib, disc_id, wait_for):
    for drives in ((), (Drive("disk3", "LITE-ON eSAU108"),)):
        burner = FakeBurner(drives=drives)
        dialog = BurnDialog(lib, disc_id, burner_factory=lambda b=burner: b)
        assert wait_for(lambda d=dialog: not d._busy)
        assert not dialog.drive_combo.isVisibleTo(dialog)
        assert burner.devices_asked == [drives[0].id if drives else None]
        dialog.close()


def test_two_drives_show_a_choice_that_starts_on_the_first(
    qapp, lib, disc_id, wait_for
):
    burner = FakeBurner(drives=TWO)
    dialog = BurnDialog(lib, disc_id, burner_factory=lambda: burner)
    assert wait_for(lambda: not dialog._busy)
    assert dialog.drive_combo.isVisibleTo(dialog)
    assert [dialog.drive_combo.itemText(i) for i in range(2)] == [
        "LITE-ON eSAU108",
        "ASUS SDRW-08D2S",
    ]
    assert dialog.drive_combo.currentData() == "disk3" and burner.devices_asked == [
        "disk3"
    ]
    dialog.close()


def test_picking_the_other_drive_looks_at_that_drive_and_remembers_it(
    qapp, lib, disc_id, wait_for
):
    from dubcrate import settings

    burner = FakeBurner(drives=TWO)
    dialog = BurnDialog(lib, disc_id, burner_factory=lambda: burner)
    assert wait_for(lambda: not dialog._busy)
    dialog.drive_combo.setCurrentIndex(1)
    dialog.drive_combo.activated.emit(1)
    assert wait_for(lambda: len(burner.devices_asked) == 2 and not dialog._busy)
    assert burner.devices_asked == ["disk3", "disk4"]
    assert settings.get_burn_drive() == "ASUS SDRW-08D2S"
    assert dialog.drive_combo.currentData() == "disk4"
    dialog.close()


def test_the_drive_used_last_time_is_chosen_again_by_name(qapp, lib, disc_id, wait_for):
    from dubcrate import settings

    settings.set_burn_drive("ASUS SDRW-08D2S")
    burner = FakeBurner(drives=TWO)
    dialog = BurnDialog(lib, disc_id, burner_factory=lambda: burner)
    assert wait_for(lambda: not dialog._busy)
    assert dialog.drive_combo.currentData() == "disk4" and burner.devices_asked == [
        "disk4"
    ]
    dialog.close()


def test_a_remembered_drive_that_is_unplugged_falls_back_to_the_first(
    qapp, lib, disc_id, wait_for
):
    from dubcrate import settings

    settings.set_burn_drive("A drive that has gone")
    burner = FakeBurner(drives=TWO)
    dialog = BurnDialog(lib, disc_id, burner_factory=lambda: burner)
    assert wait_for(lambda: not dialog._busy)
    assert dialog.drive_combo.currentData() == "disk3" and burner.devices_asked == [
        "disk3"
    ]
    dialog.close()


def test_the_burn_is_made_on_the_chosen_drive(
    qapp, lib, disc_id, wait_for, monkeypatch
):
    monkeypatch.setattr("dubcrate.ui.burn_dialog.can_read_toc", lambda: False)
    burner = FakeBurner(drives=TWO)
    dialog = BurnDialog(lib, disc_id, burner_factory=lambda: burner)
    assert wait_for(lambda: not dialog._busy)
    dialog.drive_combo.setCurrentIndex(1)
    dialog.drive_combo.activated.emit(1)
    assert wait_for(lambda: len(burner.devices_asked) == 2 and not dialog._busy)
    burn(dialog, wait_for)
    [(_disc, options)] = burner.burned
    assert options.device == "disk4"


def test_a_burn_on_the_first_drive_is_checked_afterwards_and_on_another_it_is_not(
    qapp, lib, disc_id, wait_for, monkeypatch
):
    checks = []
    monkeypatch.setattr("dubcrate.ui.burn_dialog.can_read_toc", lambda: True)
    monkeypatch.setattr(
        "dubcrate.ui.burn_dialog.check_burn",
        lambda sectors, gap, say, **_kw: checks.append(sectors),
    )
    for pick, checked in ((0, True), (1, False)):
        burner = FakeBurner(drives=TWO)
        dialog = BurnDialog(lib, disc_id, burner_factory=lambda b=burner: b)
        assert wait_for(lambda d=dialog: not d._busy)
        dialog.drive_combo.setCurrentIndex(pick)
        dialog.drive_combo.activated.emit(pick)
        assert wait_for(
            lambda d=dialog, b=burner: len(b.devices_asked) == 2 and not d._busy
        )
        before = len(checks)
        burn(dialog, wait_for)
        assert (len(checks) - before == 1) is checked
        [(_disc, options)] = burner.burned
        assert options.eject is (
            not checked
        )  # (the burner ejects it itself when Dubcrate does not check it)
        assert ("not checked after the burn" in dialog.log.toPlainText()) is (
            not checked
        )
        dialog.close()


def test_the_drive_choice_is_locked_while_working_and_after_a_burn(
    qapp, lib, disc_id, wait_for
):
    burner = FakeBurner(drives=TWO)
    dialog = BurnDialog(lib, disc_id, burner_factory=lambda: burner)
    assert wait_for(lambda: not dialog._busy)
    assert dialog.drive_combo.isEnabled()
    burn(dialog, wait_for)
    assert (
        not dialog.drive_combo.isEnabled()
    )  # (this disc is done: no other drive to pick for it)
    dialog.close()


def test_a_burned_disc_is_still_an_ordinary_editable_playlist(
    lib, disc_id, make_dialog, wait_for
):
    dialog, _burner = make_dialog()
    burn(dialog, wait_for)
    lib.remove_from_disc(disc_id, [0])
    lib.set_entry_text(disc_id, 0, "title", "Edited After Burning")
    assert lib.disc(disc_id).track_count == 1
    assert lib.disc_entries(disc_id)[0].title == "Edited After Burning"


def test_after_a_real_burn_the_dialog_cannot_burn_again_and_can_close(
    make_dialog, wait_for
):
    dialog, burner = make_dialog()
    burn(dialog, wait_for)
    assert not dialog.burn_button.isEnabled()  # the disc is no longer blank
    assert dialog.close_button.isEnabled()
    assert len(burner.burned) == 1


def test_a_failed_burn_says_so_and_records_nothing(lib, make_dialog, wait_for, caplog):
    dialog, burner = make_dialog(FakeBurner(fail="drive on fire"))
    burn(dialog, wait_for)
    assert burner.burned == [] and all(d.last_burned_at is None for d in lib.discs())
    assert "Failed: drive on fire" in dialog.log.toPlainText()
    assert [r for r in caplog.records if r.levelname == "ERROR"] == []
    assert "failed" in dialog.status_label.text()
    assert dialog.close_button.isEnabled()
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
    assert [(e.title, e.artist) for e in lib.disc_entries(disc_id)] == [
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
    assert not dialog.burn_button.isEnabled()
    assert "disc over capacity by" in dialog.problems_label.text()
    assert "use a larger disc or remove tracks" in dialog.problems_label.text()
    assert dialog.drive_label.property("level") == "critical"
    assert burner.burned == []


def test_no_disc_in_the_drive_blocks_the_burn(make_dialog):
    dialog, _burner = make_dialog(FakeBurner(MediaInfo(drive="D", present=False)))
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
                raise BurnError("No optical drives found")
            return self.media

    burner = Unreachable()
    dialog = BurnDialog(lib, disc_id, burner_factory=lambda: burner)
    assert wait_for(lambda: not dialog._busy)
    assert not dialog.burn_button.isEnabled()
    assert "No optical drives found" in dialog.drive_label.text()
    assert dialog.check_button.isVisibleTo(dialog)  # (looking again might help)
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
    assert not dialog.drive_label.isVisibleTo(
        dialog
    )  # (no drive line: it isn't looked at)
    assert not dialog.check_button.isVisibleTo(dialog)
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
    assert level == "critical" and text == "The drive. This disc isn't blank"
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
    win.open_disc(disc_id)  # a disc is burned from where it is open
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
    assert window.disc_view.burn_button.text() == "Burn…"
    assert window.disc_view.burn_button.isEnabled()


def test_the_burn_button_opens_the_burn_window_for_the_open_disc(window, wait_for):
    window.disc_view.burn_button.click()
    dialog = window.burn_dialog
    assert dialog is not None and dialog.isVisible()
    assert dialog.windowTitle() == "Burn Disc"
    assert wait_for(lambda: not dialog._busy)
    assert "Test Drive" in dialog.drive_label.text()


def test_a_finished_burn_leaves_the_disc_open_and_the_tree_says_when_it_was_burned(
    window, lib, disc_id, wait_for
):
    from dubcrate.ui.models import SELECTION_ROLE, find_selection

    window.disc_view.burn_button.click()
    dialog = window.burn_dialog
    assert wait_for(lambda: not dialog._busy)
    burn(dialog, wait_for)
    assert window.disc_view.current_disc_id() == disc_id  # still open, still editable
    assert window.center.currentIndex() == 1
    index = find_selection(window.disc_tree.model(), ("disc", disc_id))
    assert "Last burned" in index.data(Qt.ItemDataRole.ToolTipRole)
    assert "marked as burned" in window.statusBar().currentMessage()
    assert index.data(SELECTION_ROLE) == ("disc", disc_id)


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
        assert shown and "Open a disc" in shown[0][1]
        assert not win.disc_view.burn_button.isEnabled()
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
    assert "a real bug" in dialog.drive_label.text()  # still shown to the user
    assert any(r.levelname == "ERROR" for r in caplog.records)
    dialog.close()


def test_a_computer_that_cannot_burn_says_so_up_front(qapp, lib, disc_id, monkeypatch):
    """No cdrdao (or not a Mac): the window explains it at once, never starts a burner, and
    can't be talked into burning."""
    monkeypatch.setattr("dubcrate.ui.burn_dialog.default_backend", lambda: "cdrdao")
    monkeypatch.setattr(
        "dubcrate.ui.burn_dialog.backend_problem",
        lambda name: f"{name} was not found. Install it.",
    )

    def no_burner(name):
        raise AssertionError("a burner was made on a computer that can't burn")

    monkeypatch.setattr("dubcrate.ui.burn_dialog.get_burner", no_burner)
    dialog = BurnDialog(lib, disc_id)
    try:
        assert not dialog._busy and dialog._media is None
        assert "cdrdao was not found" in dialog.problems_label.text()
        assert dialog.drive_label.property("level") == "critical"
        assert not dialog.check_button.isVisibleTo(dialog)
        assert not dialog.burn_button.isEnabled()
        assert not dialog.speed_combo.isEnabled()
        assert "can't burn" in dialog.drive_label.text()
        assert "Export Disc as Files" in dialog.drive_label.text()  # the way out
    finally:
        dialog.close()
        dialog.deleteLater()


def test_export_writes_the_disc_as_files_without_a_drive(make_dialog, wait_for, tmp_path, monkeypatch):  # fmt: skip
    dialog, burner = make_dialog(export_only=True)
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr("dubcrate.ui.burn_dialog.ask_export_folder", lambda _parent: str(out))  # fmt: skip
    assert (
        dialog.export_button.isEnabled()
    )  # (no blank CD-R needed, and nothing is burned)
    dialog.export_button.click()
    assert wait_for(lambda: not dialog._busy)
    folder = out / "Friday export"
    assert sorted(p.name for p in folder.iterdir()) == ["01 Same - Alpha.wav", "02 Same - Beta.wav", "README.txt", "disc.toc"]  # fmt: skip
    assert "Exported to" in dialog.status_label.text() and str(folder) in dialog.status_label.text()  # fmt: skip
    assert burner.burned == []
    assert not dialog._finished  # the disc is not marked as burned
    assert dialog._lib.disc(dialog._disc_id).last_burned_at is None


def test_a_cancelled_export_does_nothing(make_dialog, monkeypatch, tmp_path):
    dialog, _ = make_dialog(export_only=True)
    monkeypatch.setattr("dubcrate.ui.burn_dialog.ask_export_folder", lambda _parent: "")
    dialog.export_button.click()
    assert not dialog._busy and not list(tmp_path.glob("**/README.txt"))


def test_a_failed_export_is_shown_in_the_window(make_dialog, wait_for, tmp_path, monkeypatch, caplog):  # fmt: skip
    dialog, _ = make_dialog(export_only=True)
    monkeypatch.setattr("dubcrate.ui.burn_dialog.ask_export_folder", lambda _parent: str(tmp_path / "gone"))  # fmt: skip
    dialog.export_button.click()
    assert wait_for(lambda: not dialog._busy)
    assert "is not a folder" in dialog.log.toPlainText()
    assert dialog.status_label.property("level") == "critical"
    assert [
        r for r in caplog.records if r.levelname == "ERROR"
    ] == []  # expected, not a crash


def test_the_export_window_has_no_drive_speed_or_burn_button(make_dialog):
    dialog, burner = make_dialog(export_only=True)
    assert dialog.windowTitle() == "Export Disc"
    assert dialog.export_button.isVisibleTo(dialog) and dialog.export_button.isEnabled()
    for widget in (dialog.burn_button, dialog.drive_label, dialog.check_button):
        assert not widget.isVisibleTo(dialog)
    assert burner.media_calls == 0  # (no drive is asked about)


def test_a_disc_that_cannot_be_burned_cannot_be_exported_either(make_dialog, lib):
    empty = lib.create_disc("Empty")
    dialog, _ = make_dialog(disc=empty, export_only=True)
    assert not dialog.export_button.isEnabled()


# -- level matching ---------------------------------------------------------------------------


DEFAULT = (
    loudness.DEFAULT_TARGET_LUFS
)  # what level matching aims at unless told otherwise


def choose_levels(dialog, key):
    """What the Levels row does when it is ticked and a preset picked (`key` None: unticked)."""
    if key is not None:
        index = dialog.levels_combo.findData(key)
        dialog.levels_combo.setCurrentIndex(index)
        dialog.levels_combo.activated.emit(index)
    dialog.levels_check.setChecked(key is not None)


def test_the_levels_start_off_and_nothing_is_measured(make_dialog, lib):
    dialog, _ = make_dialog()
    assert not dialog.levels_check.isChecked() and not dialog.levels_combo.isEnabled()
    assert dialog._plan.levels_note == "" and dialog.levels_combo.currentData() == "-10"
    assert all(t.loudness is None for t in lib.tracks())


def test_matching_measures_once_then_shows_the_gains(
    make_dialog, wait_for, lib, monkeypatch
):
    from dubcrate.ui import burn_dialog

    measured = []
    real = burn_dialog.measure_loudness
    monkeypatch.setattr(
        burn_dialog,
        "measure_loudness",
        lambda tracks, progress=None, cancel=None: (
            measured.append(len(tracks)) or real(tracks, progress, cancel=cancel)
        ),
    )
    dialog, _ = make_dialog()
    choose_levels(dialog, str(DEFAULT))
    assert wait_for(lambda: not dialog._measuring)
    assert measured == [2]  # both tracks, on a worker
    assert all(t.loudness for t in lib.tracks())  # and remembered
    assert dialog.levels_label.text().startswith(("Gain ", "Gains "))
    assert dialog._plan.levels_note.startswith(f"Levels matched to {DEFAULT} LUFS")
    assert dialog._plan.levels_short == dialog.levels_label.text()
    assert dialog.levels_combo.isEnabled()
    assert any(t.gain_db for t in dialog._plan.tracks)

    before = [t.gain_db for t in dialog._plan.tracks]
    choose_levels(dialog, "-14")  # a new target: new gains, nothing measured again
    assert "-14 LUFS" in dialog._plan.levels_note and measured == [2]
    assert [t.gain_db for t in dialog._plan.tracks] != before
    choose_levels(
        dialog, "peak"
    )  # peak normalising: its own note, still nothing measured
    assert dialog._plan.levels_note.startswith("Peak-normalised to -0.3 dBTP")
    assert measured == [2]
    choose_levels(dialog, None)  # and back
    assert [t.gain_db for t in dialog._plan.tracks] == [0.0, 0.0]
    assert dialog.levels_label.text() == "" and not dialog.levels_combo.isEnabled()


def test_a_burn_with_matching_on_writes_tracks_at_the_target(make_dialog, wait_for):
    from dubcrate.loudness import measure

    class Measuring(FakeBurner):
        """Measures the WAVs while it holds them (a burn's files are gone when it ends)."""

        levels = None

        def burn(self, disc, options, progress):
            self.levels = [measure(t.wav).lufs for t in disc.tracks]
            super().burn(disc, options, progress)

    burner = Measuring()
    dialog, _ = make_dialog(burner)
    choose_levels(dialog, str(DEFAULT))
    assert wait_for(lambda: not dialog._measuring)
    assert dialog.burn_button.isEnabled()
    burn(dialog, wait_for)
    assert burner.levels == [pytest.approx(DEFAULT, abs=0.3)] * 2
    assert (
        f"Levels matched to {DEFAULT} LUFS" in dialog.log.toPlainText()
    )  # the log says so


def test_the_settings_can_have_matching_on_when_the_window_opens(
    qapp, lib, disc_id, wait_for
):
    settings.set_levels_enabled(True)
    settings.set_levels_choice("-12")
    dialog = BurnDialog(lib, disc_id, burner_factory=lambda: FakeBurner())
    try:
        assert dialog.levels_check.isChecked() and dialog.levels_combo.currentData() == "-12"  # fmt: skip
        assert wait_for(lambda: not dialog._measuring and not dialog._busy)
        assert dialog._plan.levels_note.startswith("Levels matched to -12 LUFS")
        assert dialog.levels_label.text().startswith(("Gain ", "Gains "))
    finally:
        dialog.close()
        dialog.deleteLater()


def test_a_failed_measurement_blocks_the_burn_and_says_how_to_get_past_it(make_dialog, wait_for, monkeypatch):  # fmt: skip
    from dubcrate.render import RenderError
    from dubcrate.ui import burn_dialog

    def broken(tracks, progress=None, cancel=None):
        raise RenderError("ffmpeg fell over")

    monkeypatch.setattr(burn_dialog, "measure_loudness", broken)
    dialog, _ = make_dialog()
    choose_levels(dialog, str(DEFAULT))
    assert wait_for(lambda: not dialog._measuring)
    assert not dialog.burn_button.isEnabled()
    assert "ffmpeg fell over" in dialog.problems_label.text()
    assert "untick Normalise" in dialog.problems_label.text()
    choose_levels(dialog, None)  # the way out
    assert dialog.burn_button.isEnabled() and dialog.problems_label.text() == ""


def test_an_mp3_disc_has_no_levels_row(make_dialog, lib):
    mp3_disc = lib.create_disc("M", kind="mp3")
    lib.add_to_disc(mp3_disc, [t.id for t in lib.tracks(sort="title")])
    dialog, _ = make_dialog(disc=mp3_disc)
    assert not dialog._has_levels and dialog._plan.levels_note == ""


def test_exporting_with_matching_on_writes_matched_files(make_dialog, wait_for, tmp_path, monkeypatch):  # fmt: skip
    from dubcrate.loudness import measure

    dialog, _ = make_dialog()
    choose_levels(dialog, str(DEFAULT))
    assert wait_for(lambda: not dialog._measuring)
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr("dubcrate.ui.burn_dialog.ask_export_folder", lambda _parent: str(out))  # fmt: skip
    dialog.export_button.click()
    assert wait_for(lambda: not dialog._busy)
    folder = out / "Friday export"
    assert f"Levels matched to {DEFAULT} LUFS" in (folder / "README.txt").read_text(
        encoding="utf-8"
    )
    assert [measure(w).lufs for w in sorted(folder.glob("*.wav"))] == [pytest.approx(DEFAULT, abs=0.3)] * 2  # fmt: skip


def test_measuring_the_levels_can_be_cancelled_what_was_measured_is_kept_and_the_burn_waits(
    make_dialog, wait_for, lib, monkeypatch
):
    import time

    from dubcrate.loudness import Loudness
    from dubcrate.ui import burn_dialog

    started = []
    first = lib.disc_entries(lib.discs()[0].id)[0].track

    def slow(tracks, progress=None, cancel=None):
        started.append(True)
        while True:
            cancel.check(({first.id: Loudness(-12.0, -1.0)}, []))
            time.sleep(0.005)

    monkeypatch.setattr(burn_dialog, "measure_loudness", slow)
    dialog, _ = make_dialog()
    assert not dialog.cancel_levels_button.isVisibleTo(dialog)
    choose_levels(dialog, str(DEFAULT))
    assert wait_for(lambda: bool(started))
    assert dialog._measuring and dialog.cancel_levels_button.isVisibleTo(dialog)
    dialog.cancel_levels_button.click()
    assert (
        not dialog.cancel_levels_button.isEnabled()
        and dialog.levels_label.text() == "Cancelling…"
    )
    assert wait_for(lambda: not dialog._measuring)
    assert not dialog.cancel_levels_button.isVisibleTo(dialog)
    assert next(t for t in lib.tracks() if t.id == first.id).loudness == Loudness(
        -12.0, -1.0
    )  # kept
    assert (
        not dialog.burn_button.isEnabled()
    )  # the burn waits: it was asked to match levels
    assert "was cancelled" in dialog.problems_label.text() and "untick Normalise" in dialog.problems_label.text()  # fmt: skip
    choose_levels(dialog, None)  # the way out
    assert dialog.burn_button.isEnabled() and dialog.problems_label.text() == ""


# -- cancelling while the disc is being prepared ------------------------------------------------------------


def _slow_render(started, finish=None):
    """A stand-in for `render_plan` that takes its time and stops when the token says so."""
    import time

    def render(plan, workdir, say, cancel):
        started.set()
        while not cancel.cancelled and not (finish and finish.is_set()):
            time.sleep(0.01)
        cancel.check()  # (raises Cancelled if it was cancelled)
        from dubcrate.build import Rendered
        from dubcrate.burn.base import Disc

        return Rendered(Disc(title="t", performer="p"), 0, {})

    return render


def test_cancel_stops_the_preparing_and_nothing_is_written(make_dialog, lib, disc_id, wait_for, monkeypatch):  # fmt: skip
    import threading

    started = threading.Event()
    monkeypatch.setattr("dubcrate.ui.burn_dialog.render_plan", _slow_render(started))
    dialog, burner = make_dialog()
    assert not dialog.cancel_burn_button.isVisibleTo(dialog)  # (nothing to cancel yet)
    dialog.burn_button.click()
    assert wait_for(started.is_set)
    assert dialog.cancel_burn_button.isVisibleTo(dialog) and dialog.cancel_burn_button.isEnabled()  # fmt: skip
    dialog.cancel_burn_button.click()
    assert not dialog.cancel_burn_button.isEnabled()  # (asked once)
    assert wait_for(lambda: not dialog._busy)
    assert burner.burned == []
    assert "Cancelled. Nothing was written" in dialog.log.toPlainText()
    assert "cancelled before anything was written" in dialog.status_label.text()
    assert lib.disc(disc_id).last_burned_at is None  # (it was never burned)
    assert not dialog.cancel_burn_button.isVisibleTo(dialog)  # fmt: skip
    assert not dialog.labels_button.isVisibleTo(
        dialog
    )  # (no "print labels": nothing was burned)


def test_a_cancelled_burn_can_be_started_again(make_dialog, wait_for, monkeypatch):
    import threading

    from dubcrate import build

    started, real = threading.Event(), build.render_plan
    slow = _slow_render(started)
    calls = []

    def first_slow_then_real(plan, workdir, say, cancel):
        calls.append(1)
        return slow(plan, workdir, say, cancel) if len(calls) == 1 else real(plan, workdir, say, cancel)  # fmt: skip

    monkeypatch.setattr("dubcrate.ui.burn_dialog.render_plan", first_slow_then_real)
    monkeypatch.setattr("dubcrate.ui.burn_dialog.can_read_toc", lambda: False)
    dialog, burner = make_dialog()
    dialog.burn_button.click()
    assert wait_for(started.is_set)
    dialog.cancel_burn_button.click()
    assert wait_for(lambda: not dialog._busy)
    assert burner.burned == []
    burn(dialog, wait_for)  # (the disc is not marked, the box can be ticked again)
    assert len(burner.burned) == 1


def test_once_writing_starts_cancel_is_gone_and_does_nothing(make_dialog, wait_for, monkeypatch):  # fmt: skip
    import threading

    release = threading.Event()
    started = threading.Event()
    monkeypatch.setattr("dubcrate.ui.burn_dialog.render_plan", _slow_render(started, finish=started))  # fmt: skip

    class Holding(FakeBurner):
        def burn(self, disc, options, progress):
            release.wait(10)  # (the writing: it can't be stopped)
            super().burn(disc, options, progress)

    dialog, burner = make_dialog(Holding())
    dialog.burn_button.click()
    assert wait_for(lambda: not dialog.cancel_burn_button.isVisibleTo(dialog) and dialog._busy)  # fmt: skip
    assert dialog._burn_cancel is None
    dialog._cancel_burn()  # (a late press, as if the button were still there)
    release.set()
    assert wait_for(lambda: not dialog._busy)
    assert len(burner.burned) == 1  # (it was written, as asked)
    assert "Cancelled" not in dialog.log.toPlainText()


def test_a_disc_that_is_not_blank_blocks_the_burn(make_dialog):
    dialog, burner = make_dialog(
        FakeBurner(
            MediaInfo(drive="D", present=True, blank=False, free_sectors=359_849)
        )
    )
    assert not dialog.burn_button.isEnabled()
    assert dialog.drive_label.property("level") == "critical"
    assert dialog.check_button.isVisibleTo(
        dialog
    )  # (put in another disc and look again)
    assert burner.burned == []


def test_cancel_stops_an_export_and_removes_the_half_made_folder(make_dialog, wait_for, tmp_path, monkeypatch):  # fmt: skip
    import threading

    started = threading.Event()
    monkeypatch.setattr("dubcrate.export.render_plan", _slow_render(started))
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr("dubcrate.ui.burn_dialog.ask_export_folder", lambda _parent: str(out))  # fmt: skip
    dialog, _ = make_dialog(export_only=True)
    assert not dialog.cancel_burn_button.isVisibleTo(dialog)
    dialog.export_button.click()
    assert wait_for(started.is_set)
    assert dialog.cancel_burn_button.isVisibleTo(dialog) and dialog.cancel_burn_button.isEnabled()  # fmt: skip
    assert [p.name for p in out.iterdir()] == ["Friday export"]  # (made, half done)
    dialog.cancel_burn_button.click()
    assert wait_for(lambda: not dialog._busy)
    assert list(out.iterdir()) == []  # the folder was made here, so it goes
    assert "Cancelled. Nothing was exported" in dialog.log.toPlainText()
    assert "export was cancelled" in dialog.status_label.text()
    assert not dialog.cancel_burn_button.isVisibleTo(dialog)
    assert dialog.export_button.isEnabled()  # (it can be done again)


# -- a disc put in while the window is open ----------------------------------------------------------


def test_a_disc_put_in_after_the_window_opened_is_noticed_without_check_again(
    qapp, lib, disc_id, wait_for
):
    state = {"now": "empty"}
    burner = FakeBurner()
    dialog = BurnDialog(
        lib,
        disc_id,
        burner_factory=lambda: burner,
        probe=lambda device=None: state["now"],
    )
    assert wait_for(lambda: not dialog._busy) and burner.media_calls == 1
    dialog._watch_disc()
    assert burner.media_calls == 1  # nothing changed: the drive is not asked again
    state["now"] = "loading"
    dialog._watch_disc()
    assert burner.media_calls == 1  # a disc still spinning up is waited for
    state["now"] = "disc"
    dialog._watch_disc()
    assert wait_for(lambda: burner.media_calls == 2 and not dialog._busy)
    dialog._watch_disc()
    assert burner.media_calls == 2  # (settled: no more looks)
    state["now"] = "empty"  # taken out again
    dialog._watch_disc()
    assert wait_for(lambda: burner.media_calls == 3 and not dialog._busy)
    dialog.close()


def test_a_drive_that_cannot_say_is_never_asked_again_by_itself(
    qapp, lib, disc_id, wait_for
):
    burner = FakeBurner()
    dialog = BurnDialog(
        lib, disc_id, burner_factory=lambda: burner, probe=lambda device=None: None
    )
    assert wait_for(lambda: not dialog._busy)
    for _ in range(3):
        dialog._watch_disc()
    assert burner.media_calls == 1  # (Check Again is still there for it)
    dialog.close()


def test_the_watch_runs_only_while_the_window_is_shown_and_never_for_an_export(
    qapp, lib, disc_id, wait_for
):
    burner = FakeBurner()
    dialog = BurnDialog(lib, disc_id, burner_factory=lambda: burner)
    assert wait_for(lambda: not dialog._busy)
    assert not dialog._disc_timer.isActive()  # (not shown yet)
    dialog.show()
    assert dialog._disc_timer.isActive()
    dialog.hide()
    assert not dialog._disc_timer.isActive()
    dialog.close()
    export = BurnDialog(lib, disc_id, burner_factory=lambda: burner, export_only=True)
    export.show()
    assert not export._disc_timer.isActive()  # (no drive to watch)
    export.close()
