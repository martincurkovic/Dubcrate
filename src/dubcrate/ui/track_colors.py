"""One colour per track position on a disc, shared by the capacity bar and the swatches in
the disc list so a track's swatch always matches its segment (adapted from akaiutil-GUI's
volume_colors, which does the same for volumes)."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

from . import theme

SLOTS = 8


def track_color(position: int | None) -> QColor:
    """`position` is the track's place on the disc (0-based); colours are
    reused past the 8th. None (not known yet) gives a neutral grey."""
    palette = theme.current_palette()
    if position is None:
        return QColor(palette["border_hover"])
    return QColor(palette[f"volume_color_{position % SLOTS + 1}"])


def track_shade(position: int | None) -> QColor | None:
    """A theme without colour (System 6) tells tracks apart by shades of grey: this track's, or None if the theme uses colours.
    `position` None (not known yet) gives the lightest."""
    shades = theme.current_palette().get("volume_shades")
    if not shades:
        return None
    return QColor(shades[-1] if position is None else shades[position % len(shades)])


def fill_track(painter: QPainter, rect: QRectF, position: int | None) -> None:
    """Fill `rect` as this track looks in the capacity bar and the disc list: its colour, or its shade of grey."""
    painter.fillRect(rect, track_shade(position) or track_color(position))


def swatch_icon(
    position: int | None, dpr: float = 1.0, box: int = 16, size: int = 10
) -> QIcon:
    """A small rounded square in the track's colour (or an outlined square in its grey), for list rows."""
    pm = QPixmap(round(box * dpr), round(box * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    offset = (box - size) / 2
    if theme.current_palette().get("volume_shades"):
        rect = QRectF(offset, offset, size, size)
        fill_track(painter, rect, position)
        painter.setPen(QColor(theme.current_palette()["text"]))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
        painter.end()
        icon = QIcon(pm)
        # a selected row is inverted (black), and so is its swatch: Qt's own tinting would leave a black one invisible
        inverted = pm.toImage()
        inverted.invertPixels()
        icon.addPixmap(QPixmap.fromImage(inverted), QIcon.Mode.Selected)
        return icon
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(track_color(position))
    painter.drawRoundedRect(QRectF(offset, offset, size, size), 2.5, 2.5)
    painter.end()
    return QIcon(pm)
