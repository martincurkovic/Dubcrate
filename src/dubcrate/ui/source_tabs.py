"""The strip of tabs down the left of the window: Library, Explorer, and so on.

Each tab is a glyph over a small label. They are painted (no image files) from the theme's
palette, so they follow every look, System 6 included, which has no colour: there the picked
tab is inverted, black with a white glyph.
"""

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QAbstractButton, QButtonGroup, QVBoxLayout, QWidget

from . import theme

TAB_WIDTH = 68
TAB_HEIGHT = 58

Glyph = Callable[[QPainter, QRectF, QColor], None]


def _pen(colour: QColor, width: float) -> QPen:
    pen = QPen(colour, width)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    return pen


def library_glyph(p: QPainter, box: QRectF, colour: QColor) -> None:
    """A playlist: a bullet and a line, three times."""
    u = box.width() / 16
    p.setPen(_pen(colour, 1.5))
    p.setBrush(colour)
    for y in (4, 8, 12):
        p.drawEllipse(QPointF(box.left() + 2 * u, box.top() + y * u), 0.9 * u, 0.9 * u)
        p.drawLine(
            QPointF(box.left() + 5.5 * u, box.top() + y * u),
            QPointF(box.left() + 15 * u, box.top() + y * u),
        )


def explorer_glyph(p: QPainter, box: QRectF, colour: QColor) -> None:
    """A folder."""
    u = box.width() / 16
    p.setPen(_pen(colour, 1.5))
    p.setBrush(Qt.BrushStyle.NoBrush)
    points = ((1, 3.5), (6, 3.5), (8, 5.5), (15, 5.5), (15, 13.5), (1, 13.5))
    p.drawPolygon(
        QPolygonF([QPointF(box.left() + x * u, box.top() + y * u) for x, y in points])
    )


class _TabButton(QAbstractButton):
    def __init__(self, key: str, text: str, glyph: Glyph, parent=None):
        super().__init__(parent)
        self.key = key
        self._glyph = glyph
        self.setText(text)
        self.setCheckable(True)
        self.setFixedSize(QSize(TAB_WIDTH, TAB_HEIGHT))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        theme.notifier.changed.connect(self.update)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, theme.antialiasing())
        palette = theme.current_palette()
        mono = bool(palette.get("mono_icons"))
        picked = self.isChecked()
        hot = self.underMouse() or self.hasFocus()
        if mono:
            fill = QColor(palette["text"]) if picked else QColor(palette["bg_panel"])
            ink = QColor(palette["bg_panel"]) if picked else QColor(palette["text"])
        else:
            fill = QColor(
                palette["bg_selected"]
                if picked
                else palette["bg_button_hover"]
                if hot
                else palette["bg_statusbar"]
            )
            ink = QColor(palette["text_bright"] if picked or hot else palette["text"])
            if not picked and not hot:
                ink.setAlpha(190)  # a tab you are not on sits back a little
        painter.fillRect(self.rect(), fill)
        glyph_box = QRectF((self.width() - 22) / 2, 8, 22, 22)
        self._glyph(painter, glyph_box, ink)
        font = QFont(self.font())
        font.setPointSizeF(max(8.0, font.pointSizeF() * 0.85))
        painter.setFont(font)
        painter.setPen(ink)
        painter.drawText(
            QRectF(2, 33, self.width() - 4, self.height() - 35),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            self.fontMetrics().elidedText(
                self.text(), Qt.TextElideMode.ElideRight, self.width() - 6
            ),
        )


class SourceTabBar(QWidget):
    """A column of exclusive tabs; `current_changed(key)` says which was picked."""

    current_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(TAB_WIDTH + 1)  # (and a line down its right edge)
        self._buttons: dict[str, _TabButton] = {}
        self._current = ""
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 4, 1, 4)
        self._layout.setSpacing(2)
        self._layout.addStretch(1)
        theme.notifier.changed.connect(self.update)

    def add_tab(self, key: str, text: str, glyph: Glyph, tooltip: str = "") -> None:
        button = _TabButton(key, text, glyph)
        button.setToolTip(tooltip or text)
        self._group.addButton(button)
        self._layout.insertWidget(self._layout.count() - 1, button)
        self._buttons[key] = button
        button.toggled.connect(lambda on, k=key: self._picked(k) if on else None)
        if len(self._buttons) == 1:
            button.setChecked(True)

    def keys(self) -> list[str]:
        return list(self._buttons)

    def _picked(self, key: str) -> None:
        self._current = key  # (before saying so: a listener asks `current()`)
        self.current_changed.emit(key)

    def current(self) -> str:
        return self._current

    def set_current(self, key: str) -> None:
        if key in self._buttons:
            self._buttons[key].setChecked(True)  # (emits current_changed if it changed)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        palette = theme.current_palette()
        painter.fillRect(self.rect(), QColor(palette["bg_statusbar"]))
        painter.setPen(QColor(palette["border_list"]))
        painter.drawLine(self.width() - 1, 0, self.width() - 1, self.height())
