"""Qt item models: the track table, the disc list, and the source tree."""

import json
from collections.abc import Sequence
from typing import NamedTuple

from PySide6.QtCore import (
    QAbstractTableModel,
    QMimeData,
    QModelIndex,
    QObject,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QIcon,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import QApplication, QStyle

from dubcrate.cd import format_clock
from dubcrate.library import Library
from dubcrate.library.models import BurnTrack, DiscEntry, Track
from dubcrate.waveform import Waveform

from . import mono_icons, theme
from .track_colors import swatch_icon

PREVIEW_ROLE = (
    Qt.ItemDataRole.UserRole + 10
)  # the track's whole-track Waveform, if known
TRACK_MIME = "application/x-dubcrate-tracks"  # track ids dragged from the track table
DISC_ROWS_MIME = (
    "application/x-dubcrate-disc-rows"  # positions dragged inside the disc list
)

_RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
_LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter


def _clock(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _dpr() -> float:
    screen = QGuiApplication.primaryScreen()
    return screen.devicePixelRatio() if screen else 1.0


class TrackColumn(NamedTuple):
    title: str
    sort_key: str | None  # a `Library.tracks(sort=...)` key; None can't be sorted
    right: bool = False


# The marker, then what matters when picking tracks. Album and year are left out (search
# still matches the album): they are rarely how a DJ picks, and they crowd the table.
TRACK_COLUMNS = (
    TrackColumn("", None),  # the "already on the disc" marker
    TrackColumn("Title", "title"),
    TrackColumn("Artist", "artist"),
    TrackColumn("BPM", "bpm", right=True),
    TrackColumn("Key", "key"),
    TrackColumn("Time", "duration", right=True),
    TrackColumn("Preview", None),  # the whole-track waveform, drawn by PreviewDelegate
    TrackColumn("Genre", "genre"),
)


PREVIEW_COLUMN = next(i for i, c in enumerate(TRACK_COLUMNS) if c.title == "Preview")
MAX_PREVIEWS = 4000


def _track_cell(track: Track, column: int) -> str:
    match TRACK_COLUMNS[column].title:
        case "Title":
            return track.title
        case "Artist":
            return track.artist
        case "BPM":
            return f"{track.bpm:.1f}" if track.bpm else ""
        case "Key":
            return track.key
        case "Time":
            return _clock(track.duration)
        case "Genre":
            return track.genre
    return ""  # (the marker and the preview are drawn, not written)


class TrackTableModel(QAbstractTableModel):
    """Tracks as a table. Rows can be dragged out, to add them to a disc."""

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._tracks: list[Track] = []
        self._on_disc: set[int] = set()
        self._previews: dict[int, Waveform] = {}
        self._rows: dict[int, int] = {}

    def set_tracks(self, tracks: list[Track]) -> None:
        self.beginResetModel()
        self._tracks = tracks
        self._rows = {t.id: row for row, t in enumerate(tracks)}
        self.endResetModel()

    def set_preview(self, track_id: int, waveform: Waveform) -> None:
        """Remember a track's whole-track waveform and redraw its row's preview."""
        self._previews[track_id] = waveform
        while (
            len(self._previews) > MAX_PREVIEWS
        ):  # forget the oldest: a big library shouldn't hold them all
            self._previews.pop(next(iter(self._previews)))
        row = self._rows.get(track_id)
        if row is not None:
            index = self.index(row, PREVIEW_COLUMN)
            self.dataChanged.emit(index, index)

    def has_preview(self, track_id: int) -> bool:
        return track_id in self._previews

    def set_on_disc(self, track_ids: set[int]) -> None:
        self._on_disc = track_ids
        if self._tracks:
            self.dataChanged.emit(
                self.index(0, 0), self.index(len(self._tracks) - 1, 0)
            )

    def track_at(self, row: int) -> Track:
        return self._tracks[row]

    def tracks(self) -> list[Track]:
        return list(self._tracks)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._tracks)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(TRACK_COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return TRACK_COLUMNS[section].title
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return _RIGHT if TRACK_COLUMNS[section].right else _LEFT
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        track = self._tracks[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if column == 0:
                return "●" if track.id in self._on_disc else ""
            return _track_cell(track, column)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _RIGHT if TRACK_COLUMNS[column].right else _LEFT
        if role == Qt.ItemDataRole.ForegroundRole and (track.missing or track.stale):
            return QColor(theme.current_palette()["text_disabled"])
        if role == Qt.ItemDataRole.ToolTipRole:
            note = "\nFile not found" if track.missing else ""
            return (
                track.path + note + ("\nNo longer in its source" if track.stale else "")
            )
        if role == Qt.ItemDataRole.UserRole:
            return track.id
        if role == PREVIEW_ROLE:
            return self._previews.get(track.id)
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = super().flags(index)
        return base | Qt.ItemFlag.ItemIsDragEnabled if index.isValid() else base

    def supportedDragActions(self) -> Qt.DropAction:
        return Qt.DropAction.CopyAction

    def mimeTypes(self) -> list[str]:
        return [TRACK_MIME]

    def mimeData(self, indexes: Sequence[QModelIndex]) -> QMimeData:
        rows = sorted({i.row() for i in indexes})
        mime = QMimeData()
        mime.setData(
            TRACK_MIME, json.dumps([self._tracks[r].id for r in rows]).encode()
        )
        return mime


DISC_COLUMNS = ("#", "Time", "Artist", "Title", "BPM", "Key")


class DiscModel(QAbstractTableModel):
    """The tracks on the current disc, in order.

    Dropping tracks from the track table emits `tracks_dropped(ids, row)`, and
    dragging rows within the list emits `rows_moved(positions, row)`. The model
    never changes anything itself: the panel updates the library and reloads.
    """

    tracks_dropped = Signal(list, int)
    rows_moved = Signal(list, int)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._entries: list[DiscEntry] = []
        self._icons: dict[int, QIcon] = {}
        theme.notifier.changed.connect(self._theme_changed)

    def _theme_changed(self) -> None:
        self._icons.clear()
        if self._entries:
            self.dataChanged.emit(
                self.index(0, 0), self.index(len(self._entries) - 1, 0)
            )

    def set_entries(self, entries: list[DiscEntry]) -> None:
        self.beginResetModel()
        self._entries = entries
        self.endResetModel()

    def entry_at(self, row: int) -> DiscEntry:
        return self._entries[row]

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._entries)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(DISC_COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return DISC_COLUMNS[section]
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return _RIGHT if section in (1, 4) else _LEFT
        return None

    def _swatch(self, position: int) -> QIcon:
        slot = position % 8
        if slot not in self._icons:
            self._icons[slot] = swatch_icon(slot, _dpr())
        return self._icons[slot]

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        entry = self._entries[index.row()]
        track = entry.track
        column = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            match column:
                case 0:
                    return str(entry.position + 1)
                case 1:
                    return ("" if track.length_exact else "~") + format_clock(
                        entry.sectors
                    )
                case 2:
                    return track.artist
                case 3:
                    return track.title
                case 4:
                    return f"{track.bpm:.1f}" if track.bpm else ""
                case 5:
                    return track.key
        elif role == Qt.ItemDataRole.DecorationRole and column == 0:
            return self._swatch(entry.position)
        elif role == Qt.ItemDataRole.TextAlignmentRole:
            return _RIGHT if column in (1, 4) else _LEFT
        elif role == Qt.ItemDataRole.ForegroundRole and (track.missing or track.stale):
            return QColor(theme.current_palette()["critical"])
        elif role == Qt.ItemDataRole.ToolTipRole:
            if track.stale:
                return "No longer in its source. Remove it before burning."
            if track.missing:
                return f"File not found: {track.path}"
            return track.path + (
                ""
                if track.length_exact
                else "\nLength estimated; measured when the disc is burned"
            )
        return None

    # -- drag and drop ----------------------------------------------------

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = super().flags(index) | Qt.ItemFlag.ItemIsDropEnabled
        return base | Qt.ItemFlag.ItemIsDragEnabled if index.isValid() else base

    def supportedDragActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction

    def supportedDropActions(self) -> Qt.DropAction:
        return Qt.DropAction.CopyAction | Qt.DropAction.MoveAction

    def mimeTypes(self) -> list[str]:
        return [TRACK_MIME, DISC_ROWS_MIME]

    def mimeData(self, indexes: Sequence[QModelIndex]) -> QMimeData:
        rows = sorted({i.row() for i in indexes})
        mime = QMimeData()
        mime.setData(DISC_ROWS_MIME, json.dumps(rows).encode())
        return mime

    def canDropMimeData(self, data, action, row, column, parent) -> bool:
        return data.hasFormat(TRACK_MIME) or data.hasFormat(DISC_ROWS_MIME)

    def dropMimeData(self, data, action, row, column, parent) -> bool:
        if action == Qt.DropAction.IgnoreAction:
            return True
        if row >= 0:
            target = row
        elif parent.isValid():
            target = parent.row()
        else:
            target = len(self._entries)
        if data.hasFormat(DISC_ROWS_MIME):
            self.rows_moved.emit(
                json.loads(bytes(data.data(DISC_ROWS_MIME)).decode()), target
            )
            return True
        if data.hasFormat(TRACK_MIME):
            self.tracks_dropped.emit(
                json.loads(bytes(data.data(TRACK_MIME)).decode()), target
            )
            return True
        return False

    def removeRows(self, row, count, parent=QModelIndex()) -> bool:  # noqa: B008
        # A Move drag asks the source model to delete the dragged rows afterwards.
        # Rows only ever change through the library, so refuse.
        return False


BURN_COLUMNS = ("#", "Time", "Artist", "Title")


class BurnModel(QAbstractTableModel):
    """The tracks of one burn record, read-only.

    A track whose library entry has since gone is shown dimmed: it was burned,
    but it can't be put on another disc.
    """

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._tracks: list[BurnTrack] = []

    def set_tracks(self, tracks: list[BurnTrack]) -> None:
        self.beginResetModel()
        self._tracks = tracks
        self.endResetModel()

    def track_at(self, row: int) -> BurnTrack:
        return self._tracks[row]

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._tracks)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(BURN_COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return BURN_COLUMNS[section]
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return _RIGHT if section == 1 else _LEFT
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        track = self._tracks[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            match column:
                case 0:
                    return str(track.position + 1)
                case 1:
                    return format_clock(track.sectors)
                case 2:
                    return track.artist
                case 3:
                    return track.title
        elif role == Qt.ItemDataRole.TextAlignmentRole:
            return _RIGHT if column == 1 else _LEFT
        elif role == Qt.ItemDataRole.ForegroundRole and track.track_id is None:
            return QColor(theme.current_palette()["text_disabled"])
        elif role == Qt.ItemDataRole.ToolTipRole:
            gone = "" if track.track_id else "\nNo longer in your library"
            return track.path + gone
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


# -- the source tree ----------------------------------------------------------

SELECTION_ROLE = (
    Qt.ItemDataRole.UserRole + 1
)  # ("all",) | ("source", id) | ("node", id, source_id)
NAME_ROLE = Qt.ItemDataRole.UserRole + 2  # the name without the track count
Selection = tuple


def _folder_icon() -> QIcon:
    if theme.current_palette().get("mono_icons"):
        return mono_icons.folder(_dpr())
    return QApplication.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)


def _source_icon() -> QIcon:
    if theme.current_palette().get("mono_icons"):
        return mono_icons.drive(_dpr())
    return QApplication.style().standardIcon(QStyle.StandardPixmap.SP_DriveHDIcon)


def _playlist_icon() -> QIcon:
    return mono_icons.playlist(
        _dpr(), muted=not theme.current_palette().get("mono_icons")
    )


def _count_item(count: int | None) -> QStandardItem:
    item = QStandardItem("" if not count else f"{count:,}")
    item.setEditable(False)
    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    item.setForeground(QColor(theme.current_palette()["text_disabled"]))
    return item


def build_source_model(lib: Library) -> QStandardItemModel:
    """The tree of sources with their folders and playlists beneath them.

    Two columns: the name, and the number of tracks in it, right-aligned.
    """
    model = QStandardItemModel()
    root = model.invisibleRootItem()

    everything = QStandardItem("All Tracks")
    everything.setData(("all",), SELECTION_ROLE)
    everything.setData("All Tracks", NAME_ROLE)
    everything.setEditable(False)
    root.appendRow([everything, _count_item(lib.count_tracks())])

    for source in lib.sources():
        item = QStandardItem(source.name)
        item.setData(("source", source.id), SELECTION_ROLE)
        item.setData(source.name, NAME_ROLE)
        item.setIcon(_source_icon())
        item.setEditable(False)
        item.setToolTip(source.path)
        root.appendRow([item, _count_item(source.track_count)])
        parents: dict[int | None, QStandardItem] = {None: item}
        for node in lib.nodes(source.id):
            child = QStandardItem(node.name)
            child.setData(("node", node.id, source.id), SELECTION_ROLE)
            child.setData(node.name, NAME_ROLE)
            child.setEditable(False)
            # A folder source's directories are always folders (even a leaf one, which
            # has no sub-directories); only a rekordbox playlist is a playlist.
            if node.is_folder or source.kind == "folder":
                child.setIcon(_folder_icon())
            else:
                child.setIcon(_playlist_icon())
            parents.get(node.parent_id, item).appendRow(
                [child, _count_item(node.track_count)]
            )
            parents[node.id] = child
    return model


def find_selection(model: QStandardItemModel, selection: Selection) -> QModelIndex:
    """The index whose SELECTION_ROLE equals `selection`, or an invalid one."""
    stack = [model.invisibleRootItem()]
    while stack:
        parent = stack.pop()
        for row in range(parent.rowCount()):
            child = parent.child(row)
            if child.data(SELECTION_ROLE) == selection:
                return child.index()
            stack.append(child)
    return QModelIndex()
