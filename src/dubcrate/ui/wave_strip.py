"""A CDJ-1000MK3 waveform drawn as the player's display draws it (the Sync tab's track browser).

The display's waveform is a dot matrix, measured off a photo of the player's face: **100 columns by 7
rows, growing up from the bottom** (the origin is the bottom edge; a louder column lights more rows).
The dots are **square-ish with thin dark gaps** (about a fifth of the pitch), white on a black panel,
and a row is a little taller than a column is wide (`ROW_PITCH`, about 1.12). The matrix keeps those
proportions at any width (`heightForWidth`), so it is a thin strip, as on the player, not a block.

One track's `wave_model` values (1 to 15, one per column) become 0 to 7 lit rows (`rows_for`: **that
mapping is a guess**, 15 levels onto 7 rows, rounded and never fewer than one for a column that has a
value). Drawn with its own fixed look, like the splash screen (an LCD does not change with the theme).
Three looks for what the Sync tab knows about a track: **will be written** (white, what the player will
show), **already on the card** (dimmer: the card's own, which the player draws) and **not analysed yet**
(a dashed line along the bottom: Sync works it out first); **none** shows only the faintest grid.
"""

from collections.abc import Sequence

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from dubcrate.wave_model import COLUMNS, MAX_VALUE

WRITE, CARD, PENDING, NONE = "write", "card", "pending", "none"
ROWS = 7  # the display's height in dots
ROW_PITCH = 1.12  # a row's height for each unit of a column's width, off the photo
GAP = 0.2  # the dark gap between dots, as a fraction of the pitch
PADDING = 8  # the black panel's margin around the matrix, in pixels

PANEL = QColor("#0c0c0e")
PANEL_EDGE = QColor("#2a2a2e")
LIT = {WRITE: QColor("#f2f2ee"), CARD: QColor("#9a9a98")}
UNLIT = QColor(255, 255, 255, 14)
DASH = QColor("#6a6a6e")


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
        self.setMinimumWidth(0)  # (never what makes its pane wider)
        policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
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

    # -- the shape: the matrix keeps its proportions, so the height follows the width -------

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        pitch = max(width - 2 * PADDING, COLUMNS) / COLUMNS
        return round(pitch * ROW_PITCH * ROWS) + 2 * PADDING

    def sizeHint(self):
        size = super().sizeHint()
        size.setHeight(self.heightForWidth(max(self.width(), 400)))
        return size

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        try:
            area = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            painter.setPen(QPen(PANEL_EDGE, 1))
            painter.setBrush(PANEL)
            painter.drawRoundedRect(area, 3, 3)
            inner = area.adjusted(PADDING, PADDING, -PADDING, -PADDING)
            pitch_x = inner.width() / COLUMNS
            pitch_y = pitch_x * ROW_PITCH
            top = (
                inner.bottom() - pitch_y * ROWS
            )  # (the matrix sits on the bottom edge)
            gap_x, gap_y = pitch_x * GAP, pitch_y * GAP
            lit = self.lit_rows()
            if self._state == PENDING or (not lit and self._state != NONE):
                painter.setPen(QPen(DASH, 1, Qt.PenStyle.DashLine))
                y = inner.bottom() - pitch_y / 2
                painter.drawLine(inner.left(), y, inner.right(), y)
                return
            on = LIT.get(self._state, LIT[WRITE])
            painter.setPen(Qt.PenStyle.NoPen)
            for column in range(COLUMNS):
                height = lit[column] if column < len(lit) else 0
                for row in range(ROWS):  # row 0 is the bottom one
                    painter.setBrush(on if row < height else UNLIT)
                    painter.drawRect(
                        QRectF(
                            inner.left() + column * pitch_x + gap_x / 2,
                            top + (ROWS - 1 - row) * pitch_y + gap_y / 2,
                            max(pitch_x - gap_x, 0.5),
                            max(pitch_y - gap_y, 0.5),
                        )
                    )
        finally:
            painter.end()
