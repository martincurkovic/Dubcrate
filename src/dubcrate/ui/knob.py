"""A small rotary knob: drag up or down, wheel, arrow keys, double-click to reset.

Adapted from AKAISDS's `ui/knob.py` (same author, GPL-3.0): the same arc-and-pointer look and
the same drag feel (a full drag of 150 px covers the whole range; hold Shift for fine control),
cut down to what a volume knob needs: no click-to-type box, no log or bipolar variants, and no
cursor warping for the fine drag (a plain finer divisor, as AKAISDS does on macOS).
"""

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QDial

from . import theme

START_ANGLE_DEG = 240  # the minimum is at 7 o'clock...
SWEEP_DEG = 300  # ...and the maximum at 5
DRAG_PX = 150  # a drag this tall covers the whole range
FINE_DIVISOR = 12  # Shift held: the same drag moves 1/12 as far
SIZE = 24


class Knob(QDial):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setNotchesVisible(False)
        self.setFixedSize(QSize(SIZE, SIZE))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._default: int | None = None
        self._anchor_y: float | None = None
        self._drag_fraction = 0.0

    def defaultValue(self) -> int:
        """What a double-click goes back to (the middle of the range unless set)."""
        if self._default is not None:
            return self._default
        return (self.minimum() + self.maximum()) // 2

    def setDefaultValue(self, value: int) -> None:
        self._default = value

    def _fraction(self, value: float) -> float:
        span = self.maximum() - self.minimum()
        return (value - self.minimum()) / span if span else 0.0

    # -- the mouse: drag, double-click, wheel -----------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._anchor_y = event.position().y()
            self._drag_fraction = self._fraction(self.value())
            self.setSliderDown(True)

    def mouseMoveEvent(self, event) -> None:
        if self._anchor_y is None:
            return
        y = event.position().y()
        fine = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        moved = (self._anchor_y - y) / (FINE_DIVISOR if fine else 1)
        self._anchor_y = y
        # (a float carries the part of a step that doesn't reach a whole value yet, so a slow
        # careful drag still gets there instead of rounding every small move away)
        self._drag_fraction = max(0.0, min(1.0, self._drag_fraction + moved / DRAG_PX))
        span = self.maximum() - self.minimum()
        self.setValue(round(self.minimum() + self._drag_fraction * span))

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._anchor_y is not None:
            self._anchor_y = None
            self.setSliderDown(False)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setValue(self.defaultValue())

    def wheelEvent(self, event) -> None:
        super().wheelEvent(event)
        event.accept()  # at either end the wheel stays on the knob, not the window behind it

    # -- the look ---------------------------------------------------------------

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, theme.antialiasing())
        palette = theme.current_palette()
        enabled = self.isEnabled()
        ring = max(2.0, self.width() / 8)  # (a 4 px ring is too heavy at this size)
        rect = QRectF(self.rect()).adjusted(ring, ring, -ring, -ring)

        track = QPen(
            QColor(palette["border_hover"] if enabled else palette["border_disabled"])
        )
        track.setWidthF(ring)
        track.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(track)
        painter.drawArc(rect, START_ANGLE_DEG * 16, -SWEEP_DEG * 16)

        fraction = self._fraction(self.value())
        arc = QPen(QColor(palette["accent"] if enabled else palette["text_disabled"]))
        arc.setWidthF(ring)
        arc.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(arc)
        painter.drawArc(rect, START_ANGLE_DEG * 16, round(-SWEEP_DEG * fraction * 16))

        angle = math.radians(START_ANGLE_DEG - SWEEP_DEG * fraction)
        centre, radius = rect.center(), rect.width() / 2
        pointer = QPen(
            QColor(palette["text_bright"] if enabled else palette["text_disabled"])
        )
        pointer.setWidthF(max(1.5, ring * 0.75))
        pointer.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pointer)
        painter.drawLine(
            QPointF(centre.x() + radius * 0.2 * math.cos(angle), centre.y() - radius * 0.2 * math.sin(angle)),
            QPointF(centre.x() + (radius + ring / 2) * math.cos(angle), centre.y() - (radius + ring / 2) * math.sin(angle)),
        )  # fmt: skip
