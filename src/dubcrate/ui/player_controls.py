"""The small painted controls of the player: pill buttons and the round CUE button.

Like the transport buttons and the cog they are drawn in code from the theme's palette
(`theme.current_palette()`), so they follow all four looks and need no image files. A `PillButton` is a
small rounded button with a label; given a `color` it is a hot cue's lettered button (tinted when it has
something to jump to, dull when it has not), and `checkable` makes it a toggle (AU / MA, the side tabs).
`CueRing` is the round CUE button, an orange ring like the CDJ's.
"""

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton

from . import theme

CUE_RING = "#e8922b"  # the CUE button's ring (the same orange as a loop's)


def mix(base: QColor, over: QColor, amount: float) -> QColor:
    """`over` laid on `base` by `amount` (0 to 1)."""
    return QColor(
        round(base.red() + (over.red() - base.red()) * amount),
        round(base.green() + (over.green() - base.green()) * amount),
        round(base.blue() + (over.blue() - base.blue()) * amount),
    )


class PillButton(QAbstractButton):
    """A small flat button with a label, drawn from the palette."""

    def __init__(
        self,
        text: str = "",
        parent=None,
        *,
        color: str | QColor | None = None,
        checkable: bool = False,
        width: int | None = None,
        height: int = 24,
        bold: bool = False,
    ):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(checkable)
        self._color = QColor(color) if color is not None else None
        self._bold = bold
        self._fixed_width = width
        self.setFixedHeight(height)
        if width is not None:
            self.setFixedWidth(width)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        theme.notifier.changed.connect(self.update)

    def set_color(self, color: str | QColor | None) -> None:
        self._color = QColor(color) if color is not None else None
        self.update()

    def sizeHint(self) -> QSize:
        if self._fixed_width is not None:
            return QSize(self._fixed_width, self.height())
        return QSize(max(24, self.fontMetrics().horizontalAdvance(self.text()) + 18), self.height())  # fmt: skip

    def _colours(self, palette: dict) -> tuple[QColor, QColor, QColor]:
        """(fill, text, outline) as the button is now."""
        enabled, down, checked = self.isEnabled(), self.isDown(), self.isChecked()
        hot = enabled and (self.underMouse() or self.hasFocus())
        if palette.get(
            "mono_icons"
        ):  # System 6: black on white, inverted when pressed or on
            ink, paper = QColor(palette["text"]), QColor(palette["bg_panel"])
            if not enabled:
                grey = QColor(palette["text_disabled"])
                return paper, grey, grey
            return (ink, paper, ink) if (down or checked) else (paper, ink, ink)
        if not enabled and self._color is None:
            return (
                QColor(palette["bg_disabled"]),
                QColor(palette["text_disabled"]),
                QColor(palette["border_disabled"]),
            )
        base = QColor(palette["bg_button_hover" if hot else "bg_button"])
        if down:
            base = QColor(palette["bg_button_pressed"])
        if self._color is not None:
            if not enabled:  # a hot cue with nothing to jump to: dull
                return (
                    QColor(palette["bg_disabled"]),
                    mix(QColor(palette["bg_disabled"]), self._color, 0.35),
                    QColor(palette["border_disabled"]),
                )
            return (
                mix(base, self._color, 0.32 if not down else 0.5),
                self._color.lighter(150),
                self._color,
            )
        if checked:
            return QColor(palette["accent"]), QColor(palette["text_on_accent"]), QColor(palette["accent"])  # fmt: skip
        return (
            base,
            QColor(palette["text_bright" if hot else "text"]),
            QColor(palette["border_hover" if hot else "border"]),
        )

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, theme.antialiasing())
        palette = theme.current_palette()
        fill, text, outline = self._colours(palette)
        box = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = 0 if palette.get("hard_edges") else 4
        painter.setPen(QPen(outline, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(box, radius, radius)
        font = QFont(self.font())
        font.setBold(self._bold)
        font.setPointSizeF(max(8.0, font.pointSizeF() - 1.0))
        painter.setFont(font)
        painter.setPen(text)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, self.text())


class CueRing(QAbstractButton):
    """The round CUE button: a ring and a label, the CDJ's orange."""

    SIZE = 46

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setText("CUE")
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        theme.notifier.changed.connect(self.update)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, theme.antialiasing())
        palette = theme.current_palette()
        mono = bool(palette.get("mono_icons"))
        enabled, down = self.isEnabled(), self.isDown()
        ring = QColor(palette["text"] if mono else CUE_RING)
        if not enabled:
            ring = QColor(palette["text_disabled"])
        elif self.underMouse() and not mono:
            ring = ring.lighter(115)
        box = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        pressed = down and enabled
        inside = QColor(palette["bg_panel"] if mono else palette["bg"])
        painter.setPen(QPen(ring, 2.5))
        painter.setBrush(ring if pressed else inside)
        painter.drawEllipse(box)
        font = QFont(self.font())
        font.setBold(True)
        font.setPointSizeF(max(8.0, font.pointSizeF() - 1.0))
        painter.setFont(font)
        if pressed:
            label = QColor(palette["bg_panel"])
        else:
            label = QColor(palette["text" if enabled else "text_disabled"])
        painter.setPen(label)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, self.text())
