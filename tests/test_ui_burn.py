"""The burn window, driven with a fake burner and real rendered audio."""

import shutil
import subprocess

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QMessageBox

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


def test_the_default_speed_is_10x(make_dialog):
    dialog, _burner = make_dialog()
    assert dialog.speed_combo.currentData() == 10


# -- burning ------------------------------------------------------------------


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

    text = dialog.log.toPlainText()
    assert "rendering 1/2: Alpha" in text and "fake burner: done" in text
    assert "marked as burned" in text


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
    assert all(d.last_burned_at is None for d in lib.discs())  # a test is not a burn
    assert "not marked as burned" in dialog.status_label.text()
    assert not dialog.confirm_check.isChecked()
    dialog.confirm_check.setChecked(True)
    assert dialog.burn_button.isEnabled()  # a test can be repeated
    assert all(t.cd_sectors is not None for t in lib.tracks())  # lengths still learned


def test_a_failed_burn_says_so_and_records_nothing(lib, make_dialog, wait_for, caplog):
    dialog, burner = make_dialog(FakeBurner(fail="drive on fire"))
    burn(dialog, wait_for)
    assert burner.burned == [] and all(d.last_burned_at is None for d in lib.discs())
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
    assert "a real bug" in dialog.problems_label.text()  # still shown to the user
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
        dialog.confirm_check.setChecked(True)
        assert not dialog._busy and dialog._media is None
        assert "cdrdao was not found" in dialog.problems_label.text()
        assert dialog.drive_label.property("level") == "critical"
        assert not dialog.check_button.isEnabled()
        assert not dialog.burn_button.isEnabled()
        assert (
            dialog.export_button.isEnabled()
        )  # the way out: files for another program
        assert "Export" in dialog.drive_label.text()
    finally:
        dialog.close()
        dialog.deleteLater()


def test_export_writes_the_disc_as_files_without_a_drive(make_dialog, wait_for, tmp_path, monkeypatch):  # fmt: skip
    dialog, burner = make_dialog()
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
    dialog, _ = make_dialog()
    monkeypatch.setattr("dubcrate.ui.burn_dialog.ask_export_folder", lambda _parent: "")
    dialog.export_button.click()
    assert not dialog._busy and not list(tmp_path.glob("**/README.txt"))


def test_a_failed_export_is_shown_in_the_window(make_dialog, wait_for, tmp_path, monkeypatch, caplog):  # fmt: skip
    dialog, _ = make_dialog()
    monkeypatch.setattr("dubcrate.ui.burn_dialog.ask_export_folder", lambda _parent: str(tmp_path / "gone"))  # fmt: skip
    dialog.export_button.click()
    assert wait_for(lambda: not dialog._busy)
    assert "is not a folder" in dialog.log.toPlainText()
    assert dialog.status_label.property("level") == "critical"
    assert [
        r for r in caplog.records if r.levelname == "ERROR"
    ] == []  # expected, not a crash


def test_a_disc_that_cannot_be_burned_cannot_be_exported_either(make_dialog, lib):
    empty = lib.create_disc("Empty")
    dialog, _ = make_dialog(disc=empty)
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
        lambda tracks, progress=None: (
            measured.append(len(tracks)) or real(tracks, progress)
        ),
    )
    dialog, _ = make_dialog()
    choose_levels(dialog, str(DEFAULT))
    assert wait_for(lambda: not dialog._measuring)
    assert measured == [2]  # both tracks, on a worker
    assert all(t.loudness for t in lib.tracks())  # and remembered
    assert f"Levels matched to {DEFAULT} LUFS" in dialog.levels_label.text()
    assert dialog._plan.levels_note == dialog.levels_label.text()
    assert dialog.levels_combo.isEnabled()
    assert any(t.gain_db for t in dialog._plan.tracks)

    before = [t.gain_db for t in dialog._plan.tracks]
    choose_levels(dialog, "-14")  # a new target: new gains, nothing measured again
    assert "-14 LUFS" in dialog.levels_label.text() and measured == [2]
    assert [t.gain_db for t in dialog._plan.tracks] != before
    choose_levels(
        dialog, "peak"
    )  # peak normalising: its own note, still nothing measured
    assert dialog.levels_label.text().startswith("Peak-normalised to -0.3 dBTP")
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
    assert not dialog.burn_button.isEnabled()  # (still needs the blank CD-R ticked)
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
        assert "Levels matched to -12 LUFS" in dialog.levels_label.text()
    finally:
        dialog.close()
        dialog.deleteLater()


def test_a_failed_measurement_blocks_the_burn_and_says_how_to_get_past_it(make_dialog, wait_for, monkeypatch):  # fmt: skip
    from dubcrate.render import RenderError
    from dubcrate.ui import burn_dialog

    def broken(tracks, progress=None):
        raise RenderError("ffmpeg fell over")

    monkeypatch.setattr(burn_dialog, "measure_loudness", broken)
    dialog, _ = make_dialog()
    choose_levels(dialog, str(DEFAULT))
    assert wait_for(lambda: not dialog._measuring)
    dialog.confirm_check.setChecked(True)
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
