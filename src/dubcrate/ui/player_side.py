"""The player's right-hand panel: the loaded track's memory cues, hot cues and info.

Three pages behind three tab buttons, as rbxport has them, **read-only**: the cues are rekordbox's and
Dubcrate never writes them. Clicking a memory or hot cue asks the player to jump there
(`jump_requested`). The Info page shows the track's embedded **artwork** (a painted note on a square when it has
none), the rating and comment rekordbox gave it and what the file is (type, size, sample rate, bitrate), in a
bordered frame like the other two pages.
"""

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dubcrate.analysis import Cue
from dubcrate.library.models import Track

from . import theme
from .player_controls import PillButton
from .wave_style import hot_cue_color

HOT_SLOTS = 8  # A to H
TIME_ROLE = Qt.ItemDataRole.UserRole + 40


def cue_clock(seconds: float) -> str:
    """A cue's time as rbxport shows it: `01:17.933`."""
    millis = round(max(0.0, seconds) * 1000)
    minutes, rest = divmod(millis, 60_000)
    return f"{minutes:02d}:{rest // 1000:02d}.{rest % 1000:03d}"


def stars(rating: int | None) -> str:
    """Five stars, as many filled as the rating (empty for unrated)."""
    filled = max(0, min(5, rating or 0))
    return "★" * filled + "☆" * (5 - filled)


def size_text(size: int | None) -> str:
    if not size:
        return ""
    return (
        f"{size / 1_000_000:.1f} MB" if size >= 1_000_000 else f"{size / 1000:.0f} kB"
    )


def file_lines(track: Track) -> list[str]:
    """What the file is, a line each: `MP3 File`, `11.9 MB`, `44100 Hz`, `320 kbps` (what is known)."""
    suffix = track.path.rsplit(".", 1)[-1].upper() if "." in track.path else ""
    lines = [f"{suffix} File" if suffix else ""]
    lines.append(size_text(track.file_size))
    lines.append(f"{track.sample_rate} Hz" if track.sample_rate else "")
    lines.append(f"{track.bitrate} kbps" if track.bitrate else "")
    return [line for line in lines if line]


class InfoFrame(QFrame):
    """The Info page's frame: drawn like the lists beside it (their panel colour, border and corners)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        theme.notifier.changed.connect(self.update)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, theme.antialiasing())
        palette = theme.current_palette()
        radius = 0 if palette.get("hard_edges") else 4
        painter.setPen(QPen(QColor(palette["border_list"]), 1))
        painter.setBrush(QColor(palette["bg_panel"]))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)  # fmt: skip


class ArtworkView(QWidget):
    """The track's cover, a square (cropped from the middle if the picture is not), or a placeholder.

    The placeholder is painted, not an image file: a music note on a plain square in the theme's colours."""

    SIZE = 104

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self._pixmap: QPixmap | None = None
        theme.notifier.changed.connect(self.update)

    @property
    def has_image(self) -> bool:
        return self._pixmap is not None

    def set_image(self, data: bytes | None) -> None:
        """Show the picture in `data` (a JPEG or PNG), or the placeholder if there is none or it can't be read."""
        self._pixmap = None
        if data:
            image = QImage.fromData(data)
            if not image.isNull():
                ratio = self.devicePixelRatioF()
                side = round(self.SIZE * ratio)
                scaled = image.scaled(
                    side,
                    side,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                left, top = (scaled.width() - side) // 2, (scaled.height() - side) // 2
                self._pixmap = QPixmap.fromImage(scaled.copy(left, top, side, side))
                self._pixmap.setDevicePixelRatio(ratio)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, theme.antialiasing())
        palette = theme.current_palette()
        box = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = 0 if palette.get("hard_edges") else 4
        clip = QPainterPath()
        clip.addRoundedRect(box, radius, radius)
        if self._pixmap is not None:
            painter.setClipPath(clip)
            painter.drawPixmap(self.rect(), self._pixmap)
            painter.setClipping(False)
            painter.setPen(QPen(QColor(palette["border_list"]), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box, radius, radius)
            return
        painter.setPen(QPen(QColor(palette["border_list"]), 1))
        painter.setBrush(QColor(palette["bg_input"]))
        painter.drawRoundedRect(box, radius, radius)
        font = QFont(self.font())
        font.setPointSizeF(self.SIZE * 0.34)
        painter.setFont(font)
        painter.setPen(QColor(palette["text_disabled"]))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, "♪")


def _list() -> QTreeWidget:
    tree = QTreeWidget()
    tree.setHeaderHidden(True)
    tree.setRootIsDecorated(False)
    tree.setUniformRowHeights(True)
    tree.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
    tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    tree.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
    return tree


class SidePanel(QWidget):
    """MEMORY | HOT CUE | INFO."""

    jump_requested = Signal(float)  # seconds

    def __init__(self, parent=None):
        super().__init__(parent)
        self.memory = _list()
        self.hot = _list()
        self.hot.setColumnCount(2)  # (the letter, then its time)
        self.memory.itemClicked.connect(self._clicked)
        self.hot.itemClicked.connect(self._clicked)
        self.rating = QLabel()
        self.comment = QLabel()
        self.comment.setWordWrap(True)
        self.comment.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        self.file = QLabel()
        self.file.setObjectName("mutedLabel")
        self.art = ArtworkView()
        details = QVBoxLayout()
        details.setSpacing(4)
        details.addWidget(self.rating)
        details.addStretch(1)
        details.addWidget(self.file)
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(self.art, 0, Qt.AlignmentFlag.AlignTop)
        top.addLayout(details, 1)
        info = InfoFrame()
        box = QVBoxLayout(info)
        box.setContentsMargins(10, 10, 10, 10)
        box.setSpacing(8)
        box.addLayout(top)
        box.addWidget(self.comment, 1)
        for label in (self.rating, self.comment, self.file):
            label.setMinimumWidth(0)
            label.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
            )
        self.pages = QStackedWidget()
        for page in (self.memory, self.hot, info):
            self.pages.addWidget(page)

        self.tabs = [
            PillButton(name, checkable=True, height=24, bold=True)
            for name in ("MEMORY", "HOT CUE", "INFO")
        ]
        group = QButtonGroup(self)
        group.setExclusive(True)
        row = QHBoxLayout()
        row.setSpacing(4)
        for number, button in enumerate(self.tabs):
            group.addButton(button, number)
            row.addWidget(button, 1)
        group.idClicked.connect(self.pages.setCurrentIndex)
        self.tabs[0].setChecked(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.pages, 1)
        layout.addLayout(row)
        self.set_track(None)
        self.set_cues(())

    # -- what is shown -----------------------------------------------------------

    def set_artwork(self, data: bytes | None) -> None:
        """The loaded track's embedded cover picture (None: none, so the placeholder)."""
        self.art.set_image(data)

    def set_track(self, track: Track | None) -> None:
        self.art.set_image(None)  # (the cover arrives with the audio)
        if track is None:
            self.rating.setText("")
            self.comment.setText("")
            self.file.setText("")
            return
        self.rating.setText(stars(track.rating))
        self.comment.setText(track.comment)
        self.file.setText("\n".join(file_lines(track)))

    def set_cues(self, cues: tuple[Cue, ...]) -> None:
        """The loaded track's cues: memory cues and loops in time order, and the hot cues by letter."""
        self.memory.clear()
        for cue in sorted((c for c in cues if c.slot is None), key=lambda c: c.time):
            text = cue_clock(cue.time)
            if cue.kind == "loop" and cue.end:
                text += f"  →  {cue_clock(cue.end)}"
            item = QTreeWidgetItem([text])
            item.setData(0, TIME_ROLE, cue.time)
            self.memory.addTopLevelItem(item)
        self.hot.clear()
        by_slot = {c.slot: c for c in cues if c.slot}
        for slot in range(1, HOT_SLOTS + 1):
            cue = by_slot.get(slot)
            item = QTreeWidgetItem(
                ["ABCDEFGH"[slot - 1], cue_clock(cue.time) if cue else ""]
            )
            if cue:
                item.setData(0, TIME_ROLE, cue.time)
                item.setForeground(0, hot_cue_color(slot))
            else:
                item.setForeground(0, self.palette().placeholderText())
            self.hot.addTopLevelItem(item)
        self.hot.setColumnWidth(0, 28)

    def _clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        seconds = item.data(0, TIME_ROLE)
        if seconds is not None:
            self.jump_requested.emit(float(seconds))
