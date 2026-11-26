"""The strip that says how a sync finished: its words (`SyncReport`), the widget, and its colours in every look."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from dubcrate.card_report import SyncReport
from dubcrate.ui import theme
from dubcrate.ui.result_banner import OK, WARN, ResultBanner

# -- the words ------------------------------------------------------------------------------------


def test_a_report_says_what_was_written_with_the_right_plurals():
    report = SyncReport()
    report.add(2, 7, 40)
    assert (
        report.describe("CDJ CARD")
        == "Synced 2 discs to “CDJ CARD”: 7 cues and 40 waveforms."
    )
    one = SyncReport()
    one.add(1, 1, 1)
    assert one.describe("C") == "Synced 1 disc to “C”: 1 cue and 1 waveform."


def test_a_part_that_is_zero_is_left_out_and_a_disc_with_neither_is_still_a_disc():
    only_waves = SyncReport()
    only_waves.add(1, 0, 12)
    assert only_waves.describe("C") == "Synced 1 disc to “C”: 12 waveforms."
    only_cues = SyncReport()
    only_cues.add(3, 5, 0)
    assert only_cues.describe("C") == "Synced 3 discs to “C”: 5 cues."
    bare = SyncReport()
    bare.add(1, 0, 0)
    assert bare.describe("C") == "Synced 1 disc to “C”."


def test_nothing_written_says_the_card_already_has_it_all():
    assert SyncReport().describe("C") == "Nothing to write: “C” already has these cues and waveforms."  # fmt: skip


def test_stages_add_up():
    report = SyncReport()
    report.add(1, 4, 20)  # the MP3 discs
    report.add(2, 3, 10)  # then the audio discs
    assert (report.discs, report.cues, report.waves) == (3, 7, 30)


# -- the widget -----------------------------------------------------------------------------------


def test_the_banner_is_hidden_until_it_has_something_to_say(qapp):
    banner = ResultBanner()
    assert not banner.isVisible() and not banner.isVisibleTo(None)
    banner.show_result("Synced 1 disc.", OK, "backup here")
    assert banner.isVisible() and banner.text() == "Synced 1 disc."
    assert banner.tone() == OK and banner.toolTip() == "backup here"
    banner.show_result("Eject it.", WARN)
    assert banner.tone() == WARN and banner.text() == "Eject it."
    banner.deleteLater()


def test_the_cross_dismisses_it_and_says_so(qapp):
    banner = ResultBanner()
    banner.show_result("Done.", OK)
    told = []
    banner.dismissed.connect(lambda: told.append(1))
    QTest.mouseClick(banner._close, Qt.MouseButton.LeftButton)
    assert not banner.isVisible() and banner.text() == "" and told == [1]
    banner.deleteLater()


def test_clearing_it_is_quiet(qapp):
    banner = ResultBanner()
    banner.show_result("Done.", OK, "tip")
    told = []
    banner.dismissed.connect(lambda: told.append(1))
    banner.clear()
    assert not banner.isVisible() and banner.toolTip() == "" and told == []
    banner.deleteLater()


@pytest.mark.parametrize("scheme", ["dark", "light", "system6"])
def test_it_has_its_colours_in_every_look_and_draws_in_both_tones(qapp, scheme):
    theme.apply_to_app(qapp, scheme)
    palette = theme.current_palette()
    for tone in (OK, WARN):
        assert all(f"result_{tone}_{part}" in palette for part in ("bg", "border", "text"))  # fmt: skip
    banner = ResultBanner()
    banner.resize(320, 90)
    for tone in (OK, WARN):
        banner.show_result("Synced 3 discs to “CDJ CARD”: 12 cues and 86 waveforms. Ejected: safe to take out.", tone)  # fmt: skip
        assert not banner.grab().isNull()
    banner.deleteLater()
    theme.apply_to_app(qapp, "dark")


@pytest.mark.parametrize("width", [180, 260, 420])
def test_a_long_sentence_in_a_narrow_pane_is_not_cut_off(qapp, width):
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    host = QWidget()
    layout = QVBoxLayout(host)
    banner = ResultBanner()
    layout.addWidget(banner)
    layout.addStretch(1)
    host.resize(width, 320)
    banner.show_result(
        "Synced 3 discs to “CDJ CARD”: 12 cues and 86 waveforms. Eject the card before taking it out.",
        WARN,
    )
    host.show()
    qapp.processEvents()
    label = banner._label
    assert (
        label.heightForWidth(label.width()) <= label.height()
    )  # (the instruction is the point: every line shows)
    assert banner.height() >= label.heightForWidth(label.width())
    host.deleteLater()
