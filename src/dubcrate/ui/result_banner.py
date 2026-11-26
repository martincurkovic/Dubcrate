"""A strip that says how something finished, and stays until it is dismissed (the Sync tab's "Synced 3 discs…").

A status bar message is gone in a few seconds and the lists change under it, so a finished sync used to leave nothing
to read. The banner is green when it is done and safe, amber when something is still to do (eject the card before
pulling it). Its colours are the `result_ok_*` / `result_warn_*` palette keys (System 6 draws amber inverted), its icon
and cross are painted in the text colour, and it never decides how wide its pane is.
"""

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QWidget,
)

from . import theme

OK = "ok"
WARN = "warn"


def _colour(tone: str) -> QColor:
    return QColor(theme.current_palette()[f"result_{tone}_text"])


def _pen(colour: QColor, width: float) -> QPen:
    pen = QPen(colour, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


class _ToneIcon(QWidget):
    """A tick when it is done, an exclamation mark when something is left to do."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tone = OK
        self.setFixedSize(QSize(18, 18))

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, theme.antialiasing())
        colour = _colour(self.tone)
        box = QRectF(self.rect())
        u = box.width() / 16
        painter.setPen(_pen(colour, 1.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(box.adjusted(1.5 * u, 1.5 * u, -1.5 * u, -1.5 * u))
        if self.tone == OK:
            painter.drawPolyline(
                [
                    QPointF(4.8 * u, 8.4 * u),
                    QPointF(7.2 * u, 10.8 * u),
                    QPointF(11.4 * u, 5.8 * u),
                ]
            )
        else:
            painter.drawLine(QPointF(8 * u, 4.6 * u), QPointF(8 * u, 9 * u))
            painter.setBrush(colour)
            painter.drawEllipse(QPointF(8 * u, 11.6 * u), 0.9 * u, 0.9 * u)


class _CloseButton(QAbstractButton):
    """A cross in the banner's text colour (the shared glyph buttons use the theme's, which can vanish on a tint)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tone = OK
        self.setFixedSize(QSize(22, 22))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Dismiss")
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, theme.antialiasing())
        colour = _colour(self.tone)
        if self.isDown() or self.hasFocus():
            faint = QColor(colour)
            faint.setAlpha(60)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(faint)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 4, 4)
        u = self.width() / 16
        painter.setPen(_pen(colour, 1.6))
        painter.drawLine(QPointF(4.5 * u, 4.5 * u), QPointF(11.5 * u, 11.5 * u))
        painter.drawLine(QPointF(11.5 * u, 4.5 * u), QPointF(4.5 * u, 11.5 * u))


class ResultBanner(QFrame):
    """The strip itself: `show_result(text, tone)` shows it, the cross (or `clear()`) hides it."""

    dismissed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("resultBanner")
        self.setProperty("tone", OK)
        self._icon = _ToneIcon()
        self._label = QLabel()
        self._label.setObjectName("resultBannerText")
        self._label.setWordWrap(True)
        self._label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._label.setMinimumWidth(0)
        self._label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self._close = _CloseButton()
        self._close.clicked.connect(self._dismiss)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 6, 8)
        layout.setSpacing(8)
        layout.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self._label, 1)
        layout.addWidget(self._close, 0, Qt.AlignmentFlag.AlignTop)
        theme.notifier.changed.connect(self._restyle)
        self.hide()

    def show_result(self, text: str, tone: str = OK, tooltip: str = "") -> None:
        self._set_tone(tone)
        self._label.setText(text)
        self.setToolTip(tooltip)
        self.show()

    def text(self) -> str:
        return self._label.text()

    def tone(self) -> str:
        return str(self.property("tone"))

    def clear(self) -> None:
        self.hide()
        self._label.clear()
        self.setToolTip("")

    def _dismiss(self) -> None:
        self.clear()
        self.dismissed.emit()

    def _set_tone(self, tone: str) -> None:
        self.setProperty("tone", tone)
        self._icon.tone = self._close.tone = tone
        self._restyle()

    def _restyle(self) -> None:
        # (a style sheet keyed on a property is only re-read when the widget is polished again)
        for widget in (self, self._label):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        self._icon.update()
        self._close.update()
