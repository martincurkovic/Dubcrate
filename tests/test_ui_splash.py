"""The window shown while Dubcrate starts."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPixmap
from PySide6.QtWidgets import QApplication

from dubcrate.ui import splash as splash_module
from dubcrate.ui import theme
from dubcrate.ui.splash import BACKGROUND, CARD_SIZE, StartupSplash


@pytest.fixture
def splash(qapp):
    s = StartupSplash("1.2.3")
    yield s
    s.close()
    s.deleteLater()


def image(s):
    return s.grab().toImage()


def test_it_has_a_fixed_card_size_and_sits_in_the_middle_of_the_screen(splash):
    assert (splash.width(), splash.height()) == CARD_SIZE
    screen = QGuiApplication.primaryScreen().availableGeometry()
    assert abs(splash.geometry().center().x() - screen.center().x()) <= 1
    assert abs(splash.geometry().center().y() - screen.center().y()) <= 1


def test_it_is_a_frameless_window_kept_on_top(splash):
    flags = splash.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint


def test_a_real_splash_does_not_take_focus_but_a_preview_does(qapp):
    real, preview = StartupSplash(), StartupSplash(activating=True)
    try:
        assert real.windowType() == Qt.WindowType.SplashScreen
        assert preview.windowType() == Qt.WindowType.Window
    finally:
        real.close(), preview.close()


def test_the_status_line_changes_and_the_window_repaints_at_once(splash):
    splash.show()
    before = image(splash)
    splash.set_status("Reading the rekordbox library…")
    assert splash.status == "Reading the rekordbox library…"
    assert image(splash) != before


def test_it_starts_by_saying_it_is_starting(splash):
    assert splash.status == "Starting…"


def test_finish_closes_it(splash):
    splash.show()
    assert splash.isVisible()
    splash.finish()
    assert not splash.isVisible()


def test_the_card_is_painted_with_its_own_dark_look_and_rounded_corners(splash):
    splash.show()
    img = image(splash)
    centre = QColor(img.pixelColor(CARD_SIZE[0] // 2, 20))
    assert centre.rgb() == BACKGROUND.rgb()
    assert QColor(img.pixelColor(0, 0)).alpha() == 0  # the corner is cut away


def test_it_looks_the_same_whatever_the_theme_it_appears_before_one_exists(qapp):
    images = []
    for preference in ("dark", "light", "system6"):
        theme.apply_to_app(
            qapp, preference
        )  # the real thing: the stylesheet and the font
        s = StartupSplash("1.0")
        s.set_status("Starting…")
        images.append(s.grab().toImage())
        s.close()
    theme.apply_to_app(qapp, "dark")
    assert images[0] == images[1] == images[2]


def test_an_application_stylesheet_does_not_paint_over_its_rounded_corners(qapp):
    # The theme's stylesheet gives every widget a background; the splash must stay a rounded
    # card on a transparent window whatever the application's stylesheet says. (Offscreen this
    # holds even without the splash's own `background: transparent`, which is kept as a
    # precaution for real displays: the property is what is protected here.)
    before = qapp.styleSheet()
    qapp.setStyleSheet("QWidget { background-color: #ff00ff; }")
    s = StartupSplash("1.0")
    try:
        s.show()
        corner = QColor(s.grab().toImage().pixelColor(0, 0))
        assert corner.alpha() == 0 or corner.rgb() != QColor("#ff00ff").rgb()
        centre = QColor(s.grab().toImage().pixelColor(CARD_SIZE[0] // 2, 20))
        assert centre.rgb() == BACKGROUND.rgb()  # and the card is still its own colour
    finally:
        s.close()
        qapp.setStyleSheet(before)


def test_the_version_is_shown(qapp):
    a, b = StartupSplash("1.0"), StartupSplash("2.0")
    try:
        assert a.grab().toImage() != b.grab().toImage()
    finally:
        a.close(), b.close()


def test_the_app_icon_is_drawn_when_there_is_one_and_it_is_optional(qapp):
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor("#ff0000"))
    with_icon, without = StartupSplash("1", icon=pixmap), StartupSplash("1")
    try:
        assert with_icon.grab().toImage() != without.grab().toImage()
    finally:
        with_icon.close(), without.close()


def test_a_very_long_status_is_cut_short_not_overflowed(splash):
    splash.set_status("Reading the rekordbox library " * 40)
    assert image(splash).width() == CARD_SIZE[0]


def test_the_preview_runs_and_quits_by_itself(qapp):
    assert splash_module.preview(["splash", "--seconds", "0.05"]) == 0
    assert QApplication.instance() is qapp  # it reused the running application
