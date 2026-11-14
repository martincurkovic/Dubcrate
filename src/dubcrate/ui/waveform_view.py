"""The two waveform displays: the whole track at a glance, and a zoomed view that scrolls.

Both draw the same waveform in the look chosen in Settings: the 3-band one (lows blue,
mids amber, highs white, as rekordbox does), RGB, or blue. The overview also shows phrases (INTRO, CHORUS, ...) and cues, and dims what has
been played; the detail view keeps the playhead fixed in the middle and shows the beat grid.
"""

import math

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from dubcrate.analysis import Cue, Phrase
from dubcrate.waveform import BAND_HIGH, BAND_LOW, BAND_MID, STYLE_BANDS, Waveform

from . import theme
from .wave_style import (
    cache_token,
    color,
    current_style,
    hot_cue_color,
    is_mono,
    normalized,
    phrase_color,
    render_columns,
)
from .wave_style import notifier as wave_notifier

_BANDS = ((BAND_LOW, "wave_low"), (BAND_MID, "wave_mid"), (BAND_HIGH, "wave_high"))
CUE_STRIP = 12  # room above the overview's waveform for cue flags
WAVE_HEIGHT = 40
PHRASE_STRIP = 14
ZOOMS = (4.0, 8.0, 16.0, 32.0)  # seconds the detail view shows
DEFAULT_ZOOM = 8.0


FLAG_WIDTH = 10  # a hot cue's lettered flag
MEMORY_COLOR = "#dd0907"
LOOP_COLOR = "#e8922b"


def flag_lefts(xs: list[float], width: float = FLAG_WIDTH) -> list[float]:
    """Left edges for flags `width` wide at `xs` (in order), pushed right so a flag never covers the
    one before: hot cues C and D can be milliseconds apart."""
    lefts: list[float] = []
    edge = -math.inf
    for x in xs:
        lefts.append(max(x, edge))
        edge = lefts[-1] + width + 1
    return lefts


def _draw_flag(
    p: QPainter, left: float, top: float, height: float, fill: QColor, letter: str = ""
) -> None:
    flag = QRectF(left, top, FLAG_WIDTH, height)
    p.fillRect(flag, fill)
    if letter:
        p.setPen(QColor("#ffffff"))
        p.drawText(flag, Qt.AlignmentFlag.AlignCenter, letter)


def _cue_color(cue: Cue) -> QColor:
    """A cue's colour: its hot cue's, else red for a memory cue and orange for a loop."""
    if cue.slot:
        return hot_cue_color(cue.slot)
    return QColor(MEMORY_COLOR if cue.kind == "memory" else LOOP_COLOR)


def _seconds_at(x: float, width: int, duration: float) -> float:
    return 0.0 if width <= 0 else max(0.0, min(1.0, x / width)) * duration


class OverviewView(QWidget):
    """The whole track in one strip; click or drag to seek."""

    seek_requested = Signal(float)

    HEIGHT = CUE_STRIP + WAVE_HEIGHT + PHRASE_STRIP

    def __init__(self, parent=None):
        super().__init__(parent)
        self._waveform: Waveform | None = None
        self._duration = 0.0
        self._phrases: tuple[Phrase, ...] = ()
        self._cues: tuple[Cue, ...] = ()
        self._position = 0.0
        self._cache: QPixmap | None = None
        self._cache_key: tuple | None = None
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        theme.notifier.changed.connect(self._invalidate)
        wave_notifier.changed.connect(self._invalidate)

    # -- content ----------------------------------------------------------

    def set_track(
        self,
        duration: float,
        waveform: Waveform | None,
        phrases: tuple[Phrase, ...] = (),
        cues: tuple[Cue, ...] = (),
    ) -> None:
        self._duration, self._waveform, self._phrases, self._cues = (
            duration,
            waveform,
            phrases,
            cues,
        )
        self._position = 0.0
        self._invalidate()

    def set_cues(self, cues: tuple[Cue, ...]) -> None:
        """Replace the cues of the track on show (they can arrive after the track)."""
        self._cues = cues
        self._invalidate()

    def clear(self) -> None:
        self.set_track(0.0, None)

    def set_position(self, seconds: float) -> None:
        if seconds != self._position:
            self._position = seconds
            self.update()

    def time_at(self, x: float) -> float:
        return _seconds_at(x, self.width(), self._duration)

    def _invalidate(self) -> None:
        self._cache = None
        self.update()

    # -- painting ---------------------------------------------------------

    def _render(self, width: int, dpr: float) -> QPixmap:
        pixmap = QPixmap(round(width * dpr), round(self.HEIGHT * dpr))
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(color("wave_bg"))
        p = QPainter(pixmap)
        p.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            not theme.current_palette().get("hard_edges"),
        )
        base = (
            CUE_STRIP + WAVE_HEIGHT
        )  # the waveform sits on this line, as in rekordbox's overview
        if self._waveform is not None and self._waveform.columns and width > 0:
            self._paint_waveform(p, width, dpr, base)
        if self._duration > 0:
            self._paint_phrases(p, width, base)
            self._paint_cues(p, width)
        p.end()
        return pixmap

    def _paint_waveform(self, p: QPainter, width: int, dpr: float, base: int) -> None:
        wf = self._waveform
        assert wf is not None
        style = current_style()
        if style == STYLE_BANDS:
            bands = normalized(wf.resampled(width, how="mean").bands, style)
            for band, key in _BANDS:
                p.setPen(QPen(color(key), 1))
                heights = bands[:, band] * WAVE_HEIGHT
                p.drawLines(
                    [
                        QLineF(x + 0.5, base, x + 0.5, base - h)
                        for x, h in enumerate(heights)
                        if h > 0.3
                    ]
                )
            return
        pixels = round(width * dpr)  # one column per device pixel
        bands = normalized(wf.resampled(pixels, how="mean").bands, style)
        rows = round(WAVE_HEIGHT * dpr)
        image = render_columns(bands, style, rows=rows, extent=rows, mirrored=False)
        image.setDevicePixelRatio(dpr)
        p.drawImage(QPointF(0, base - WAVE_HEIGHT), image)

    def _paint_phrases(self, p: QPainter, width: int, base: int) -> None:
        font = QFont(p.font())
        font.setPixelSize(9)
        font.setBold(True)
        p.setFont(font)
        mono = is_mono()
        for i, phrase in enumerate(self._phrases):
            x0 = round(phrase.start / self._duration * width)
            x1 = round(phrase.end / self._duration * width)
            rect = QRectF(x0, base + 1, max(1, x1 - x0 - 1), PHRASE_STRIP - 1)
            p.fillRect(rect, phrase_color(phrase.label, i))
            p.setPen(QColor("#000000") if mono else QColor("#ffffff"))
            if p.fontMetrics().horizontalAdvance(phrase.label) + 6 < rect.width():
                p.drawText(
                    rect.adjusted(3, 0, 0, 0),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                    phrase.label,
                )

    def _paint_cues(self, p: QPainter, width: int) -> None:
        font = QFont(p.font())
        font.setPixelSize(9)
        font.setBold(True)
        p.setFont(font)
        cues = sorted(self._cues, key=lambda c: c.time)
        flagged = [c for c in cues if c.slot]
        lefts = flag_lefts([round(c.time / self._duration * width) for c in flagged])
        for cue, left in zip(flagged, lefts, strict=True):
            _draw_flag(
                p,
                left,
                0,
                CUE_STRIP - 2,
                hot_cue_color(cue.slot),
                chr(ord("A") + cue.slot - 1),
            )
        for cue in cues:
            if cue.slot:
                continue
            x = round(cue.time / self._duration * width)
            p.setPen(Qt.PenStyle.NoPen)  # memory cues and loops: a small marker
            p.setBrush(_cue_color(cue))
            p.drawPolygon(
                QPolygonF([QPointF(x - 3, 0), QPointF(x + 3, 0), QPointF(x, 7)])
            )

    def paintEvent(self, _event) -> None:
        width = self.width()
        dpr = self.devicePixelRatioF()
        key = (width, dpr, cache_token())
        if self._cache is None or self._cache_key != key:
            self._cache, self._cache_key = self._render(width, dpr), key
        p = QPainter(self)
        p.drawPixmap(0, 0, self._cache)
        if self._duration > 0:
            x = self._position / self._duration * width
            dim = color("wave_bg")
            dim.setAlpha(150)
            p.fillRect(
                QRectF(0, CUE_STRIP, x, WAVE_HEIGHT), dim
            )  # what has been played, dimmed
            p.fillRect(QRectF(x - 1, 0, 2, self.HEIGHT), color("wave_playhead"))

    # -- seeking ----------------------------------------------------------

    def _seek_from(self, event) -> None:
        if self._duration > 0:
            self.seek_requested.emit(self.time_at(event.position().x()))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._seek_from(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._seek_from(event)


class DetailView(QWidget):
    """A zoomed waveform scrolling past a fixed playhead, with the beat grid.

    Drag it to scratch, as on a CDJ's jog wheel: the waveform sticks to the pointer
    (drag right and the track goes back), and `scratch_moved` says where the playhead
    now is. While a hand is on it the player's own position is ignored.
    """

    scratch_started = Signal()
    scratch_moved = Signal(float)  # seconds into the track
    scratch_ended = Signal()

    HEIGHT = 112

    def __init__(self, parent=None):
        super().__init__(parent)
        self._waveform: Waveform | None = None
        self._beat_times = np.zeros(0)
        self._beat_numbers = np.zeros(0, dtype=np.int8)
        self._cues: tuple[Cue, ...] = ()
        self._position = 0.0
        self._window = DEFAULT_ZOOM
        self._message = ""
        self._dragging = False
        self._drag_x = 0.0
        self._drag_from = 0.0
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        theme.notifier.changed.connect(self.update)
        wave_notifier.changed.connect(self.update)

    # -- content ----------------------------------------------------------

    def set_track(
        self,
        waveform: Waveform | None,
        beat_times=None,
        beat_numbers=None,
        message: str = "",
        cues: tuple[Cue, ...] = (),
    ) -> None:
        self._waveform = waveform
        self._cues = tuple(sorted(cues, key=lambda c: c.time))
        self._beat_times = np.zeros(0) if beat_times is None else np.asarray(beat_times)
        self._beat_numbers = (
            np.zeros(0, dtype=np.int8)
            if beat_numbers is None
            else np.asarray(beat_numbers)
        )
        self._message = message
        self._position = 0.0
        self._dragging = False  # a new track: whatever was being dragged is gone
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.update()

    def set_cues(self, cues: tuple[Cue, ...]) -> None:
        """Replace the cues of the track on show (they can arrive after the track)."""
        self._cues = tuple(sorted(cues, key=lambda c: c.time))
        self.update()

    def clear(self, message: str = "") -> None:
        self.set_track(None, message=message)

    @property
    def dragging(self) -> bool:
        return self._dragging

    def set_position(self, seconds: float) -> None:
        if self._dragging:  # the hand decides where the waveform is
            return
        if seconds != self._position:
            self._position = seconds
            self.update()

    @property
    def window_seconds(self) -> float:
        return self._window

    def set_window_seconds(self, seconds: float) -> None:
        self._window = max(ZOOMS[0], min(ZOOMS[-1], seconds))
        self.update()

    def zoom(self, steps: int) -> None:
        """Positive zooms in (a shorter stretch of the track), negative zooms out."""
        index = min(range(len(ZOOMS)), key=lambda i: abs(ZOOMS[i] - self._window))
        self.set_window_seconds(ZOOMS[max(0, min(len(ZOOMS) - 1, index - steps))])

    def time_at(self, x: float) -> float:
        """The track time at horizontal position `x`, with the playhead in the middle."""
        return self._position + (x - self.width() / 2) * self._window / max(
            1, self.width()
        )

    # -- painting ---------------------------------------------------------

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            not theme.current_palette().get("hard_edges"),
        )
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), color("wave_bg"))
        if self._waveform is None or not self._waveform.columns:
            p.setPen(color("wave_text"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message)
            return
        centre, middle = w / 2, h / 2
        per_second = w / self._window
        self._paint_zero_line(p, w, middle)
        if current_style() == STYLE_BANDS:
            self._paint_bands(p, w, centre, middle, per_second)
        else:
            self._paint_columns(p, w, h, centre, per_second)
        self._paint_beats(p, w, h, centre, per_second)
        self._paint_cues(p, w, h, centre, per_second)
        p.fillRect(QRectF(centre - 1, 0, 2, h), color("wave_playhead"))

    def _paint_zero_line(self, p: QPainter, width: int, middle: float) -> None:
        """A faint line along the waveform's centre (silence), under the waveform, so the gaps before,
        after and inside a track are not bare. Not in System 6, which has no faint."""
        if is_mono():
            return
        line = color("wave_text")
        line.setAlpha(55)
        p.setPen(QPen(line, 1))
        y = math.floor(middle) + 0.5  # on a pixel row, so it stays one crisp line
        p.drawLine(QPointF(0, y), QPointF(width, y))

    def _paint_columns(
        self, p: QPainter, width: int, height: int, centre: float, per_second: float
    ) -> None:
        """The RGB and blue looks: one bar per device pixel, each in its own colour."""
        wf = self._waveform
        assert wf is not None
        dpr = self.devicePixelRatioF()
        pixels, rows = round(width * dpr), round(height * dpr)
        seconds = self._position + (np.arange(pixels) / dpr - centre) / per_second
        index = np.floor(seconds * wf.columns_per_second).astype(np.int64)
        inside = (index >= 0) & (index < wf.columns)
        bands = np.zeros((pixels, 3), dtype=np.float32)
        bands[inside] = wf.bands[index[inside]]
        image = render_columns(
            bands,
            current_style(),
            rows=rows,
            extent=rows / 2 - 6 * dpr,
            mirrored=True,
        )
        image.setDevicePixelRatio(dpr)
        p.drawImage(QPointF(0, 0), image)

    def _paint_bands(
        self, p: QPainter, width: int, centre: float, middle: float, per_second: float
    ) -> None:
        wf = self._waveform
        assert wf is not None
        rate = wf.columns_per_second
        first = max(0, math.floor((self._position - self._window / 2) * rate))
        last = min(
            wf.columns, math.ceil((self._position + self._window / 2) * rate) + 1
        )
        step = max(1, round(rate / per_second))  # about one column per pixel
        index = np.arange(first, last, step)
        if len(index) < 2:
            return
        xs = centre + (index / rate - self._position) * per_second
        reach = middle - 6
        p.setPen(Qt.PenStyle.NoPen)
        for band, key in _BANDS:
            half = wf.bands[index, band] * reach
            top = [
                QPointF(x, middle - v)
                for x, v in zip(xs.tolist(), half.tolist(), strict=True)
            ]
            bottom = [
                QPointF(x, middle + v)
                for x, v in zip(xs[::-1].tolist(), half[::-1].tolist(), strict=True)
            ]
            p.setBrush(color(key))
            p.drawPolygon(QPolygonF(top + bottom))

    def _paint_beats(
        self, p: QPainter, width: int, height: int, centre: float, per_second: float
    ) -> None:
        if not len(self._beat_times):
            return
        low = self._position - self._window / 2
        start, stop = np.searchsorted(self._beat_times, [low, low + self._window])
        faint, bar = color("wave_beat"), color("wave_text")
        bar.setAlpha(150)
        caps = QColor("#dd0907")
        for i in range(max(0, start - 1), min(len(self._beat_times), stop + 1)):
            x = centre + (self._beat_times[i] - self._position) * per_second
            if self._beat_numbers[i] == 1:  # the first beat of a bar
                p.setPen(QPen(bar, 1))
                p.drawLine(QPointF(x, 0), QPointF(x, height))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(caps)
                p.drawPolygon(
                    QPolygonF([QPointF(x - 4, 0), QPointF(x + 4, 0), QPointF(x, 6)])
                )
                p.drawPolygon(
                    QPolygonF(
                        [
                            QPointF(x - 4, height),
                            QPointF(x + 4, height),
                            QPointF(x, height - 6),
                        ]
                    )
                )
            else:
                p.setPen(QPen(faint, 1))
                p.drawLine(QPointF(x, 0), QPointF(x, height))

    def _paint_cues(
        self, p: QPainter, width: int, height: int, centre: float, per_second: float
    ) -> None:
        """Memory cues, hot cues and loops that are in view: a line each, hot cues with their
        lettered flag at the top and memory cues with a plain one at the bottom (the beat grid's bar
        markers use the corners), a loop as a tinted span."""
        if not self._cues:
            return
        reach = (
            FLAG_WIDTH + 2
        ) / per_second  # a flag sticks out to the right of its line
        low = self._position - self._window / 2
        high = low + self._window
        seen = [
            c for c in self._cues if c.time <= high and (c.end or c.time + reach) >= low
        ]

        def x_of(seconds: float) -> float:
            return centre + (seconds - self._position) * per_second

        for cue in seen:  # the loops go under the lines
            if cue.end:
                span = _cue_color(cue)
                span.setAlpha(55)
                p.fillRect(
                    QRectF(
                        x_of(cue.time), 0, (cue.end - cue.time) * per_second, height
                    ),
                    span,
                )
        font = QFont(p.font())
        font.setPixelSize(9)
        font.setBold(True)
        p.setFont(font)
        for cue in seen:
            p.setPen(QPen(_cue_color(cue), 1.5 if cue.slot else 1))
            for t in (cue.time, cue.end) if cue.end else (cue.time,):
                p.drawLine(QPointF(x_of(t), 0), QPointF(x_of(t), height))
        flagged = [c for c in seen if c.slot]
        lefts = flag_lefts([x_of(c.time) for c in flagged])
        for cue, left in zip(flagged, lefts, strict=True):
            _draw_flag(
                p, left, 6, 12, hot_cue_color(cue.slot), chr(ord("A") + cue.slot - 1)
            )
        for cue in seen:  # plain red flags for the memory cues
            if cue.kind == "memory":
                _draw_flag(p, x_of(cue.time), height - 16, 8, _cue_color(cue))

    # -- interaction ------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        wave = self._waveform
        if (
            event.button() != Qt.MouseButton.LeftButton
            or wave is None
            or not wave.columns
        ):
            return
        self._dragging = True
        self._drag_x = event.position().x()
        self._drag_from = self._position
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.scratch_started.emit()

    def mouseMoveEvent(self, event) -> None:
        wave = self._waveform
        if not self._dragging or wave is None:
            return
        per_second = max(1, self.width()) / self._window
        seconds = self._drag_from - (event.position().x() - self._drag_x) / per_second
        seconds = max(0.0, min(seconds, wave.duration))
        if seconds != self._position:
            self._position = seconds
            self.update()
            self.scratch_moved.emit(seconds)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self._dragging:
            return
        self._dragging = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.scratch_ended.emit()

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta:
            self.zoom(1 if delta > 0 else -1)
            event.accept()
