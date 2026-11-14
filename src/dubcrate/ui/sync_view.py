"""The Sync tab: the CDJ-1000MK3's SD card on the left, your audio discs in the middle.

Read-only so far. It finds the card, lists Dubcrate's audio discs with what a sync would do for
each (`card_discs.survey_discs`: not on the card, cues to add, in sync, or why it can't be synced),
and shows that dry run in words for the disc you pick. Ticking discs sums up what Sync would do;
**the Sync button is there but off** (`WRITING_ENABLED`): no card file written by Dubcrate has been
loaded by a player yet (`dev_docs/card-sync.md`, Phase 0). Nothing here writes to a card.

The library belongs to the GUI thread, so reading rekordbox's database and probing track lengths
(the only slow parts) run on workers and the discs are looked at on the GUI thread afterwards.
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dubcrate import cdj_card as cc
from dubcrate.analysis import Cue
from dubcrate.card_discs import DiscSurvey, RekordboxCues, survey_discs
from dubcrate.library import Library
from dubcrate.library.lengths import needs_measuring, probe_lengths
from dubcrate.library.models import Track
from dubcrate.library.rekordbox import RekordboxError, read_cues

from .explorer import mounted_volumes
from .label_dialog import disc_paths
from .models import _disc_icon, _folder_icon
from .tasks import run_async

log = logging.getLogger(__name__)

# Flip this once the player has loaded a card file we wrote (dev_docs/card-sync.md, Phase 0).
WRITING_ENABLED = False
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


class CardPanel(QWidget):
    """The left side: the cards that are plugged in (and folders chosen by hand), one picked."""

    card_changed = Signal(object)  # the card's folder (a Path), or None

    def __init__(
        self,
        volumes: Callable[[], list[Path]] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._volumes = volumes  # None: the drives that are plugged in
        self._chosen: list[Path] = []  # folders picked by hand, kept for the session
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
        self.cards.currentItemChanged.connect(self._picked)
        layout.addWidget(self.cards, 1)
        self.hint = QLabel(
            "Plug the CDJ's SD card into this Mac, then click Refresh: it is found by its "
            "PIONEER folder.\n\nA copy of a card's folder, or a disk image of a card, can be "
            "chosen by hand."
        )
        self.hint.setObjectName("mutedLabel")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.choose_button = QPushButton("Choose Folder…")
        self.choose_button.clicked.connect(self.choose_folder)
        layout.addWidget(self.choose_button)
        self.subtitle.setText(
            "No card found"
        )  # (the drives are looked at when the tab opens)

    def current_card(self) -> Path | None:
        item = self.cards.currentItem()
        return None if item is None else Path(item.data(Qt.ItemDataRole.UserRole))

    def refresh(self, announce: bool = True) -> None:
        """Look again for cards. `card_changed` is emitted if the picked card changed (and only
        then), unless `announce` is False and the caller asks `current_card()` itself."""
        before = self.current_card()
        found = cc.find_cards((self._volumes or mounted_volumes)())
        shown = [*found, *(p for p in self._chosen if p not in found)]
        self.cards.blockSignals(True)
        self.cards.clear()
        for path in shown:
            by_hand = path not in found
            item = QListWidgetItem(
                f"{path.name or path}  (folder)" if by_hand else path.name or str(path)
            )
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self.cards.addItem(item)
            if path == before:
                self.cards.setCurrentItem(item)
        if self.cards.currentItem() is None and shown:
            self.cards.setCurrentRow(0)
        self.cards.blockSignals(False)
        n = len(found)
        self.subtitle.setText(
            "No card found" if not n else f"{n} card{'s' if n != 1 else ''} found"
        )
        self.hint.setVisible(not shown)
        if announce and self.current_card() != before:
            self.card_changed.emit(self.current_card())

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
    sync_requested = Signal(list)  # disc ids ticked (only while `WRITING_ENABLED`)

    def __init__(self, lib: Library, parent: QWidget | None = None):
        super().__init__(parent)
        self._lib = lib
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
        foot = QHBoxLayout()
        foot.addLayout(words, 1)
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
        if not self._measuring:  # (which has its own progress to show)
            self.subtitle.setText(
                f"{len(ticked)} of {len(writable)} disc{'s' if len(writable) != 1 else ''} selected"
                if self._surveys
                else "No audio discs"
            )
        self.clear_button.setEnabled(bool(ticked))
        self.measure_button.setEnabled(bool(self._unmeasured) and not self._measuring)
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
            WRITING_ENABLED and bool(ticked) and self._card is not None
        )
        self.sync_button.setToolTip("" if WRITING_ENABLED else WAITING_NOTE)

    def _on_sync(self) -> None:
        if WRITING_ENABLED:
            self.sync_requested.emit(self.ticked_ids())

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
