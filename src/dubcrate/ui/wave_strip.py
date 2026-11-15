"""A CDJ-1000MK3 waveform drawn as the player's display draws it (the Sync tab's track browser).

The display's waveform is a dot matrix: **100 columns, 7 rows, growing up from the bottom** (the
origin is the bottom edge; a louder column lights more rows), seen on the player's face in the
owner's photo of it. One track's `wave_model` values (1 to 15, one per column) become 0 to 7 lit
rows (`rows_for`: **that mapping is a guess**, 15 levels onto 7 rows, rounded and never fewer than
one for a column that has a value). Custom painted from the theme's palette (`theme.current_palette()`),
so it can't disagree with the stylesheet, with four looks for what the Sync tab knows about a track:
**will be written** (the theme's accent), **already on the card** (muted: the card's own, which the
player draws), **not analysed yet** (a dashed line along the bottom: Sync works it out first) and
**none** (no file to analyse: only the unlit dots).
"""

from collections.abc import Sequence

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from dubcrate.wave_model import COLUMNS, MAX_VALUE

from . import theme

WRITE, CARD, PENDING, NONE = "write", "card", "pending", "none"
ROWS = 7  # the display's height in dots
STRIP_HEIGHT = 96


def rows_for(value: int) -> int:
    """How many of the 7 rows a value (0 to 15) lights: none for 0, else at least one."""
    value = min(max(int(value), 0), MAX_VALUE)
    return 0 if value == 0 else max(1, round(value * ROWS / MAX_VALUE))


class CdjWaveStrip(QWidget):
    """One track's waveform as a 100 by 7 dot matrix, the loudest column the full height."""

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
        self.setMinimumWidth(240)
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

    def lit_rows(self) -> list[int]:
        """For each column, how many dots are lit from the bottom (0 to 7)."""
        return [rows_for(v) for v in self._values]

    def paintEvent(self, _event) -> None:
        palette = theme.current_palette()
        painter = QPainter(self)
        try:
            area = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            painter.setPen(QPen(QColor(palette["border_list"]), 1))
            painter.setBrush(QColor(palette["bg_input"]))
            painter.drawRoundedRect(area, 3, 3)
            inner = area.adjusted(6, 6, -6, -6)
            cell_w = inner.width() / COLUMNS
            cell_h = inner.height() / ROWS
            gap = max(1.0, min(cell_w, cell_h) * 0.18)
            lit = self.lit_rows()
            if self._state == PENDING or (not lit and self._state != NONE):
                pen = QPen(QColor(palette["text_disabled"]), 1, Qt.PenStyle.DashLine)
                painter.setPen(pen)
                y = inner.bottom() - cell_h / 2
                painter.drawLine(inner.left(), y, inner.right(), y)
                return
            on = QColor(
                palette["accent"] if self._state == WRITE else palette["text_disabled"]
            )
            off = QColor(palette["border_list"])
            off.setAlphaF(0.35)
            painter.setPen(Qt.PenStyle.NoPen)
            for column in range(COLUMNS):
                height = lit[column] if column < len(lit) else 0
                for row in range(ROWS):  # row 0 is the bottom one
                    painter.setBrush(on if row < height else off)
                    painter.drawRect(
                        QRectF(
                            inner.left() + column * cell_w + gap / 2,
                            inner.bottom() - (row + 1) * cell_h + gap / 2,
                            max(cell_w - gap, 0.5),
                            max(cell_h - gap, 0.5),
                        )
                    )
        finally:
            painter.end()
