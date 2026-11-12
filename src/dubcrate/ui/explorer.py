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

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QAbstractItemView, QTreeView

from . import theme
from .models import _folder_icon, _source_icon

log = logging.getLogger(__name__)

PATH_ROLE = Qt.ItemDataRole.UserRole + 20  # the folder's full path
LOADED_ROLE = Qt.ItemDataRole.UserRole + 21  # True once its subfolders have been listed
VOLUME_ROLE = Qt.ItemDataRole.UserRole + 22  # True on a drive (for its icon)


def mounted_volumes() -> list[Path]:
    """The drives that are plugged in (not the one the system is on), by name.

    macOS keeps them in /Volumes, where the boot disk is a link to "/". Linux (/media,
    /run/media, /mnt) and Windows (drive letters) are not handled: NOT tried on those."""
    if sys.platform != "darwin":
        return []
    base = Path("/Volumes")
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


def explorer_roots() -> list[tuple[str, Path, bool]]:
    """(name, folder, is a drive) for each top-level row: Music, home, then the drives."""
    home = Path.home()
    roots: list[tuple[str, Path, bool]] = []
    if (home / "Music").is_dir():
        roots.append(("Music", home / "Music", False))
    roots.append((home.name or "Home", home, False))
    roots += [(v.name, v, True) for v in mounted_volumes()]
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

    def __init__(
        self,
        parent=None,
        roots: Callable[[], list[tuple[str, Path, bool]]] | None = None,
    ):
        super().__init__(parent)
        self._roots = roots or explorer_roots
        self.setHeaderHidden(True)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setUniformRowHeights(True)
        self._model = QStandardItemModel(self)
        self.setModel(self._model)
        self.expanded.connect(self._on_expanded)
        self.selectionModel().currentChanged.connect(self._on_current_changed)
        theme.notifier.changed.connect(self._refresh_icons)
        self.reload_roots()

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

    def _load(self, item: QStandardItem) -> None:
        if item.data(LOADED_ROLE):
            return
        item.removeRows(0, item.rowCount())
        for sub in subfolders(Path(item.data(PATH_ROLE))):
            item.appendRow(self._item(sub.name, sub))
        item.setData(True, LOADED_ROLE)

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

    # -- the choice ------------------------------------------------------

    def _on_current_changed(self, current, _previous) -> None:
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
