"""Help > Keyboard Shortcuts: every shortcut in one searchable list.

The menu shortcuts are read from the window's own menus, so this can't drift from what the
keys really do: a shortcut added or changed in a menu shows up here by itself. Keys that are
not in a menu (Space, the arrows, Return) and the mouse actions are listed below them.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QMainWindow,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

# What is not in a menu: (group, what it does, the keys or the mouse action).
EXTRA_SHORTCUTS: tuple[tuple[str, str, str], ...] = (
    ("Playing", "Play or pause (anywhere except a text box)", "Space"),
    ("Playing", "Beat jump back", "←"),
    ("Playing", "Beat jump forward", "→"),
    ("Playing", "Preview a track", "Double-click it"),
    ("Playing", "Scratch", "Drag the zoomed waveform"),
    ("Track lists", "Add the selected tracks to the disc", "Return"),
    ("Track lists", "Take the selected tracks off the disc", "Delete"),
    ("Track lists", "Edit the artist or title written on the CD", "Double-click it"),
    ("Discs", "Rename a disc or folder in place", "Double-click its name"),
    ("Discs", "Add tracks to a disc or folder", "Drag them onto it"),
    ("Discs", "Move tracks to another folder", "Drag them onto the folder"),
)


def menu_shortcuts(window: QMainWindow) -> list[tuple[str, str, str]]:
    """(menu, action, keys) for every menu action that has a shortcut, in menu order."""
    rows: list[tuple[str, str, str]] = []

    def walk(menu: QMenu, group: str, prefix: str = "") -> None:
        for action in menu.actions():
            if action.menu() is not None:
                walk(action.menu(), group, f"{_clean(action.text())} > ")
            elif not action.isSeparator() and action.shortcuts():
                rows.append((group, prefix + _clean(action.text()), _keys(action)))

    for top in window.menuBar().actions():
        if top.menu() is not None:
            walk(top.menu(), _clean(top.text()))
    return rows


def _clean(text: str) -> str:
    """A menu's text as plain words: no & for the mnemonic, no trailing ellipsis."""
    return text.replace("&", "").removesuffix("…").strip()


def _keys(action: QAction) -> str:
    """The keys as the platform writes them (⌘N on a Mac), alternatives separated by "or"."""
    return "  or  ".join(
        seq.toString(QKeySequence.SequenceFormat.NativeText)
        for seq in action.shortcuts()
    )


class ShortcutsDialog(QDialog):
    def __init__(self, window: QMainWindow, parent: QWidget | None = None):
        super().__init__(parent or window)
        self.setWindowTitle("Keyboard Shortcuts")
        self.resize(540, 600)
        self.rows = [*menu_shortcuts(window), *EXTRA_SHORTCUTS]

        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a shortcut…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Action", "Keys"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
        self.tree.setAlternatingRowColors(False)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, header.ResizeMode.Stretch)
        header.setSectionResizeMode(1, header.ResizeMode.ResizeToContents)
        self._groups: list[QTreeWidgetItem] = []
        by_group: dict[str, QTreeWidgetItem] = {}
        for group, action, keys in self.rows:
            if group not in by_group:
                item = QTreeWidgetItem([group])
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.tree.addTopLevelItem(item)
                by_group[group] = item
                self._groups.append(item)
            row = QTreeWidgetItem([action, keys])
            row.setFlags(Qt.ItemFlag.ItemIsEnabled)
            by_group[group].addChild(row)
        self.tree.expandAll()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addWidget(self.tree, 1)
        layout.addWidget(buttons)
        self.search.setFocus()

    def _filter(self, text: str) -> None:
        """Show only the rows that mention `text` (in the action, the keys or the menu's name)."""
        needle = text.strip().casefold()
        for group in self._groups:
            shown = 0
            for i in range(group.childCount()):
                row = group.child(i)
                hit = (
                    not needle
                    or needle
                    in " ".join((group.text(0), row.text(0), row.text(1))).casefold()
                )
                row.setHidden(not hit)
                shown += hit
            group.setHidden(shown == 0)

    def visible_rows(self) -> list[tuple[str, str, str]]:
        """What is showing, as (group, action, keys): for the tests, and anyone curious."""
        out = []
        for group in self._groups:
            if group.isHidden():
                continue
            for i in range(group.childCount()):
                row = group.child(i)
                if not row.isHidden():
                    out.append((group.text(0), row.text(0), row.text(1)))
        return out
