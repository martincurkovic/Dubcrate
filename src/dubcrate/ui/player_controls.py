"""The small painted controls of the player: pill buttons, the beat jump menu and the round CUE button.

Like the transport buttons and the cog they are drawn in code from the theme's palette
(`theme.current_palette()`), so they follow all four looks and need no image files. A `PillButton` is a
small button with a label in one of four `shape`s: `pill` (outlined, the default), `tile` (a flat dark tile,
for the jump arrows), `circle` (a thin ring, for the zoom buttons) and `text` (a faint backing and no outline, for RST). Given
a `color` it is a hot cue's lettered button (tinted when it has something to jump to, dull when it has not),
and `checkable` makes it a toggle (AU / MA, the side tabs). `ChoiceButton` is a tile that opens a menu with
a tick by the current choice (the beat jump amount). `CueRing` and the play button are rings, orange and
green, with a dark middle, like a CDJ's lit buttons.
"""

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QMenu

from . import theme

CUE_RING = "#e8922b"  # the CUE button's ring (the same orange as a loop's)
PLAY_RING = "#3fa24f"  # the play button's ring
RING_SIZE = 42  # CUE and play are the same size
SHAPES = ("pill", "tile", "circle", "text")


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
        shape: str = "pill",
    ):
        if shape not in SHAPES:
            raise ValueError(f"shape must be one of {SHAPES}, not {shape!r}")
        super().__init__(parent)
        self.shape = shape
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
        if self.shape == "tile":  # a flat dark tile, no outline
            fill = QColor(
                palette[
                    "bg_button_pressed"
                    if down
                    else "bg_button_hover"
                    if hot
                    else "bg_input"
                ]
            )
            return fill, QColor(palette["text_bright" if hot else "text"]), fill
        if self.shape == "circle":  # a thin ring
            ring = QColor(palette["border_hover" if hot else "border"])
            return QColor(palette["bg_input"]), QColor(palette["text_bright" if hot else "text"]), ring  # fmt: skip
        if (
            self.shape == "text"
        ):  # the label on a faint backing (it sits over a waveform), brighter under the pointer
            backing = QColor(palette["bg_input"])
            backing.setAlpha(190 if hot else 140)
            return backing, QColor(palette["text_bright" if hot else "text"]), backing
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
        if self.shape == "circle" and not palette.get("hard_edges"):
            side = min(box.width(), box.height())
            painter.drawEllipse(QRectF(box.center().x() - side / 2, box.center().y() - side / 2, side, side))  # fmt: skip
        else:
            painter.drawRoundedRect(box, radius, radius)
        font = QFont(self.font())
        font.setBold(self._bold)
        font.setPointSizeF(max(8.0, font.pointSizeF() - 1.0))
        painter.setFont(font)
        painter.setPen(text)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, self.text())


class ChoiceButton(PillButton):
    """A tile that opens a menu of choices, with a tick by the current one (the beat jump amount).

    Used like a combo box: `addItem(text, data)`, `currentData()`, `setCurrentIndex(i)`, `findData(data)`,
    and `activated(index)` when the user picks one (not when the program does)."""

    activated = Signal(int)

    def __init__(self, parent=None, *, width: int = 76, height: int = 26):
        super().__init__("", parent, width=width, height=height, shape="tile")
        self._items: list[tuple[str, object]] = []
        self._index = -1
        self.clicked.connect(self._open_menu)

    def addItem(self, text: str, data: object = None) -> None:
        self._items.append((text, data))
        if self._index < 0:
            self.setCurrentIndex(0)

    def count(self) -> int:
        return len(self._items)

    def itemText(self, index: int) -> str:
        return self._items[index][0]

    def currentIndex(self) -> int:
        return self._index

    def currentText(self) -> str:
        return self._items[self._index][0] if self._index >= 0 else ""

    def currentData(self):
        return self._items[self._index][1] if self._index >= 0 else None

    def findData(self, data: object) -> int:
        return next((i for i, (_t, d) in enumerate(self._items) if d == data), -1)

    def setCurrentIndex(self, index: int) -> None:
        if 0 <= index < len(self._items):
            self._index = index
            self.setText(f"{self._items[index][0]} ▾")

    def menu(self) -> QMenu:
        """The menu as it would open now (a tick by the current choice)."""
        menu = QMenu(self)
        group = QActionGroup(menu)
        for number, (text, _data) in enumerate(self._items):
            action = QAction(text, menu)
            action.setCheckable(True)
            action.setChecked(number == self._index)
            action.setActionGroup(group)
            action.triggered.connect(lambda _c=False, n=number: self._pick(n))
            menu.addAction(action)
        return menu

    def _pick(self, index: int) -> None:
        self.setCurrentIndex(index)
        self.activated.emit(index)

    def _open_menu(self) -> None:
        self.menu().popup(self.mapToGlobal(self.rect().bottomLeft()))


class CueRing(QAbstractButton):
    """The round CUE button: a ring and a label, the CDJ's orange."""

    SIZE = RING_SIZE

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
        inside = QColor(palette["bg_panel"] if mono else palette["bg_input"])
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
