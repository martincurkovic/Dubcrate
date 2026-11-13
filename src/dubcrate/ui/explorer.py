"""The Explorer tab's folder tree: Music, your home folder and any drives plugged in.

It lists folders only, and only when you open them (a folder shows an arrow until it has been
read), so nothing is scanned that you do not look at. The system disk as a whole is not offered:
`Macintosh HD` and its `System`, `Library` and `Applications` are not where a DJ keeps music.
Picking a folder emits `folder_selected(path)`; the window reads its audio files.
"""

import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QModelIndex, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QAbstractItemView, QMenu, QTreeView

from . import theme
from .models import _folder_icon, _source_icon

log = logging.getLogger(__name__)

PATH_ROLE = Qt.ItemDataRole.UserRole + 20  # the folder's full path
LOADED_ROLE = Qt.ItemDataRole.UserRole + 21  # True once its subfolders have been listed
VOLUME_ROLE = Qt.ItemDataRole.UserRole + 22  # True on a drive (for its icon)
DRIVE_SETTLE_MS = (
    600  # after a change in /Volumes and the like, before the drives are listed again
)


def mounted_volumes() -> list[Path]:
    """The drives that are plugged in (not the one the system is on), by name.

    One helper per system, so the branches stay in one place. macOS and Linux are folders
    where drives are mounted; Windows has drive letters. Linux and Windows: NOT tried."""
    if sys.platform == "darwin":
        return _mac_volumes()
    if sys.platform == "win32":
        return _windows_volumes(_windows_drives(), os.environ.get("SystemDrive", "C:"))
    return _linux_volumes()


def _mac_volumes(base: Path = Path("/Volumes")) -> list[Path]:
    """macOS keeps drives in /Volumes, where the boot disk is a link to "/"."""
    volumes = []
    try:
        entries = sorted(base.iterdir(), key=lambda p: p.name.casefold())
    except OSError:
        return []
    for entry in entries:
        try:
            if entry.name.startswith(".") or not entry.is_dir():
                continue
            if entry.resolve() == Path("/"):
                continue
        except OSError:
            continue
        volumes.append(entry)
    return volumes


def _linux_bases() -> list[Path]:
    user = os.environ.get("USER") or Path.home().name
    return [
        Path("/media") / user,
        Path("/run/media") / user,
        Path("/media"),
        Path("/mnt"),
    ]


def _linux_volumes(
    bases: list[Path] | None = None, is_mount: Callable[[Path], bool] = os.path.ismount
) -> list[Path]:
    """Linux desktops mount a plugged-in drive under /media/<user> or /run/media/<user> (udisks);
    a drive someone mounted by hand is usually under /mnt. Only real mount points count, since
    /mnt and /media hold empty folders too. The system disk is "/", which is never offered."""
    if bases is None:
        bases = _linux_bases()
    volumes: list[Path] = []
    seen: set[Path] = set()
    for base in bases:
        try:
            entries = sorted(base.iterdir(), key=lambda p: p.name.casefold())
        except OSError:
            continue
        for entry in entries:
            try:
                if (
                    entry.name.startswith(".")
                    or not entry.is_dir()
                    or not is_mount(entry)
                ):
                    continue
                real = entry.resolve()
            except OSError:
                continue
            if real not in seen:
                seen.add(real)
                volumes.append(entry)
    return volumes


# What GetDriveType says (winbase.h): 2 removable, 3 fixed, 4 network. A CD drive (5) holds no folders.
_WINDOWS_USABLE_DRIVE_TYPES = (2, 3, 4)


def _windows_drives() -> list[tuple[str, int]]:
    """(drive letter, GetDriveType) for each letter in use. Windows only: NOT tried."""
    import ctypes

    kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
    mask = kernel.GetLogicalDrives()
    letters = [chr(ord("A") + i) for i in range(26) if mask >> i & 1]
    return [(letter, kernel.GetDriveTypeW(f"{letter}:\\")) for letter in letters]


def _windows_volumes(drives: list[tuple[str, int]], system_drive: str) -> list[Path]:
    """The drives worth showing: removable, fixed and network ones, without the system drive
    (your home folder, which is on it, is already a root)."""
    system = system_drive.rstrip(":\\/").upper()
    return [
        Path(f"{letter}:/")
        for letter, kind in drives
        if kind in _WINDOWS_USABLE_DRIVE_TYPES and letter.upper() != system
    ]


def volume_parents() -> list[Path]:
    """The folders whose contents change when a drive is plugged in or taken out, for the tree to
    watch. One helper per system. Windows has none (a drive letter is not a folder): its list is
    read again whenever the Explorer tab is opened. Linux: NOT tried; a drive mounted by hand
    onto an existing folder under /mnt changes nothing there, so it waits for the tab."""
    if sys.platform == "darwin":
        return [Path("/Volumes")]
    if sys.platform == "win32":
        return []
    return [*_linux_bases(), Path("/run/media")]


def volume_label(volume: Path) -> str:
    """A drive's name for the tree: its folder name, or for a Windows drive "D:"."""
    return volume.name or str(volume).rstrip("\\/")


def explorer_roots() -> list[tuple[str, Path, bool]]:
    """(name, folder, is a drive) for each top-level row: Music, home, then the drives."""
    home = Path.home()
    roots: list[tuple[str, Path, bool]] = []
    if (home / "Music").is_dir():
        roots.append(("Music", home / "Music", False))
    roots.append((home.name or "Home", home, False))
    roots += [(volume_label(v), v, True) for v in mounted_volumes()]
    return roots


def subfolders(folder: Path) -> list[Path]:
    """The folders directly inside `folder` (hidden ones left out), by name. A folder that cannot
    be read has none: it is shown, and picking it says why."""
    try:
        with os.scandir(folder) as entries:
            found = [
                Path(e.path)
                for e in entries
                if not e.name.startswith(".") and e.is_dir()
            ]
    except OSError as exc:
        log.debug("cannot list %s: %s", folder, exc)
        return []
    return sorted(found, key=lambda p: p.name.casefold())


class ExplorerTree(QTreeView):
    folder_selected = Signal(str)
    pin_requested = Signal(str)  # right-click > Pin Folder
    unpin_requested = Signal(str)  # right-click > Unpin (a folder that is pinned)
    refresh_requested = Signal(
        str
    )  # right-click > Refresh: the folder was listed again

    def __init__(
        self,
        parent=None,
        roots: Callable[[], list[tuple[str, Path, bool]]] | None = None,
        watch: list[Path] | None = None,
    ):
        """`watch`: folders where a drive appears; the rows are listed again when one changes."""
        super().__init__(parent)
        self._roots = roots or explorer_roots
        self._pinned: set[str] = set()
        self._syncing = False  # rows are being brought up to date, not picked
        self.setHeaderHidden(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setUniformRowHeights(True)
        self._model = QStandardItemModel(self)
        self.setModel(self._model)
        self.expanded.connect(self._on_expanded)
        self.selectionModel().currentChanged.connect(self._on_current_changed)
        theme.notifier.changed.connect(self._refresh_icons)
        self.reload_roots()
        self._watch = list(watch or [])
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(lambda _path: self._drives_timer.start())
        self._drives_timer = QTimer(self)  # one refresh for a burst of changes, and a
        self._drives_timer.setSingleShot(
            True
        )  # moment for the drive to finish mounting
        self._drives_timer.setInterval(DRIVE_SETTLE_MS)
        self._drives_timer.timeout.connect(self._drives_changed)
        self._watch_folders()

    # -- the rows --------------------------------------------------------

    def _item(self, name: str, path: Path, volume: bool = False) -> QStandardItem:
        item = QStandardItem(name)
        item.setEditable(False)
        item.setData(str(path), PATH_ROLE)
        item.setData(False, LOADED_ROLE)
        item.setData(volume, VOLUME_ROLE)
        item.setIcon(_source_icon() if volume else _folder_icon())
        item.appendRow(QStandardItem())  # (so it has an arrow until it is read)
        return item

    def reload_roots(self) -> None:
        """Start again from the top: the drives that are plugged in now."""
        self._model.clear()
        for name, path, volume in self._roots():
            self._model.appendRow(self._item(name, path, volume))

    def _watch_folders(self) -> None:
        """Watch the folders that exist (one that appears later is picked up on the next change)."""
        have = set(self._watcher.directories())
        new = [str(p) for p in self._watch if str(p) not in have and p.is_dir()]
        if new:
            self._watcher.addPaths(new)

    def _drives_changed(self) -> None:
        self._watch_folders()
        self.refresh_roots()

    def _load(self, item: QStandardItem) -> None:
        if item.data(LOADED_ROLE):
            return
        item.removeRows(0, item.rowCount())
        for sub in subfolders(Path(item.data(PATH_ROLE))):
            item.appendRow(self._item(sub.name, sub))
        item.setData(True, LOADED_ROLE)

    def _sync_children(
        self,
        parent: QStandardItem | None,
        entries: list[tuple[str, Path, bool]],
    ) -> None:
        """Make the rows under `parent` (None: the top level) match `entries`, in order, taking
        away the ones that are gone and adding the new ones. A row that stays is not touched, so
        it keeps its place, what is open under it and whether it is picked."""
        container = parent if parent is not None else self._model.invisibleRootItem()
        wanted = {str(path) for _name, path, _volume in entries}
        for row in reversed(range(container.rowCount())):
            if container.child(row).data(PATH_ROLE) not in wanted:
                container.removeRow(row)
        for position, (name, path, volume) in enumerate(entries):
            have = (
                container.child(position) if position < container.rowCount() else None
            )
            if have is None or have.data(PATH_ROLE) != str(path):
                container.insertRow(position, [self._item(name, path, volume)])

    def refresh_roots(self) -> None:
        """List the drives again (one plugged in since, or taken out) without losing what is
        open or picked. If the picked folder was on a drive that is gone, nothing is picked
        afterwards and `folder_selected` is not emitted (Qt would move the pick to the next row,
        and the window would jump to a folder nobody chose)."""
        current = self.current_path()
        self._syncing = True
        try:
            self._sync_children(None, self._roots())
            if current is not None and self._item_for(current) is None:
                self.setCurrentIndex(QModelIndex())
        finally:
            self._syncing = False

    def refresh_folder(self, path: str) -> None:
        """List a folder's subfolders again (a loaded one: an unopened one reads when opened)."""
        item = self._item_for(path)
        if item is not None and item.data(LOADED_ROLE):
            self._sync_children(
                item, [(p.name, p, False) for p in subfolders(Path(path))]
            )

    def _item_for(self, path: str) -> QStandardItem | None:
        stack = [self._model.item(r) for r in range(self._model.rowCount())]
        while stack:
            item = stack.pop()
            if item.data(PATH_ROLE) == path:
                return item
            if item.data(LOADED_ROLE):
                stack += [item.child(r) for r in range(item.rowCount())]
        return None

    def _on_expanded(self, index) -> None:
        item = self._model.itemFromIndex(index)
        if item is not None:
            self._load(item)

    def _refresh_icons(self) -> None:
        stack = [self._model.item(r) for r in range(self._model.rowCount())]
        while stack:
            item = stack.pop()
            item.setIcon(_source_icon() if item.data(VOLUME_ROLE) else _folder_icon())
            if item.data(LOADED_ROLE):
                stack += [item.child(r) for r in range(item.rowCount())]

    # -- pinning ---------------------------------------------------------

    def set_pinned(self, paths: set[str]) -> None:
        """The folders that are pinned now, so the menu offers Unpin for them."""
        self._pinned = set(paths)

    def _pin_menu(self, path: str) -> QMenu:
        menu = QMenu(self)
        if path in self._pinned:
            menu.addAction("Unpin Folder", lambda: self.unpin_requested.emit(path))
        else:
            menu.addAction("Pin Folder", lambda: self.pin_requested.emit(path))
        menu.addAction("Refresh", lambda: self._refresh(path))
        return menu

    def _refresh(self, path: str) -> None:
        self.refresh_folder(path)
        self.refresh_requested.emit(path)

    def _on_context_menu(self, pos: QPoint) -> None:
        index = self.indexAt(pos)
        path = index.data(PATH_ROLE) if index.isValid() else None
        if path:
            self._pin_menu(path).exec(self.viewport().mapToGlobal(pos))

    # -- the choice ------------------------------------------------------

    def _on_current_changed(self, current, _previous) -> None:
        if self._syncing:
            return
        path = current.data(PATH_ROLE) if current.isValid() else None
        if path:
            self.folder_selected.emit(path)

    def current_path(self) -> str | None:
        index = self.currentIndex()
        return index.data(PATH_ROLE) if index.isValid() else None

    def select_path(self, path: str) -> bool:
        """Open the tree down to `path` and pick it (which emits `folder_selected`). False if it
        is not under any of the rows."""
        target = Path(path)
        best: QStandardItem | None = None
        for row in range(self._model.rowCount()):
            item = self._model.item(row)
            root = Path(item.data(PATH_ROLE))
            if (target == root or root in target.parents) and (
                best is None or len(root.parts) > len(Path(best.data(PATH_ROLE)).parts)
            ):
                best = item
        if best is None:
            return False
        item = best
        for part in target.relative_to(Path(best.data(PATH_ROLE))).parts:
            self._load(item)
            self.setExpanded(item.index(), True)
            child = next(
                (
                    item.child(r)
                    for r in range(item.rowCount())
                    if item.child(r).text() == part
                ),
                None,
            )
            if child is None:
                return False
            item = child
        self.setCurrentIndex(item.index())
        self.scrollTo(item.index())
        return True
