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
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from dubcrate import settings
from dubcrate.analysis import read_preview, share_root
from dubcrate.audio.player import AudioPlayer
from dubcrate.library import Library
from dubcrate.library.folder import FolderScan, scan_folder
from dubcrate.library.rekordbox import (
    RekordboxLibrary,
    default_master_db,
    read_master_db,
)
from dubcrate.library.store import LibraryError

from . import theme, wave_style
from .burn_dialog import BurnDialog, BurnerFactory
from .disc_panel import DiscPanel
from .help_dialog import show_help_dialog
from .models import (
    NAME_ROLE,
    PREVIEW_COLUMN,
    SELECTION_ROLE,
    TRACK_COLUMNS,
    TrackTableModel,
    build_source_model,
    find_selection,
)
from .player_strip import PlayerStrip
from .preview import PreviewController
from .settings_dialog import SettingsDialog
from .splitter import Splitter
from .tasks import run_async
from .track_table import ROW_HEIGHT, PreviewDelegate, TrackTable

SEARCH_DELAY_MS = 200
PREVIEW_DELAY_MS = 40
PREVIEW_ROWS_AROUND = 8  # also read this many rows above and below what is visible
MAX_NOTE_LINES = 12


def _hours(seconds: float) -> str:
    minutes = int(seconds) // 60
    return (
        f"{minutes // 60}:{minutes % 60:02d} h" if minutes >= 60 else f"{minutes} min"
    )


class MainWindow(QMainWindow):
    def __init__(
        self,
        lib: Library,
        *,
        persist: bool = False,
        player: AudioPlayer | None = None,
        burner_factory: BurnerFactory | None = None,
    ):
        super().__init__()
        self._lib = lib
        self._burner_factory = burner_factory
        self.burn_dialog: BurnDialog | None = None
        self._help_dialog = None
        self._player = player
        self._persist = persist
        self._selection: tuple = ("all",)
        self._sort: tuple[str | None, bool] = (None, False)
        self._busy = False
        self._theme_actions: dict[str, QAction] = {}
        self._wave_actions: dict[str, QAction] = {}
        self.setWindowTitle("Dubcrate")
        self.setMinimumSize(
            980, 660
        )  # below this the player and the three panes are too cramped to use
        self.resize(1440, 920)

        self._build_panes()
        self._build_menus()
        self._build_status_bar()
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DELAY_MS)
        self._search_timer.timeout.connect(self.refresh_tracks)
        self._preview_timer = QTimer(
            self
        )  # previews are read for the rows on screen, a moment after scrolling stops
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(PREVIEW_DELAY_MS)
        self._preview_timer.timeout.connect(self._request_previews)
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

        self.player_strip = PlayerStrip()
        self.preview = PreviewController(
            self._lib, self.player_strip, self._player, parent=self
        )
        self.preview.message.connect(
            lambda text: self.statusBar().showMessage(text, 8000)
        )
        self.preview.preview_ready.connect(self._on_preview_ready)
        self.player_strip.previous_requested.connect(lambda: self._step_track(-1))
        self.player_strip.next_requested.connect(lambda: self._step_track(1))

        self.track_model = TrackTableModel(self)
        self.track_table = TrackTable()
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
        for column, spec in enumerate(TRACK_COLUMNS):
            self.track_table.setColumnWidth(column, spec.width)
        header.setStretchLastSection(
            True
        )  # the last column takes the slack; the title keeps its width
        self.track_table.setItemDelegateForColumn(
            PREVIEW_COLUMN, PreviewDelegate(self.track_table)
        )
        self.track_table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT + 4)
        self.track_table.add_requested.connect(self.add_selected_to_disc)
        self.track_table.play_pause_requested.connect(self.preview.toggle)
        self.track_table.beat_jump_requested.connect(self.preview.beat_jump)
        self.track_table.verticalScrollBar().valueChanged.connect(
            lambda _v: self._preview_timer.start()
        )
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._show_column_menu)
        header.setSectionsMovable(True)  # drag a heading to reorder the columns
        header.setFirstSectionMovable(
            False
        )  # the "already on the disc" marker stays first
        self._apply_column_visibility()
        self._apply_column_order()
        header.sectionMoved.connect(lambda *_: self._save_column_order())
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
        self.disc_panel.burn_requested.connect(self.burn_current_disc)
        self.disc_panel.progress.connect(self._on_progress)

        self.splitter = Splitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(left)
        self.splitter.addWidget(middle)
        self.splitter.addWidget(self.disc_panel)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setStretchFactor(2, 2)
        self.splitter.setSizes([210, 760, 470])
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self.player_strip)
        body_layout.addWidget(self.splitter, 1)
        self.setCentralWidget(body)
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
        settings_action = file_menu.addAction(
            "&Settings…", self.open_settings, QKeySequence("Ctrl+,")
        )
        settings_action.setMenuRole(QAction.MenuRole.PreferencesRole)
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
        self.act_burn = disc_menu.addAction(
            "&Burn Disc…", self.burn_current_disc, QKeySequence("Ctrl+B")
        )
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
        self.act_find = view_menu.addAction(
            "&Find Tracks", self.focus_search, QKeySequence.StandardKey.Find
        )  # Cmd+F on a Mac, Ctrl+F elsewhere
        view_menu.addSeparator()
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

        wave_menu: QMenu = view_menu.addMenu("&Waveform")
        wave_group = QActionGroup(self)
        wave_group.setExclusive(True)
        for label, value in wave_style.WAVE_STYLE_LABELS:
            action = QAction(label, self, checkable=True)
            action.triggered.connect(
                lambda _checked=False, v=value: wave_style.set_style(v)
            )
            self._wave_actions[value] = action
            wave_group.addAction(action)
            wave_menu.addAction(action)
        wave_style.notifier.changed.connect(self._sync_wave_actions)
        self._sync_wave_actions()

        help_menu = menus.addMenu("&Help")
        self.act_help = help_menu.addAction(
            "&User Guide", self.show_help, QKeySequence.StandardKey.HelpContents
        )
        help_menu.addSeparator()
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

    def _sync_wave_actions(self) -> None:
        current = settings.get_wave_style()
        for key, action in self._wave_actions.items():
            action.setChecked(key == current)

    def focus_search(self) -> None:
        """Put the cursor in the search box, with what was typed selected to type over."""
        self.search.setFocus()
        self.search.selectAll()

    def show_help(self) -> None:
        show_help_dialog(self)

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

    def burn_current_disc(self) -> None:
        """Open the burn window for the disc being planned."""
        disc_id = self.disc_panel.current_disc_id()
        if disc_id is None:
            self._notify("Burn Disc", "Make a disc and add some tracks first.")
            return
        self.burn_dialog = BurnDialog(
            self._lib, disc_id, self, burner_factory=self._burner_factory
        )
        self.burn_dialog.burned.connect(self._on_burned)
        self.burn_dialog.finished.connect(lambda _r: self.disc_panel.reload())
        self.burn_dialog.open()

    def _on_burned(self, burn_id: int) -> None:
        self.disc_panel.show_burned(burn_id)
        self.statusBar().showMessage("Burn finished and saved", 10000)

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
        self._preview_timer.start()

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
        self.preview.load(self.track_model.track_at(index.row()))

    def _step_track(self, delta: int) -> None:
        """Load the track before or after the loaded one in the list (or the selected one)."""
        tracks = self.track_model.tracks()
        if not tracks:
            return
        current = (
            self.preview.requested
        )  # not the last one finished loading: quick clicks must add up
        row = next(
            (
                i
                for i, t in enumerate(tracks)
                if current is not None and t.id == current.id
            ),
            None,
        )
        if (
            row is None
        ):  # nothing requested yet (or it isn't in this list): go on from the selection
            chosen = self.track_table.selectionModel().selectedRows()
            row = chosen[0].row() if chosen else (-1 if delta > 0 else len(tracks))
        target = row + delta
        if 0 <= target < len(tracks):
            self.track_table.selectRow(target)
            self.preview.load(tracks[target])

    def _on_preview_ready(self, track_id: int, waveform) -> None:
        self.track_model.set_preview(track_id, waveform)

    def _request_previews(self) -> None:
        """Read the stored waveform for the rows on screen (rekordbox tracks: 0.4 ms each)."""
        model = self.track_model
        if model.rowCount() == 0:
            return
        first = max(0, self.track_table.rowAt(0))
        last = self.track_table.rowAt(self.track_table.viewport().height() - 1)
        last = model.rowCount() - 1 if last < 0 else last
        roots = {
            s.id: share_root(s.path)
            for s in self._lib.sources()
            if s.kind == "rekordbox"
        }
        for row in range(
            max(0, first - PREVIEW_ROWS_AROUND),
            min(model.rowCount(), last + PREVIEW_ROWS_AROUND + 1),
        ):
            track = model.track_at(row)
            if (
                track.analysis_path
                and track.source_id in roots
                and not model.has_preview(track.id)
            ):
                waveform = read_preview(roots[track.source_id], track.analysis_path)
                if waveform is not None:
                    model.set_preview(track.id, waveform)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._preview_timer.start()

    # -- columns ----------------------------------------------------------

    def _visible_titles(self) -> set[str]:
        saved = settings.get_visible_columns()
        if saved is not None:
            return set(saved)
        # Never chosen: the default columns, minus any a layout from before there were
        # optional columns had hidden (it recorded what was hidden, not what was shown).
        legacy = set(settings.get_hidden_columns())
        return {c.title for c in TRACK_COLUMNS if c.shown and c.title not in legacy}

    def _apply_column_visibility(self) -> None:
        visible = self._visible_titles()
        for column, spec in enumerate(TRACK_COLUMNS):
            if column > 1:  # the marker and the title are always there
                self.track_table.setColumnHidden(column, spec.title not in visible)

    def _save_visible_columns(self) -> None:
        settings.set_visible_columns(
            [
                spec.title
                for column, spec in enumerate(TRACK_COLUMNS)
                if column <= 1 or not self.track_table.isColumnHidden(column)
            ]
        )

    def _apply_column_order(self) -> None:
        """Put the columns in their saved order. Names it doesn't know are ignored,
        and columns it doesn't mention follow in their default order."""
        header = self.track_table.horizontalHeader()
        by_title = {spec.title: c for c, spec in enumerate(TRACK_COLUMNS) if c > 0}
        wanted = [by_title[n] for n in settings.get_column_order() if n in by_title]
        order = [0, *dict.fromkeys(wanted)]
        order += [c for c in range(1, len(TRACK_COLUMNS)) if c not in order]
        header.blockSignals(True)  # not a user's drag: nothing to save
        for visual, logical in enumerate(order):
            header.moveSection(header.visualIndex(logical), visual)
        header.blockSignals(False)

    def _save_column_order(self) -> None:
        header = self.track_table.horizontalHeader()
        settings.set_column_order(
            [
                TRACK_COLUMNS[header.logicalIndex(v)].title
                for v in range(1, header.count())
            ]
        )

    def reset_columns(self) -> None:
        """Back to the default order and the default columns."""
        settings.set_column_order([])
        settings.set_visible_columns([c.title for c in TRACK_COLUMNS if c.shown])
        self._apply_column_visibility()
        self._apply_column_order()
        self._preview_timer.start()

    def column_menu(self) -> QMenu:
        """Which columns to show: the usual ones, and the rest under "More Columns"."""
        menu = QMenu(self)
        more = QMenu("More Columns", menu)
        for column, spec in enumerate(TRACK_COLUMNS):
            if column <= 1:  # the marker and the title are always there
                continue
            action = (menu if spec.shown else more).addAction(spec.title)
            action.setCheckable(True)
            action.setChecked(not self.track_table.isColumnHidden(column))
            action.toggled.connect(
                lambda shown, c=column: self.set_column_visible(c, shown)
            )
        menu.addMenu(more)
        menu.addSeparator()
        menu.addAction("Reset Columns", self.reset_columns)
        return menu

    def _show_column_menu(self, position) -> None:
        menu = self.column_menu()
        menu.popup(self.track_table.horizontalHeader().mapToGlobal(position))

    def set_column_visible(self, column: int, visible: bool) -> None:
        self.track_table.setColumnHidden(column, not visible)
        self._save_visible_columns()
        self._preview_timer.start()

    def open_settings(self) -> None:
        dialog = SettingsDialog(self)
        dialog.audio_changed.connect(self.preview.apply_settings)
        dialog.theme_chosen.connect(self.set_theme)
        dialog.exec()

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
        self._search_timer.stop()
        self._preview_timer.stop()
        self.preview.shutdown()
        if self._persist:
            settings.set_bytes("window/geometry", self.saveGeometry())
            settings.set_bytes("window/splitter", self.splitter.saveState())
        super().closeEvent(event)
