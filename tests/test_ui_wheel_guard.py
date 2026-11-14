# ui/wheel_guard.py (ported from AKAISDS): scrolling a page never changes a control it passes over; only a clicked control answers the wheel.

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from dubcrate.ui import wheel_guard


class Page:
    """A scroll area much taller than its window, with one of each control in it."""

    def __init__(self, qapp):
        self.guard = wheel_guard.install(qapp)
        self.controls = {
            "spin": QSpinBox(),
            "double": QDoubleSpinBox(),
            "combo": QComboBox(),
            "slider": QSlider(Qt.Orientation.Horizontal),
        }
        self.controls["spin"].setRange(0, 100)
        self.controls["double"].setRange(0, 100)
        self.controls["combo"].addItems(["a", "b", "c", "d"])
        self.controls["slider"].setRange(0, 100)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        for control in self.controls.values():
            layout.addWidget(control)
            layout.addSpacing(300)  # (so the page really scrolls)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setWidget(inner)
        self.area.resize(300, 400)
        self.area.show()
        qapp.processEvents()
        self.bar = self.area.verticalScrollBar()

    def value(self, name):
        control = self.controls[name]
        return (
            control.currentIndex()
            if isinstance(control, QComboBox)
            else control.value()
        )

    def wheel(self, control, delta=-120):
        """One wheel tick over `control`, as the system sends it."""
        centre = QPointF(control.rect().center())
        event = QWheelEvent(
            centre,
            QPointF(control.mapToGlobal(centre.toPoint())),
            QPoint(0, 0),
            QPoint(0, delta),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        QApplication.sendEvent(control, event)

    def click(self, control):
        self.area.ensureWidgetVisible(control)
        QApplication.processEvents()
        QTest.mouseClick(
            control, Qt.MouseButton.LeftButton, pos=control.rect().center()
        )
        if isinstance(control, QComboBox):
            control.hidePopup()  # (a click opens its drop-down; the user picks or dismisses it)


@pytest.fixture
def page(qapp):
    made = Page(qapp)
    yield made
    made.area.close()
    made.area.deleteLater()
    wheel_guard.uninstall(qapp)


NAMES = ["spin", "double", "combo", "slider"]


@pytest.mark.parametrize("name", NAMES)
def test_an_unclicked_control_ignores_the_wheel_and_the_page_scrolls_instead(
    page, name
):
    control = page.controls[name]
    page.area.ensureWidgetVisible(control)
    QApplication.processEvents()
    before_value, before_scroll = page.value(name), page.bar.value()
    page.wheel(control, delta=-120)
    assert page.value(name) == before_value
    assert page.bar.value() > before_scroll  # (the wheel went to the scroll area)


@pytest.mark.parametrize("name", NAMES)
def test_a_clicked_control_answers_the_wheel_and_the_page_stays_put(page, name):
    control = page.controls[name]
    page.click(control)
    assert control.property("armed") is True
    before_value, before_scroll = page.value(name), page.bar.value()
    page.wheel(
        control, delta=-120 if name == "combo" else 120
    )  # (the combo starts on its first item)
    assert page.value(name) != before_value
    assert page.bar.value() == before_scroll


def test_only_one_control_is_armed_and_a_click_elsewhere_disarms_it(page):
    spin, combo = page.controls["spin"], page.controls["combo"]
    page.click(spin)
    assert page.guard.armed_control() is spin
    page.click(combo)
    assert page.guard.armed_control() is combo and spin.property("armed") is False
    page.click(page.area.widget())  # (the page itself, away from any control)
    assert page.guard.armed_control() is None and combo.property("armed") is False


def test_the_scroll_bar_always_scrolls(page):
    before = page.bar.value()
    page.wheel(page.bar, delta=-120)
    assert page.bar.value() > before


def test_a_control_that_is_hidden_comes_back_unarmed(page):
    spin = page.controls["spin"]
    page.click(spin)
    spin.hide()
    QApplication.processEvents()
    assert page.guard.armed_control() is None
    spin.show()
    assert spin.property("armed") is False


def test_typing_still_works_in_a_box_the_wheel_has_passed_over(page):
    spin = page.controls["spin"]
    spin.setFocus()
    page.wheel(spin)  # (ignored by the unclicked box: it goes to the page)
    spin.lineEdit().selectAll()
    QTest.keyClicks(spin.lineEdit(), "42")
    assert spin.value() == 42


def test_installing_twice_gives_the_same_guard_and_uninstalling_removes_the_filter(
    qapp,
):
    first = wheel_guard.install(qapp)
    assert wheel_guard.install(qapp) is first
    wheel_guard.uninstall(qapp)
    spin = QSpinBox()
    spin.setRange(0, 10)
    spin.show()
    qapp.processEvents()
    centre = QPointF(spin.rect().center())
    event = QWheelEvent(
        centre, QPointF(spin.mapToGlobal(centre.toPoint())), QPoint(0, 0), QPoint(0, 120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
    )  # fmt: skip
    QApplication.sendEvent(spin, event)
    assert spin.value() == 1  # (no guard: the box takes the wheel as before)
    spin.close()
