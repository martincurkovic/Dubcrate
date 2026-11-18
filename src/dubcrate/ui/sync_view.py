"""The Sync tab: the CDJ-1000MK3's SD card on the left, your audio discs in the middle.

It finds the card, lists Dubcrate's audio discs with what a sync would do for
each (`card_discs.survey_discs`: not on the card, cues to add, in sync, or why it can't be synced),
and shows that dry run in words for the disc you pick. Ticking discs sums up what Sync would do.
**The Sync button is off** (`WRITING_ENABLED`): no card file written by Dubcrate has been loaded by
a player yet (`dev_docs/card-sync.md`, Phase 0). With it on, Sync asks first (what will be added,
where the card's files are copied), then `card_apply.apply_sync` plans the card fresh, copies its
files aside, writes and reads back, on a worker.

The library belongs to the GUI thread, so reading rekordbox's database and probing track lengths
(the only slow parts) run on workers and the discs are looked at on the GUI thread afterwards.
"""

import logging
import shutil
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dubcrate import card_format, settings
from dubcrate import cdj_card as cc
from dubcrate.analysis import Cue
from dubcrate.cancel import CancelToken
from dubcrate.card_apply import apply_sync, default_backup_dir
from dubcrate.card_discs import (
    DiscSurvey,
    RekordboxCues,
    survey_discs,
    wave_rows,
)
from dubcrate.card_import import survey_new_cues
from dubcrate.eject import EjectError, eject_card
from dubcrate.library import Library
from dubcrate.library.lengths import needs_measuring, probe_lengths
from dubcrate.library.models import Track
from dubcrate.library.rekordbox import RekordboxError, read_cues
from dubcrate.library.waves import measure_waveforms, store_waveforms

from . import theme
from .card_import_dialog import ask_import
from .explorer import DrivePoller, mounted_volumes
from .format_card_dialog import FormatCardDialog
from .glyph_widgets import GlyphButton, GlyphIcon, close_glyph, eject_glyph, ink
from .label_dialog import disc_paths
from .models import _disc_icon, _folder_icon
from .source_tabs import explorer_glyph, sd_card_glyph
from .tasks import TaskError, run_async
from .tick_header import TickHeader
from .toc_manager_dialog import TocManagerDialog
from .track_browser import TrackBrowser, unsized

log = logging.getLogger(__name__)

# Off until the player had loaded a card file we wrote; on since 2026-11-14 (dev_docs/card-sync.md,
# Phase 0: a CDJ-1000MK3, firmware 2.20, showed the cues of files written by `dubcrate card sync`).
WRITING_ENABLED = True
WAITING_NOTE = (
    "Waiting on the player test: no card file written by Dubcrate has been loaded by a "
    "CDJ yet. `dubcrate card sync` is the way to try it, on an imaged card."
)

COL_NAME, COL_TRACKS = range(2)  # (the tick box is in the name column, on the left)


def ask_card_folder(parent: QWidget | None) -> str:
    """Ask for a card's folder (a copy of it, or a mounted disk image); "" if cancelled.
    (Tests patch this: a real chooser blocks.)"""
    return QFileDialog.getExistingDirectory(
        parent, "Choose the Card or a Copy of Its Folder", str(Path.home())
    )


def ask_sync_confirmation(parent: QWidget | None, text: str) -> bool:
    """Ask whether to write; False if declined (the default button). (Tests patch this.)"""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("Sync to the Card")
    box.setText(text)
    write = box.addButton("Write to Card", QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(cancel)
    box.exec()
    return box.clickedButton() is write


def show_problem(parent: QWidget | None, title: str, text: str) -> None:
    """Tell the user a sync failed. (Tests patch this.)"""
    QMessageBox.warning(parent, title, text)


def confirmation_text(
    surveys: Sequence[DiscSurvey],
    card_name: str,
    backup_dir: Path,
    eject: bool = False,
) -> str:
    """What the question before a write says: just the question, and a line if cues won't fit (a disc's own warnings are in the list). The card's files are always copied aside first, so that is
    not said every time."""
    lines = [f"Write rekordbox cues to “{card_name}”?"]
    if left_out := sum(len(s.plan.overflow) for s in surveys if s.plan):
        lines.append(
            f"{left_out} cue{'s' if left_out != 1 else ''} won't fit (the player holds "
            f"{cc.MAX_CUES} per disc) and will be left out."
        )
    if others := sum(len(s.extra_plans) for s in surveys if s.writable):
        lines.append(
            f"The same cues are written for {others} other TOC{'s' if others != 1 else ''} "
            "recorded for these discs (other copies of them)."
        )
    return lines[0] + ("\n\n" + "\n".join(lines[1:]) if lines[1:] else "")


CARD_SETTLE_MS = (
    800  # after a change in /Volumes, a moment for the card to finish mounting
)
ROW_HEIGHT = 62
# A card not set up: its note wraps (two lines for "Has other files…"), and the Format… button has a line.
UNSET_ROW_HEIGHT = 104
UNSET_FILES_ROW_HEIGHT = 136


def card_capacity(path: Path) -> int | None:
    """The size of the volume `path` is on, in bytes (None if it can't be asked). Tests stand it in."""
    try:
        return shutil.disk_usage(path).total
    except OSError:
        return None


def _size_text(size: int) -> str:
    return f"{size / 1e9:.1f} GB" if size >= 1e9 else f"{size / 1e6:.0f} MB"


def _real_unset() -> list[card_format.UnsetCard]:
    """The plugged-in cards with no PIONEER folder (on a worker). Tests stand it in: a real one asks the system."""
    return card_format.unset_cards()


def unset_detail(card: card_format.UnsetCard) -> str:
    """A card that is not set up for the CDJ, in two lines: its size, and what formatting would cost."""
    note = (
        "Has other files: formatting erases them"
        if card.has_files
        else "Not set up for the CDJ"
    )
    return f"{card_format.size_text(card.disk.size)}\n{note}"


class _ElidedLabel(QLabel):
    """A label that shortens its text with “…” when it is narrower than the text, rather than
    being cut off (a long name, or the wide System 6 font, beside the eject button)."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(10)
        self.setText(text)

    def setText(self, text: str) -> None:
        self._full = text
        self._fit()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        shown = self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideRight, max(self.width(), 10)
        )
        super().setText(shown)
        self.setToolTip(self._full if shown != self._full else "")


class CardRow(QWidget):
    """One card in the list: an icon, its name, its size and how many discs it holds, and a button
    to eject it (for a folder chosen by hand, one that takes it off the list instead)."""

    eject_requested = Signal(object)  # the card's path
    remove_requested = Signal(object)
    format_requested = Signal(str)  # a card not set up yet: its disk ("disk4")

    def __init__(
        self,
        path: Path,
        by_hand: bool,
        detail: str,
        parent: QWidget | None = None,
        unset: card_format.UnsetCard | None = None,
    ):
        super().__init__(parent)
        self.path, self.by_hand, self.unset = path, by_hand, unset
        self._selected = False
        name = unset.disk.name or unset.disk.device if unset else path.name or str(path)
        self.icon = GlyphIcon(explorer_glyph if by_hand else sd_card_glyph, 36)
        self.title = _ElidedLabel(name)
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        self.detail = _ElidedLabel(detail)
        if unset is not None:  # (a sentence, not a size: it is shown whole)
            self.detail = QLabel(detail)
            self.detail.setWordWrap(True)
        words = QVBoxLayout()
        words.setSpacing(1)
        words.addStretch(1)
        words.addWidget(self.title)
        words.addWidget(self.detail)
        words.addStretch(1)
        if unset is not None:
            self.button = QPushButton("Format…")
            self.button.setToolTip(
                f"Set up “{name}” for the CDJ-1000MK3: it asks before erasing anything"
            )
            self.button.clicked.connect(
                lambda: self.format_requested.emit(unset.disk.device)
            )
            layout = QVBoxLayout(self)
            layout.setContentsMargins(10, 6, 8, 6)
            layout.setSpacing(4)
            top = QHBoxLayout()
            top.setSpacing(10)
            top.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
            top.addLayout(words, 1)
            layout.addLayout(top, 1)
            bottom = QHBoxLayout()
            bottom.addStretch(1)
            bottom.addWidget(self.button)
            layout.addLayout(bottom)
        else:
            self.button = GlyphButton(
                close_glyph if by_hand else eject_glyph,
                "Take this folder off the list" if by_hand else f"Eject “{name}”",
            )
            self.button.clicked.connect(
                lambda: (
                    self.remove_requested if by_hand else self.eject_requested
                ).emit(path)
            )
            layout = QHBoxLayout(self)
            layout.setContentsMargins(10, 6, 8, 6)
            layout.setSpacing(10)
            layout.addWidget(self.icon)
            layout.addLayout(words, 1)
            layout.addWidget(self.button)
        self.setToolTip(
            f"{unset.disk.node}: not set up for the CDJ" if unset else str(path)
        )
        theme.notifier.changed.connect(self._restyle)
        self._restyle()

    def set_selected(self, selected: bool) -> None:
        """A picked row has the list's selection colour behind it: text and glyphs follow."""
        self._selected = selected
        self.icon.set_selected(selected)
        if isinstance(self.button, GlyphButton):
            self.button.set_selected(selected)
        self._restyle()

    def _restyle(self) -> None:
        self.title.setStyleSheet(f"color: {ink(self._selected).name()};")
        muted = ink(self._selected) if self._selected else ink(enabled=False)
        self.detail.setStyleSheet(f"color: {muted.name()};")


class CardPanel(QWidget):
    """The left side: the cards that are plugged in (and folders chosen by hand), one picked.

    A card plugged in or taken out shows up (or goes) by itself a moment later: the folders where
    drives appear are watched, as the Explorer does. **Refresh** is for when that misses one."""

    card_changed = Signal(object)  # the card's folder (a Path), or None
    sync_requested = Signal(
        object
    )  # right-click > Sync to this card: the card's folder (a Path)
    import_requested = Signal(
        object
    )  # right-click > Import Cues: the card's folder (a Path)
    message = Signal(str)  # for the status bar

    def __init__(
        self,
        volumes: Callable[[], list[Path]] | None = None,
        parent: QWidget | None = None,
        watch: Sequence[Path] = (),
        poll: bool = False,
        unset: Callable[[], list[card_format.UnsetCard]] | None = None,
    ):
        super().__init__(parent)
        self._volumes = volumes  # None: the drives that are plugged in
        self._chosen: list[Path] = []  # folders picked by hand, kept for the session
        self._found: list[Path] = []  # the cards plugged in, as last looked for
        self._find_unset = unset or _real_unset
        self._unset: list[card_format.UnsetCard] = []  # plugged in, not set up yet
        self._unset_serial = 0  # a newer look makes an older one's answer stale
        self._busy = False  # a sync is writing: nothing is ejected under it
        self._ejecting = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        title = QLabel("Card")
        title.setObjectName("panelTitle")
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setToolTip("Look for a card that has been plugged in")
        self.refresh_button.clicked.connect(lambda: self.refresh())
        self.sync_button = QPushButton("Sync")
        self.sync_button.setToolTip(
            "Write the ticked discs' cues and waveforms to the picked card (it asks first)"
        )
        self.sync_button.setEnabled(False)
        self.sync_button.clicked.connect(
            lambda: self.current_card() and self._sync_to(self.current_card())
        )
        head = QHBoxLayout()
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.refresh_button)
        head.addWidget(self.sync_button)
        layout.addLayout(head)
        self.subtitle = QLabel()
        self.subtitle.setObjectName("mutedLabel")
        layout.addWidget(self.subtitle)
        self.cards = QListWidget()
        self.cards.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.cards.currentItemChanged.connect(self._picked)
        self.cards.itemSelectionChanged.connect(self._restyle_rows)
        self.cards.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.cards.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self.cards, 1)
        self.choose_button = QPushButton("Choose Folder…")
        self.choose_button.clicked.connect(self.choose_folder)
        layout.addWidget(self.choose_button)
        self.subtitle.setText(
            "No card found"
        )  # (the drives are looked at when the tab opens, and whenever one comes or goes)
        self._watch = list(watch)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(lambda _path: self._settle.start())
        self._settle = QTimer(
            self
        )  # one look for a burst of changes, and a moment for the
        self._settle.setSingleShot(True)  # card to finish mounting
        self._settle.setInterval(CARD_SETTLE_MS)
        self._settle.timeout.connect(self._drives_changed)
        self._poller: DrivePoller | None = None
        if poll:  # (Windows: a drive is a letter, not a folder to watch)
            self._poller = DrivePoller(self)
            self._poller.changed.connect(self._settle.start)
        self._watch_folders()

    def showEvent(self, event) -> None:
        if self._poller is not None:
            self._poller.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        if self._poller is not None:
            self._poller.stop()
        super().hideEvent(event)

    # -- finding cards -------------------------------------------------------------

    def _watch_folders(self) -> None:
        """Watch the folders that exist (one that appears later is picked up on the next change)."""
        have = set(self._watcher.directories())
        new = [str(p) for p in self._watch if str(p) not in have and p.is_dir()]
        if new:
            self._watcher.addPaths(new)

    def _drives_changed(self) -> None:
        self._watch_folders()
        self.refresh()

    def is_volume(self, path: Path) -> bool:
        """Whether `path` is a card that is plugged in (and so can be ejected), not a folder chosen by hand."""
        return path in self._found

    def current_card(self) -> Path | None:
        item = self.cards.currentItem()
        return None if item is None else Path(item.data(Qt.ItemDataRole.UserRole))

    def row_for(self, path: Path) -> CardRow | None:
        for number in range(self.cards.count()):
            item = self.cards.item(number)
            if item.data(Qt.ItemDataRole.UserRole) == str(path):
                return self.cards.itemWidget(item)
        return None

    def _detail(self, path: Path, by_hand: bool) -> str:
        if by_hand:
            return f"Folder · {path.parent}"
        parts = []
        if (size := card_capacity(path)) is not None:
            parts.append(_size_text(size))
        try:
            discs = len(cc.read_card(path)[0].entries)
            parts.append(f"{discs} disc{'s' if discs != 1 else ''}")
        except (cc.CardFormatError, OSError):
            pass
        return " · ".join(parts)

    def refresh(self, announce: bool = True) -> None:
        """Look again for cards. `card_changed` is emitted if the picked card changed (and only
        then), unless `announce` is False and the caller asks `current_card()` itself. Cards that are
        plugged in but not set up are looked for on a worker and join the list when it answers."""
        before = self.current_card()
        self._found = cc.find_cards((self._volumes or mounted_volumes)())
        self._populate(before)
        self._look_for_unset()
        if announce and self.current_card() != before:
            self.card_changed.emit(self.current_card())

    def _populate(self, before: Path | None) -> None:
        """Fill the list: the cards, folders chosen by hand, then the cards not set up (not pickable)."""
        found = self._found
        shown = [*found, *(p for p in self._chosen if p not in found)]
        self.cards.blockSignals(True)
        self.cards.clear()
        for path in shown:
            by_hand = path not in found
            row = CardRow(path, by_hand, self._detail(path, by_hand))
            row.eject_requested.connect(self._eject)
            row.remove_requested.connect(self._remove)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setSizeHint(QSize(0, ROW_HEIGHT))
            self.cards.addItem(item)
            self.cards.setItemWidget(item, row)
            if path == before:
                self.cards.setCurrentItem(item)
        if self.cards.currentItem() is None and shown:
            self.cards.setCurrentRow(0)
        for card in self._unset:
            row = CardRow(Path(card.disk.node), False, unset_detail(card), unset=card)
            row.format_requested.connect(self.format_card)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, card.disk.node)
            item.setFlags(
                Qt.ItemFlag.NoItemFlags
            )  # shown, never picked: nothing to sync to yet
            item.setSizeHint(
                QSize(0, UNSET_FILES_ROW_HEIGHT if card.has_files else UNSET_ROW_HEIGHT)
            )
            self.cards.addItem(item)
            self.cards.setItemWidget(item, row)
        self.cards.blockSignals(False)
        self._restyle_rows()
        n, blank = len(found), len(self._unset)
        said = []
        if n or not blank:
            said.append(
                "No card found" if not n else f"{n} card{'s' if n != 1 else ''} found"
            )
        if blank:
            said.append(f"{blank} to set up")
        self.subtitle.setText(" · ".join(said))

    def _look_for_unset(self) -> None:
        """Ask the system (on a worker: it is slow) which plugged-in cards are not set up for the CDJ."""
        self._unset_serial += 1
        serial = self._unset_serial

        def work() -> list[card_format.UnsetCard]:
            try:
                return self._find_unset()
            except card_format.FormatError as exc:
                raise TaskError(str(exc)) from exc

        def done(found: list[card_format.UnsetCard]) -> None:
            if serial == self._unset_serial and found != self._unset:
                self._unset = found
                self._populate(self.current_card())

        def failed(text: str) -> None:
            log.info("couldn't list cards that are not set up: %s", text)

        run_async(work, done, failed)

    def _restyle_rows(self) -> None:
        for number in range(self.cards.count()):
            item = self.cards.item(number)
            row = self.cards.itemWidget(item)
            if row is not None:
                row.set_selected(item.isSelected())
                row.button.setEnabled(not (self._busy or self._ejecting))

    # -- the right-click menu ------------------------------------------------------

    def card_menu(self, path: Path | None) -> QMenu:
        """The menu for a card in the list (`path`), or for the empty part of it (None): Sync the ticked
        discs to it, Eject it (or take a folder chosen by hand off the list), and Format a Card…, which
        is also here for a blank card that is not listed yet."""
        menu = QMenu(self)
        idle = not (self._busy or self._ejecting)
        if path is not None:
            name = path.name or str(path)
            row = self.row_for(path)
            sync = menu.addAction(f"Sync to “{name}”…")
            sync.setEnabled(idle)
            sync.setToolTip(
                "Write the ticked discs' cues and waveforms to this card (it asks first)"
            )
            sync.triggered.connect(lambda: self._sync_to(path))
            grab = menu.addAction(f"Import Cues from “{name}”…")
            grab.setEnabled(idle)
            grab.setToolTip(
                "Bring the memory cues set on the player back into Dubcrate (you choose which)"
            )
            grab.triggered.connect(lambda: self._import_from(path))
            if row is not None and row.by_hand:
                menu.addAction("Take This Folder Off the List").triggered.connect(
                    lambda: self._remove(path)
                )
            else:
                eject = menu.addAction(f"Eject “{name}”")
                eject.setEnabled(idle)
                eject.triggered.connect(lambda: self._eject(path))
            menu.addSeparator()
        fmt = menu.addAction("Format a Card…")
        fmt.setEnabled(idle)
        fmt.setToolTip(
            "Set up a blank SD card from scratch: erase it, make it FAT16 and add the PIONEER "
            "folder, so cues and waveforms can go on it before it has been in a player"
        )
        fmt.triggered.connect(lambda: self.format_card())
        return menu

    def _pick_quietly(self, path: Path) -> None:
        """Show `path` as the picked card without telling the Sync view (the caller's signal does, once)."""
        self.cards.blockSignals(True)
        for row in range(self.cards.count()):
            if self.cards.item(row).data(Qt.ItemDataRole.UserRole) == str(path):
                self.cards.setCurrentRow(row)
        self.cards.blockSignals(False)
        self._restyle_rows()

    def _sync_to(self, path: Path) -> None:
        """Right-click > Sync: show `path` as the picked card and ask for a sync to it."""
        self._pick_quietly(path)
        self.sync_requested.emit(path)

    def _import_from(self, path: Path) -> None:
        """Right-click > Import Cues: show `path` as the picked card and ask for its new cues."""
        self._pick_quietly(path)
        self.import_requested.emit(path)

    def _on_context_menu(self, where) -> None:
        item = self.cards.itemAt(where)
        row = self.cards.itemWidget(item) if item is not None else None
        # (a card not set up has nothing to sync to or eject: it gets the empty list's menu)
        path = (
            Path(item.data(Qt.ItemDataRole.UserRole))
            if item is not None and not (row is not None and row.unset)
            else None
        )
        self.card_menu(path).exec(self.cards.viewport().mapToGlobal(where))

    # -- ejecting and removing -----------------------------------------------------

    def set_busy(self, busy: bool) -> None:
        """A sync is writing to the card: its eject button waits, and no card is formatted under it."""
        self._busy = busy
        self._restyle_rows()
        self._update_sync_button()

    def _update_sync_button(self) -> None:
        self.sync_button.setEnabled(
            self.current_card() is not None and not (self._busy or self._ejecting)
        )

    def _eject(self, path: Path) -> None:
        if self._busy or self._ejecting:
            return
        self._ejecting = True
        self._restyle_rows()
        name = path.name or str(path)

        def work() -> None:
            try:
                eject_card(path)
            except EjectError as exc:
                raise TaskError(str(exc)) from exc

        def done(_result) -> None:
            self._ejecting = False
            self.message.emit(f"Ejected “{name}”: it is safe to take out.")
            self.refresh()  # (it has gone, and so has the card the tab was showing)

        def failed(text: str) -> None:
            self._ejecting = False
            self.message.emit(
                f"{text} Eject it ({card_format.eject_hint()}) before taking it out."
            )
            self.refresh()

        run_async(work, done, failed)

    def _remove(self, path: Path) -> None:
        """Take a folder chosen by hand off the list (the folder itself is not touched)."""
        if path in self._chosen:
            self._chosen.remove(path)
        self.refresh()

    # -- formatting a blank card ---------------------------------------------------

    def format_card(self, device: str | None = None) -> None:
        """Open the Format a Card window (with `device` picked, from a card's own button); a card it
        makes is listed and picked at once."""
        if self._busy or self._ejecting:
            return
        dialog = (
            FormatCardDialog(self, device=device) if device else FormatCardDialog(self)
        )
        dialog.formatted.connect(self._formatted)
        dialog.exec()

    def _formatted(self, volume: Path) -> None:
        """A card was just formatted (and is mounted): look again, and pick it."""
        self._unset = []  # (the card just formatted is one of them, and is a card now)
        self.cards.blockSignals(True)
        self.refresh(announce=False)
        for row in range(self.cards.count()):
            if self.cards.item(row).data(Qt.ItemDataRole.UserRole) == str(volume):
                self.cards.setCurrentRow(row)
        self.cards.blockSignals(False)
        self._restyle_rows()
        self.message.emit(
            f"Formatted “{volume.name}”: it is ready for cues and waveforms. Eject it before taking it out."
        )
        self.card_changed.emit(self.current_card())

    # -- choosing by hand ----------------------------------------------------------

    def choose_folder(self) -> None:
        folder = ask_card_folder(self)
        if not folder:
            return
        path = Path(folder)
        try:
            cc.card_dir(path)
        except cc.CardFormatError as exc:
            self.subtitle.setText(str(exc))
            return
        if path not in self._chosen:
            self._chosen.append(path)
        self.cards.blockSignals(True)
        self.refresh(announce=False)
        for row in range(self.cards.count()):
            if self.cards.item(row).data(Qt.ItemDataRole.UserRole) == str(path):
                self.cards.setCurrentRow(row)
        self.cards.blockSignals(False)
        self._restyle_rows()
        self.card_changed.emit(self.current_card())

    def _picked(self, *_: object) -> None:
        self._update_sync_button()
        self.card_changed.emit(self.current_card())


def _read_all(paths: Sequence[Path]) -> dict[Path, Mapping[str, Sequence[Cue]] | str]:
    """On a worker: rekordbox's cues for each database, or the reason one couldn't be read."""
    found: dict[Path, Mapping[str, Sequence[Cue]] | str] = {}
    for path in paths:
        try:
            found[path] = read_cues(path)
        except RekordboxError as exc:
            found[path] = str(exc)
    return found


class SyncView(QWidget):
    """The middle: the discs, what a sync would do for each, and the dry run for the one picked."""

    message = Signal(str)  # for the status bar
    card_ejected = (
        Signal()
    )  # the card was ejected after a sync: it is gone from the Mac
    busy_changed = Signal(bool)  # a sync is writing (or ejecting): leave the card alone
    cues_chosen = Signal(
        object
    )  # [ImportChoice]: the changes from the card to bring in (`card_import`)

    def __init__(
        self,
        lib: Library,
        parent: QWidget | None = None,
        backup_dir: Path | None = None,
        can_eject: Callable[[Path], bool] | None = None,
    ):
        super().__init__(parent)
        self._lib = lib
        self._backup_dir = backup_dir or default_backup_dir()
        self._can_eject = can_eject or (
            lambda _path: False
        )  # (a card plugged in, not a folder)
        self._writing = False
        self._toc_dialog: TocManagerDialog | None = None
        self._card_path: Path | None = None
        self._card: tuple[cc.IndexFile, Mapping[str, cc.TrackFile | None]] | None = None
        self._card_note = ""  # why the card couldn't be read, if it couldn't
        self._surveys: dict[int, DiscSurvey] = {}
        self._unmeasured: list[Track] = []
        self._loaded: dict = {}  # rekordbox's cues as last read, for looking at the discs again
        self._cue_loader: Callable[[Path], Mapping[str, Sequence[Cue]]] = lambda _p: {}
        self._serial = 0  # a newer refresh makes an older one's result stale
        self._measuring = False
        self._analysing = False
        self._cancel: CancelToken | None = (
            None  # set while measuring or analysing: the Cancel button's
        )
        self._ticking = False
        self._sync_when_ready = (
            False  # sync as soon as the discs have been looked at against a new card
        )
        self._import_when_ready = False  # likewise, to import the card's new cues

        # The right-hand side of the window while the Sync tab is on (the window puts `disc_panel` there, in place
        # of the Discs list): the discs, each with a tick to say what to sync, and what to do with the ticked ones.
        self.disc_panel = QWidget()
        panel = QVBoxLayout(self.disc_panel)
        panel.setContentsMargins(12, 12, 12, 12)
        panel.setSpacing(8)
        title = QLabel("Discs to sync")
        title.setObjectName("panelTitle")
        panel.addWidget(title)
        self.subtitle = unsized(QLabel())
        self.subtitle.setObjectName("mutedLabel")
        panel.addWidget(self.subtitle)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search discs")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        panel.addWidget(self.search)

        self.tree = QTreeWidget()
        self.tick_header = TickHeader(self.tree)
        self.tree.setHeader(self.tick_header)
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Disc", "Tracks"])
        self.tick_header.set_indent(self.tree.indentation())
        self.tick_header.tick_clicked.connect(self._tick_clicked)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_TRACKS, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.currentItemChanged.connect(lambda *_: self._show_detail())
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(
            lambda where: self.disc_menu().popup(
                self.tree.viewport().mapToGlobal(where)
            )
        )
        panel.addWidget(self.tree, 1)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setToolTip(
            "Stop measuring or analysing: what is already done is kept, and nothing is written to the card"
        )
        self.cancel_button.clicked.connect(self._cancel_work)
        self.cancel_button.hide()
        self.import_button = QPushButton("Import Cues…")
        self.import_button.setToolTip(
            "Look for what changed on the player (cues and loops set, deleted or moved) that Dubcrate doesn't "
            "have, and bring in the ones you pick. Reads the card; changes nothing on it."
        )
        self.import_button.clicked.connect(self.import_from_card)
        self.sync_button = QPushButton("Sync")
        self.sync_button.clicked.connect(self._on_sync)
        self.eject_check = QCheckBox("Eject after syncing")
        self.eject_check.setChecked(settings.get_sync_eject())
        self.eject_check.toggled.connect(settings.set_sync_eject)
        buttons = QHBoxLayout()
        buttons.addWidget(self.import_button)
        buttons.addWidget(self.eject_check)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.sync_button)
        panel.addLayout(buttons)

        # The middle: the picked disc's tracks, and the picked track's waveform at the bottom.
        self.browser = TrackBrowser()
        self.waves = (
            self.browser
        )  # (its rows and message, as the tests and callers look)
        self.analyse_note = self.browser.analyse_note
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.browser)
        self._update_buttons()
        self._show_detail()

    # -- what is shown -----------------------------------------------------------

    def set_card(self, path: Path | None) -> None:
        """The card to compare with (None: none yet). Looks at the discs again."""
        self._card_path = path
        self.refresh()

    def refresh(self) -> None:
        """Read the card, then rekordbox's cues (on a worker), then look at every audio disc."""
        self._serial += 1
        serial = self._serial
        self._card, self._card_note = None, ""
        if self._card_path is not None:
            try:
                self._card = cc.read_card(self._card_path)
            except cc.CardFormatError as exc:
                self._card_note = str(exc)
        databases = [Path(s.path) for s in self._lib.sources() if s.kind == "rekordbox"]
        if not databases:
            self._look_at_discs({})
            return
        self.subtitle.setText("Reading rekordbox's cues…")

        def done(loaded: dict) -> None:
            if serial == self._serial:
                self._look_at_discs(loaded)

        def failed(_text: str) -> None:
            if serial == self._serial:
                self._look_at_discs(
                    {}
                )  # (each disc then says it couldn't read rekordbox)

        run_async(lambda: _read_all(databases), done, failed)

    def _look_at_discs(self, loaded: dict) -> None:
        self._loaded = loaded

        def load(path: Path) -> Mapping[str, Sequence[Cue]]:
            found = loaded.get(path)
            if not isinstance(found, Mapping):
                raise RekordboxError(found or "it wasn't read")
            return found

        self._cue_loader = load
        surveys = survey_discs(
            self._lib,
            self._card,
            RekordboxCues(self._lib, load=load),
            disc_paths(self._lib),
            waves=True,
        )
        self._surveys = {s.disc.id: s for s in surveys}
        self._unmeasured = self._tracks_to_measure(surveys)
        self._fill()
        if (
            self._sync_when_ready
        ):  # (a right-click > Sync to a card that was not the one shown)
            self._sync_when_ready = False
            self._request_sync()
        if (
            self._import_when_ready
        ):  # (a right-click > Import Cues from a card that was not the one shown)
            self._import_when_ready = False
            self.import_from_card()

    def _tracks_to_measure(self, surveys: Sequence[DiscSurvey]) -> list[Track]:
        """The tracks of discs that can't be synced for want of a length, that can be probed."""
        found: dict[int, Track] = {}
        for survey in surveys:
            if any("measured length" in p for p in survey.input.problems):
                for entry in self._lib.disc_entries(survey.disc.id):
                    if needs_measuring(entry.track):
                        found[entry.track.id] = entry.track
        return list(found.values())

    def _fill(self) -> None:
        """Folders, then discs, each A to Z, as in the Discs list; a folder with no audio disc inside
        is left out."""
        ticked = set(self.ticked_ids())
        current = self._current_disc_id()
        folders = self._lib.disc_folders()
        by_parent: dict[int | None, list] = {}
        for folder in folders:
            by_parent.setdefault(folder.parent_id, []).append(folder)
        in_folder: dict[int | None, list[DiscSurvey]] = {}
        for survey in self._surveys.values():
            in_folder.setdefault(survey.disc.folder_id, []).append(survey)

        def has_discs(folder_id: int) -> bool:
            return bool(in_folder.get(folder_id)) or any(
                has_discs(f.id) for f in by_parent.get(folder_id, [])
            )

        def fill(parent: QTreeWidget | QTreeWidgetItem, folder_id: int | None) -> None:
            for folder in sorted(
                by_parent.get(folder_id, []), key=lambda f: f.name.casefold()
            ):
                if not has_discs(folder.id):
                    continue
                item = QTreeWidgetItem(parent, [folder.name])
                item.setIcon(COL_NAME, _folder_icon())
                fill(item, folder.id)
            for survey in sorted(
                in_folder.get(folder_id, []), key=lambda s: s.disc.name.casefold()
            ):
                item = QTreeWidgetItem(
                    parent,
                    [
                        survey.disc.name,
                        str(survey.disc.track_count),
                    ],
                )
                item.setData(COL_NAME, Qt.ItemDataRole.UserRole, survey.disc.id)
                item.setIcon(COL_NAME, _disc_icon("audio"))
                item.setToolTip(COL_NAME, "\n".join([survey.status, *survey.detail()]))
                item.setTextAlignment(
                    COL_TRACKS,
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                )
                if not survey.writable:  # (no box; greyed, and the tooltip says why)
                    for column in (COL_NAME, COL_TRACKS):
                        item.setForeground(column, self.palette().placeholderText())
                else:
                    item.setCheckState(
                        COL_NAME,
                        Qt.CheckState.Checked
                        if survey.disc.id in ticked
                        else Qt.CheckState.Unchecked,
                    )

        self._ticking = True
        # (clearing the list is not the user picking nothing: that would forget which track was picked)
        self.tree.blockSignals(True)
        self.tree.clear()
        fill(self.tree, None)
        self.tree.expandAll()
        for folder in self._folder_items():
            self._update_folder(folder)
        self._ticking = False
        if current is not None:
            item = next((i for i in self._disc_items() if self._id(i) == current), None)
            if item is not None:
                self.tree.setCurrentItem(item)
        self.tree.blockSignals(False)
        self._filter(self.search.text())
        self._update_buttons()
        self._show_detail()

    # -- the tree ------------------------------------------------------------------

    @staticmethod
    def _id(item: QTreeWidgetItem) -> int | None:
        return item.data(COL_NAME, Qt.ItemDataRole.UserRole)

    def _walk(self, parent: QTreeWidgetItem | None = None):
        count = self.tree.topLevelItemCount() if parent is None else parent.childCount()
        for row in range(count):
            item = self.tree.topLevelItem(row) if parent is None else parent.child(row)
            yield item
            yield from self._walk(item)

    def _disc_items(self) -> list[QTreeWidgetItem]:
        return [i for i in self._walk() if self._id(i) is not None]

    def _folder_items(self) -> list[QTreeWidgetItem]:
        """Every folder, the innermost first (so one is worked out after what is inside it)."""
        return [i for i in reversed(list(self._walk())) if self._id(i) is None]

    @staticmethod
    def _tickable(item: QTreeWidgetItem) -> bool:
        return item.data(COL_NAME, Qt.ItemDataRole.CheckStateRole) is not None

    def _update_folder(self, folder: QTreeWidgetItem) -> None:
        """A folder shows ticked, unticked or partly, from the discs inside it (none: no box)."""
        states = [
            i.checkState(COL_NAME)
            for i in self._walk(folder)
            if self._id(i) is not None and self._tickable(i)
        ]
        if not states:
            return
        folder.setCheckState(
            COL_NAME,
            Qt.CheckState.Checked
            if all(s == Qt.CheckState.Checked for s in states)
            else Qt.CheckState.Unchecked
            if all(s == Qt.CheckState.Unchecked for s in states)
            else Qt.CheckState.PartiallyChecked,
        )

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        """A tick on a folder ticks every disc in it; a tick on a disc brings its folders up to date."""
        if self._ticking or column != COL_NAME:
            return
        self._ticking = True
        try:
            if self._id(item) is None:
                state = item.checkState(COL_NAME)
                if state != Qt.CheckState.PartiallyChecked:
                    for disc in self._walk(item):
                        if self._id(disc) is not None and self._tickable(disc):
                            disc.setCheckState(COL_NAME, state)
            for folder in self._folder_items():
                self._update_folder(folder)
        finally:
            self._ticking = False
        self._update_buttons()

    def _tick_all(self, state: Qt.CheckState) -> None:
        self._ticking = True
        for item in self._disc_items():
            if self._tickable(item):
                item.setCheckState(COL_NAME, state)
        for folder in self._folder_items():
            self._update_folder(folder)
        self._ticking = False
        self._update_buttons()

    def _tick_clicked(self) -> None:
        """The header's box: tick every disc that can be synced, or, when all are ticked, none."""
        every = len(self.ticked_ids()) == len(self._tickable_discs())
        self._tick_all(Qt.CheckState.Unchecked if every else Qt.CheckState.Checked)

    def _tickable_discs(self) -> list[QTreeWidgetItem]:
        return [i for i in self._disc_items() if self._tickable(i)]

    def _update_header_tick(self) -> None:
        """The header's box shows how many of the discs that can be synced are ticked."""
        discs = self._tickable_discs()
        ticked = len(self.ticked_ids())
        state = (
            Qt.CheckState.Unchecked
            if not ticked
            else Qt.CheckState.Checked
            if ticked == len(discs)
            else Qt.CheckState.PartiallyChecked
        )
        self.tick_header.set_state(state, bool(discs))

    def ticked_ids(self) -> list[int]:
        """The ticked discs' ids, in the order listed."""
        return [
            self._id(i)
            for i in self._disc_items()
            if self._tickable(i)
            and i.checkState(COL_NAME) == Qt.CheckState.Checked
            and self._surveys[self._id(i)].writable
        ]

    def _current_disc_id(self) -> int | None:
        item = self.tree.currentItem()
        return None if item is None else self._id(item)

    def _filter(self, text: str) -> None:
        """Show the discs whose name has `text` in it, and the folders that hold one."""
        needle = text.strip().casefold()
        for item in reversed(list(self._walk())):
            if self._id(item) is not None:
                item.setHidden(
                    bool(needle) and needle not in item.text(COL_NAME).casefold()
                )
            else:
                item.setHidden(
                    all(item.child(r).isHidden() for r in range(item.childCount()))
                )

    # -- the words -------------------------------------------------------------------

    def _show_detail(self) -> None:
        """The middle for the disc picked on the right: its tracks and the picked track's waveform."""
        survey = self._surveys.get(self._current_disc_id() or -1)
        if survey is None:
            self.browser.show_message(
                "No audio discs yet. Make one, or open the Library, and it is listed on the right."
                if not self._surveys
                else ""
            )
        elif survey.input.problems:
            self.browser.show_message(
                "This disc can't be synced: " + survey.input.problems[0],
                title=survey.disc.name,
            )
        else:
            entries = self._lib.disc_entries(survey.disc.id)
            plan = survey.plan
            self.browser.show_disc(
                survey.disc.id,
                survey.disc.name,
                "",
                wave_rows(survey, entries, True),
                warnings=[*survey.input.warnings, *(plan.notes if plan else [])],
            )
        self._update_analyse()

    def _update_buttons(self) -> None:
        writable = [s for s in self._surveys.values() if s.writable]
        ticked = [self._surveys[i] for i in self.ticked_ids()]
        if not (
            self._measuring or self._writing or self._analysing
        ):  # (which have their own to show)
            self.subtitle.setText(
                self._card_note  # (a folder that isn't the player's card: said where it is seen)
                if self._card is None and self._card_note
                else f"{len(ticked)} of {len(writable)} disc{'s' if len(writable) != 1 else ''} selected"
                if self._surveys
                else "No audio discs"
            )
        self._update_header_tick()
        busy = self._measuring or self._writing or self._analysing
        self.cancel_button.setVisible(self._measuring or self._analysing)
        self._update_analyse()
        self.import_button.setEnabled(
            self._card is not None and bool(self._surveys) and not busy
        )
        self.sync_button.setEnabled(
            WRITING_ENABLED
            and bool(ticked)
            and self._card is not None
            and not self._writing
            and not self._analysing
        )
        self.sync_button.setToolTip("" if WRITING_ENABLED else WAITING_NOTE)
        can_eject = (
            self._card_path is not None
            and self._can_eject(self._card_path)
            and not self._writing
        )
        self.eject_check.setEnabled(can_eject)
        self.eject_check.setToolTip(
            "Eject the card once it is written, so it is safe to take out."
            if can_eject or self._writing
            else "Only a card that is plugged in can be ejected, not a folder you chose."
        )

    def disc_menu(self) -> QMenu:
        """What a right-click on the disc list offers: measure the lengths a disc's card entry needs (a burn
        measures them too, and so does adding a track), and clear the ticks."""
        menu = QMenu(self)
        busy = self._measuring or self._writing or self._analysing
        measure = menu.addAction("Measure Lengths", self.measure_lengths)
        measure.setEnabled(bool(self._unmeasured) and not busy)
        measure.setToolTip(
            "A card entry is the disc's table of contents, which needs every track's exact length "
            "on the CD. Reads the files; changes nothing."
        )
        clear = menu.addAction(
            "Clear Selection", lambda: self._tick_all(Qt.CheckState.Unchecked)
        )
        clear.setEnabled(bool(self.ticked_ids()))
        menu.addSeparator()
        toc = menu.addAction("TOC Manager…", self.open_toc_manager)
        toc.setEnabled(self._current_disc_id() is not None)
        return menu

    def open_toc_manager(self, disc_id: int | None = None) -> None:
        """The TOC Manager for a disc (the picked one by default); the discs are looked at again when it changes."""
        disc_id = disc_id if disc_id is not None else self._current_disc_id()
        if disc_id is None:
            return
        self._toc_dialog = TocManagerDialog(self._lib, disc_id, self)
        self._toc_dialog.changed.connect(self.refresh)
        self._toc_dialog.open()

    def import_from_card(self) -> None:
        """Find what changed on the card that Dubcrate doesn't have (cues and loops set on the player, and, after a
        recorded sync, cues deleted or moved there), let the user pick, and hand the choices on (`cues_chosen`).
        Reads the card's files as already read; writes nothing."""
        if self._card is None or not self._surveys:
            self.message.emit("Choose a card first")
            return
        findings = survey_new_cues(
            self._lib,
            self._card,
            RekordboxCues(self._lib, load=self._cue_loader),
            list(self._surveys.values()),
        )
        if not findings:
            self.message.emit("Nothing new on the card")
            return
        chosen = ask_import(self, findings)
        if chosen:
            self.cues_chosen.emit(chosen)

    def _picked_to_analyse(self) -> list[Track]:
        """The picked disc's tracks that have no waveform yet."""
        survey = self._surveys.get(self._current_disc_id() or -1)
        if survey is None or survey.input.problems:
            return []
        return list(survey.input.to_analyse)

    def _update_analyse(self) -> None:
        """The line above the waveform: how many of the picked disc's tracks have none yet (adding a track to a
        disc analyses it in the background, a burn does it from the rendered audio, and a sync does what is left)."""
        survey = self._surveys.get(self._current_disc_id() or -1)
        todo = self._picked_to_analyse()
        if survey is None or survey.input.problems:
            note = ""
        elif todo:
            note = f"{len(todo)} track{'s' if len(todo) != 1 else ''} not analysed yet: a sync does that first."
        else:
            note = ""
        self.analyse_note.setText(note)
        self.analyse_note.setVisible(bool(note))

    def _set_writing(self, writing: bool) -> None:
        """A sync is (or is no longer) writing and ejecting: the card list must leave the card alone."""
        self._writing = writing
        self.busy_changed.emit(writing)

    def sync_to(self, card: Path) -> None:
        """The card's right-click > Sync: make `card` the one shown (looking at the discs against it
        first, if it is not already) and sync the ticked discs to it, asking as the Sync button does."""
        if card != self._card_path:
            self._sync_when_ready = True
            self.set_card(card)
            return
        self._request_sync()

    def import_from(self, card: Path) -> None:
        """The card's right-click > Import Cues: make `card` the one shown (looking at the discs against
        it first, if it is not already), then offer its new memory cues as the button does."""
        if card != self._card_path:
            self._import_when_ready = True
            self.set_card(card)
            return
        self.import_from_card()

    def _request_sync(self) -> None:
        if not self.ticked_ids():
            self.message.emit("Tick the discs you want to sync first.")
            return
        self._on_sync()

    def _on_sync(self) -> None:
        """Ask, then plan the card fresh, copy its files aside and write (on a worker); then, if
        asked, eject the card."""
        card = self._card_path
        if not WRITING_ENABLED or self._writing or card is None or self._card is None:
            return
        surveys = [self._surveys[i] for i in self.ticked_ids()]
        if not surveys:
            return
        todo = self._tracks_to_analyse(surveys)
        if todo:
            self._analyse_then_write(todo, [s.disc.id for s in surveys])
            return
        self._confirm_and_write(surveys)

    def _tracks_to_analyse(self, surveys: Sequence[DiscSurvey]) -> list[Track]:
        found: dict[int, Track] = {}
        for survey in surveys:
            for track in survey.input.to_analyse:
                found[track.id] = track
        return list(found.values())

    def _analyse_then_write(self, todo: list[Track], disc_ids: list[int]) -> None:
        """Analyse the tracks that have no waveform yet, look at the discs again, and then ask and write."""

        def then() -> None:
            surveys = [self._surveys[i] for i in disc_ids if i in self._surveys]
            if surveys:
                self._confirm_and_write(surveys)

        self._analyse(todo, then)

    def _analyse(
        self, todo: list[Track], then: Callable[[], None] | None = None
    ) -> None:
        """Analyse tracks (on a worker: the first time takes a few seconds a track), keep the results
        and look at the discs again; then `then()`, unless it was cancelled or failed."""
        self._analysing = True
        token = self._cancel = CancelToken()
        self.cancel_button.setEnabled(True)
        self.subtitle.setText(f"Analysing {len(todo)} tracks for their waveforms…")
        self._update_buttons()

        def done(result: tuple[dict[int, list[int]], list[str]]) -> None:
            found, problems = result
            store_waveforms(self._lib, found)
            self._analysing = False
            self._cancel = None
            if problems:
                self.message.emit(
                    f"No waveform for {len(problems)} file(s): {problems[0]}"
                )
            elif then is None:
                self.message.emit(
                    f"Analysed {len(found)} track{'s' if len(found) != 1 else ''}."
                )
            self._look_at_discs(self._loaded)
            if then is not None:
                then()

        def failed(text: str) -> None:
            self._analysing = False
            self._cancel = None
            self.message.emit(f"Couldn't analyse the tracks: {text}")
            self._update_buttons()

        def cancelled(partial) -> None:
            """Stopped: keep the waveforms already worked out (they are as good as any), write nothing."""
            found = partial[0] if partial else {}
            store_waveforms(self._lib, found)
            self._analysing = False
            self._cancel = None
            self.message.emit(
                f"Cancelled: {len(found)} of {len(todo)} tracks analysed, kept for next time. "
                "Nothing was written to the card."
            )
            self._update_buttons()
            self._look_at_discs(self._loaded)

        run_async(
            lambda report: measure_waveforms(todo, report, cancel=token),
            done,
            failed,
            on_progress=lambda n, total: self.subtitle.setText(
                f"Analysing waveforms: {n} of {total}"
            ),
            on_cancel=cancelled,
        )

    def _confirm_and_write(self, surveys: list[DiscSurvey]) -> None:
        card = self._card_path
        if card is None or self._card is None or self._writing:
            return
        eject = self.eject_check.isChecked() and self.eject_check.isEnabled()
        text = confirmation_text(
            surveys, card.name or str(card), self._backup_dir, eject
        )
        if not ask_sync_confirmation(self, text):
            return
        requests = [r for s in surveys for r in s.input.all_requests()]
        backup_dir = self._backup_dir
        self._set_writing(True)
        self.subtitle.setText("Writing to the card…")
        self._update_buttons()

        def work():
            try:
                return apply_sync(card, requests, backup_dir)
            except cc.CardFormatError as exc:
                raise TaskError(str(exc)) from exc

        def done(outcome) -> None:
            for (
                key,
                cues,
            ) in (
                outcome.baselines.items()
            ):  # (this is the GUI thread: the library is ours)
                self._lib.set_card_baseline(key, cues)
            if outcome.wrote:
                n = len(outcome.written)
                what = f"{outcome.cues_written} cues"
                if outcome.waveforms_written:
                    what += f", {outcome.waveforms_written} waveforms"
                said = (
                    f"Wrote {n} disc file{'s' if n != 1 else ''} ({what}) to “{card.name}”; its old "
                    f"files are copied to {outcome.backup}."
                )
            else:
                said = (
                    "Nothing to write: the card already has these cues and waveforms."
                )
            if eject:
                self._eject_card(card, said)
                return
            self._set_writing(False)
            self.message.emit(f"{said} Eject the card before taking it out.")
            self.refresh()  # (the card as it is now, so the list says "In sync")

        def failed(text: str) -> None:
            self._set_writing(False)
            self.message.emit(text)
            show_problem(
                self,
                "Couldn't Sync to the Card",
                f"{text}\n\nThe card's files are copied to {backup_dir} before anything is "
                "written, so the old ones can be put back from there.",
            )
            self.refresh()  # (the card may have changed)

        run_async(work, done, failed)

    def _eject_card(self, card: Path, said: str) -> None:
        """Eject the card that was just written (on a worker: macOS flushes it first)."""
        self.subtitle.setText("Ejecting the card…")

        def work() -> None:
            try:
                eject_card(card)
            except EjectError as exc:
                raise TaskError(str(exc)) from exc

        def done(_result) -> None:
            self._set_writing(False)
            self.message.emit(f"{said} Ejected “{card.name}”: it is safe to take out.")
            self.card_ejected.emit()

        def failed(text: str) -> None:
            self._set_writing(False)
            self.message.emit(
                f"{said} {text} Eject it ({card_format.eject_hint()}) before taking it out."
            )
            self.refresh()

        run_async(work, done, failed)

    def _cancel_work(self) -> None:
        """The Cancel button: stop measuring or analysing (between files, so it takes a moment)."""
        if self._cancel is not None:
            self._cancel.cancel()
            self.cancel_button.setEnabled(False)
            self.subtitle.setText("Cancelling…")

    # -- measuring lengths -------------------------------------------------------------

    def measure_lengths(self) -> None:
        """Probe the tracks that have no CD length yet (on a worker), store them, and look again."""
        todo = self._unmeasured
        if not todo or self._measuring:
            return
        self._measuring = True
        token = self._cancel = CancelToken()
        self.cancel_button.setEnabled(True)
        self._update_buttons()
        self.subtitle.setText(f"Measuring {len(todo)} tracks…")

        def done(result: tuple[dict[int, int], list[str]]) -> None:
            lengths, problems = result
            for track_id, sectors in lengths.items():
                self._lib.set_cd_sectors(track_id, sectors)
            self._measuring = False
            self._cancel = None
            if problems:
                self.message.emit(
                    f"Couldn't measure {len(problems)} file(s): {problems[0]}"
                )
            self.refresh()

        def failed(text: str) -> None:
            self._measuring = False
            self._cancel = None
            self.message.emit(f"Couldn't measure the tracks: {text}")
            self._update_buttons()

        def cancelled(partial) -> None:
            lengths = partial[0] if partial else {}
            for (
                track_id,
                sectors,
            ) in lengths.items():  # (a length measured is a length, kept)
                self._lib.set_cd_sectors(track_id, sectors)
            self._measuring = False
            self._cancel = None
            self.message.emit(
                f"Cancelled: {len(lengths)} of {len(todo)} tracks measured, kept."
            )
            self._update_buttons()
            self.refresh()

        run_async(
            lambda report: probe_lengths(todo, report, cancel=token),
            done,
            failed,
            on_progress=lambda n, total: self.subtitle.setText(
                f"Measuring lengths: {n} of {total}"
            ),
            on_cancel=cancelled,
        )
