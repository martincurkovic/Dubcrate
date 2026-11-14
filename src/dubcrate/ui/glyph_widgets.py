"""Small painted icons and icon buttons, from the theme's palette (no image files).

The same approach as the tab strip's glyphs (`source_tabs`): a glyph is a function that paints into a box
with a colour, so it follows every look, System 6's black and white included. `GlyphIcon` shows one;
`GlyphButton` is a flat button with one that lights up under the pointer.
"""

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QAbstractButton, QWidget

from . import theme

Glyph = Callable[[QPainter, QRectF, QColor], None]


def _pen(colour: QColor, width: float) -> QPen:
    pen = QPen(colour, width)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    return pen


def eject_glyph(p: QPainter, box: QRectF, colour: QColor) -> None:
    """The eject symbol: a triangle over a bar."""
    u = box.width() / 16
    p.setPen(_pen(colour, 1.5))
    p.setBrush(colour)
    p.drawPolygon(
        QPolygonF(
            [
                QPointF(box.left() + 8 * u, box.top() + 2.5 * u),
                QPointF(box.left() + 14 * u, box.top() + 9.5 * u),
                QPointF(box.left() + 2 * u, box.top() + 9.5 * u),
            ]
        )
    )
    p.drawRect(QRectF(box.left() + 2 * u, box.top() + 11.5 * u, 12 * u, 2.2 * u))


def close_glyph(p: QPainter, box: QRectF, colour: QColor) -> None:
    """A cross: take this away (a folder chosen by hand, from the list)."""
    u = box.width() / 16
    p.setPen(_pen(colour, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(
        QPointF(box.left() + 4 * u, box.top() + 4 * u),
        QPointF(box.left() + 12 * u, box.top() + 12 * u),
    )
    p.drawLine(
        QPointF(box.left() + 12 * u, box.top() + 4 * u),
        QPointF(box.left() + 4 * u, box.top() + 12 * u),
    )


def ink(selected: bool = False, enabled: bool = True) -> QColor:
    """The colour for text and glyphs: on a picked row System 6 inverts (its selection is black)."""
    palette = theme.current_palette()
    if not enabled:
        return QColor(palette["text_disabled"])
    if selected:
        if palette.get("mono_icons"):
            return QColor(palette["bg_panel"])
        return QColor(palette["text_bright"])
    return QColor(palette["text"])


class GlyphIcon(QWidget):
    """A glyph, drawn square in a fixed-size widget."""

    def __init__(self, glyph: Glyph, size: int = 32, parent: QWidget | None = None):
        super().__init__(parent)
        self._glyph = glyph
        self._selected = False
        self.setFixedSize(QSize(size, size))
        theme.notifier.changed.connect(self.update)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, theme.antialiasing())
        inset = self.width() * 0.1
        self._glyph(
            painter,
            QRectF(self.rect()).adjusted(inset, inset, -inset, -inset),
            ink(self._selected),
        )


class GlyphButton(QAbstractButton):
    """A flat button that is only a glyph, with a soft square behind it under the pointer."""

    def __init__(
        self,
        glyph: Glyph,
        tooltip: str = "",
        size: int = 30,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._glyph = glyph
        self._selected = False
        self.setFixedSize(QSize(size, size))
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        theme.notifier.changed.connect(self.update)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, theme.antialiasing())
        palette = theme.current_palette()
        mono = bool(palette.get("mono_icons"))
        lit = self.isEnabled() and (
            self.underMouse() or self.isDown() or self.hasFocus()
        )
        colour = ink(self._selected, self.isEnabled())
        if lit:
            if mono:  # System 6 inverts: a square of the opposite colour, the glyph in the other
                back = QColor(
                    palette["bg_panel"] if self._selected else palette["text"]
                )
                colour = QColor(
                    palette["text"] if self._selected else palette["bg_panel"]
                )
            else:
                back = QColor(palette["bg_button_hover"])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(back)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 4, 4)
        inset = self.width() * 0.2
        self._glyph(
            painter,
            QRectF(self.rect()).adjusted(inset, inset, -inset, -inset),
            colour,
        )
