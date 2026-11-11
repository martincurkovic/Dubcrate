"""The disc being planned (pick or create one, see its tracks and how full it is),
and the discs already burned, shown read-only beside it."""

from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
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
    QTabBar,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from dubcrate import settings
from dubcrate.cd import (
    SECTORS_PER_SECOND,
    format_clock,
)
from dubcrate.library import Library
from dubcrate.library.lengths import needs_measuring, probe_lengths
from dubcrate.library.models import BurnRecord
from dubcrate.library.store import LibraryError

from .capacity_bar import CapacitySummary
from .models import BurnModel, DiscModel
from .overlaps import overlap_message
from .tasks import run_async


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
        super().keyPressEvent(event)


def burn_summary(record: BurnRecord) -> str:
    """Two lines for under the burned-disc picker: when and how, then how full."""
    when = datetime.fromisoformat(record.burned_at).astimezone()
    how = [when.strftime("%Y-%m-%d %H:%M")]
    if record.drive:
        how.append(record.drive)
    if record.speed:
        how.append(f"{record.speed}x")
    n = record.track_count
    return (
        " · ".join(how)
        + f"\n{format_clock(record.used_sectors)} of {format_clock(record.capacity_sectors)}"
        + f" · {n} track{'s' if n != 1 else ''}"
    )


class DiscPanel(QWidget):
    changed = Signal()  # the current disc, or what is on it, changed
    busy = Signal(bool, str)  # (working, what it is doing)
    progress = Signal(int, int)
    message = Signal(str)
    burn_requested = Signal()

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
        empty_label = QLabel("No disc yet.\nCreate one, then drag tracks onto it.")
        empty_label.setObjectName("emptyState")
        empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addStretch(1)
        empty_layout.addWidget(empty_label)
        empty_layout.addStretch(2)

        self.stack = QStackedWidget()
        self.stack.addWidget(empty)
        self.stack.addWidget(full)

        planned = QWidget()
        planned_layout = QVBoxLayout(planned)
        planned_layout.setContentsMargins(0, 0, 0, 0)
        planned_layout.setSpacing(8)
        planned_layout.addLayout(picker)
        planned_layout.addWidget(self.stack, 1)

        self.views = QStackedWidget()
        self.views.addWidget(planned)
        self.views.addWidget(self._build_burned_page())

        self.tabs = QTabBar()
        self.tabs.addTab("Planned")
        self.tabs.addTab("Burned")
        self.tabs.setExpanding(True)
        self.tabs.setDrawBase(False)
        self.tabs.currentChanged.connect(self.views.setCurrentIndex)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(title)
        layout.addWidget(self.tabs)
        layout.addWidget(self.views, 1)

        self.reload()

    def _build_burned_page(self) -> QWidget:
        self.burn_combo = QComboBox()
        self.burn_combo.activated.connect(lambda _i: self._show_burn())
        self.duplicate_button = QPushButton("Duplicate")
        self.duplicate_button.setToolTip("Make an editable copy to tweak or burn again")
        self.duplicate_button.clicked.connect(self.duplicate_burn)
        self.delete_burn_button = QPushButton("Delete")
        self.delete_burn_button.setToolTip(
            "Forget this record (the CD itself is unaffected)"
        )
        self.delete_burn_button.clicked.connect(self.delete_burn)
        picker = QHBoxLayout()
        picker.addWidget(self.burn_combo, 1)
        picker.addWidget(self.duplicate_button)
        picker.addWidget(self.delete_burn_button)

        self.burn_info = QLabel()
        self.burn_model = BurnModel(self)
        self.burn_table = QTableView()
        self.burn_table.setModel(self.burn_model)
        self.burn_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.burn_table.setShowGrid(False)
        self.burn_table.setAlternatingRowColors(True)
        self.burn_table.verticalHeader().hide()
        self.burn_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.burn_table.horizontalHeader()
        header.setStretchLastSection(False)
        for column, width in ((0, 36), (1, 50), (2, 140)):
            self.burn_table.setColumnWidth(column, width)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        full = QWidget()
        full_layout = QVBoxLayout(full)
        full_layout.setContentsMargins(0, 0, 0, 0)
        full_layout.setSpacing(8)
        full_layout.addWidget(self.burn_info)
        full_layout.addWidget(self.burn_table, 1)

        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_label = QLabel(
            "Nothing burned yet.\nA disc you burn is kept here,\nso you can see what went on it."
        )
        empty_label.setObjectName("emptyState")
        empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addStretch(1)
        empty_layout.addWidget(empty_label)
        empty_layout.addStretch(2)

        self.burn_stack = QStackedWidget()
        self.burn_stack.addWidget(empty)
        self.burn_stack.addWidget(full)

        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(8)
        page_layout.addLayout(picker)
        page_layout.addWidget(self.burn_stack, 1)
        return page

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
        self.reload_burns()

    @staticmethod
    def _label(name: str, count: int) -> str:
        return f"{name}  ({count})"

    def _show_disc(self) -> None:
        disc_id = self.current_disc_id()
        has_disc = disc_id is not None
        self.rename_action.setEnabled(has_disc)
        self.delete_action.setEnabled(has_disc)
        self.burn_button.setEnabled(has_disc)
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
            settings.set_last_disc(disc_id)
        else:
            self.model.set_entries([])
            self.summary.clear()
        self._update_buttons()
        self.changed.emit()

    def _update_buttons(self) -> None:
        self.remove_button.setEnabled(bool(self.table.selectionModel().selectedRows()))

    # -- burned discs -----------------------------------------------------

    def current_burn_id(self) -> int | None:
        return self.burn_combo.currentData() if self.burn_combo.count() else None

    def reload_burns(self, select_id: int | None = None) -> None:
        """Re-read the burn records and show `select_id` (else the one already shown, else the newest)."""
        wanted = select_id if select_id is not None else self.current_burn_id()
        self.burn_combo.blockSignals(True)
        self.burn_combo.clear()
        for record in self._lib.burns():
            when = record.burned_at[:10]
            self.burn_combo.addItem(
                f"{record.name}  ·  {when}  ({record.track_count})", record.id
            )
        index = self.burn_combo.findData(wanted) if wanted is not None else -1
        self.burn_combo.setCurrentIndex(max(index, 0))
        self.burn_combo.blockSignals(False)
        self._show_burn()

    def _show_burn(self) -> None:
        burn_id = self.current_burn_id()
        has_burn = burn_id is not None
        self.burn_stack.setCurrentIndex(1 if has_burn else 0)
        self.duplicate_button.setEnabled(has_burn)
        self.delete_burn_button.setEnabled(has_burn)
        if has_burn:
            record = self._lib.burn(burn_id)
            self.burn_model.set_tracks(list(record.tracks))
            self.burn_info.setText(burn_summary(record))
        else:
            self.burn_model.set_tracks([])
            self.burn_info.clear()

    def show_burned(self, burn_id: int | None = None) -> None:
        """Switch to the Burned view, on `burn_id` if given."""
        self.reload_burns(select_id=burn_id)
        self.tabs.setCurrentIndex(1)

    def duplicate_burn(self) -> None:
        burn_id = self.current_burn_id()
        if burn_id is None:
            return
        result = self._lib.duplicate_burn(burn_id)
        self.tabs.setCurrentIndex(0)
        self.reload(select_id=result.disc_id)
        text = f"Made “{self._lib.disc(result.disc_id).name}” from the burn record"
        if result.skipped:
            n = len(result.skipped)
            text += (
                f"; left out {n} track{'s' if n != 1 else ''} no longer in your library"
            )
        self.message.emit(text)

    def delete_burn(self) -> None:
        burn_id = self.current_burn_id()
        if burn_id is None:
            return
        record = self._lib.burn(burn_id)
        answer = QMessageBox.question(
            self,
            "Delete Burn Record",
            f'Delete the record of "{record.name}"?\n'
            "The CD itself is unaffected, but Dubcrate will stop warning about its tracks.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._lib.delete_burn(burn_id)
            self.reload_burns()

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
        """A new disc with the size and gap chosen in Settings."""
        return self._lib.create_disc(
            name,
            capacity_sectors=settings.get_disc_minutes() * 60 * SECTORS_PER_SECOND,
            pregap_sectors=settings.get_pregap_seconds() * SECTORS_PER_SECOND,
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

    def _edit_text(self, position: int, field: str, text: str) -> None:
        """The user edited an artist or title: change what is written on the CD for this
        disc. Only this disc's list changes: the library, rekordbox and the audio file don't."""
        disc_id = self.current_disc_id()
        if disc_id is None:
            return
        try:
            self._lib.set_entry_text(disc_id, position, field, text)
        except LibraryError as exc:
            self._warn("Can't change that", str(exc))
        # Rebuild once the editor has finished committing: resetting the model inside its
        # own setData would pull the editor out from under Qt.
        QTimer.singleShot(0, self._show_disc)

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
    ) -> None:
        """Add tracks to the current disc (creating one if there is none yet)."""
        if not track_ids:
            return

        def add() -> None:
            disc_id = self.current_disc_id()
            if not self._confirm_overlaps(disc_id, track_ids):
                return
            if disc_id is None:
                disc_id = self._create("Disc 1")
            try:
                result = self._lib.add_to_disc(
                    disc_id, track_ids, position=position, fit=fit, strict=strict
                )
            except LibraryError as exc:
                self._warn("Can't add those tracks", str(exc))
                return
            self.tabs.setCurrentIndex(0)
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
                capacity_sectors=settings.get_disc_minutes() * 60 * SECTORS_PER_SECOND,
                pregap_sectors=settings.get_pregap_seconds() * SECTORS_PER_SECOND,
            )
            self.tabs.setCurrentIndex(0)
            self.reload(select_id=result.disc_ids[0] if result.disc_ids else None)
            text = f"Created {len(result.disc_ids)} disc{'s' if len(result.disc_ids) != 1 else ''}"
            if result.too_long:
                text += f"; skipped {len(result.too_long)} track{'s' if len(result.too_long) != 1 else ''} longer than a disc"
            self.message.emit(text)

        self._measured(track_ids, go)
