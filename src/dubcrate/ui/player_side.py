"""The player's right-hand panel: the loaded track's memory cues, hot cues and info.

Three pages behind three tab buttons, as rbxport has them, **read-only**: the cues are rekordbox's and
Dubcrate never writes them. Clicking a memory or hot cue asks the player to jump there
(`jump_requested`); a loop's row asks for the loop instead (`loop_requested`: go to its start with that loop on). The Info page shows the track's embedded **artwork** (a painted note on a square when it has
none), the rating and comment rekordbox gave it and what the file is (type, size, sample rate, bitrate), in a
bordered frame like the other two pages.

The Memory page is also where **memory cues are edited** (the rekordbox library itself is never written): a row's ✕
deletes it, right-click moves it to the playhead, and the right-click menu of an edited track offers **Reset**, which goes back to
rekordbox's. A **memory loop** (MEMORY with a loop on saves one; rekordbox's own are listed too) has its ✕ as well (no
move: save it again where you want it); hot cues are only shown.
"""

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
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
DELETABLE_ROLE = (
    Qt.ItemDataRole.UserRole + 41
)  # a memory cue or a loop saved here: one that can be deleted here
MOVABLE_ROLE = Qt.ItemDataRole.UserRole + 42  # a plain memory cue (a loop has no move)
LOOP_ROLE = Qt.ItemDataRole.UserRole + 43  # a loop: a small loop mark follows its time
END_ROLE = Qt.ItemDataRole.UserRole + 44  # a loop's end, seconds
LOOP_COLOR = "#f0922b"  # the orange rbxport marks a loop with


def draw_loop_mark(painter: QPainter, centre: QPointF, color: QColor) -> None:
    """A small circular arrow (a loop) about 12 pixels across, centred on `centre`."""
    radius = 5.0
    start, span = (
        120.0,
        -285.0,
    )  # degrees, counter-clockwise from 3 o'clock: clockwise, with a gap at the top
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(color, 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    box = QRectF(centre.x() - radius, centre.y() - radius, 2 * radius, 2 * radius)
    painter.drawArc(box, round(start * 16), round(span * 16))
    # the arrow head at the end of the arc, pointing the way the arc runs (clockwise)
    end = math.radians(start + span)
    tip_from = QPointF(
        centre.x() + radius * math.cos(end), centre.y() - radius * math.sin(end)
    )
    along = QPointF(math.sin(end), math.cos(end))
    across = QPointF(-along.y(), along.x())
    head = QPolygonF(
        [
            tip_from + along * 3.5,
            tip_from + across * 3.2,
            tip_from - across * 3.2,
        ]
    )
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawPolygon(head)
    painter.restore()


class _LoopMarkDelegate(QStyledItemDelegate):
    """The Memory list's rows, with a small orange loop mark after the time of a loop (as rbxport marks one)."""

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        if not index.data(LOOP_ROLE):
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        text_box = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemText, opt, widget
        )
        margin = style.pixelMetric(QStyle.PixelMetric.PM_FocusFrameHMargin, opt, widget) + 1  # fmt: skip
        x = text_box.left() + margin + opt.fontMetrics.horizontalAdvance(opt.text) + 12
        mono = theme.current_palette().get("hard_edges")
        color = opt.palette.text().color() if mono else QColor(LOOP_COLOR)
        draw_loop_mark(painter, QPointF(x, opt.rect.center().y()), color)


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

    The placeholder is painted, not an image file: a music note on a plain square in the theme's colours.
    It is as big as `SIZE` when there is room and shrinks to fit when there is not, so the Info page never asks the
    player strip for more height than the Memory and Hot Cue pages need (the window used to grow when INFO was picked)."""

    SIZE = 160

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setMaximumHeight(self.SIZE)
        self._source: QImage | None = (
            None  # the cover, cropped to a square at its own size
        )
        self._scaled: tuple[int, QPixmap] | None = (
            None  # (device pixels a side, the cover at that size)
        )
        theme.notifier.changed.connect(self.update)

    @property
    def has_image(self) -> bool:
        return self._source is not None

    def sizeHint(self) -> QSize:
        return QSize(self.SIZE, self.SIZE)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 0)

    def set_image(self, data: bytes | None) -> None:
        """Show the picture in `data` (a JPEG or PNG), or the placeholder if there is none or it can't be read."""
        self._source = None
        self._scaled = None
        if data:
            image = QImage.fromData(data)
            if not image.isNull():
                side = min(image.width(), image.height())
                left, top = (image.width() - side) // 2, (image.height() - side) // 2
                self._source = image.copy(left, top, side, side)
        self.update()

    def _square(self) -> QRectF:
        """The square the cover is drawn in: as big as the room allows (at most `SIZE`), in the middle."""
        side = min(self.width(), self.height(), self.SIZE)
        return QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)

    def _cover(self, side: float) -> QPixmap | None:
        if self._source is None:
            return None
        ratio = self.devicePixelRatioF()
        device = max(1, round(side * ratio))
        if self._scaled is None or self._scaled[0] != device:
            scaled = self._source.scaled(
                device,
                device,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            pixmap = QPixmap.fromImage(scaled)
            pixmap.setDevicePixelRatio(ratio)
            self._scaled = (device, pixmap)
        return self._scaled[1]

    def paintEvent(self, _event) -> None:
        square = self._square()
        if square.width() < 1:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, theme.antialiasing())
        palette = theme.current_palette()
        box = square.adjusted(0.5, 0.5, -0.5, -0.5)
        radius = 0 if palette.get("hard_edges") else 4
        clip = QPainterPath()
        clip.addRoundedRect(box, radius, radius)
        cover = self._cover(square.width())
        if cover is not None:
            painter.setClipPath(clip)
            painter.drawPixmap(square.toRect(), cover)
            painter.setClipping(False)
            painter.setPen(QPen(QColor(palette["border_list"]), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box, radius, radius)
            return
        painter.setPen(QPen(QColor(palette["border_list"]), 1))
        painter.setBrush(QColor(palette["bg_input"]))
        painter.drawRoundedRect(box, radius, radius)
        font = QFont(self.font())
        font.setPointSizeF(max(1.0, square.width() * 0.34))
        painter.setFont(font)
        painter.setPen(QColor(palette["text_disabled"]))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, "♪")


class _CommentArea(QScrollArea):
    """Holds the track's comment, which wraps. A wrapping label's height depends on its width, which would make the
    Info page, the side panel and the whole player strip ask for a taller window; in a scroll area it is cut off and
    scrolls instead, and the area itself asks for no height."""

    def __init__(self, label: QLabel, parent=None):
        super().__init__(parent)
        self.setWidget(label)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.viewport().setAutoFillBackground(False)
        label.setAutoFillBackground(False)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self.fontMetrics().lineSpacing())

    def sizeHint(self) -> QSize:
        return QSize(0, self.fontMetrics().lineSpacing() * 2)


def _list() -> QTreeWidget:
    tree = QTreeWidget()
    tree.setHeaderHidden(True)
    tree.setRootIsDecorated(False)
    tree.setUniformRowHeights(True)
    tree.setAlternatingRowColors(
        True
    )  # (zebra rows: a faint stripe tells one cue from the next)
    tree.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
    tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    tree.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
    return tree


class SidePanel(QWidget):
    """MEMORY | HOT CUE | INFO."""

    jump_requested = Signal(float)  # seconds
    loop_requested = Signal(
        float, float
    )  # a loop row: its start and end, seconds (go there with the loop on)
    memory_delete_requested = Signal(float)  # a memory cue's time: delete it
    memory_move_requested = Signal(
        float
    )  # a memory cue's time: move it to the playhead
    memory_reset_requested = Signal()  # go back to rekordbox's memory cues

    def __init__(self, parent=None):
        super().__init__(parent)
        self.memory = _list()
        self.hot = _list()
        self.hot.setColumnCount(2)  # (the letter, then its time)
        self.memory.setColumnCount(2)  # (the time, and a ✕ to delete it)
        self.memory.header().setStretchLastSection(False)
        self.memory.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.memory.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.memory.setColumnWidth(1, 26)
        self.memory.setItemDelegateForColumn(0, _LoopMarkDelegate(self.memory))
        self.memory.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.memory.customContextMenuRequested.connect(self._memory_context_menu)
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
        self.file.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.art = ArtworkView()
        # the cover at the top and big, the stars and file name under it, then the comment
        info = InfoFrame()
        box = QVBoxLayout(info)
        box.setContentsMargins(10, 10, 10, 10)
        box.setSpacing(8)
        box.addWidget(self.art, 4)  # (the cover gives way first when the page is short)
        facts = QHBoxLayout()  # (stars at the left, the file at the right: one line, so the cover can be big)
        facts.setSpacing(8)
        facts.addWidget(self.rating)
        facts.addWidget(self.file, 1)
        box.addLayout(facts)
        box.addWidget(_CommentArea(self.comment), 1)
        for label in (self.rating, self.comment, self.file):
            label.setMinimumWidth(0)
            label.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
            )
        self._edited = False
        memory_page = QWidget()
        memory_box = QVBoxLayout(memory_page)
        memory_box.setContentsMargins(0, 0, 0, 0)
        memory_box.setSpacing(4)
        memory_box.addWidget(self.memory, 1)
        self.pages = QStackedWidget()
        for page in (memory_page, self.hot, info):
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

    def sizeHint(self) -> QSize:
        """As tall as the least it needs, not as tall as the page showing wants: the player strip's height comes from
        its left and middle columns, so choosing a page (the Hot Cue list's and the Info page's own sizes are bigger
        than the Memory list's) never makes the strip taller or the window with it."""
        return QSize(super().sizeHint().width(), self.minimumSizeHint().height())

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

    def set_edited(self, edited: bool) -> None:
        """Say whether the track's memory cues are the ones edited here: the Memory list's right-click menu
        then offers Reset (nothing else on the page says so)."""
        self._edited = edited

    @property
    def edited(self) -> bool:
        return self._edited

    def set_cues(self, cues: tuple[Cue, ...]) -> None:
        """The loaded track's cues: memory cues and loops in time order, and the hot cues by letter."""
        self.memory.clear()
        for cue in sorted((c for c in cues if c.slot is None), key=lambda c: c.time):
            text = cue_clock(cue.time)
            plain = cue.kind == "memory"
            is_loop = cue.kind == "loop" and bool(cue.end)
            deletable = (
                plain or is_loop
            )  # (any memory loop: rekordbox's too, see PreviewController._delete_loop)
            item = QTreeWidgetItem([text, "✕" if deletable else ""])
            item.setData(0, TIME_ROLE, cue.time)
            item.setData(0, DELETABLE_ROLE, deletable)
            item.setData(0, MOVABLE_ROLE, plain)
            item.setData(
                0, LOOP_ROLE, is_loop
            )  # (a loop shows a loop mark after its time, as in rbxport)
            if is_loop:
                item.setData(0, END_ROLE, cue.end)
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignCenter)
            if is_loop:
                item.setToolTip(
                    0, f"Loop: {cue_clock(cue.time)} to {cue_clock(cue.end)}"
                )
            if deletable:
                what = "memory cue" if plain else "loop"
                item.setToolTip(1, f"Delete this {what}")
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

    def _clicked(self, item: QTreeWidgetItem, column: int) -> None:
        seconds = item.data(0, TIME_ROLE)
        if seconds is None:
            return
        if column == 1 and item.treeWidget() is self.memory:  # the ✕
            if item.data(0, DELETABLE_ROLE):
                self.memory_delete_requested.emit(float(seconds))
            return
        end = item.data(0, END_ROLE)
        if (
            end is not None and item.treeWidget() is self.memory
        ):  # a loop: go there and loop it
            self.loop_requested.emit(float(seconds), float(end))
            return
        self.jump_requested.emit(float(seconds))

    def memory_menu(self, item: QTreeWidgetItem | None) -> QMenu:
        """The right-click menu of the Memory list: move to the playhead (a plain memory cue only) and delete (a
        memory cue or a memory loop), and Reset when the track's memory cues or loops are edited."""
        menu = QMenu(self)
        seconds = item.data(0, TIME_ROLE) if item is not None else None
        if seconds is not None and item.data(0, DELETABLE_ROLE):
            if item.data(0, MOVABLE_ROLE):
                move = menu.addAction("Move to Playhead")
                move.triggered.connect(
                    lambda: self.memory_move_requested.emit(float(seconds))
                )
            delete = menu.addAction("Delete")
            delete.triggered.connect(
                lambda: self.memory_delete_requested.emit(float(seconds))
            )
        if self._edited:
            if not menu.isEmpty():
                menu.addSeparator()
            menu.addAction("Reset to rekordbox's").triggered.connect(
                self.memory_reset_requested
            )
        return menu

    def _memory_context_menu(self, where) -> None:
        menu = self.memory_menu(self.memory.itemAt(where))
        if not menu.isEmpty():
            menu.popup(self.memory.viewport().mapToGlobal(where))
