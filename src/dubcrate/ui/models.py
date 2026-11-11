"""Qt item models: the track table, the disc list, and the source tree."""

import json
from collections.abc import Sequence
from pathlib import PurePath
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
from dubcrate.library.models import DiscEntry, Track
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
    shown: bool = True  # on by default; the rest are there to switch on
    width: int = 90  # pixels, when first shown
    fixed: bool = False  # content of a known size: the width can't be dragged


# The marker, then what matters when picking tracks: these are the columns shown by default.
# The rest come from what rekordbox knows about a track; there are many, and few matter to
# most people, so they are all off until switched on (right-click a column heading). They
# come after the first ones, so a saved layout of the first ones stays valid.
TRACK_COLUMNS = (
    # the "already on the disc" marker
    TrackColumn("", None, width=24, fixed=True),
    TrackColumn("Title", "title", width=230),
    TrackColumn("Artist", "artist", width=130),
    TrackColumn("BPM", "bpm", right=True, width=50, fixed=True),
    TrackColumn("Key", "key", width=40, fixed=True),
    TrackColumn("Time", "duration", right=True, width=46, fixed=True),
    # the whole-track waveform, drawn by PreviewDelegate
    TrackColumn("Preview", None, width=120, fixed=True),
    TrackColumn("Genre", "genre"),
    TrackColumn("Album", "album", shown=False, width=160),
    TrackColumn("Year", "year", right=True, shown=False, width=50, fixed=True),
    TrackColumn("Rating", "rating", shown=False, width=70, fixed=True),
    TrackColumn("My Tags", "my_tags", shown=False, width=140),
    TrackColumn("Label", "label", shown=False, width=120),
    TrackColumn("Remixer", "remixer", shown=False, width=120),
    TrackColumn("Composer", "composer", shown=False, width=120),
    TrackColumn("Comment", "comment", shown=False, width=160),
    TrackColumn("Plays", "play_count", right=True, shown=False, width=46, fixed=True),
    TrackColumn("Date Added", "date_added", shown=False, width=90, fixed=True),
    TrackColumn("Bitrate", "bitrate", right=True, shown=False, width=56, fixed=True),
    TrackColumn(
        "Sample Rate", "sample_rate", right=True, shown=False, width=80, fixed=True
    ),
    TrackColumn("Format", None, shown=False, width=56, fixed=True),
    TrackColumn("Path", "path", shown=False, width=260),
)


PREVIEW_COLUMN = next(i for i, c in enumerate(TRACK_COLUMNS) if c.title == "Preview")
MAX_PREVIEWS = 4000


def _stars(rating: int | None) -> str:
    """Five stars, as many filled as the rating (none for unrated)."""
    if not rating:
        return ""
    filled = max(0, min(5, rating))
    return "★" * filled + "☆" * (5 - filled)


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
        case "Album":
            return track.album
        case "Year":
            return str(track.year) if track.year else ""
        case "Rating":
            return _stars(track.rating)
        case "My Tags":
            return track.my_tags
        case "Label":
            return track.label
        case "Remixer":
            return track.remixer
        case "Composer":
            return track.composer
        case "Comment":
            return track.comment
        case "Plays":
            return str(track.play_count) if track.play_count else ""
        case "Date Added":
            return track.date_added
        case "Bitrate":
            return str(track.bitrate) if track.bitrate else ""
        case "Sample Rate":
            return f"{track.sample_rate / 1000:g} kHz" if track.sample_rate else ""
        case "Format":
            return PurePath(track.path).suffix.lstrip(".").upper()
        case "Path":
            return track.path
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


# Only what a CD can hold: the track number, its length, and the CD-TEXT artist and
# track name (the disc's own name is the CD title). Artist and title can be edited here.
DISC_COLUMNS = ("#", "Time", "Artist", "Title")
_EDITABLE = {2: "artist", 3: "title"}  # column -> the CD text field it edits


class DiscModel(QAbstractTableModel):
    """The tracks on the current disc, in order.

    Dropping tracks from the track table emits `tracks_dropped(ids, row)`, dragging
    rows within the list emits `rows_moved(positions, row)`, and editing an artist or
    title emits `text_edited(position, field, text)`. The model never changes anything
    itself: the panel updates the library and reloads.
    """

    tracks_dropped = Signal(list, int)
    rows_moved = Signal(list, int)
    text_edited = Signal(int, str, str)  # (position, "artist" | "title", the new text)

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
                return _RIGHT if section == 1 else _LEFT
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
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            match column:
                case 0:
                    return str(entry.position + 1)
                case 1:
                    return ("" if track.length_exact else "~") + format_clock(
                        entry.sectors
                    )
                case 2:
                    return entry.artist  # what is written on the CD, not the library's
                case 3:
                    return entry.title
        elif role == Qt.ItemDataRole.DecorationRole and column == 0:
            return self._swatch(entry.position)
        elif role == Qt.ItemDataRole.TextAlignmentRole:
            return _RIGHT if column == 1 else _LEFT
        elif role == Qt.ItemDataRole.ForegroundRole and (track.missing or track.stale):
            return QColor(theme.current_palette()["critical"])
        elif role == Qt.ItemDataRole.FontRole and self._is_edited(entry, column):
            font = QGuiApplication.font()
            font.setItalic(True)  # edited for this disc: it differs from the library's
            return font
        elif role == Qt.ItemDataRole.ToolTipRole:
            lines = []
            if self._is_edited(entry, column):
                original = track.artist if column == 2 else track.title
                lines.append(
                    f"Edited for this disc only. The library has “{original}”."
                    " Clear the text to go back to it."
                )
            elif column in _EDITABLE:
                lines.append("Double-click to edit what is written on the CD")
            if track.stale:
                lines.append("No longer in its source. Remove it before burning.")
            elif track.missing:
                lines.append(f"File not found: {track.path}")
            else:
                lines.append(track.path)
                if not track.length_exact:
                    lines.append("Length estimated; measured when the disc is burned")
            return "\n".join(lines)
        return None

    @staticmethod
    def _is_edited(entry: DiscEntry, column: int) -> bool:
        return (column == 2 and entry.artist_override is not None) or (
            column == 3 and entry.title_override is not None
        )

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole) -> bool:
        """An edit of the artist or title: ask for it, but change nothing here."""
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        field = _EDITABLE.get(index.column())
        if field is None:
            return False
        entry = self._entries[index.row()]
        text = str(value).strip()
        if text != (entry.artist if field == "artist" else entry.title):
            self.text_edited.emit(entry.position, field, text)
        return False  # the panel updates the library and reloads; the model never edits

    # -- drag and drop ----------------------------------------------------

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = super().flags(index) | Qt.ItemFlag.ItemIsDropEnabled
        if not index.isValid():
            return base
        base |= Qt.ItemFlag.ItemIsDragEnabled
        if index.column() in _EDITABLE:
            base |= Qt.ItemFlag.ItemIsEditable
        return base

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


# -- the source tree ----------------------------------------------------------

# The library tree: ("all",) | ("source", id) | ("node", id, source_id).
# The disc tree (right pane): ("disc_folder", id) | ("disc", id).
SELECTION_ROLE = Qt.ItemDataRole.UserRole + 1
NAME_ROLE = Qt.ItemDataRole.UserRole + 2  # the name without the track count
Selection = tuple
DISC_ITEM_MIME = (
    "application/x-dubcrate-disc-items"  # discs and folders dragged in the tree
)


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


def _disc_icon() -> QIcon:
    """A disc's own icon: a playlist that is going on a CD."""
    return mono_icons.disc_list(
        _dpr(), muted=not theme.current_palette().get("mono_icons")
    )


def _count_item(count: int | None) -> QStandardItem:
    item = QStandardItem("" if not count else f"{count:,}")
    item.setEditable(False)
    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    item.setForeground(QColor(theme.current_palette()["text_disabled"]))
    return item


class DiscTreeModel(QStandardItemModel):
    """The tree of discs and the folders they are filed in (the right-hand pane).

    Tracks dragged from the track list onto a disc emit `tracks_dropped(disc_id, ids)`.
    Discs and folders can be dragged into a folder, or onto empty space for the top level:
    `items_moved(items, folder_id)`. Like the other models it never changes anything
    itself: the window updates the library and rebuilds the tree.
    """

    tracks_dropped = Signal(int, list)
    items_moved = Signal(
        list, object
    )  # ([("disc", id) | ("disc_folder", id)], folder id or None)

    @staticmethod
    def _kind(index: QModelIndex) -> str | None:
        data = (
            index.siblingAtColumn(0).data(SELECTION_ROLE) if index.isValid() else None
        )
        return data[0] if data else None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = super().flags(index) & ~Qt.ItemFlag.ItemIsEditable
        if self._kind(index) in ("disc", "disc_folder"):
            base |= Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled
        return base

    def supportedDropActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction | Qt.DropAction.CopyAction

    def mimeTypes(self) -> list[str]:
        return [TRACK_MIME, DISC_ITEM_MIME]

    def mimeData(self, indexes: Sequence[QModelIndex]) -> QMimeData:
        items = [
            list(i.data(SELECTION_ROLE))
            for i in indexes
            if i.column() == 0 and self._kind(i)
        ]
        mime = QMimeData()
        mime.setData(DISC_ITEM_MIME, json.dumps(items).encode())
        return mime

    def canDropMimeData(self, data, action, row, column, parent) -> bool:
        kind = self._kind(parent)
        if data.hasFormat(TRACK_MIME):
            return kind == "disc"  # tracks are added to a disc, nowhere else
        if data.hasFormat(DISC_ITEM_MIME):
            # into a folder, or (dropped on empty space) to the top level; never into a disc
            return kind == "disc_folder" or not parent.isValid()
        return False

    def dropMimeData(self, data, action, row, column, parent) -> bool:
        if action == Qt.DropAction.IgnoreAction:
            return True
        if not self.canDropMimeData(data, action, row, column, parent):
            return False
        if data.hasFormat(TRACK_MIME):
            ids = json.loads(bytes(data.data(TRACK_MIME)).decode())
            self.tracks_dropped.emit(
                parent.siblingAtColumn(0).data(SELECTION_ROLE)[1], ids
            )
            return True
        items = [
            tuple(i) for i in json.loads(bytes(data.data(DISC_ITEM_MIME)).decode())
        ]
        folder = (
            parent.siblingAtColumn(0).data(SELECTION_ROLE)[1]
            if parent.isValid()
            else None
        )
        self.items_moved.emit(items, folder)
        return True

    def removeRows(self, row, count, parent=QModelIndex()) -> bool:  # noqa: B008
        return False  # a Move drag asks for this afterwards; rows only change via the library


def _disc_tooltip(disc) -> str:
    n = disc.track_count
    fill = f"{format_clock(disc.used_sectors)} of {format_clock(disc.capacity_sectors)}"
    lines = [disc.name, f"{n} track{'s' if n != 1 else ''} · {fill}"]
    if disc.last_burned_at:
        lines.append(f"Last burned {disc.last_burned_at[:10]}")
    return "\n".join(lines)


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


def build_disc_model(lib: Library, target_disc_id: int | None = None) -> DiscTreeModel:
    """The tree of discs: folders (first) and discs, each A to Z, nested as filed.

    Discs are playlists that go on a CD. Two columns, like the library tree: the name,
    and the number of tracks. The disc that tracks are being added to (`target_disc_id`)
    is shown in bold.
    """
    model = DiscTreeModel()
    root = model.invisibleRootItem()
    folders = lib.disc_folders()
    folder_items: dict[int, QStandardItem] = {}
    for (
        folder
    ) in folders:  # create every folder first, then hang them where they belong
        item = QStandardItem(folder.name)
        item.setData(("disc_folder", folder.id), SELECTION_ROLE)
        item.setData(folder.name, NAME_ROLE)
        item.setIcon(_folder_icon())
        item.setEditable(False)
        folder_items[folder.id] = item

    def key(name: str) -> str:
        return name.casefold()

    for folder in sorted(folders, key=lambda f: key(f.name)):
        parent = folder_items.get(folder.parent_id, root)
        parent.appendRow([folder_items[folder.id], _count_item(None)])
    for disc in sorted(lib.discs(), key=lambda d: key(d.name)):
        item = QStandardItem(disc.name)
        item.setData(("disc", disc.id), SELECTION_ROLE)
        item.setData(disc.name, NAME_ROLE)
        item.setIcon(_disc_icon())
        item.setEditable(False)
        item.setToolTip(_disc_tooltip(disc))
        if disc.id == target_disc_id:
            font = item.font()
            font.setBold(True)  # tracks are being added to this one
            item.setFont(font)
        parent = folder_items.get(disc.folder_id, root)
        parent.appendRow([item, _count_item(disc.track_count)])
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
