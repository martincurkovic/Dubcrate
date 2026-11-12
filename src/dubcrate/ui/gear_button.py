"""A small cog-wheel button, drawn in code so it takes the theme's colours (and is crisp at any size)."""

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QAbstractButton

from . import theme

SIZE = 24  # the same as the volume knob beside it
TEETH = 8


def gear_path(centre: QPointF, outer: float) -> QPainterPath:
    """A cog: `TEETH` square teeth round a body, with a hole in the middle."""
    body, root = outer * 0.74, outer * 0.62
    points = []
    for tooth in range(TEETH):
        middle = 2 * math.pi * tooth / TEETH
        half_tooth = math.pi / TEETH * 0.42  # a tooth is a bit narrower than the gap
        half_root = math.pi / TEETH * 0.5
        for angle, radius in (
            (middle - half_root, body),
            (middle - half_tooth, outer),
            (middle + half_tooth, outer),
            (middle + half_root, body),
        ):
            points.append(QPointF(centre.x() + radius * math.cos(angle), centre.y() + radius * math.sin(angle)))  # fmt: skip
    path = QPainterPath()
    path.moveTo(points[0])
    for point in points[1:]:
        path.lineTo(point)
    path.closeSubpath()
    path.addEllipse(
        centre, root * 0.5, root * 0.5
    )  # the hole (the two shapes cancel where they overlap)
    path.setFillRule(Qt.FillRule.OddEvenFill)
    return path


class GearButton(QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(QSize(SIZE, SIZE))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        theme.notifier.changed.connect(self.update)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, theme.antialiasing())
        palette = theme.current_palette()
        if not self.isEnabled():
            colour = palette["text_disabled"]
        elif self.isDown():
            colour = palette["accent"]
        elif self.underMouse() or self.hasFocus():
            colour = palette["text_bright"]
        else:
            colour = (
                palette["text_disabled"]
                if not palette.get("mono_icons")
                else palette["text"]
            )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(colour))
        painter.drawPath(gear_path(QRectF(self.rect()).center(), self.width() / 2 - 2))
