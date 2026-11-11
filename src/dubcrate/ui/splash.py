"""The window shown while Dubcrate starts: the name, the version and one line saying what it
is doing right now ("Reading the rekordbox library…").

Startup is a few slow steps in a row (opening the library, bringing your rekordbox library up
to date, building the window), and without this the app simply doesn't appear for a while,
which looks like it hasn't started.

It is drawn entirely in code, with its own fixed dark look, because it appears before the
application's theme is applied and must not depend on any file being found (the app icon is
the one optional extra). Whatever takes time during startup reports into it with `set_status()`.

Modelled on the splash in the owner's Cobalt app: a rounded card on a transparent window.
"""

import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QWidget

CARD_SIZE = (520, 300)
MARGIN = 36
BACKGROUND = QColor("#15151b")
BORDER = QColor("#2c2c36")
TEXT = QColor("#ffffff")
MUTED = QColor("#8f8fa0")
# the three bands of a waveform, as rekordbox draws them: lows, mids, highs
WAVE_COLOURS = (QColor("#2f62ff"), QColor("#e8922b"), QColor("#f4f4f4"))
TAGLINE = "Burn audio CDs for old-style CDJs"


class StartupSplash(QWidget):
    def __init__(
        self, version: str = "", icon: QPixmap | None = None, activating: bool = False
    ):
        """`activating=False` makes it a true splash window: it appears without taking focus and
        has no taskbar entry, which is right in a built app. On macOS that kind of window is
        also non-activating, so one shown by a bare `python` run stays behind other windows
        until the Dock icon is clicked; so a run from source (and the preview) is an ordinary
        frameless window instead, which brings itself forward."""
        kind = Qt.WindowType.Window if activating else Qt.WindowType.SplashScreen
        super().__init__(
            None,
            kind
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.version = version
        self.icon = icon
        self.status = "Starting…"
        # a rounded card on a transparent window; the soft edge is painted, not the window's
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # The application's stylesheet reaches every widget, this one too (it would fill the
        # corners with the theme's background): this one is its own.
        self.setStyleSheet("background: transparent;")
        self.setFixedSize(*CARD_SIZE)
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            self.move(screen.availableGeometry().center() - self.rect().center())

    def set_status(self, text: str) -> None:
        """Say what is happening now, and repaint at once: the caller is about to do
        something slow on this same thread, so there is no later chance."""
        self.status = text
        self.repaint()
        QApplication.processEvents()

    def finish(self) -> None:
        self.close()

    # -- painting ---------------------------------------------------------

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        width, height = self.width(), self.height()

        card = QPainterPath()
        card.addRoundedRect(QRectF(0.5, 0.5, width - 1, height - 1), 14, 14)
        painter.fillPath(card, BACKGROUND)
        painter.setPen(QPen(BORDER, 1))
        painter.drawPath(card)

        painter.setPen(TEXT)
        painter.setFont(self._font(64, QFont.Weight.Bold))
        painter.drawText(
            QRectF(MARGIN, 34, width - 2 * MARGIN, 80),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "Dubcrate",
        )
        painter.setPen(MUTED)
        painter.setFont(self._font(15))
        painter.drawText(
            QRectF(MARGIN, 116, width - 2 * MARGIN, 22),
            Qt.AlignmentFlag.AlignLeft,
            TAGLINE,
        )
        self._paint_wave(painter, QRectF(MARGIN, 156, width - 2 * MARGIN, 44))

        painter.setPen(MUTED)
        painter.setFont(self._font(12))
        painter.drawText(
            QRectF(MARGIN, height - 80, 360, 18),
            Qt.AlignmentFlag.AlignLeft,
            self.version,
        )
        status_font = self._font(12)
        room = width - 2 * MARGIN - 70
        shown = QFontMetrics(status_font).elidedText(
            self.status, Qt.TextElideMode.ElideRight, room
        )
        painter.setPen(TEXT)
        painter.setFont(status_font)
        painter.drawText(
            QRectF(MARGIN, height - 58, room, 20), Qt.AlignmentFlag.AlignLeft, shown
        )
        if self.icon is not None and not self.icon.isNull():
            size = 46
            target = QRectF(width - MARGIN - size, height - MARGIN - size, size, size)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(target, self.icon, QRectF(self.icon.rect()))

    @staticmethod
    def _paint_wave(painter: QPainter, area: QRectF) -> None:
        """A little 3-band waveform, as a nod to what the app draws: the lows tall in blue,
        the mids amber over them, the highs white on top. The same every time."""
        bars = 64
        step = area.width() / bars
        middle = area.center().y()
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(bars):
            beat = 0.5 + 0.5 * math.cos(
                i / bars * 4 * math.pi * 2
            )  # a pulse, like a kick
            heights = (
                0.35 + 0.65 * beat,
                0.30 + 0.35 * abs(math.sin(i * 0.9)),
                0.12 + 0.2 * abs(math.sin(i * 2.3 + 1)),
            )
            for colour, fraction in zip(WAVE_COLOURS, heights, strict=True):
                half = area.height() / 2 * fraction
                painter.setBrush(colour)
                painter.drawRect(
                    QRectF(
                        area.left() + i * step + 1,
                        middle - half,
                        max(1.0, step - 2),
                        2 * half,
                    )
                )

    @staticmethod
    def _font(pixel_size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
        # the platform's own font, not the application's (a theme can change that one)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
        font.setPixelSize(pixel_size)
        font.setWeight(weight)
        return font


# -- previewing it --------------------------------------------------------------
#
#   uv run python -m dubcrate.ui.splash              stays up, cycling through the real
#                                                    status lines, until you click it
#   uv run python -m dubcrate.ui.splash --seconds 5  closes itself after 5 seconds
#
# Startup is normally too quick to see it properly, so this shows it on its own.

PREVIEW_STATUSES = (
    "Opening your library…",
    "Reading the rekordbox library…",
    "Updating your library…",
    "Loading Dubcrate…",
)


class _PreviewSplash(StartupSplash):
    def mousePressEvent(self, event) -> None:
        QApplication.quit()


def preview(argv: list[str]) -> int:
    from PySide6.QtCore import QTimer

    app = QApplication.instance() or QApplication(argv)
    splash = _PreviewSplash("Preview", activating=True)
    splash.show()
    splash.raise_()
    splash.activateWindow()

    position = {"i": 0}

    def next_status() -> None:
        splash.set_status(PREVIEW_STATUSES[position["i"] % len(PREVIEW_STATUSES)])
        position["i"] += 1

    cycle = QTimer()
    cycle.timeout.connect(next_status)
    cycle.start(1300)
    next_status()

    if "--seconds" in argv:
        QTimer.singleShot(
            int(float(argv[argv.index("--seconds") + 1]) * 1000), app.quit
        )
    else:
        print("Showing the splash screen: click it to close.")
    code = app.exec()
    splash.close()
    return code


if __name__ == "__main__":
    import sys

    raise SystemExit(preview(sys.argv))
