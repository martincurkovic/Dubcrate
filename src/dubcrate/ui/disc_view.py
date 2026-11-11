"""A disc, in the middle of the window: its tracks as they will be written on the CD, how
full it is, and everything for managing discs and the folders they are filed in.

A disc is just a playlist. Opening one (clicking it in the tree) shows it here and makes it
the *target*: the disc that tracks are added to (Return, the Add button, or dragging onto it).
The target stays the same while you browse your library, so you can pick tracks and add them.
"""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from dubcrate import settings
from dubcrate.cd import SECTORS_PER_SECOND, format_clock
from dubcrate.library import Library
from dubcrate.library.lengths import needs_measuring, probe_lengths
from dubcrate.library.store import LibraryError

from .capacity_bar import CapacitySummary
from .models import DiscModel
from .overlaps import overlap_message
from .tasks import run_async
from .track_table import transport_action


class DiscTable(QTableView):
    """The disc's track list: reorder by dragging, remove with Delete, edit the CD text."""

    remove_requested = Signal()
    play_pause_requested = Signal()
    beat_jump_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.verticalHeader().hide()
        # Double-click (or the edit key) edits the artist and title written on the CD.
        self.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )

    def dropEvent(self, event) -> None:
        if event.source() is self:
            # The model refuses to delete rows, but don't ask: a reorder is a copy as far as Qt cares.
            event.setDropAction(Qt.DropAction.CopyAction)
        super().dropEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.remove_requested.emit()
            return
        action = transport_action(event)
        if action == "play_pause":
            self.play_pause_requested.emit()
            return
        if action in ("jump_back", "jump_forward"):
            self.beat_jump_requested.emit(-1 if action == "jump_back" else 1)
            return
        super().keyPressEvent(event)


class DiscView(QWidget):
    changed = Signal()  # the disc being shown, or what is on it, changed
    discs_changed = Signal()  # a disc or folder was made, renamed, moved or deleted
    opened = Signal(int)  # a disc was made or copied, and should be opened in the tree
    busy = Signal(bool, str)  # (working, what it is doing)
    progress = Signal(int, int)
    message = Signal(str)
    burn_requested = Signal()
    preview_requested = Signal(object)  # a Track to listen to

    def __init__(self, lib: Library, parent=None):
        super().__init__(parent)
        self._lib = lib
        self._measuring = False
        self._disc_id: int | None = None

        self.title = QLabel()
        self.title.setObjectName("panelTitle")
        self.count = QLabel()
        self.count.setObjectName("mutedLabel")
        head = QHBoxLayout()
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.count)

        self.model = DiscModel(self)
        self.model.tracks_dropped.connect(
            lambda ids, row: self.add_tracks(ids, position=row)
        )
        self.model.rows_moved.connect(self._move_rows)
        self.model.text_edited.connect(self._edit_text)
        self.table = DiscTable()
        self.table.setModel(self.model)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in ((0, 52), (1, 54)):
            self.table.setColumnWidth(column, width)
        # the artist and the title share what is left: both are text that wants room
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.remove_requested.connect(self.remove_selected)
        self.table.doubleClicked.connect(self._on_double_clicked)
        self.table.selectionModel().selectionChanged.connect(
            lambda *_: self._update_buttons()
        )

        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove_selected)
        self.burn_button = QPushButton("Burn…")
        self.burn_button.setToolTip("Burn this disc to a blank CD-R")
        self.burn_button.clicked.connect(self.burn_requested)
        buttons = QHBoxLayout()
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)
        buttons.addWidget(self.burn_button)

        self.summary = CapacitySummary()

        full = QWidget()
        full_layout = QVBoxLayout(full)
        full_layout.setContentsMargins(0, 0, 0, 0)
        full_layout.setSpacing(8)
        full_layout.addWidget(self.table, 1)
        full_layout.addLayout(buttons)
        full_layout.addWidget(self.summary)

        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_label = QLabel(
            "No disc open.\nChoose one in the Discs list on the right,\nor make one with Disc > New Disc."
        )
        empty_label.setObjectName("emptyState")
        empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addStretch(1)
        empty_layout.addWidget(empty_label)
        empty_layout.addStretch(2)

        self.stack = QStackedWidget()
        self.stack.addWidget(empty)
        self.stack.addWidget(full)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addLayout(head)
        layout.addWidget(self.stack, 1)

        last = settings.get_last_disc()
        self._disc_id = last if last in {d.id for d in self._lib.discs()} else None
        self._show_disc()

    # -- state ------------------------------------------------------------

    def current_disc_id(self) -> int | None:
        """The disc shown here, which is also the one tracks are added to."""
        return self._disc_id

    def current_track_ids(self) -> set[int]:
        if self._disc_id is None:
            return set()
        return {e.track.id for e in self._lib.disc_entries(self._disc_id)}

    def set_disc(self, disc_id: int | None) -> None:
        """Open a disc: show it, and make it the one that tracks are added to."""
        self._disc_id = disc_id
        self._show_disc()

    def reload(self) -> None:
        """Re-read the disc being shown (it may have been changed or deleted)."""
        if self._disc_id is not None and self._disc_id not in {
            d.id for d in self._lib.discs()
        }:
            self._disc_id = None
        self._show_disc()

    def _show_disc(self) -> None:
        disc_id = self._disc_id
        has_disc = disc_id is not None
        self.burn_button.setEnabled(has_disc)
        self.stack.setCurrentIndex(1 if has_disc else 0)
        if has_disc:
            summary = self._lib.disc(disc_id)
            entries = self._lib.disc_entries(disc_id)
            self.model.set_entries(entries)
            self.summary.show_disc(summary, entries)
            self.title.setText(summary.name)
            n = summary.track_count
            self.count.setText(
                f"{n} track{'s' if n != 1 else ''}  ·  {format_clock(summary.used_sectors)}"
                f" of {format_clock(summary.capacity_sectors)}"
            )
            settings.set_last_disc(disc_id)
        else:
            self.model.set_entries([])
            self.summary.clear()
            self.title.setText("")
            self.count.setText("")
        self._update_buttons()
        self.changed.emit()

    def _update_buttons(self) -> None:
        self.remove_button.setEnabled(bool(self.table.selectionModel().selectedRows()))

    def _on_double_clicked(self, index) -> None:
        """Double-clicking the artist or title edits it; anywhere else listens to the track."""
        if index.column() in (0, 1):
            self.preview_requested.emit(self.model.entry_at(index.row()).track)

    # -- discs and folders ------------------------------------------------

    def _warn(self, title: str, text: str) -> None:
        QMessageBox.warning(self, title, text)

    def _ask_name(self, title: str, label: str, default: str) -> str | None:
        name, ok = QInputDialog.getText(self, title, label, text=default)
        name = name.strip()
        return name if ok and name else None

    def _create(self, name: str, folder_id: int | None = None) -> int:
        """A new disc with the size and gap chosen in Settings."""
        return self._lib.create_disc(
            name,
            capacity_sectors=settings.get_disc_minutes() * 60 * SECTORS_PER_SECOND,
            pregap_sectors=settings.get_pregap_seconds() * SECTORS_PER_SECOND,
            folder_id=folder_id,
        )

    def new_disc(self, folder_id: int | None = None) -> int | None:
        """Ask for a name and make a disc (in `folder_id`), and open it."""
        name = self._ask_name("New Disc", "Name:", f"Disc {len(self._lib.discs()) + 1}")
        if name is None:
            return None
        disc_id = self._create(name, folder_id)
        self.set_disc(disc_id)
        self.discs_changed.emit()
        self.opened.emit(disc_id)
        return disc_id

    def rename_disc(self, disc_id: int | None = None) -> None:
        disc_id = self._disc_id if disc_id is None else disc_id
        if disc_id is None:
            return
        name = self._ask_name("Rename Disc", "Name:", self._lib.disc(disc_id).name)
        if name:
            self._lib.update_disc(disc_id, name=name)
            self._show_disc() if disc_id == self._disc_id else None
            self.discs_changed.emit()

    def delete_disc(self, disc_id: int | None = None) -> None:
        disc_id = self._disc_id if disc_id is None else disc_id
        if disc_id is None:
            return
        name = self._lib.disc(disc_id).name
        answer = QMessageBox.question(
            self,
            "Delete Disc",
            f'Delete "{name}"?\nThe tracks stay in your library; only this disc is removed.',
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._lib.delete_disc(disc_id)
            self.reload()
            self.discs_changed.emit()

    def duplicate_disc(self, disc_id: int | None = None) -> int | None:
        """Make a copy of a disc (to re-burn, or to change), and open it."""
        disc_id = self._disc_id if disc_id is None else disc_id
        if disc_id is None:
            return None
        copy = self._lib.duplicate_disc(disc_id)
        self.set_disc(copy)
        self.discs_changed.emit()
        self.opened.emit(copy)
        self.message.emit(f"Made “{self._lib.disc(copy).name}”")
        return copy

    def new_folder(self, parent_id: int | None = None) -> int | None:
        name = self._ask_name("New Folder", "Name:", "New Folder")
        if name is None:
            return None
        folder_id = self._lib.create_disc_folder(name, parent_id)
        self.discs_changed.emit()
        return folder_id

    def rename_folder(self, folder_id: int) -> None:
        current = next(f for f in self._lib.disc_folders() if f.id == folder_id).name
        name = self._ask_name("Rename Folder", "Name:", current)
        if name:
            self._lib.rename_disc_folder(folder_id, name)
            self.discs_changed.emit()

    def delete_folder(self, folder_id: int) -> None:
        name = next(f for f in self._lib.disc_folders() if f.id == folder_id).name
        answer = QMessageBox.question(
            self,
            "Delete Folder",
            f'Delete the folder "{name}"?\n'
            "The discs and folders in it move up a level. No disc is deleted.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._lib.delete_disc_folder(folder_id)
            self.discs_changed.emit()

    def move_items(self, items: list[tuple], folder_id: int | None) -> None:
        """File discs and folders (as dragged in the tree) into a folder, or the top level."""
        try:
            for item in items:
                if item[0] == "disc":
                    self._lib.move_disc(item[1], folder_id)
                elif item[0] == "disc_folder":
                    self._lib.move_disc_folder(item[1], folder_id)
        except LibraryError as exc:
            self._warn("Can't move that", str(exc))
        self.discs_changed.emit()

    # -- tracks on the disc -----------------------------------------------

    def remove_selected(self) -> None:
        rows = sorted(i.row() for i in self.table.selectionModel().selectedRows())
        if self._disc_id is None or not rows:
            return
        self._lib.remove_from_disc(
            self._disc_id, [self.model.entry_at(r).position for r in rows]
        )
        self._show_disc()
        self.discs_changed.emit()

    def _edit_text(self, position: int, field: str, text: str) -> None:
        """The user edited an artist or title: change what is written on the CD for this
        disc. Only this disc's list changes: the library, rekordbox and the audio file don't."""
        if self._disc_id is None:
            return
        try:
            self._lib.set_entry_text(self._disc_id, position, field, text)
        except LibraryError as exc:
            self._warn("Can't change that", str(exc))
        # Rebuild once the editor has finished committing: resetting the model inside its
        # own setData would pull the editor out from under Qt.
        QTimer.singleShot(0, self._show_disc)

    def _move_rows(self, rows: list[int], target: int) -> None:
        if self._disc_id is None:
            return
        try:
            self._lib.reorder_disc(self._disc_id, rows, target)
        except LibraryError as exc:
            self._warn("Can't move those tracks", str(exc))
        self._show_disc()

    def _measured(self, track_ids: list[int], then) -> None:
        """Run `then()` once every track in `track_ids` has a measured length.

        Probing happens on a worker thread; its results are stored here, on the
        thread that owns the library.
        """
        if self._measuring:
            self.message.emit("Still measuring the last tracks…")
            return
        todo = [t for t in self._lib.tracks_by_ids(track_ids) if needs_measuring(t)]
        if not todo:
            then()
            return
        self._measuring = True
        self.busy.emit(
            True, f"Measuring {len(todo)} track{'s' if len(todo) != 1 else ''}…"
        )

        def done(result) -> None:
            lengths, problems = result
            for track_id, sectors in lengths.items():
                self._lib.set_cd_sectors(track_id, sectors)
            self._measuring = False
            self.busy.emit(False, "")
            if problems:
                self.message.emit(
                    f"Couldn't read {len(problems)} file{'s' if len(problems) != 1 else ''}"
                )
            then()

        def failed(text: str) -> None:
            self._measuring = False
            self.busy.emit(False, "")
            self._warn("Couldn't measure those tracks", text)

        run_async(
            lambda report: probe_lengths(todo, report),
            done,
            failed,
            on_progress=self.progress.emit,
        )

    def _confirm_overlaps(self, disc_id: int | None, track_ids: list[int]) -> bool:
        """Tell the user which tracks are already on a disc; False if they back out."""
        text = overlap_message(
            self._lib.tracks_by_ids(track_ids),
            self._lib.track_usage(track_ids),
            disc_id,
        )
        if text is None:
            return True
        answer = QMessageBox.question(self, "Already used", text)
        return answer == QMessageBox.StandardButton.Yes

    def add_tracks(
        self,
        track_ids: list[int],
        position: int | None = None,
        *,
        fit: bool = False,
        strict: bool = False,
        disc_id: int | None = None,
    ) -> None:
        """Add tracks to a disc: `disc_id`, else the open one (making one if there is none).

        Adding to a disc by name (a drop onto it in the tree) leaves the open disc as it is.
        """
        if not track_ids:
            return

        def add() -> None:
            target = self._disc_id if disc_id is None else disc_id
            if not self._confirm_overlaps(target, track_ids):
                return
            created = target is None
            if target is None:
                target = self._create("Disc 1")
                self._disc_id = target
            try:
                result = self._lib.add_to_disc(
                    target, track_ids, position=position, fit=fit, strict=strict
                )
            except LibraryError as exc:
                self._warn("Can't add those tracks", str(exc))
                return
            self._show_disc()
            self.discs_changed.emit()  # the counts in the tree, and a new disc's entry
            name = self._lib.disc(target).name
            n = len(result.added)
            text = f"Added {n} track{'s' if n != 1 else ''} to “{name}”"
            if result.rejected:
                text += f"; {len(result.rejected)} didn't fit"
            if created:
                text += " (a new disc)"
            self.message.emit(text)

        self._measured(track_ids, add)

    def split(self, track_ids: list[int], name: str) -> None:
        """Cut `track_ids` (in order) across as many discs as it takes."""
        if not track_ids:
            return

        def go() -> None:
            result = self._lib.split_into_discs(
                track_ids,
                name,
                capacity_sectors=settings.get_disc_minutes() * 60 * SECTORS_PER_SECOND,
                pregap_sectors=settings.get_pregap_seconds() * SECTORS_PER_SECOND,
            )
            if result.disc_ids:
                self.set_disc(result.disc_ids[0])
            self.discs_changed.emit()
            if result.disc_ids:
                self.opened.emit(result.disc_ids[0])
            text = f"Created {len(result.disc_ids)} disc{'s' if len(result.disc_ids) != 1 else ''}"
            if result.too_long:
                text += f"; skipped {len(result.too_long)} track{'s' if len(result.too_long) != 1 else ''} longer than a disc"
            self.message.emit(text)

        self._measured(track_ids, go)
