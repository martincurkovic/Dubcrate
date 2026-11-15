"""A CDJ-1000MK3 waveform drawn as the player draws it: 100 columns, 16 levels (the Sync tab's preview).

One track's `wave_model` values (1 to 15, one per column) as bars. Custom painted from the theme's palette
(`theme.current_palette()`), so it can't disagree with the stylesheet, and it has four looks for what the
Sync tab knows about a track: **will be written** (the theme's accent), **already on the card** (muted: the
card's own, which the player draws), **not analysed yet** (a dashed line: Sync works it out first) and
**none** (no file to analyse: nothing is drawn).
"""

from collections.abc import Sequence

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from dubcrate.wave_model import COLUMNS, MAX_VALUE

from . import theme

WRITE, CARD, PENDING, NONE = "write", "card", "pending", "none"
STRIP_HEIGHT = 26


class CdjWaveStrip(QWidget):
    """One track's waveform as bars across the width, loudest column the full height."""

    def __init__(
        self,
        values: Sequence[int] | None = None,
        state: str = WRITE,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._values: list[int] = []
        self._state = state
        self.setFixedHeight(STRIP_HEIGHT)
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.set_values(values, state)

    @property
    def state(self) -> str:
        return self._state

    @property
    def values(self) -> list[int]:
        return list(self._values)

    def set_values(self, values: Sequence[int] | None, state: str = WRITE) -> None:
        self._values = [] if values is None else [int(v) for v in values]
        self._state = state
        self.update()

    def bar_heights(self, height: float) -> list[float]:
        """The height of each column's bar in a strip `height` pixels tall: a value of 15 is all of it."""
        return [min(max(v, 0), MAX_VALUE) / MAX_VALUE * height for v in self._values]

    def paintEvent(self, _event) -> None:
        palette = theme.current_palette()
        painter = QPainter(self)
        try:
            area = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            painter.setPen(QPen(QColor(palette["border_list"]), 1))
            painter.setBrush(QColor(palette["bg_input"]))
            painter.drawRoundedRect(area, 3, 3)
            inner = area.adjusted(2, 2, -2, -2)
            if self._state == PENDING or (not self._values and self._state != NONE):
                pen = QPen(QColor(palette["text_disabled"]), 1, Qt.PenStyle.DashLine)
                painter.setPen(pen)
                y = inner.center().y()
                painter.drawLine(inner.left(), y, inner.right(), y)
                return
            if not self._values:
                return
            colour = QColor(
                palette["accent"] if self._state == WRITE else palette["text_disabled"]
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            columns = len(self._values)
            width = inner.width() / max(columns, COLUMNS)
            for i, h in enumerate(self.bar_heights(inner.height())):
                if h <= 0:
                    continue
                # a bar grows from the middle, as a waveform does
                bar = QRectF(inner.left() + i * width, inner.center().y() - h / 2, max(width - 0.5, 0.5), h)  # fmt: skip
                painter.drawRect(bar)
        finally:
            painter.end()
