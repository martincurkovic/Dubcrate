"""The track table: keyboard shortcuts and the whole-track waveform column."""

import numpy as np
from PySide6.QtCore import QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem, QTableView

from dubcrate.waveform import BAND_HIGH, BAND_LOW, BAND_MID, Waveform

from . import theme
from .models import PREVIEW_ROLE
from .wave_style import color

_BANDS = ((BAND_LOW, "wave_low"), (BAND_MID, "wave_mid"), (BAND_HIGH, "wave_high"))
PREVIEW_WIDTH = 150
ROW_HEIGHT = 28


class TrackTable(QTableView):
    """Return adds the selection to the disc; Space plays or pauses the preview."""

    add_requested = Signal()
    play_pause_requested = Signal()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.add_requested.emit()
            return
        if event.key() == Qt.Key.Key_Space and not event.modifiers():
            self.play_pause_requested.emit()
            return
        super().keyPressEvent(event)


def render_preview(
    waveform: Waveform, width: int, height: int, dpr: float = 1.0
) -> QPixmap:
    """The whole track as a small bottom-aligned 3-band strip, scaled so quiet tracks stay visible."""
    pixmap = QPixmap(round(width * dpr), round(height * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(color("wave_bg"))
    if waveform.columns == 0 or width <= 0:
        return pixmap
    bands = waveform.resampled(width, how="mean").bands
    loudest = float(np.percentile(bands.max(axis=1), 98))
    if loudest > 0.05:
        bands = np.clip(bands / loudest, 0.0, 1.0)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    for band, key in _BANDS:
        p.setPen(QPen(color(key), 1))
        for x, value in enumerate(bands[:, band].tolist()):
            if value * height >= 0.5:
                p.drawLine(x, height - 1, x, round(height - 1 - value * (height - 1)))
    p.end()
    return pixmap


class PreviewDelegate(QStyledItemDelegate):
    """Draws a track's whole-track waveform in its cell, caching what it has drawn."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cache: dict[tuple, QPixmap] = {}
        theme.notifier.changed.connect(self._cache.clear)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(PREVIEW_WIDTH, ROW_HEIGHT)

    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex
    ) -> None:
        super().paint(painter, option, index)  # the row's background and selection
        waveform = index.data(PREVIEW_ROLE)
        if waveform is None:
            return
        rect = option.rect.adjusted(2, 3, -2, -3)
        if rect.width() <= 0 or rect.height() <= 0:
            return
        dpr = painter.device().devicePixelRatioF()
        key = (
            index.data(Qt.ItemDataRole.UserRole),
            rect.width(),
            rect.height(),
            dpr,
            id(theme.current_palette()),
        )
        pixmap = self._cache.get(key)
        if pixmap is None:
            if len(self._cache) > 600:
                self._cache.clear()
            pixmap = self._cache[key] = render_preview(
                waveform, rect.width(), rect.height(), dpr
            )
        painter.drawPixmap(QRect(rect.topLeft(), rect.size()), pixmap)
        painter.setPen(QColor(theme.current_palette()["border_item"]))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
