"""The main window: sources on the left, tracks in the middle, the disc on the right."""

from importlib import metadata
from pathlib import Path

from PySide6.QtCore import QModelIndex, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableView,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from dubcrate import settings
from dubcrate.library import Library
from dubcrate.library.folder import FolderScan, scan_folder
from dubcrate.library.rekordbox import (
    RekordboxLibrary,
    default_master_db,
    read_master_db,
)
from dubcrate.library.store import LibraryError

from . import theme
from .disc_panel import DiscPanel
from .models import (
    NAME_ROLE,
    SELECTION_ROLE,
    TRACK_COLUMNS,
    TrackTableModel,
    build_source_model,
    find_selection,
)
from .splitter import Splitter
from .tasks import run_async

SEARCH_DELAY_MS = 200
MAX_NOTE_LINES = 12


def _hours(seconds: float) -> str:
    minutes = int(seconds) // 60
    return (
        f"{minutes // 60}:{minutes % 60:02d} h" if minutes >= 60 else f"{minutes} min"
    )


class MainWindow(QMainWindow):
    def __init__(self, lib: Library, *, persist: bool = False):
        super().__init__()
        self._lib = lib
        self._persist = persist
        self._selection: tuple = ("all",)
        self._sort: tuple[str | None, bool] = (None, False)
        self._busy = False
        self._theme_actions: dict[str, QAction] = {}
        self.setWindowTitle("Dubcrate")
        self.setMinimumSize(
            980, 560
        )  # below this the three panes are too cramped to use
        self.resize(1440, 800)

        self._build_panes()
        self._build_menus()
        self._build_status_bar()
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DELAY_MS)
        self._search_timer.timeout.connect(self.refresh_tracks)
        theme.notifier.changed.connect(self._on_theme_changed)

        self.refresh_sources()
        self._sync_disc_markers()
        if persist:
            geometry = settings.get_bytes("window/geometry")
            if geometry:
                self.restoreGeometry(geometry)
            state = settings.get_bytes("window/splitter")
            if state:
                self.splitter.restoreState(state)

    # -- layout -----------------------------------------------------------

    @staticmethod
    def _title(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("panelTitle")
        return label

    def _build_panes(self) -> None:
        # sources
        self.tree = QTreeView()
        self.tree.setHeaderHidden(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(False)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(8)
        left_layout.addWidget(self._title("Sources"))
        left_layout.addWidget(self.tree, 1)

        # tracks
        self.tracks_title = self._title("All Tracks")
        self.tracks_count = QLabel()
        self.tracks_count.setObjectName("mutedLabel")
        head = QHBoxLayout()
        head.addWidget(self.tracks_title)
        head.addStretch(1)
        head.addWidget(self.tracks_count)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search title, artist, album or genre")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _text: self._search_timer.start())

        self.track_model = TrackTableModel(self)
        self.track_table = QTableView()
        self.track_table.setModel(self.track_model)
        self.track_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.track_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.track_table.setDragEnabled(True)
        self.track_table.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.track_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.track_table.setShowGrid(False)
        self.track_table.setAlternatingRowColors(True)
        self.track_table.verticalHeader().hide()
        header = self.track_table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((24, 200, 150, 52, 44, 48, 110)):
            self.track_table.setColumnWidth(column, width)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.sortIndicatorChanged.connect(self._on_sort_changed)
        self.track_table.doubleClicked.connect(self._on_track_double_clicked)
        self.track_table.selectionModel().selectionChanged.connect(
            lambda *_: self._update_track_buttons()
        )

        self.add_button = QPushButton("Add to Disc")
        self.add_button.setToolTip(
            "Add the selected tracks to the current disc (or drag them across)"
        )
        self.add_button.clicked.connect(self.add_selected_to_disc)
        self.fit_button = QPushButton("Fit All to Disc")
        self.fit_button.setToolTip(
            "Add every track shown, in order, until the disc is full"
        )
        self.fit_button.clicked.connect(self.fit_all_to_disc)
        buttons = QHBoxLayout()
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.fit_button)
        buttons.addStretch(1)

        middle = QWidget()
        middle_layout = QVBoxLayout(middle)
        middle_layout.setContentsMargins(12, 12, 12, 12)
        middle_layout.setSpacing(8)
        middle_layout.addLayout(head)
        middle_layout.addWidget(self.search)
        middle_layout.addWidget(self.track_table, 1)
        middle_layout.addLayout(buttons)

        # the disc
        self.disc_panel = DiscPanel(self._lib)
        self.disc_panel.changed.connect(self._sync_disc_markers)
        self.disc_panel.message.connect(
            lambda text: self.statusBar().showMessage(text, 8000)
        )
        self.disc_panel.busy.connect(self._set_busy)
        self.disc_panel.progress.connect(self._on_progress)

        self.splitter = Splitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(left)
        self.splitter.addWidget(middle)
        self.splitter.addWidget(self.disc_panel)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setStretchFactor(2, 2)
        self.splitter.setSizes([250, 660, 530])
        self.setCentralWidget(self.splitter)
        self._update_track_buttons()

    def _build_status_bar(self) -> None:
        self.busy_bar = QProgressBar()
        self.busy_bar.setMaximumWidth(180)
        self.busy_bar.setTextVisible(False)
        self.busy_bar.hide()
        self.statusBar().addPermanentWidget(self.busy_bar)

    def _build_menus(self) -> None:
        menus = self.menuBar()

        file_menu = menus.addMenu("&File")
        self.act_import_folder = file_menu.addAction(
            "Import &Folder…", self.import_folder, QKeySequence("Ctrl+O")
        )
        self.act_import_rekordbox = file_menu.addAction(
            "Import &rekordbox Library…",
            self.import_rekordbox,
            QKeySequence("Ctrl+Shift+O"),
        )
        file_menu.addSeparator()
        self.act_reimport = file_menu.addAction(
            "Re-import Selected &Source", self.reimport_selected, QKeySequence("Ctrl+R")
        )
        self.act_remove_source = file_menu.addAction(
            "&Remove Selected Source…", self.remove_selected_source
        )
        file_menu.addSeparator()
        quit_action = file_menu.addAction(
            "&Quit", self.close, QKeySequence.StandardKey.Quit
        )
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        self._import_actions = [
            self.act_import_folder,
            self.act_import_rekordbox,
            self.act_reimport,
            self.act_remove_source,
        ]

        disc_menu = menus.addMenu("&Disc")
        disc_menu.addAction(
            "&New Disc…", self.disc_panel.new_disc, QKeySequence("Ctrl+N")
        )
        disc_menu.addAction("&Rename Disc…", self.disc_panel.rename_disc)
        disc_menu.addAction("&Delete Disc…", self.disc_panel.delete_disc)
        disc_menu.addSeparator()
        disc_menu.addAction(
            "&Add Selected Tracks", self.add_selected_to_disc, QKeySequence("Ctrl+D")
        )
        disc_menu.addAction("&Fit All Shown Tracks", self.fit_all_to_disc)
        disc_menu.addAction(
            "&Split Shown Tracks Across Discs…",
            self.split_shown,
            QKeySequence("Ctrl+Shift+D"),
        )

        view_menu = menus.addMenu("&View")
        theme_menu: QMenu = view_menu.addMenu("&Theme")
        group = QActionGroup(self)
        group.setExclusive(True)
        current = theme.current_preference()
        for label, value in theme.CHOICES:
            action = QAction(label, self, checkable=True)
            action.setChecked(value == current)
            action.triggered.connect(lambda _checked=False, v=value: self.set_theme(v))
            self._theme_actions[value] = action
            group.addAction(action)
            theme_menu.addAction(action)

        help_menu = menus.addMenu("&Help")
        about = help_menu.addAction("&About Dubcrate", self.show_about)
        about.setMenuRole(QAction.MenuRole.AboutRole)

    # -- theme / about ----------------------------------------------------

    def set_theme(self, value: str) -> None:
        settings.set_theme(value)
        theme.set_theme_preference(value)
        for key, action in self._theme_actions.items():
            action.setChecked(key == value)

    def _on_theme_changed(self) -> None:
        self.refresh_sources()  # the tree's icons are baked in when it is built

    def show_about(self) -> None:
        try:
            version = metadata.version("dubcrate")
        except metadata.PackageNotFoundError:
            version = "development"
        QMessageBox.about(
            self,
            "About Dubcrate",
            f"<b>Dubcrate</b> {version}<br>Burn audio CDs for old-style CDJs from your DJ library.<br><br>"
            "The Macintosh System 6 theme uses the ChiKareGo2 font by Giles Booth "
            "(a recreation of Chicago 12), licensed CC BY. Not affiliated with Apple.",
        )

    # -- status / busy ----------------------------------------------------

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        for action in self._import_actions:
            action.setEnabled(not busy)
        self.busy_bar.setVisible(busy)
        if busy:
            self.busy_bar.setRange(
                0, 0
            )  # indeterminate until a progress report arrives
            self.statusBar().showMessage(message)
        else:
            self.statusBar().clearMessage()

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.busy_bar.setRange(0, total)
            self.busy_bar.setValue(done)

    def _notify(self, title: str, text: str) -> None:
        QMessageBox.information(self, title, text)

    def _warn(self, title: str, text: str) -> None:
        QMessageBox.warning(self, title, text)

    # -- sources ----------------------------------------------------------

    def refresh_sources(self, select: tuple | None = None) -> None:
        """Rebuild the source tree and select `select` (default: what was selected)."""
        target = select or self._selection
        model = build_source_model(self._lib)
        self.tree.setModel(model)
        self.tree.selectionModel().currentChanged.connect(self._on_tree_current_changed)
        tree_header = self.tree.header()
        tree_header.setStretchLastSection(False)
        tree_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        tree_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.expandToDepth(0)
        index = find_selection(model, target)
        if not index.isValid():
            index = find_selection(model, ("all",))
        self.tree.setCurrentIndex(index)
        self._on_tree_current_changed(
            index, QModelIndex()
        )  # (the same index isn't a change for Qt)

    def _on_tree_current_changed(
        self, current: QModelIndex, _previous: QModelIndex
    ) -> None:
        current = current.siblingAtColumn(0)  # (a click on the count column)
        selection = current.data(SELECTION_ROLE) if current.isValid() else None
        if selection is None:
            return
        self._selection = selection
        self.tracks_title.setText(current.data(NAME_ROLE))
        self.refresh_tracks()
        self._update_source_actions()

    def selected_source_id(self) -> int | None:
        kind = self._selection[0]
        if kind == "source":
            return self._selection[1]
        if kind == "node":
            return self._selection[2]
        return None

    def _update_source_actions(self) -> None:
        has_source = self.selected_source_id() is not None
        self.act_reimport.setEnabled(has_source and not self._busy)
        self.act_remove_source.setEnabled(has_source and not self._busy)

    # -- importing --------------------------------------------------------

    def import_folder(self) -> None:
        start = Path.home() / "Music"
        path = QFileDialog.getExistingDirectory(
            self, "Import Folder", str(start if start.is_dir() else Path.home())
        )
        if path:
            self.start_import("folder", Path(path))

    def import_rekordbox(self) -> None:
        found = default_master_db()
        path: Path | None = None
        if found is not None:
            box = QMessageBox(self)
            box.setWindowTitle("Import rekordbox Library")
            box.setText(f"Import the rekordbox library found here?\n{found}")
            box.setInformativeText(
                "It is only read, from a temporary copy. rekordbox is never changed."
            )
            import_button = box.addButton("Import", QMessageBox.ButtonRole.AcceptRole)
            choose_button = box.addButton(
                "Choose File…", QMessageBox.ButtonRole.ActionRole
            )
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            clicked = box.clickedButton()
            if clicked is import_button:
                path = found
            elif clicked is not choose_button:
                return
        if path is None:
            chosen, _ = QFileDialog.getOpenFileName(
                self,
                "Choose master.db",
                str(Path.home()),
                "rekordbox database (master.db)",
            )
            if not chosen:
                return
            path = Path(chosen)
        self.start_import("rekordbox", path)

    def start_import(self, kind: str, path: Path, name: str | None = None) -> None:
        """Read a source on a worker thread, then store it here on the GUI thread."""
        if self._busy:
            return
        if kind == "folder":
            label = f"Scanning {path.name}…"

            def work(report):
                return scan_folder(path, progress=report)
        else:
            label = "Reading the rekordbox library…"

            def work(_report):
                return read_master_db(path)

        self._set_busy(True, label)
        run_async(
            work,
            lambda result: self._import_done(kind, path, name, result),
            self._import_failed,
            on_progress=self._on_progress,
        )

    def _import_failed(self, message: str) -> None:
        self._set_busy(False)
        self._warn("Import failed", message)

    def _import_done(
        self,
        kind: str,
        path: Path,
        name: str | None,
        result: FolderScan | RekordboxLibrary,
    ) -> None:
        if isinstance(result, FolderScan):
            notes = [f"Couldn't read {Path(p).name}: {why}" for p, why in result.errors]
            summary = self._lib.replace_source(
                "folder",
                str(result.root),
                name or result.root.name,
                result.tracks,
                result.nodes,
                notes,
            )
        else:
            summary = self._lib.replace_source(
                "rekordbox",
                str(result.source),
                name or "rekordbox",
                result.tracks,
                result.nodes,
                result.notes,
            )
        self._set_busy(False)
        self.refresh_sources(select=("source", summary.source_id))
        self.disc_panel.reload()  # a re-import can mark tracks on a disc as no longer in their source
        self.statusBar().showMessage(
            f"Imported {summary.added + summary.updated} tracks ({summary.added} new, {summary.removed} removed)",
            10000,
        )
        if summary.notes:
            shown = list(summary.notes[:MAX_NOTE_LINES])
            if len(summary.notes) > MAX_NOTE_LINES:
                shown.append(f"…and {len(summary.notes) - MAX_NOTE_LINES} more")
            self._notify("Import notes", "\n".join(shown))

    def reimport_selected(self) -> None:
        source_id = self.selected_source_id()
        source = next((s for s in self._lib.sources() if s.id == source_id), None)
        if source is None:
            return
        path = Path(source.path)
        if not path.exists():
            self._warn("Can't re-import", f"{path} is no longer there.")
            return
        self.start_import(source.kind, path, name=source.name)

    def remove_selected_source(self) -> None:
        source_id = self.selected_source_id()
        source = next((s for s in self._lib.sources() if s.id == source_id), None)
        if source is None:
            return
        answer = QMessageBox.question(
            self,
            "Remove Source",
            f'Remove "{source.name}" from Dubcrate?\nYour music files and the original library are not touched.',
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._lib.remove_source(source.id)
        except LibraryError:
            force = QMessageBox.question(
                self,
                "Tracks are on discs",
                f'Some tracks from "{source.name}" are on discs.\nRemove them from those discs too?',
            )
            if force != QMessageBox.StandardButton.Yes:
                return
            self._lib.remove_source(source.id, force=True)
        self._selection = ("all",)
        self.refresh_sources()
        self.disc_panel.reload()

    # -- tracks -----------------------------------------------------------

    def refresh_tracks(self) -> None:
        key, descending = self._sort
        filters: dict = {}
        if self._selection[0] == "source":
            filters["source_id"] = self._selection[1]
        elif self._selection[0] == "node":
            filters["node_id"] = self._selection[1]
        tracks = self._lib.tracks(
            search=self.search.text() or None,
            sort=key,
            descending=descending,
            **filters,
        )
        self.track_model.set_tracks(tracks)
        total = sum(t.duration for t in tracks)
        count = f"{len(tracks):,} track{'s' if len(tracks) != 1 else ''}"
        self.tracks_count.setText(f"{count}  ·  {_hours(total)}" if tracks else count)
        self._update_track_buttons()

    def _on_sort_changed(self, column: int, order: Qt.SortOrder) -> None:
        key = TRACK_COLUMNS[column].sort_key if column >= 0 else None
        if (
            key is None
        ):  # the marker column, or the indicator being cleared: back to the natural order
            self._sort = (None, False)
            header = self.track_table.horizontalHeader()
            if header.sortIndicatorSection() != -1:
                header.blockSignals(True)
                header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
                header.blockSignals(False)
        else:
            self._sort = (key, order == Qt.SortOrder.DescendingOrder)
        self.refresh_tracks()

    def selected_track_ids(self) -> list[int]:
        rows = sorted(i.row() for i in self.track_table.selectionModel().selectedRows())
        return [self.track_model.track_at(r).id for r in rows]

    def _update_track_buttons(self) -> None:
        self.add_button.setEnabled(
            bool(self.track_table.selectionModel().selectedRows())
        )
        self.fit_button.setEnabled(self.track_model.rowCount() > 0)

    def _on_track_double_clicked(self, index: QModelIndex) -> None:
        self.disc_panel.add_tracks([self.track_model.track_at(index.row()).id])

    def add_selected_to_disc(self) -> None:
        self.disc_panel.add_tracks(self.selected_track_ids())

    def fit_all_to_disc(self) -> None:
        self.disc_panel.add_tracks([t.id for t in self.track_model.tracks()], fit=True)

    def split_shown(self) -> None:
        tracks = self.track_model.tracks()
        if not tracks:
            self.statusBar().showMessage("There are no tracks shown to split.", 6000)
            return
        name, ok = QInputDialog.getText(
            self,
            "Split Across Discs",
            "Name for the new discs:",
            text=self.tracks_title.text(),
        )
        if ok and name.strip():
            self.disc_panel.split([t.id for t in tracks], name.strip())

    def _sync_disc_markers(self) -> None:
        self.track_model.set_on_disc(self.disc_panel.current_track_ids())

    # -- closing ----------------------------------------------------------

    def closeEvent(self, event) -> None:
        if self._persist:
            settings.set_bytes("window/geometry", self.saveGeometry())
            settings.set_bytes("window/splitter", self.splitter.saveState())
        super().closeEvent(event)
