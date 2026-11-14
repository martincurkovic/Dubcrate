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
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dubcrate import cdj_card as cc
from dubcrate import settings
from dubcrate.analysis import Cue
from dubcrate.card_apply import apply_sync, default_backup_dir
from dubcrate.card_discs import DiscSurvey, RekordboxCues, survey_discs
from dubcrate.eject import EjectError, eject_card
from dubcrate.library import Library
from dubcrate.library.lengths import needs_measuring, probe_lengths
from dubcrate.library.models import Track
from dubcrate.library.rekordbox import RekordboxError, read_cues

from . import theme
from .explorer import mounted_volumes
from .glyph_widgets import GlyphButton, GlyphIcon, close_glyph, eject_glyph, ink
from .label_dialog import disc_paths
from .models import _disc_icon, _folder_icon
from .source_tabs import explorer_glyph, sd_card_glyph
from .tasks import TaskError, run_async

log = logging.getLogger(__name__)

# Off until the player had loaded a card file we wrote; on since 2026-11-14 (dev_docs/card-sync.md,
# Phase 0: a CDJ-1000MK3, firmware 2.20, showed the cues of files written by `dubcrate card sync`).
WRITING_ENABLED = True
WAITING_NOTE = (
    "Waiting on the player test: no card file written by Dubcrate has been loaded by a "
    "CDJ yet. `dubcrate card sync` is the way to try it, on an imaged card."
)

COL_NAME, COL_TRACKS, COL_STATUS, COL_TICK = range(4)


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
    """What the question before a write says: each disc and what it adds, what is kept, the backup."""
    cues = sum(s.to_add for s in surveys)
    added = f"{cues} cue{'s' if cues != 1 else ''} will be added as memory cues."
    lines = [f"Write rekordbox cues to “{card_name}”?", ""]
    lines += [f"  {s.disc.name}: {s.status[0].lower()}{s.status[1:]}" for s in surveys]
    lines += [
        "",
        f"{added} The cues already on the card, its hot cues and its waveforms are kept.",
        f"The card's files are copied to {backup_dir} first.",
    ]
    if eject:
        lines.append("The card will be ejected afterwards, so it is safe to take out.")
    if left_out := sum(len(s.plan.overflow) for s in surveys if s.plan):
        lines.append(
            f"{left_out} cue{'s' if left_out != 1 else ''} won't fit (the player holds "
            f"{cc.MAX_CUES} per disc) and will be left out."
        )
    if warned := sum(bool(s.input.warnings) for s in surveys):
        lines.append(
            f"{warned} disc{'s have' if warned != 1 else ' has'} a warning in the list: "
            "worth reading before you write."
        )
    return "\n".join(lines)


CARD_SETTLE_MS = (
    800  # after a change in /Volumes, a moment for the card to finish mounting
)
ROW_HEIGHT = 62


def card_capacity(path: Path) -> int | None:
    """The size of the volume `path` is on, in bytes (None if it can't be asked). Tests stand it in."""
    try:
        return shutil.disk_usage(path).total
    except OSError:
        return None


def _size_text(size: int) -> str:
    return f"{size / 1e9:.1f} GB" if size >= 1e9 else f"{size / 1e6:.0f} MB"


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

    def __init__(
        self, path: Path, by_hand: bool, detail: str, parent: QWidget | None = None
    ):
        super().__init__(parent)
        self.path, self.by_hand = path, by_hand
        self._selected = False
        name = path.name or str(path)
        self.icon = GlyphIcon(explorer_glyph if by_hand else sd_card_glyph, 36)
        self.title = _ElidedLabel(name)
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        self.detail = _ElidedLabel(detail)
        words = QVBoxLayout()
        words.setSpacing(1)
        words.addStretch(1)
        words.addWidget(self.title)
        words.addWidget(self.detail)
        words.addStretch(1)
        self.button = GlyphButton(
            close_glyph if by_hand else eject_glyph,
            "Take this folder off the list" if by_hand else f"Eject “{name}”",
        )
        self.button.clicked.connect(
            lambda: (self.remove_requested if by_hand else self.eject_requested).emit(
                path
            )
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 8, 6)
        layout.setSpacing(10)
        layout.addWidget(self.icon)
        layout.addLayout(words, 1)
        layout.addWidget(self.button)
        self.setToolTip(str(path))
        theme.notifier.changed.connect(self._restyle)
        self._restyle()

    def set_selected(self, selected: bool) -> None:
        """A picked row has the list's selection colour behind it: text and glyphs follow."""
        self._selected = selected
        self.icon.set_selected(selected)
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
    message = Signal(str)  # for the status bar

    def __init__(
        self,
        volumes: Callable[[], list[Path]] | None = None,
        parent: QWidget | None = None,
        watch: Sequence[Path] = (),
    ):
        super().__init__(parent)
        self._volumes = volumes  # None: the drives that are plugged in
        self._chosen: list[Path] = []  # folders picked by hand, kept for the session
        self._found: list[Path] = []  # the cards plugged in, as last looked for
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
        head = QHBoxLayout()
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.refresh_button)
        layout.addLayout(head)
        self.subtitle = QLabel()
        self.subtitle.setObjectName("mutedLabel")
        layout.addWidget(self.subtitle)
        self.cards = QListWidget()
        self.cards.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.cards.currentItemChanged.connect(self._picked)
        self.cards.itemSelectionChanged.connect(self._restyle_rows)
        layout.addWidget(self.cards, 1)
        self.hint = QLabel(
            "Plug the CDJ's SD card into this Mac: it appears here by itself, found by its "
            "PIONEER folder (click Refresh if it doesn't).\n\nA copy of a card's folder, or a "
            "disk image of a card, can be chosen by hand."
        )
        self.hint.setObjectName("mutedLabel")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
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
        self._watch_folders()

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
        then), unless `announce` is False and the caller asks `current_card()` itself."""
        before = self.current_card()
        found = cc.find_cards((self._volumes or mounted_volumes)())
        self._found = found
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
        self.cards.blockSignals(False)
        self._restyle_rows()
        n = len(found)
        self.subtitle.setText(
            "No card found" if not n else f"{n} card{'s' if n != 1 else ''} found"
        )
        self.hint.setVisible(not shown)
        if announce and self.current_card() != before:
            self.card_changed.emit(self.current_card())

    def _restyle_rows(self) -> None:
        for number in range(self.cards.count()):
            item = self.cards.item(number)
            row = self.cards.itemWidget(item)
            if row is not None:
                row.set_selected(item.isSelected())
                row.button.setEnabled(not (self._busy or self._ejecting))

    # -- ejecting and removing -----------------------------------------------------

    def set_busy(self, busy: bool) -> None:
        """A sync is writing to the card: its eject button waits."""
        self._busy = busy
        self._restyle_rows()

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
            self.message.emit(f"{text} Eject it in Finder before taking it out.")
            self.refresh()

        run_async(work, done, failed)

    def _remove(self, path: Path) -> None:
        """Take a folder chosen by hand off the list (the folder itself is not touched)."""
        if path in self._chosen:
            self._chosen.remove(path)
        self.refresh()

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
        self._card_path: Path | None = None
        self._card: tuple[cc.IndexFile, Mapping[str, cc.TrackFile | None]] | None = None
        self._card_note = ""  # why the card couldn't be read, if it couldn't
        self._surveys: dict[int, DiscSurvey] = {}
        self._unmeasured: list[Track] = []
        self._serial = 0  # a newer refresh makes an older one's result stale
        self._measuring = False
        self._ticking = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        title = QLabel("Dubcrate CDs")
        title.setObjectName("panelTitle")
        self.clear_button = QPushButton("Clear selection")
        self.clear_button.setFlat(True)
        self.clear_button.clicked.connect(
            lambda: self._tick_all(Qt.CheckState.Unchecked)
        )
        head = QHBoxLayout()
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.clear_button)
        layout.addLayout(head)
        self.subtitle = QLabel()
        self.subtitle.setObjectName("mutedLabel")
        layout.addWidget(self.subtitle)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search discs")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Disc", "Tracks", "Status", ""])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_TRACKS, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_TICK, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(COL_TICK, 40)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.currentItemChanged.connect(lambda *_: self._show_detail())
        layout.addWidget(self.tree, 1)

        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMinimumHeight(150)
        self.detail.setMaximumHeight(220)
        layout.addWidget(self.detail)

        self.footer_title = QLabel()
        self.footer_note = QLabel()
        self.footer_note.setObjectName("mutedLabel")
        self.footer_note.setWordWrap(True)
        words = QVBoxLayout()
        words.setSpacing(0)
        words.addWidget(self.footer_title)
        words.addWidget(self.footer_note)
        self.measure_button = QPushButton("Measure Lengths")
        self.measure_button.setToolTip(
            "A card entry is the disc's table of contents, which needs every track's exact length "
            "on the CD (a burn measures them too). Reads the files; changes nothing."
        )
        self.measure_button.clicked.connect(self.measure_lengths)
        self.sync_button = QPushButton("Sync")
        self.sync_button.clicked.connect(self._on_sync)
        self.eject_check = QCheckBox("Eject after syncing")
        self.eject_check.setChecked(settings.get_sync_eject())
        self.eject_check.toggled.connect(settings.set_sync_eject)
        foot = QHBoxLayout()
        foot.addLayout(words, 1)
        foot.addWidget(self.eject_check)
        foot.addWidget(self.measure_button)
        foot.addWidget(self.sync_button)
        layout.addLayout(foot)
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
        def load(path: Path) -> Mapping[str, Sequence[Cue]]:
            found = loaded.get(path)
            if not isinstance(found, Mapping):
                raise RekordboxError(found or "it wasn't read")
            return found

        surveys = survey_discs(
            self._lib,
            self._card,
            RekordboxCues(self._lib, load=load),
            disc_paths(self._lib),
        )
        self._surveys = {s.disc.id: s for s in surveys}
        self._unmeasured = self._tracks_to_measure(surveys)
        self._fill()

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
                        survey.status,
                    ],
                )
                item.setData(COL_NAME, Qt.ItemDataRole.UserRole, survey.disc.id)
                item.setIcon(COL_NAME, _disc_icon("audio"))
                item.setToolTip(COL_STATUS, "\n".join(survey.detail()))
                item.setTextAlignment(
                    COL_TRACKS,
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                )
                if survey.input.problems:
                    item.setForeground(COL_STATUS, self.palette().placeholderText())
                if survey.writable:
                    item.setCheckState(
                        COL_TICK,
                        Qt.CheckState.Checked
                        if survey.disc.id in ticked
                        else Qt.CheckState.Unchecked,
                    )

        self._ticking = True
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
        return item.data(COL_TICK, Qt.ItemDataRole.CheckStateRole) is not None

    def _update_folder(self, folder: QTreeWidgetItem) -> None:
        """A folder shows ticked, unticked or partly, from the discs inside it (none: no box)."""
        states = [
            i.checkState(COL_TICK)
            for i in self._walk(folder)
            if self._id(i) is not None and self._tickable(i)
        ]
        if not states:
            return
        folder.setCheckState(
            COL_TICK,
            Qt.CheckState.Checked
            if all(s == Qt.CheckState.Checked for s in states)
            else Qt.CheckState.Unchecked
            if all(s == Qt.CheckState.Unchecked for s in states)
            else Qt.CheckState.PartiallyChecked,
        )

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        """A tick on a folder ticks every disc in it; a tick on a disc brings its folders up to date."""
        if self._ticking or column != COL_TICK:
            return
        self._ticking = True
        try:
            if self._id(item) is None:
                state = item.checkState(COL_TICK)
                if state != Qt.CheckState.PartiallyChecked:
                    for disc in self._walk(item):
                        if self._id(disc) is not None and self._tickable(disc):
                            disc.setCheckState(COL_TICK, state)
            for folder in self._folder_items():
                self._update_folder(folder)
        finally:
            self._ticking = False
        self._update_buttons()

    def _tick_all(self, state: Qt.CheckState) -> None:
        self._ticking = True
        for item in self._disc_items():
            if self._tickable(item):
                item.setCheckState(COL_TICK, state)
        for folder in self._folder_items():
            self._update_folder(folder)
        self._ticking = False
        self._update_buttons()

    def ticked_ids(self) -> list[int]:
        """The ticked discs' ids, in the order listed."""
        return [
            self._id(i)
            for i in self._disc_items()
            if self._tickable(i)
            and i.checkState(COL_TICK) == Qt.CheckState.Checked
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
        survey = self._surveys.get(self._current_disc_id() or -1)
        if survey is not None:
            self.detail.setPlainText("\n".join(survey.detail()))
            return
        if not self._surveys:
            self.detail.setPlainText(
                "No audio discs yet. Make one, or open the Library, and it is listed here."
            )
        else:
            self.detail.setPlainText(
                "Pick a disc to see what a sync would do for it.\n\n"
                "Every rekordbox cue (memory cues, hot cues and loops) becomes a memory cue on the "
                "card. The player's hot cues A, B and C are never touched, and cues already on "
                "the card are kept."
            )

    def _update_buttons(self) -> None:
        writable = [s for s in self._surveys.values() if s.writable]
        ticked = [self._surveys[i] for i in self.ticked_ids()]
        if not self._measuring and not self._writing:  # (which have their own to show)
            self.subtitle.setText(
                f"{len(ticked)} of {len(writable)} disc{'s' if len(writable) != 1 else ''} selected"
                if self._surveys
                else "No audio discs"
            )
        self.clear_button.setEnabled(bool(ticked))
        busy = self._measuring or self._writing
        self.measure_button.setEnabled(bool(self._unmeasured) and not busy)
        card = self._card_path.name if self._card_path else ""
        if self._card is None:
            self.footer_title.setText("No card")
            self.footer_note.setText(self._card_note or "Choose a card on the left.")
        else:
            cues = sum(s.to_add for s in ticked)
            self.footer_title.setText(
                f"{len(ticked)} disc{'s' if len(ticked) != 1 else ''} → {card or 'the card'}"
            )
            self.footer_note.setText(
                f"{cues} cue{'s' if cues != 1 else ''} to add."
                if ticked
                else "Tick the discs to sync."
            )
        self.sync_button.setEnabled(
            WRITING_ENABLED
            and bool(ticked)
            and self._card is not None
            and not self._writing
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

    def _set_writing(self, writing: bool) -> None:
        """A sync is (or is no longer) writing and ejecting: the card list must leave the card alone."""
        self._writing = writing
        self.busy_changed.emit(writing)

    def _on_sync(self) -> None:
        """Ask, then plan the card fresh, copy its files aside and write (on a worker); then, if
        asked, eject the card."""
        card = self._card_path
        if not WRITING_ENABLED or self._writing or card is None or self._card is None:
            return
        surveys = [self._surveys[i] for i in self.ticked_ids()]
        if not surveys:
            return
        eject = self.eject_check.isChecked() and self.eject_check.isEnabled()
        text = confirmation_text(
            surveys, card.name or str(card), self._backup_dir, eject
        )
        if not ask_sync_confirmation(self, text):
            return
        requests = [s.input.request for s in surveys if s.input.request is not None]
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
            if outcome.wrote:
                n = len(outcome.written)
                said = (
                    f"Wrote {n} disc file{'s' if n != 1 else ''} to “{card.name}”; its old files "
                    f"are copied to {outcome.backup}."
                )
            else:
                said = "Nothing to write: the card already has these cues."
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
            self.message.emit(f"{said} {text} Eject it in Finder before taking it out.")
            self.refresh()

        run_async(work, done, failed)

    # -- measuring lengths -------------------------------------------------------------

    def measure_lengths(self) -> None:
        """Probe the tracks that have no CD length yet (on a worker), store them, and look again."""
        todo = self._unmeasured
        if not todo or self._measuring:
            return
        self._measuring = True
        self._update_buttons()
        self.subtitle.setText(f"Measuring {len(todo)} tracks…")

        def done(result: tuple[dict[int, int], list[str]]) -> None:
            lengths, problems = result
            for track_id, sectors in lengths.items():
                self._lib.set_cd_sectors(track_id, sectors)
            self._measuring = False
            if problems:
                self.message.emit(
                    f"Couldn't measure {len(problems)} file(s): {problems[0]}"
                )
            self.refresh()

        def failed(text: str) -> None:
            self._measuring = False
            self.message.emit(f"Couldn't measure the tracks: {text}")
            self._update_buttons()

        run_async(
            lambda report: probe_lengths(todo, report),
            done,
            failed,
            on_progress=lambda n, total: self.subtitle.setText(
                f"Measuring lengths: {n} of {total}"
            ),
        )
