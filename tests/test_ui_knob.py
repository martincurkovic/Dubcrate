"""The volume knob and the settings cog beside it."""

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest

from dubcrate import settings
from dubcrate.ui import theme
from dubcrate.ui.gear_button import SIZE as GEAR_SIZE
from dubcrate.ui.gear_button import GearButton
from dubcrate.ui.knob import FINE_DIVISOR, SIZE, Knob
from dubcrate.ui.player_strip import PlayerStrip


@pytest.fixture
def knob(qapp):
    k = Knob()
    k.setRange(0, 100)
    k.setValue(50)
    k.show()
    yield k
    k.deleteLater()


def drag(knob, dy):
    """Press in the middle of the knob, move up by `dy` pixels (down if negative), release."""
    start = knob.rect().center()
    end = QPoint(start.x(), start.y() - dy)
    QTest.mousePress(
        knob, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start
    )
    QTest.mouseMove(knob, end)
    QTest.mouseRelease(
        knob, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end
    )


def test_dragging_up_raises_it_and_down_lowers_it(knob):
    drag(knob, 30)
    up = knob.value()
    assert up > 50
    knob.setValue(50)
    drag(knob, -30)
    assert knob.value() < 50


def test_a_full_drag_covers_the_whole_range_and_stops_at_the_ends(knob):
    drag(knob, 400)
    assert knob.value() == 100
    drag(knob, -400)
    assert knob.value() == 0


def test_a_slow_drag_still_gets_there(knob):
    """Many one-pixel moves, each less than a whole step of the range: none may be rounded away."""
    knob.setRange(0, 20)  # 150 px for 20 steps: 1 px is 0.13 of a step
    knob.setValue(0)
    start = knob.rect().center()
    QTest.mousePress(
        knob, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start
    )
    for step in range(1, 61):
        QTest.mouseMove(knob, QPoint(start.x(), start.y() - step))
    QTest.mouseRelease(knob, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(start.x(), start.y() - 60))  # fmt: skip
    assert knob.value() == 8  # 60/150 of 20


def test_double_click_goes_back_to_the_default(knob):
    knob.setDefaultValue(80)
    knob.setValue(10)
    QTest.mouseDClick(knob, Qt.MouseButton.LeftButton)
    assert knob.value() == 80
    plain = Knob()
    plain.setRange(0, 100)
    assert plain.defaultValue() == 50  # the middle, unless told otherwise


def test_the_wheel_changes_it_and_never_passes_to_the_window_behind(knob):
    knob.setValue(50)
    event = QWheelEvent(
        QPointF(knob.rect().center()), QPointF(knob.mapToGlobal(knob.rect().center())),
        QPoint(0, 0), QPoint(0, 120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False,
    )  # fmt: skip
    knob.wheelEvent(event)
    assert knob.value() > 50 and event.isAccepted()
    knob.setValue(100)
    event.setAccepted(False)
    knob.wheelEvent(event)  # at the top already: still the knob's, not the page's
    assert event.isAccepted()


def test_the_arrow_keys_change_it(knob):
    QTest.keyClick(knob, Qt.Key.Key_Right)
    assert knob.value() > 50
    QTest.keyClick(knob, Qt.Key.Key_Left)
    QTest.keyClick(knob, Qt.Key.Key_Left)
    assert knob.value() < 50


def test_it_is_small_and_paints_in_every_theme(knob):
    assert (knob.width(), knob.height()) == (SIZE, SIZE) == (GEAR_SIZE, GEAR_SIZE)
    for look in ("dark", "light", "system6"):
        theme.set_theme_preference(look)
        for value in (0, 37, 100):
            knob.setValue(value)
            assert not knob.grab().isNull()
        knob.setEnabled(False)
        assert not knob.grab().isNull()
        knob.setEnabled(True)
    theme.set_theme_preference("dark")


def test_fine_dragging_is_a_twelfth_as_far():
    assert FINE_DIVISOR == 12  # as AKAISDS's own knob on a Mac


# -- the strip -----------------------------------------------------------------------


def test_the_strip_has_the_cog_then_the_knob_at_the_same_size(qapp):
    strip = PlayerStrip()
    strip.show()
    assert isinstance(strip.volume, Knob) and isinstance(
        strip.settings_button, GearButton
    )
    assert strip.settings_button.size() == strip.volume.size()
    assert strip.settings_button.geometry().right() < strip.volume.geometry().left()  # cog on the left  # fmt: skip
    assert (
        strip.settings_button.geometry().top() == strip.volume.geometry().top()
        or abs(
            strip.settings_button.geometry().center().y()
            - strip.volume.geometry().center().y()
        )
        <= 1
    )
    strip.deleteLater()


def test_the_knob_is_the_volume_and_remembers_its_default(qapp):
    strip = PlayerStrip()
    strip.set_volume(0.3)
    assert strip.volume.value() == 30 and strip.volume.defaultValue() == 80
    seen = []
    strip.volume_changed.connect(seen.append)
    strip.volume.setValue(55)
    assert seen == [0.55]
    strip.set_volume(0.9)  # setting it from outside doesn't echo back
    assert strip.volume.value() == 90 and seen == [0.55]
    assert (
        "double-click" in strip.volume.toolTip() and "Shift" in strip.volume.toolTip()
    )
    strip.deleteLater()


def test_pressing_the_cog_asks_for_settings(qapp):
    strip = PlayerStrip()
    seen = []
    strip.settings_requested.connect(lambda: seen.append("settings"))
    strip.settings_button.click()
    assert seen == ["settings"]
    assert "Settings" in strip.settings_button.toolTip()
    strip.deleteLater()


def test_the_cog_opens_the_settings_window(qapp, monkeypatch):
    from dubcrate.library import Library
    from dubcrate.ui import main_window
    from dubcrate.ui.main_window import MainWindow

    opened = []

    class FakeSettings:
        def __init__(self, *a, **k):
            opened.append("built")

        theme_chosen = type("S", (), {"connect": lambda *a, **k: None})()

        def exec(self):
            opened.append("exec")

        def show(self):
            opened.append("show")

        def open(self):
            opened.append("open")

    monkeypatch.setattr(main_window, "SettingsDialog", FakeSettings)
    with Library.open(":memory:") as lib:
        win = MainWindow(lib)
        win.player_strip.settings_button.click()
        win.close()
        win.deleteLater()
    assert "built" in opened


def test_the_cog_draws_in_every_theme_and_state(qapp):
    button = GearButton()
    button.show()
    for look in ("dark", "light", "system6"):
        theme.set_theme_preference(look)
        assert not button.grab().isNull()
        button.setDown(True)
        assert not button.grab().isNull()
        button.setDown(False)
        button.setEnabled(False)
        assert not button.grab().isNull()
        button.setEnabled(True)
    theme.set_theme_preference("dark")
    button.deleteLater()


def test_the_saved_volume_still_comes_back_through_the_knob(qapp):
    settings.set_volume(0.42)
    strip = PlayerStrip()
    strip.set_volume(settings.get_volume())
    assert strip.volume.value() == 42
