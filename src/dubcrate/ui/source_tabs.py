"""The strip of tabs down the left of the window: Library, Explorer, and so on.

Each tab is a glyph over a small label. They are painted (no image files) from the theme's
palette, so they follow every look, System 6 included, which has no colour: there the picked
tab is inverted, black with a white glyph.
"""

from collections.abc import Callable

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QButtonGroup,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

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


def pinned_glyph(p: QPainter, box: QRectF, colour: QColor) -> None:
    """A folder with a pin: the Explorer's folder, and a dot on its corner."""
    explorer_glyph(p, box, colour)
    u = box.width() / 16
    p.setPen(_pen(colour, 1.5))
    p.setBrush(colour)
    p.drawEllipse(QPointF(box.left() + 13 * u, box.top() + 3.5 * u), 2.2 * u, 2.2 * u)


class _TabButton(QAbstractButton):
    drag_moved = Signal(
        QPoint
    )  # a movable tab is being dragged: the pointer, in screen pixels
    drag_finished = Signal()

    def __init__(
        self, key: str, text: str, glyph: Glyph, parent=None, movable: bool = False
    ):
        super().__init__(parent)
        self.key = key
        self.movable = movable
        self._press_at: QPoint | None = None
        self._dragging = False
        self._glyph = glyph
        self.setText(text)
        self.setCheckable(True)
        self.setFixedSize(QSize(TAB_WIDTH, TAB_HEIGHT))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        theme.notifier.changed.connect(self.update)

    # A movable tab can be dragged up and down; a click that does not move far is still a click.

    def mousePressEvent(self, event) -> None:
        if self.movable and event.button() == Qt.MouseButton.LeftButton:
            self._press_at = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._press_at is not None:
            here = event.position().toPoint()
            if (
                self._dragging
                or (here - self._press_at).manhattanLength()
                >= QApplication.startDragDistance()
            ):
                if not self._dragging:
                    self._dragging = True
                    self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.drag_moved.emit(event.globalPosition().toPoint())
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        was_dragging = self._dragging
        self._press_at = None
        self._dragging = False
        if was_dragging:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setDown(
                False
            )  # (the end of a drag is not a click: do not pick the tab)
            self.drag_finished.emit()
            return
        super().mouseReleaseEvent(event)

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


class _EdgeHints(QWidget):
    """Chevrons at the top and bottom of the strip when tabs are cut off beyond that edge.
    It sits over the scroll area and lets every click through."""

    def __init__(self, area: QScrollArea, parent: QWidget):
        super().__init__(parent)
        self._bar = area.verticalScrollBar()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._bar.valueChanged.connect(self.update)
        self._bar.rangeChanged.connect(self.update)

    def paintEvent(self, _event) -> None:
        if self._bar.maximum() <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, theme.antialiasing())
        palette = theme.current_palette()
        ink = QColor(palette["text"])
        back = QColor(palette["bg_statusbar"])
        centre = self.width() / 2
        for up, shown in (
            (True, self._bar.value() > 0),
            (False, self._bar.value() < self._bar.maximum()),
        ):
            if not shown:
                continue
            y = 0 if up else self.height() - 12
            painter.fillRect(QRectF(0, y, self.width(), 12), back)
            painter.setPen(_pen(ink, 1.5))
            tip, base = (y + 3, y + 8) if up else (y + 8, y + 3)
            painter.drawPolyline(
                QPolygonF(
                    [
                        QPointF(centre - 5, base),
                        QPointF(centre, tip),
                        QPointF(centre + 5, base),
                    ]
                )
            )


class SourceTabBar(QWidget):
    """A column of exclusive tabs; `current_changed(key)` says which was picked.

    With more tabs than fit, the column scrolls (wheel or trackpad; no scroll bar, which would
    not fit in so narrow a strip), chevrons show which edge has more, and the picked tab is
    always scrolled into view."""

    current_changed = Signal(str)
    context_menu_requested = Signal(
        str, QPoint
    )  # (a tab's key, where, in screen pixels)
    tabs_reordered = (
        Signal()
    )  # a tab was dragged to a new place: `keys()` is the new order

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(TAB_WIDTH + 1)  # (and a line down its right edge)
        self._buttons: dict[str, _TabButton] = {}
        self._current = ""
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._area = QScrollArea(self)
        self._area.setObjectName("tabScroll")
        self._area.setWidgetResizable(True)
        self._area.setFrameShape(QScrollArea.Shape.NoFrame)
        self._area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._area.setMinimumHeight(TAB_HEIGHT * 2)
        inner = QWidget()
        inner.setObjectName("tabInner")
        # (transparent, so the strip's own painting shows through)
        self._area.setStyleSheet(
            "#tabScroll, #tabScroll > QWidget > #tabInner { background: transparent; border: none; }"
        )
        self._area.viewport().setAutoFillBackground(False)
        self._layout = QVBoxLayout(inner)
        self._layout.setContentsMargins(0, 4, 0, 4)
        self._layout.setSpacing(2)
        self._layout.addStretch(1)
        self._area.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 1, 0)
        outer.addWidget(self._area)
        self._hints = _EdgeHints(self._area, self)
        theme.notifier.changed.connect(self.update)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._hints.setGeometry(0, 0, self.width() - 1, self.height())
        self._hints.raise_()

    def add_tab(
        self,
        key: str,
        text: str,
        glyph: Glyph,
        tooltip: str = "",
        movable: bool = False,
    ) -> None:
        """Add a tab at the bottom. A `movable` tab can be dragged, but only among the movable
        ones, which stay below the tabs that are not (so those keep their places)."""
        button = _TabButton(key, text, glyph, movable=movable)
        if movable:
            button.drag_moved.connect(lambda pos, b=button: self._drag_to(b, pos))
            button.drag_finished.connect(self._drag_done)
        button.setToolTip(tooltip or text)
        self._group.addButton(button)
        self._layout.insertWidget(self._layout.count() - 1, button)
        self._buttons[key] = button
        button.toggled.connect(lambda on, k=key: self._picked(k) if on else None)
        button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        button.customContextMenuRequested.connect(
            lambda pos, k=key, b=button: self.context_menu_requested.emit(
                k, b.mapToGlobal(pos)
            )
        )
        if len(self._buttons) == 1:
            button.setChecked(True)

    def _movable_keys(self) -> list[str]:
        return [k for k, b in self._buttons.items() if b.movable]

    def move_tab(self, key: str, offset: int) -> None:
        """Move a movable tab up (negative) or down by `offset` places, as far as there is room
        among the movable tabs."""
        button = self._buttons.get(key)
        if button is None or not button.movable:
            return
        keys = list(self._buttons)
        first = keys.index(self._movable_keys()[0])
        old = keys.index(key)
        new = max(first, min(len(keys) - 1, old + offset))
        if new == old:
            return
        keys.insert(new, keys.pop(old))
        self._buttons = {k: self._buttons[k] for k in keys}
        self._layout.removeWidget(button)
        self._layout.insertWidget(new, button)
        self._layout.activate()  # (so the next drag event sees where the tabs are now)

    def _drag_to(self, button: _TabButton, pointer: QPoint) -> None:
        """A tab is being dragged: put it where the pointer is, among the movable tabs."""
        movable = self._movable_keys()
        y = self._area.widget().mapFromGlobal(pointer).y()
        target = movable[-1]  # (below them all, unless it is over one)
        for k in movable:
            if y < self._buttons[k].geometry().bottom():
                target = k
                break
        keys = list(self._buttons)
        self.move_tab(button.key, keys.index(target) - keys.index(button.key))
        self._area.ensureWidgetVisible(button, 0, 14)
        # (near the top or bottom of the strip, keep scrolling that way)
        local = self._area.viewport().mapFromGlobal(pointer).y()
        scroll = self._area.verticalScrollBar()
        if local < 10:
            scroll.setValue(scroll.value() - 12)
        elif local > self._area.viewport().height() - 10:
            scroll.setValue(scroll.value() + 12)

    def _reveal(self, key: str) -> None:
        button = self._buttons.get(key)
        if button is not None:
            self._area.ensureWidgetVisible(button, 0, 14)

    def _drag_done(self) -> None:
        self.tabs_reordered.emit()

    def remove_tab(self, key: str) -> None:
        """Take a tab away. If it was the picked one, the first tab is picked instead."""
        button = self._buttons.pop(key, None)
        if button is None:
            return
        was_current = self._current == key
        self._group.removeButton(button)
        self._layout.removeWidget(button)
        button.hide()
        button.deleteLater()
        if was_current and self._buttons:
            self.set_current(next(iter(self._buttons)))

    def keys(self) -> list[str]:
        return list(self._buttons)

    def _picked(self, key: str) -> None:
        self._current = key  # (before saying so: a listener asks `current()`)
        # (a moment later: a tab added just now has not been laid out yet)
        QTimer.singleShot(
            0, self, lambda: self._reveal(key)
        )  # (dropped if the bar is gone by then)
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
