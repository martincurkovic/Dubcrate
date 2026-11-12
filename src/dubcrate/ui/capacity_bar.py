"""How full a disc is: one bar with a segment per track, plus a summary line.

Adapted from akaiutil-GUI's UsageBar: a single rounded track with the segments
flush against each other and clipped to its outline, a tooltip per segment, and
a label whose `level` property lets the stylesheet colour a warning or an
overflow. When a disc is over capacity the bar shows everything on it and marks
where the disc would have ended.
"""

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QLabel, QSizePolicy, QToolTip, QVBoxLayout, QWidget

from dubcrate import mp3
from dubcrate.library.models import DiscEntry, DiscSummary
from dubcrate.mp3 import format_span

from . import theme
from .track_colors import fill_track

CORNER_RADIUS = 3
MIN_SEGMENT_PX = 1.0  # a track narrower than this still shows as a hairline


@dataclass(frozen=True)
class Span:
    """A stretch of the bar, `weight` sectors long."""

    weight: float
    kind: str  # "track" | "gap"
    position: int = 0  # for a track: its place on the disc, which picks the colour
    tooltip: str = ""


def disc_spans(entries: list[DiscEntry], pregap_sectors: int = 0) -> list[Span]:
    spans: list[Span] = []
    for entry in entries:
        if entry.position > 0 and pregap_sectors:
            spans.append(Span(pregap_sectors, "gap", tooltip="Silence between tracks"))
        note = "" if entry.exact else "  (estimated)"
        name = " – ".join(part for part in (entry.artist, entry.title) if part)
        spans.append(
            Span(
                entry.sectors,
                "track",
                entry.position,
                f"{entry.position + 1}. {name}\n{format_span(entry.sectors, entry.kind)}{note}",
            )
        )
    return spans


def _fullest_folder(entries: list[DiscEntry]) -> int:
    """The most files in any one folder (the root counts as one) of an MP3 disc."""
    counts: dict[int | None, int] = {}
    for e in entries:
        counts[e.dir_id] = counts.get(e.dir_id, 0) + 1
    return max(counts.values(), default=0)


class CapacityBar(QWidget):
    HEIGHT = 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self._spans: list[Span] = []
        self._capacity = 0
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        theme.notifier.changed.connect(self.update)

    def set_spans(self, spans: list[Span], capacity: int) -> None:
        self._spans = [s for s in spans if s.weight > 0]
        self._capacity = capacity
        self.update()

    @property
    def spans(self) -> list[Span]:
        return list(self._spans)

    def _track_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

    def _scale(self) -> float:
        """Sectors the full width stands for: the capacity, or more if the disc overflows."""
        return max(self._capacity, sum(s.weight for s in self._spans), 1)

    def span_rects(self) -> list[QRectF]:
        """Where each span sits. Widths are proportional to sectors, except that a
        track too narrow to see is widened to a hairline (taken from the rest)."""
        spans = self._spans
        if not spans:
            return []
        track = self._track_rect()
        scale = self._scale()
        widths = [track.width() * s.weight / scale for s in spans]
        small = [
            i
            for i, s in enumerate(spans)
            if s.kind == "track" and widths[i] < MIN_SEGMENT_PX
        ]
        if small:
            big = [i for i in range(len(spans)) if i not in small]
            big_sum = sum(widths[i] for i in big)
            extra = MIN_SEGMENT_PX * len(small) - sum(widths[i] for i in small)
            if big_sum > extra > 0:  # only if there is room to take it from
                for i in big:
                    widths[i] *= (big_sum - extra) / big_sum
                for i in small:
                    widths[i] = MIN_SEGMENT_PX
        rects, x = [], track.left()
        for w in widths:
            rects.append(QRectF(x, track.top(), w, track.height()))
            x += w
        return rects

    def _snapped(self, rects: list[QRectF]) -> list[QRectF]:
        """The rects with their edges on whole device pixels, so neighbours meet
        exactly and no anti-aliased seam shows between them."""
        dpr = self.devicePixelRatioF()

        def snap(x: float) -> float:
            return round(x * dpr) / dpr

        out = []
        for r in rects:
            left, right = snap(r.left()), snap(r.right())
            out.append(QRectF(left, r.top(), max(0.0, right - left), r.height()))
        return out

    def over_x(self) -> float | None:
        """The x where the disc would have ended, if what's on it is too long."""
        total = sum(s.weight for s in self._spans)
        if self._capacity <= 0 or total <= self._capacity:
            return None
        track = self._track_rect()
        return track.left() + track.width() * self._capacity / total

    # --------------------------------------------------------------- painting
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        palette = theme.current_palette()
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing, not palette.get("hard_edges")
        )
        track = self._track_rect()
        path = QPainterPath()
        path.addRoundedRect(track, CORNER_RADIUS, CORNER_RADIUS)
        painter.fillPath(
            path, QColor(palette["bg_disabled"])
        )  # the empty track, as in QProgressBar

        painter.save()
        painter.setClipPath(path)  # only the bar's true ends are rounded
        rects = self._snapped(self.span_rects())
        for span, rect in zip(self._spans, rects, strict=True):
            if span.kind == "track":
                fill_track(painter, rect, span.position)
            else:
                painter.fillRect(
                    rect,
                    QBrush(QColor(palette["border_hover"]), Qt.BrushStyle.BDiagPattern),
                )
        if palette.get("hard_edges"):
            # Neighbouring greys need a line between them; colours don't.
            painter.setPen(QPen(QColor(palette["bg_panel"]), 1))
            for span, rect in zip(self._spans, rects, strict=True):
                if span.kind == "track" and rect.right() < track.right() - 0.5:
                    painter.drawLine(
                        QPointF(rect.right(), track.top()),
                        QPointF(rect.right(), track.bottom()),
                    )
        edge = self.over_x()
        if edge is not None:  # what lies past the end of the disc
            critical = QColor(palette["critical"])
            tint = QColor(critical)
            tint.setAlpha(110)
            painter.fillRect(
                QRectF(edge, track.top(), track.right() - edge, track.height()), tint
            )
            painter.setPen(QPen(critical, 2))
            painter.drawLine(QPointF(edge, track.top()), QPointF(edge, track.bottom()))
        painter.restore()

        painter.setPen(QColor(palette["border_progress"]))
        painter.drawPath(path)
        painter.end()

    # ---------------------------------------------------------------- tooltip
    def span_at(self, pos: QPointF) -> Span | None:
        for span, rect in zip(self._spans, self.span_rects(), strict=True):
            if rect.left() <= pos.x() < rect.right() or (
                rect.width() < 3 and abs(pos.x() - rect.center().x()) < 2
            ):
                return span
        return None

    def event(self, e) -> bool:
        if e.type() == QEvent.Type.ToolTip:
            span = self.span_at(QPointF(e.pos()))
            if span is not None and span.tooltip:
                QToolTip.showText(e.globalPos(), span.tooltip, self)
            else:
                QToolTip.hideText()
                e.ignore()
            return True
        return super().event(e)


class CapacitySummary(QWidget):
    """The bar with a short text line under it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bar = CapacityBar()
        self.label = QLabel()
        self.label.setObjectName(
            "usageLabel"
        )  # the stylesheet's [level="warning" / "critical"] colours
        self.label.setWordWrap(True)
        self.label.setMinimumHeight(
            2 * self.label.fontMetrics().lineSpacing()
        )  # no jumping as the text changes
        self.label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(5)
        layout.addWidget(self.bar)
        layout.addWidget(self.label)

    def _set_text(self, text: str, level: str = "normal") -> None:
        self.label.setText(text)
        if self.label.property("level") != level:
            self.label.setProperty("level", level)
            self.label.style().unpolish(
                self.label
            )  # make the QSS [level=...] rule re-evaluate
            self.label.style().polish(self.label)

    def clear(self) -> None:
        self.bar.set_spans([], 0)
        self._set_text("")

    def show_disc(self, summary: DiscSummary, entries: list[DiscEntry]) -> None:
        self.bar.set_spans(
            disc_spans(entries, summary.pregap_sectors), summary.capacity_sectors
        )
        kind = summary.kind
        estimated = any(not e.exact for e in entries)
        broken = sum(1 for e in entries if e.track.missing or e.track.stale)
        count = f"{summary.track_count} track{'s' if summary.track_count != 1 else ''}"
        used = format_span(summary.used_sectors, kind)
        total = f"{'~' if estimated else ''}{used} of {format_span(summary.capacity_sectors, kind)}"
        if kind == "mp3":
            count += "  ·  MP3 CD"
        if summary.over:
            text = f"{total}  ·  over by {format_span(-summary.free_sectors, kind)}  ·  {count}"
            level = "critical"
        else:
            text = f"{total}  ·  {format_span(summary.free_sectors, kind)} free  ·  {count}"
            level = "normal"
        if broken:
            text += f"\n{broken} track{'s are' if broken != 1 else ' is'} missing or no longer in its source"
            level = "critical" if summary.over else "warning"
        elif kind == "audio" and summary.track_count > 99:
            text += "\nA CD holds at most 99 tracks"
            level = "critical"
        elif kind == "mp3" and _fullest_folder(entries) > mp3.MAX_FILES_PER_FOLDER:
            text += f"\nA folder holds at most {mp3.MAX_FILES_PER_FOLDER} files: put some in another folder"
            level = "critical"
        self._set_text(text, level)
