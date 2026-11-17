"""The player's previous, play/pause and next buttons, drawn in code so they take the theme.

Play is a round button with a green ring and a dark middle (the ring brightens and the middle warms while it
plays, and its glyph turns into pause), like the CDJ's lit buttons; previous and next are flat dark tiles.
In the Macintosh System 6 look they are plain black outlines that invert when pressed. Like the cog beside
the volume knob they have no image files and are crisp at any size.
"""

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton

from . import theme
from .player_controls import PLAY_RING, mix

SKIP_SIZE = 28
SKIP_WIDTH, SKIP_HEIGHT = (
    50,
    30,
)  # previous and next: the same tiles as the beat jump buttons
PLAY_SIZE = 34
KINDS = ("previous", "play", "next")


class TransportButton(QAbstractButton):
    def __init__(self, kind: str, parent=None, size: int | None = None):
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")
        super().__init__(parent)
        self.kind = kind
        self._playing = False
        if kind == "play" or size:
            size = size or PLAY_SIZE
            self.setFixedSize(QSize(size, size))
        else:
            self.setFixedSize(QSize(SKIP_WIDTH, SKIP_HEIGHT))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        theme.notifier.changed.connect(self.update)

    @property
    def playing(self) -> bool:
        """Whether the play button is showing its pause glyph."""
        return self._playing

    def set_playing(self, playing: bool) -> None:
        if playing != self._playing:
            self._playing = playing
            self.update()

    # -- the look -----------------------------------------------------------------

    def _colours(self, palette: dict) -> tuple[QColor, QColor, QColor | None]:
        """(fill, glyph, outline) for the button as it is now."""
        mono = bool(palette.get("mono_icons"))
        enabled, down = self.isEnabled(), self.isDown()
        hot = enabled and (self.underMouse() or self.hasFocus())
        if mono:  # System 6: black on white, inverted while pressed
            ink, paper = QColor(palette["text"]), QColor(palette["bg_panel"])
            if not enabled:
                return (
                    paper,
                    QColor(palette["text_disabled"]),
                    QColor(palette["text_disabled"]),
                )
            return (ink, paper, ink) if down else (paper, ink, ink)
        if not enabled:
            return QColor(palette["bg_disabled"]), QColor(palette["text_disabled"]), QColor(palette["border_disabled"])  # fmt: skip
        if (
            self.kind == "play"
        ):  # (the ring is drawn by `_paint_ring`; this is its middle and glyph)
            inside = QColor(palette["bg_button_pressed" if down else "bg_input"])
            if self._playing:
                inside = mix(inside, QColor(PLAY_RING), 0.18)
            return inside, QColor(palette["text_bright"]), None
        fill = QColor(palette["bg_button_pressed" if down else "bg_button_hover" if hot else "bg_input"])  # fmt: skip
        return fill, QColor(palette["text_bright" if hot else "text"]), None

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, theme.antialiasing())
        palette = theme.current_palette()
        fill, glyph, outline = self._colours(palette)
        box = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setBrush(fill)
        if outline is not None:
            painter.setPen(QPen(outline, 1))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
        if self.kind == "play":
            if not palette.get("mono_icons") and self.isEnabled():
                ring = QColor(PLAY_RING)
                if self._playing:
                    ring = ring.lighter(130)
                elif self.underMouse() or self.hasFocus():
                    ring = ring.lighter(115)
                painter.setPen(QPen(ring, 2.5))
            elif not palette.get("mono_icons"):
                painter.setPen(QPen(QColor(palette["border_disabled"]), 2.5))
            painter.drawEllipse(box.adjusted(1.2, 1.2, -1.2, -1.2))
        else:
            radius = 0 if palette.get("hard_edges") else 4
            painter.drawRoundedRect(box, radius, radius)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glyph)
        painter.drawPath(self._glyph(box))

    def _glyph(self, box: QRectF) -> QPainterPath:
        """The symbol, centred in `box` and about 40% of its width."""
        cx, cy = box.center().x(), box.center().y()
        unit = min(box.width(), box.height()) / 10  # (a 28 px button has a 2.8 px unit)
        path = QPainterPath()
        if self.kind == "play":
            if self._playing:  # pause: two bars
                for dx in (-2.0, 0.6):
                    path.addRect(
                        QRectF(cx + dx * unit, cy - 2.3 * unit, 1.4 * unit, 4.6 * unit)
                    )
            else:
                # play: a triangle. Its weight is nearer the flat side, so centring its bounding
                # box looks too far left and centring its balance point (a third of the way in)
                # looks too far right: the flat side goes two-fifths of the width left of centre.
                width = 2.6 * unit
                path.addPolygon(self._triangle(cx - 0.4 * width, cy, width, 2.6 * unit, right=True))  # fmt: skip
            path.closeSubpath()
            return path
        height = 2.2 * unit
        if self.kind == "previous":
            path.addRect(QRectF(cx - 2.4 * unit, cy - height, 0.9 * unit, 2 * height))
            # (the triangle points left, its tip on the bar: the mirror of the next button)
            path.addPolygon(self._triangle(cx + 2.2 * unit, cy, 3.4 * unit, height, right=False))  # fmt: skip
        else:
            path.addRect(QRectF(cx + 1.5 * unit, cy - height, 0.9 * unit, 2 * height))
            path.addPolygon(self._triangle(cx - 2.2 * unit, cy, 3.4 * unit, height, right=True))  # fmt: skip
        return path

    @staticmethod
    def _triangle(
        x: float, cy: float, width: float, half_height: float, *, right: bool
    ):
        """A triangle with its flat side at `x`, pointing right or left."""
        from PySide6.QtGui import QPolygonF

        tip = x + width if right else x - width
        return QPolygonF([QPointF(x, cy - half_height), QPointF(x, cy + half_height), QPointF(tip, cy)])  # fmt: skip
