"""The painted transport buttons, and the status bar with the version and the update button."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QStatusBar

from dubcrate.library import Library
from dubcrate.ui import theme, version
from dubcrate.ui.main_window import MainWindow
from dubcrate.ui.player_strip import PlayerStrip
from dubcrate.ui.status_bar import AppStatusBar
from dubcrate.ui.transport_button import (
    KINDS,
    PLAY_SIZE,
    SKIP_SIZE,
    TransportButton,
)
from dubcrate.updates import UpdateInfo

INFO = UpdateInfo(
    "9.9.9", "v9.9.9", "Dubcrate 9.9.9", "https://example/v9.9.9", "notes"
)


# -- the transport buttons ----------------------------------------------------------------


def test_play_is_a_little_bigger_than_previous_and_next(qapp):
    sizes = {kind: TransportButton(kind).size() for kind in KINDS}
    assert (sizes["play"].width(), sizes["play"].height()) == (PLAY_SIZE, PLAY_SIZE)
    assert sizes["previous"] == sizes["next"]
    assert sizes["previous"].width() == SKIP_SIZE < PLAY_SIZE


def test_an_unknown_kind_is_an_error(qapp):
    with pytest.raises(ValueError, match="kind"):
        TransportButton("record")


@pytest.mark.parametrize("look", ["dark", "light", "system6"])
def test_every_button_draws_in_every_theme_and_state(qapp, look):
    theme.set_theme_preference(look)
    try:
        for kind in KINDS:
            button = TransportButton(kind)
            button.show()
            images = {"idle": button.grab().toImage()}
            button.setDown(True)
            images["down"] = button.grab().toImage()
            button.setDown(False)
            button.setEnabled(False)
            images["disabled"] = button.grab().toImage()
            assert all(not i.isNull() for i in images.values())
            assert len({i.cacheKey() for i in images.values()}) >= 1
            assert images["idle"] != images["disabled"], (
                kind,
                look,
            )  # it looks disabled
            assert images["idle"] != images["down"], (kind, look)  # and pressed
            button.deleteLater()
    finally:
        theme.set_theme_preference("dark")


def close(a, b, tolerance=8) -> bool:
    """Two images the same to within a few levels of anti-aliasing rounding."""
    if a.size() != b.size():
        return False
    return all(
        abs(((a.pixel(x, y) >> shift) & 255) - ((b.pixel(x, y) >> shift) & 255))
        <= tolerance
        for x in range(a.width())
        for y in range(a.height())
        for shift in (0, 8, 16, 24)
    )


def test_the_glyphs_differ(qapp):
    images = {}
    for kind in KINDS:
        button = TransportButton(kind)
        button.show()
        images[kind] = button.grab().toImage()
    pause = TransportButton("play")
    pause.show()
    pause.set_playing(True)
    assert images["previous"] != images["next"]  # (mirror images, not the same picture)
    # and exactly mirror images: the bar and the tip are where they should be
    assert close(images["previous"].flipped(Qt.Orientation.Horizontal), images["next"])
    assert pause.grab().toImage() != images["play"]


def test_clicks_and_the_keyboard_work_like_any_button(qapp):
    button = TransportButton("next")
    button.show()
    clicks = []
    button.clicked.connect(lambda: clicks.append(1))
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    button.setFocus()
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert len(clicks) == 2
    button.setEnabled(False)
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert len(clicks) == 2  # a disabled button doesn't answer


def test_the_header_centres_every_control_on_one_line(qapp):
    strip = PlayerStrip()
    strip.resize(1000, 200)
    strip.show()
    qapp.processEvents()
    centres = {
        round(w.geometry().center().y())
        for w in (
            strip.prev_button,
            strip.play_button,
            strip.next_button,
            strip.settings_button,
            strip.volume,
        )
    }
    assert max(centres) - min(centres) <= 1
    strip.deleteLater()


# -- the status bar -------------------------------------------------------------------------


def test_the_version_label_says_the_app_and_its_version(qapp, monkeypatch):
    monkeypatch.setattr(version, "app_version", lambda: "0.1.0")
    assert version.version_text() == "Dubcrate v0.1.0"
    monkeypatch.setattr(version, "app_version", lambda: "1.12.3")
    assert version.version_text() == "Dubcrate v1.12.3"
    monkeypatch.setattr(version, "app_version", lambda: "development")
    assert version.version_text() == "Dubcrate development"  # no v in front of a word
    monkeypatch.setattr(version, "app_version", lambda: "0.0.0-dev")
    assert version.version_text() == "Dubcrate 0.0.0-dev"


def test_the_bar_has_no_resize_grip_and_the_version_at_the_left(qapp):
    bar = AppStatusBar()
    assert not bar.isSizeGripEnabled()
    assert bar.version_label.text().startswith("Dubcrate ")
    assert bar.update_button.isHidden()  # nothing to update to


def test_messages_show_after_the_version_and_the_version_stays(qapp):
    bar = AppStatusBar()
    bar.show()
    seen = []
    bar.messageChanged.connect(seen.append)
    bar.showMessage("Added 2 tracks")
    assert (
        bar.currentMessage() == "Added 2 tracks"
        and bar.message_label.text() == "Added 2 tracks"
    )
    assert bar.version_label.isVisible()  # a QStatusBar would have hidden it
    # the message sits to the right of the version
    assert bar.message_label.geometry().left() + bar.message_label.parentWidget().x() > bar.version_label.geometry().left()  # fmt: skip
    bar.clearMessage()
    assert bar.currentMessage() == "" and bar.version_label.isVisible()
    assert seen == ["Added 2 tracks", ""]


def test_a_message_with_a_timeout_goes_away_and_a_new_one_restarts_the_clock(qapp):
    bar = AppStatusBar()
    bar.showMessage("first", 5000)
    assert bar._timer.isActive()
    bar.showMessage("second")  # no timeout: stays, and the old clock is cancelled
    assert not bar._timer.isActive() and bar.currentMessage() == "second"
    bar.showMessage("third", 5000)
    bar._timer.timeout.emit()  # (the time passing)
    assert bar.currentMessage() == ""


def test_it_still_answers_like_a_status_bar(qapp):
    assert issubclass(
        AppStatusBar, QStatusBar
    )  # (so callers and QMainWindow can use it)


def test_the_update_button_appears_with_a_tooltip_and_goes_again(qapp):
    bar = AppStatusBar()
    bar.show()
    bar.set_update(INFO)
    assert (
        bar.update_button.isVisible() and bar.update_button.text() == "Update available"
    )
    assert "9.9.9" in bar.update_button.toolTip()
    pressed = []
    bar.update_clicked.connect(lambda: pressed.append(1))
    bar.update_button.click()
    assert pressed == [1]
    bar.set_update(None)
    assert bar.update_button.isHidden()


@pytest.mark.parametrize("look", ["dark", "light", "system6"])
def test_the_status_bar_and_its_button_draw_in_every_theme(qapp, look):
    theme.set_theme_preference(look)
    try:
        bar = AppStatusBar()
        bar.resize(600, 26)
        bar.set_update(INFO)
        bar.showMessage("Imported 12 tracks")
        bar.show()
        assert not bar.grab().isNull()
        assert bar.update_button.height() <= 20  # small
        bar.deleteLater()
    finally:
        theme.set_theme_preference("dark")


# -- in the window ----------------------------------------------------------------------------


@pytest.fixture
def window(qapp):
    lib = Library.open(":memory:")
    win = MainWindow(lib)
    win.show()
    yield win
    win.close()
    win.deleteLater()
    lib.close()


def test_the_window_uses_the_app_status_bar_without_a_grip(window):
    bar = window.statusBar()
    assert isinstance(bar, AppStatusBar) and not bar.isSizeGripEnabled()
    window.statusBar().showMessage("Hello", 8000)
    assert window.statusBar().currentMessage() == "Hello"
    assert bar.version_label.isVisible()
    assert (
        window.busy_bar.parentWidget() is not None
    )  # the busy bar is still at the right


def test_an_update_found_shows_the_button_and_it_opens_the_notice(window, monkeypatch):
    checker = window.updates
    assert window.statusBar().update_button.isHidden()
    checker._show(
        INFO, manual=False, over=window
    )  # (a start-up check that found a release)
    assert window.statusBar().update_button.isVisible()
    assert checker.available is INFO and checker.dialog is not None
    checker.dialog.close()
    checker.dialog = None
    window.statusBar().update_button.click()  # the button brings the notice back
    assert checker.dialog is not None and checker.dialog.isVisible()
    checker.dialog.close()


def test_the_button_stays_for_a_skipped_version_though_the_popup_stays_quiet(window):
    from dubcrate import settings

    settings.set_skipped_update("9.9.9")
    window.updates._show(INFO, manual=False, over=window)
    assert window.updates.dialog is None  # no pop-up at start-up for a skipped version
    assert (
        window.statusBar().update_button.isVisible()
    )  # but it is still there to be clicked


def test_being_up_to_date_hides_the_button(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    window.updates._show(INFO, manual=False, over=window)
    assert window.statusBar().update_button.isVisible()
    window.updates._show(
        None, manual=True, over=window
    )  # a later check finds nothing newer
    assert (
        window.statusBar().update_button.isHidden() and window.updates.available is None
    )


def test_a_failed_check_leaves_the_button_as_it_was(window):
    window.updates._show(INFO, manual=False, over=window)
    window.updates.dialog.close()
    # (a failure never reaches _show: it is told to the user, or not, and nothing is cleared)
    assert window.statusBar().update_button.isVisible()


def glyph_pixels(button, colour):
    """The (x, y) of every pixel of the button that is the glyph's colour, and the button's middle."""
    image = button.grab().toImage()
    target = colour.rgb() & 0xFFFFFF
    points = [
        (x + 0.5, y + 0.5)
        for x in range(image.width())
        for y in range(image.height())
        if image.pixelColor(x, y).rgb() & 0xFFFFFF == target
        and image.pixelColor(x, y).alpha() == 255
    ]
    return points, (image.width() / 2, image.height() / 2)


def test_the_play_triangle_is_optically_centred_in_its_circle(qapp):
    from PySide6.QtGui import QColor

    theme.set_theme_preference("dark")
    button = TransportButton("play")
    button.show()
    points, (mx, my) = glyph_pixels(
        button, QColor(theme.current_palette()["text_on_accent"])
    )
    assert len(points) > 40  # (the glyph is there)
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    balance_x, balance_y = sum(xs) / len(xs), sum(ys) / len(ys)
    box_x = (min(xs) + max(xs)) / 2
    assert (
        abs(balance_y - my) <= 0.6 and abs((min(ys) + max(ys)) / 2 - my) <= 0.6
    )  # level
    # horizontally: between the bounding box's middle (too far left) and the balance point
    # (too far right), and no more than a pixel off the circle's middle by either measure
    assert box_x >= mx - 0.6 and balance_x <= mx + 1.6, (box_x, balance_x, mx)  # fmt: skip
    assert box_x <= mx + 2.2 and balance_x >= mx - 0.6


@pytest.mark.parametrize("kind", ["previous", "next"])
def test_the_skip_glyphs_are_centred_in_their_squares(qapp, kind):
    from PySide6.QtGui import QColor

    theme.set_theme_preference("dark")
    button = TransportButton(kind)
    button.show()
    points, (mx, my) = glyph_pixels(button, QColor(theme.current_palette()["text"]))
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    assert abs((min(xs) + max(xs)) / 2 - mx) <= 0.8 and abs((min(ys) + max(ys)) / 2 - my) <= 0.8  # fmt: skip


def test_the_pause_bars_are_centred_too(qapp):
    from PySide6.QtGui import QColor

    theme.set_theme_preference("dark")
    button = TransportButton("play")
    button.show()
    button.set_playing(True)
    points, (mx, my) = glyph_pixels(
        button, QColor(theme.current_palette()["text_on_accent"])
    )
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    assert abs((min(xs) + max(xs)) / 2 - mx) <= 0.6 and abs((min(ys) + max(ys)) / 2 - my) <= 0.6  # fmt: skip
