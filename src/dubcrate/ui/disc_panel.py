"""The disc being planned: pick or create a disc, see its tracks and how full it is."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from dubcrate import settings
from dubcrate.cd import DISC_74_SECTORS, DISC_80_SECTORS, SECTORS_PER_SECOND
from dubcrate.library import Library
from dubcrate.library.lengths import needs_measuring, probe_lengths
from dubcrate.library.store import LibraryError

from .capacity_bar import CapacitySummary
from .models import DiscModel
from .tasks import run_async

SIZES = ((80, DISC_80_SECTORS), (74, DISC_74_SECTORS))
GAPS = ((0, "Gapless"), (1, "1 s"), (2, "2 s"), (3, "3 s"))


class DiscTable(QTableView):
    """The disc's track list: reorder by dragging, remove with Delete."""

    remove_requested = Signal()

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
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

    def dropEvent(self, event) -> None:
        if event.source() is self:
            # The model refuses to delete rows, but don't ask: a reorder is a copy as far as Qt cares.
            event.setDropAction(Qt.DropAction.CopyAction)
        super().dropEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.remove_requested.emit()
            return
        super().keyPressEvent(event)


class DiscPanel(QWidget):
    changed = Signal()  # the current disc, or what is on it, changed
    busy = Signal(bool, str)  # (working, what it is doing)
    progress = Signal(int, int)
    message = Signal(str)

    def __init__(self, lib: Library, parent=None):
        super().__init__(parent)
        self._lib = lib
        self._measuring = False

        title = QLabel("Disc")
        title.setObjectName("panelTitle")

        self.combo = QComboBox()
        self.combo.activated.connect(self._on_disc_chosen)
        self.new_button = QPushButton("New")
        self.new_button.clicked.connect(self.new_disc)
        self.menu_button = QToolButton()
        self.menu_button.setText("⋯")
        self.menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.menu_button)
        self.rename_action = menu.addAction("Rename…", self.rename_disc)
        self.delete_action = menu.addAction("Delete…", self.delete_disc)
        self.menu_button.setMenu(menu)

        picker = QHBoxLayout()
        picker.addWidget(self.combo, 1)
        picker.addWidget(self.new_button)
        picker.addWidget(self.menu_button)

        self.size_combo = QComboBox()
        for minutes, sectors in SIZES:
            self.size_combo.addItem(f"{minutes} min", sectors)
        self.size_combo.activated.connect(self._on_size_chosen)
        self.gap_combo = QComboBox()
        for seconds, label in GAPS:
            self.gap_combo.addItem(label, seconds * SECTORS_PER_SECOND)
        self.gap_combo.activated.connect(self._on_gap_chosen)
        self.settings_row = QWidget()
        row = QHBoxLayout(self.settings_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel("Size"))
        row.addWidget(self.size_combo)
        row.addSpacing(10)
        row.addWidget(QLabel("Between tracks"))
        row.addWidget(self.gap_combo)
        row.addStretch(1)

        self.model = DiscModel(self)
        self.model.tracks_dropped.connect(
            lambda ids, row: self.add_tracks(ids, position=row)
        )
        self.model.rows_moved.connect(self._move_rows)
        self.table = DiscTable()
        self.table.setModel(self.model)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in ((0, 58), (1, 50), (2, 120), (3, 150), (4, 52), (5, 46)):
            self.table.setColumnWidth(column, width)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.remove_requested.connect(self.remove_selected)
        self.table.selectionModel().selectionChanged.connect(
            lambda *_: self._update_buttons()
        )

        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove_selected)
        buttons = QHBoxLayout()
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)

        self.summary = CapacitySummary()

        full = QWidget()
        full_layout = QVBoxLayout(full)
        full_layout.setContentsMargins(0, 0, 0, 0)
        full_layout.setSpacing(8)
        full_layout.addWidget(self.settings_row)
        full_layout.addWidget(self.table, 1)
        full_layout.addLayout(buttons)
        full_layout.addWidget(self.summary)

        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_label = QLabel("No disc yet.\nCreate one, then drag tracks onto it.")
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
        layout.addWidget(title)
        layout.addLayout(picker)
        layout.addWidget(self.stack, 1)

        self.reload()

    # -- state ------------------------------------------------------------

    def current_disc_id(self) -> int | None:
        return self.combo.currentData() if self.combo.count() else None

    def current_track_ids(self) -> set[int]:
        disc_id = self.current_disc_id()
        if disc_id is None:
            return set()
        return {e.track.id for e in self._lib.disc_entries(disc_id)}

    def reload(self, select_id: int | None = None) -> None:
        """Re-read the list of discs and show `select_id` (else the last one used, else the first)."""
        wanted = (
            select_id
            if select_id is not None
            else (self.current_disc_id() or settings.get_last_disc())
        )
        self.combo.blockSignals(True)
        self.combo.clear()
        for summary in self._lib.discs():
            self.combo.addItem(
                self._label(summary.name, summary.track_count), summary.id
            )
        index = self.combo.findData(wanted) if wanted is not None else -1
        self.combo.setCurrentIndex(max(index, 0))
        self.combo.blockSignals(False)
        self._show_disc()

    @staticmethod
    def _label(name: str, count: int) -> str:
        return f"{name}  ({count})"

    def _show_disc(self) -> None:
        disc_id = self.current_disc_id()
        has_disc = disc_id is not None
        self.rename_action.setEnabled(has_disc)
        self.delete_action.setEnabled(has_disc)
        self.stack.setCurrentIndex(1 if has_disc else 0)
        if has_disc:
            summary = self._lib.disc(disc_id)
            entries = self._lib.disc_entries(disc_id)
            self.model.set_entries(entries)
            self.summary.show_disc(summary, entries)
            self.combo.setItemText(
                self.combo.currentIndex(),
                self._label(summary.name, summary.track_count),
            )
            for combo, value in (
                (self.size_combo, summary.capacity_sectors),
                (self.gap_combo, summary.pregap_sectors),
            ):
                combo.blockSignals(True)
                combo.setCurrentIndex(max(0, combo.findData(value)))
                combo.blockSignals(False)
            settings.set_last_disc(disc_id)
        else:
            self.model.set_entries([])
            self.summary.clear()
        self._update_buttons()
        self.changed.emit()

    def _update_buttons(self) -> None:
        self.remove_button.setEnabled(bool(self.table.selectionModel().selectedRows()))

    # -- the disc itself --------------------------------------------------

    def _warn(self, title: str, text: str) -> None:
        QMessageBox.warning(self, title, text)

    def _ask_name(self, title: str, label: str, default: str) -> str | None:
        name, ok = QInputDialog.getText(self, title, label, text=default)
        name = name.strip()
        return name if ok and name else None

    def new_disc(self) -> int | None:
        name = self._ask_name("New Disc", "Name:", f"Disc {len(self._lib.discs()) + 1}")
        if name is None:
            return None
        disc_id = self._create(name)
        self.reload(select_id=disc_id)
        return disc_id

    def _create(self, name: str) -> int:
        """A new disc with the size and gap currently chosen in the panel."""
        return self._lib.create_disc(
            name,
            capacity_sectors=self.size_combo.currentData(),
            pregap_sectors=self.gap_combo.currentData(),
        )

    def rename_disc(self) -> None:
        disc_id = self.current_disc_id()
        if disc_id is None:
            return
        name = self._ask_name("Rename Disc", "Name:", self._lib.disc(disc_id).name)
        if name:
            self._lib.update_disc(disc_id, name=name)
            self.reload(select_id=disc_id)

    def delete_disc(self) -> None:
        disc_id = self.current_disc_id()
        if disc_id is None:
            return
        name = self._lib.disc(disc_id).name
        answer = QMessageBox.question(
            self,
            "Delete Disc",
            f'Delete "{name}"?\nThe tracks stay in your library; only this plan is removed.',
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._lib.delete_disc(disc_id)
            self.reload()

    def _on_disc_chosen(self, _index: int) -> None:
        self._show_disc()

    def _on_size_chosen(self, _index: int) -> None:
        disc_id = self.current_disc_id()
        if disc_id is not None:
            self._lib.update_disc(
                disc_id, capacity_sectors=self.size_combo.currentData()
            )
            self._show_disc()

    def _on_gap_chosen(self, _index: int) -> None:
        disc_id = self.current_disc_id()
        if disc_id is not None:
            self._lib.update_disc(disc_id, pregap_sectors=self.gap_combo.currentData())
            self._show_disc()

    # -- tracks on the disc -----------------------------------------------

    def remove_selected(self) -> None:
        disc_id = self.current_disc_id()
        rows = sorted(i.row() for i in self.table.selectionModel().selectedRows())
        if disc_id is None or not rows:
            return
        self._lib.remove_from_disc(
            disc_id, [self.model.entry_at(r).position for r in rows]
        )
        self._show_disc()

    def _move_rows(self, rows: list[int], target: int) -> None:
        disc_id = self.current_disc_id()
        if disc_id is None:
            return
        try:
            self._lib.reorder_disc(disc_id, rows, target)
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

    def add_tracks(
        self,
        track_ids: list[int],
        position: int | None = None,
        *,
        fit: bool = False,
        strict: bool = False,
    ) -> None:
        """Add tracks to the current disc (creating one if there is none yet)."""
        if not track_ids:
            return

        def add() -> None:
            disc_id = self.current_disc_id()
            if disc_id is None:
                disc_id = self._create("Disc 1")
            try:
                result = self._lib.add_to_disc(
                    disc_id, track_ids, position=position, fit=fit, strict=strict
                )
            except LibraryError as exc:
                self._warn("Can't add those tracks", str(exc))
                return
            self.reload(select_id=disc_id)
            text = f"Added {len(result.added)} track{'s' if len(result.added) != 1 else ''}"
            if result.rejected:
                text += f"; {len(result.rejected)} didn't fit"
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
                capacity_sectors=self.size_combo.currentData(),
                pregap_sectors=self.gap_combo.currentData(),
            )
            self.reload(select_id=result.disc_ids[0] if result.disc_ids else None)
            text = f"Created {len(result.disc_ids)} disc{'s' if len(result.disc_ids) != 1 else ''}"
            if result.too_long:
                text += f"; skipped {len(result.too_long)} track{'s' if len(result.too_long) != 1 else ''} longer than a disc"
            self.message.emit(text)

        self._measured(track_ids, go)
