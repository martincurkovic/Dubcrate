"""The track table: keyboard shortcuts and the whole-track waveform column."""

from PySide6.QtCore import QModelIndex, QPointF, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractSlider,
    QAbstractSpinBox,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QTextEdit,
    QTreeView,
    QWidget,
)

from dubcrate.waveform import BAND_HIGH, BAND_LOW, BAND_MID, STYLE_BANDS, Waveform

from . import theme
from .models import PREVIEW_ROLE
from .wave_style import (
    cache_token,
    color,
    current_style,
    normalized,
    render_columns,
)
from .wave_style import notifier as wave_notifier

_BANDS = ((BAND_LOW, "wave_low"), (BAND_MID, "wave_mid"), (BAND_HIGH, "wave_high"))
PREVIEW_WIDTH = 150
ROW_HEIGHT = 28


def transport_action(event) -> str | None:
    """What a key does to the preview player, if anything: "play_pause" (Space), or
    "jump_back" / "jump_forward" (Left / Right: beat jump, as in rekordbox).

    On macOS Qt marks the arrow keys with KeypadModifier, so "no modifiers" would never
    be true for them: only the modifiers that matter (shift, control, alt, command) count.
    """
    modifiers = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
    if modifiers:
        return None
    key = (
        event.key()
    )  # (compared, not looked up: any key code can arrive, not only Qt's named ones)
    if key == Qt.Key.Key_Space:
        return "play_pause"
    if key == Qt.Key.Key_Left:
        return "jump_back"
    if key == Qt.Key.Key_Right:
        return "jump_forward"
    return None


def transport_key_is_taken(widget: QWidget | None, action: str) -> bool:
    """Whether the focused widget needs this key for itself, so it must not play or jump.

    Anything you type into keeps every one of them (Space is a character, the arrows move
    the cursor); a slider or drop-down keeps its arrows; a tree keeps Left and Right, which
    close and open its folders. Space is free everywhere else, even on a button.
    """
    if isinstance(
        widget, QLineEdit | QTextEdit | QPlainTextEdit | QAbstractSpinBox | QComboBox
    ):
        return True
    if action == "play_pause":
        return False
    return isinstance(widget, QAbstractSlider | QTreeView)


class TrackTable(QTableView):
    """Return adds the selection to the disc; Space plays or pauses the preview."""

    add_requested = Signal()  # ⌘Return: add the selected tracks to the disc
    load_requested = Signal()  # Return: load the highlighted track into the player
    play_pause_requested = Signal()
    beat_jump_requested = Signal(int)  # -1 back, 1 forward

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.add_requested.emit()  # (⌘ on a Mac)
            else:
                self.load_requested.emit()
            return
        action = transport_action(event)
        if action == "play_pause":
            self.play_pause_requested.emit()
            return
        if action in ("jump_back", "jump_forward"):
            # (The table has no use for sideways cell moves.)
            self.beat_jump_requested.emit(-1 if action == "jump_back" else 1)
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
    style = current_style()
    if style != STYLE_BANDS:
        pixels = round(width * dpr)  # one column per device pixel
        bands = normalized(waveform.resampled(pixels, how="mean").bands, style)
        rows = round(height * dpr)
        image = render_columns(
            bands, style, rows=rows, extent=rows - dpr, mirrored=False
        )
        image.setDevicePixelRatio(dpr)
        p = QPainter(pixmap)
        p.drawImage(QPointF(0, 0), image)
        p.end()
        return pixmap
    bands = normalized(waveform.resampled(width, how="mean").bands, style)
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
        wave_notifier.changed.connect(self._cache.clear)

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
            cache_token(),
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
