"""The main window: the library's file tree on the left; in the middle the tracks of what
you picked, or the disc you opened; your discs (burn playlists, in folders) on the right."""

import logging
import os
from pathlib import Path

from PySide6.QtCore import QEvent, QModelIndex, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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
    QStackedWidget,
    QTableView,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from dubcrate import logsetup, settings
from dubcrate.analysis import read_preview, share_root
from dubcrate.audio.player import AudioPlayer
from dubcrate.library import Library
from dubcrate.library.folder import (
    DirectoryScan,
    FolderScan,
    scan_directory,
    scan_folder,
)
from dubcrate.library.models import DiscKind, Track
from dubcrate.library.rekordbox import (
    RekordboxLibrary,
    default_master_db,
    read_master_db,
)
from dubcrate.library.store import LibraryError

from . import theme, wave_style
from .about_dialog import AboutDialog, NoticesDialog
from .burn_dialog import BurnDialog, BurnerFactory
from .disc_tree import COLUMN_WIDTH, DiscTree
from .disc_view import DiscView
from .explorer import ExplorerTree, volume_parents
from .help_dialog import show_help_dialog
from .label_dialog import LabelDialog
from .models import (
    NAME_ROLE,
    PREVIEW_COLUMN,
    SELECTION_ROLE,
    TRACK_COLUMNS,
    TrackTableModel,
    build_disc_model,
    build_source_model,
    find_selection,
)
from .player_strip import PlayerStrip
from .preview import PreviewController
from .settings_dialog import SettingsDialog
from .shortcuts_dialog import ShortcutsDialog
from .source_tabs import SourceTabBar, explorer_glyph, library_glyph, pinned_glyph
from .splitter import Splitter
from .status_bar import AppStatusBar
from .tasks import TaskError, run_async
from .track_table import (
    ROW_HEIGHT,
    PreviewDelegate,
    TrackTable,
    transport_action,
    transport_key_is_taken,
)
from .update_check import UpdateChecker
from .update_check import due as update_check_due
from .version import icon_path

log = logging.getLogger(__name__)

SEARCH_DELAY_MS = 200
PREVIEW_DELAY_MS = 40
UPDATE_CHECK_DELAY_MS = (
    2000  # the start-up update check waits until the window has settled
)
PREVIEW_ROWS_AROUND = 8  # also read this many rows above and below what is visible
MAX_NOTE_LINES = 12


def _hours(seconds: float) -> str:
    minutes = int(seconds) // 60
    return (
        f"{minutes // 60}:{minutes % 60:02d} h" if minutes >= 60 else f"{minutes} min"
    )


class SearchBox(QLineEdit):
    """The search box. Escape says it is done (`escape_pressed`), so the keys that play, pause and
    jump work again without a click elsewhere; what was typed stays."""

    escape_pressed = Signal()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and not event.modifiers():
            self.escape_pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class MainWindow(QMainWindow):
    def __init__(
        self,
        lib: Library,
        *,
        persist: bool = False,
        player: AudioPlayer | None = None,
        burner_factory: BurnerFactory | None = None,
        update_check: bool = False,
    ):
        super().__init__()
        self._lib = lib
        self._burner_factory = burner_factory
        self.burn_dialog: BurnDialog | None = None
        self._help_dialog = None
        # one update check at a time: the Help menu's, About's and the start-up one
        self.updates = UpdateChecker(self)
        self._player = player
        self._persist = persist
        # what the middle's track list shows (a disc has its own view): an item of the library
        # tree, or ("explorer", folder). The library tree keeps its own pick for when its tab is
        # showing again.
        self._selection: tuple = ("all",)
        self._library_selection: tuple = ("all",)
        self._explorer_path: str | None = None
        self._pins: list[str] = []  # the pinned folders, one tab each (`pin:` + path)
        self._pin_trees: dict[str, ExplorerTree] = {}  # by tab key
        # the folder or disc that is current in the disc tree
        self._disc_pick: tuple | None = None
        self.shortcuts_dialog: ShortcutsDialog | None = None  # kept while it is open
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
        # Space and Left / Right drive the preview from anywhere in this window
        QApplication.instance().installEventFilter(self)

        self.refresh_sources()
        self.refresh_discs()
        self._sync_disc_markers()
        if (
            update_check and update_check_due()
        ):  # (the app turns it on; tests never touch the network)
            QTimer.singleShot(
                UPDATE_CHECK_DELAY_MS, lambda: self.updates.start(manual=False)
            )
        if persist:
            geometry = settings.get_bytes("window/geometry")
            if geometry:
                self.restoreGeometry(geometry)
            state = settings.get_bytes("window/splitter3")
            if state:
                self.splitter.restoreState(state)
            last_folder = settings.get_explorer_path()
            if last_folder and os.path.isdir(last_folder):
                self.explorer_tree.select_path(
                    last_folder
                )  # (the tab is not showing: just picks)
            for pinned in settings.get_pinned_folders():
                self._add_pinned_tab(pinned)
            self._save_pins()
            tab = settings.get_left_tab()
            known_tabs = self.source_tabs.keys()
            if tab != "library" and tab in known_tabs:
                self.source_tabs.set_current(tab)

    # -- layout -----------------------------------------------------------

    @staticmethod
    def _title(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("panelTitle")
        return label

    def _folder_page(self, title: str, tree: ExplorerTree) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(self._title(title))
        layout.addWidget(tree, 1)
        return page

    def _build_panes(self) -> None:
        # sources
        self.tree = QTreeView()
        self.tree.setHeaderHidden(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(False)
        # (a click on the item that is already current changes nothing for Qt, so it needs this)
        self.tree.clicked.connect(self._on_tree_clicked)
        library_page = QWidget()
        library_layout = QVBoxLayout(library_page)
        library_layout.setContentsMargins(12, 12, 12, 12)
        library_layout.setSpacing(8)
        library_layout.addWidget(self._title("Library"))
        library_layout.addWidget(self.tree, 1)

        # the Explorer: folders on this Mac, read when you open them, with nothing imported
        self.explorer_tree = ExplorerTree(watch=volume_parents())
        self.explorer_tree.folder_selected.connect(self._on_explorer_folder)
        explorer_page = self._folder_page("Explorer", self.explorer_tree)
        self.explorer_tree.pin_requested.connect(self.pin_folder)
        self.explorer_tree.unpin_requested.connect(self.unpin_folder)
        self.explorer_tree.refresh_requested.connect(self._on_refresh_folder)

        self.source_pages = QStackedWidget()
        self.source_pages.addWidget(library_page)
        self.source_pages.addWidget(explorer_page)
        self._left_pages: dict[str, QWidget] = {
            "library": library_page,
            "explorer": explorer_page,
        }
        self.source_tabs = SourceTabBar()
        self.source_tabs.add_tab(
            "library",
            "Library",
            library_glyph,
            "Your imported libraries and folders",
        )
        self.source_tabs.add_tab(
            "explorer",
            "Explorer",
            explorer_glyph,
            "Browse folders on this Mac, or a drive, without importing them",
        )
        self.source_tabs.current_changed.connect(self._on_left_tab)
        self.source_tabs.context_menu_requested.connect(self._on_tab_menu)
        self.source_tabs.tabs_reordered.connect(self._on_tabs_dragged)
        left = QWidget()
        left_layout = QHBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_layout.addWidget(self.source_tabs)
        left_layout.addWidget(self.source_pages, 1)

        # tracks
        self.tracks_title = self._title("All Tracks")
        self.tracks_count = QLabel()
        self.tracks_count.setObjectName("mutedLabel")
        head = QHBoxLayout()
        head.addWidget(self.tracks_title)
        head.addStretch(1)
        head.addWidget(self.tracks_count)
        self.search = SearchBox()
        self.search.setPlaceholderText("Search title, artist, album or genre")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _text: self._search_timer.start())
        self.search.escape_pressed.connect(self.leave_search)
        self.search.returnPressed.connect(lambda: self.leave_search(pick_first=True))

        self.player_strip = PlayerStrip()
        self.player_strip.settings_requested.connect(self.open_settings)
        self.preview = PreviewController(
            self._lib, self.player_strip, self._player, parent=self
        )
        self.preview.message.connect(
            lambda text: self.statusBar().showMessage(text, 8000)
        )
        self.preview.preview_ready.connect(self._on_preview_ready)
        self.player_strip.previous_requested.connect(lambda: self._step_track(-1))
        self.player_strip.next_requested.connect(lambda: self._step_track(1))
        self.preview.player.finished.connect(self._play_on_after_end)

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
        # (Qt's default minimum depends on the style and font, and is wider than the narrow marker
        # column on Linux, which would then open wider than it is meant to be)
        header.setMinimumSectionSize(min(spec.width for spec in TRACK_COLUMNS))
        for column, spec in enumerate(TRACK_COLUMNS):
            self.track_table.setColumnWidth(column, spec.width)
            if spec.fixed:  # a waveform, a marker, a number: content of one size
                header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        header.setStretchLastSection(
            True
        )  # the last column takes the slack; the title keeps its width
        self.track_table.setItemDelegateForColumn(
            PREVIEW_COLUMN, PreviewDelegate(self.track_table)
        )
        self.track_table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT + 4)
        self.track_table.add_requested.connect(self.add_selected_to_disc)
        self.track_table.load_requested.connect(self.load_highlighted_track)
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
            "Add the selected tracks to the open disc (or drag them onto a disc in the list on the left)"
        )
        self.add_button.clicked.connect(self.add_selected_to_disc)
        self.fit_button = QPushButton("Fit All to Disc")
        self.fit_button.setToolTip(
            "Add every track shown, in order, until the disc is full"
        )
        self.fit_button.clicked.connect(self.fit_all_to_disc)
        self.target_label = QLabel()
        self.target_label.setObjectName("mutedLabel")
        buttons = QHBoxLayout()
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.fit_button)
        buttons.addStretch(1)
        buttons.addWidget(self.target_label)

        middle = QWidget()
        middle_layout = QVBoxLayout(middle)
        middle_layout.setContentsMargins(12, 12, 12, 12)
        middle_layout.setSpacing(8)
        middle_layout.addLayout(head)
        middle_layout.addWidget(self.search)
        middle_layout.addWidget(self.track_table, 1)
        middle_layout.addLayout(buttons)

        # the disc: opened from the tree, edited here
        self.disc_view = DiscView(self._lib)
        self.disc_view.changed.connect(self._sync_disc_markers)
        self.disc_view.discs_changed.connect(self.refresh_discs)
        self.disc_view.discs_changed.connect(
            self._sync_disc_markers
        )  # (other discs too)
        self.disc_view.opened.connect(self.open_disc)
        self.disc_view.navigate_requested.connect(self._navigate_to)
        self.disc_view.message.connect(
            lambda text: self.statusBar().showMessage(text, 8000)
        )
        self.disc_view.busy.connect(self._set_busy)
        self.disc_view.burn_requested.connect(self.burn_current_disc)
        self.disc_view.progress.connect(self._on_progress)
        self.disc_view.preview_requested.connect(self.preview.load)
        self.disc_view.table.play_pause_requested.connect(self.preview.toggle)
        self.disc_view.table.beat_jump_requested.connect(self.preview.beat_jump)

        self.center = QStackedWidget()  # the library's tracks, or the open disc
        self.center.addWidget(middle)
        self.center.addWidget(self.disc_view)

        # your discs: burn playlists, filed in folders. Click one to open it in the middle;
        # drag tracks onto one to add them; drag discs and folders about to file them.
        self.disc_tree = DiscTree()
        self.disc_tree.new_disc_requested.connect(self._on_plus_clicked)
        self.disc_tree.new_folder_requested.connect(self._on_new_folder_clicked)
        self.disc_tree.clicked.connect(self._on_disc_clicked)
        self.disc_tree.setHeaderHidden(True)
        self.disc_tree.setUniformRowHeights(True)
        self.disc_tree.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.disc_tree.setDragEnabled(True)
        self.disc_tree.setAcceptDrops(True)
        self.disc_tree.setDropIndicatorShown(True)
        self.disc_tree.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.disc_tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        # a name is edited in place: double-click it (a new disc or folder opens like this)
        self.disc_tree.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.disc_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.disc_tree.customContextMenuRequested.connect(self._show_disc_tree_menu)
        self.new_disc_button = QPushButton("New Disc")
        self.new_disc_button.setToolTip("Make a disc (a playlist to burn to CD)")
        self.new_disc_button.clicked.connect(lambda: self.new_disc())
        self.new_folder_button = QPushButton("New Folder")
        self.new_folder_button.setToolTip("Make a folder to organise your discs")
        self.new_folder_button.clicked.connect(lambda: self.new_disc_folder())
        disc_buttons = QHBoxLayout()
        disc_buttons.addWidget(self.new_disc_button)
        disc_buttons.addWidget(self.new_folder_button)
        disc_buttons.addStretch(1)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(8)
        right_layout.addWidget(self._title("Discs"))
        right_layout.addWidget(self.disc_tree, 1)
        right_layout.addLayout(disc_buttons)

        self.splitter = Splitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(left)
        self.splitter.addWidget(self.center)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([300, 760, 330])
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self.player_strip)
        body_layout.addWidget(self.splitter, 1)
        self.setCentralWidget(body)
        self._update_track_buttons()

    def _build_status_bar(self) -> None:
        self.setStatusBar(AppStatusBar(self))  # (version, update button, then messages)
        self.statusBar().update_clicked.connect(lambda: self.updates.show_available())
        self.updates.on_available = self.statusBar().set_update
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
            "Re-import Selected &Source",
            self.reimport_selected,
            QKeySequence("Ctrl+Shift+R"),
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
            "&New Disc", lambda: self.new_disc(), QKeySequence("Ctrl+N")
        )
        self.act_new_mp3_disc = disc_menu.addAction(
            "New &MP3 Disc",
            lambda: self.new_disc(kind="mp3"),
            QKeySequence("Ctrl+Shift+N"),
        )
        self.act_new_mp3_disc.setToolTip(
            "Make a disc for an MP3 CD: a data disc of MP3 files, sized in megabytes, for players that read MP3 discs."
        )
        disc_menu.addAction("New &Folder", lambda: self.new_disc_folder())
        self.act_rename = disc_menu.addAction(
            "&Rename", self.rename_picked, QKeySequence("Ctrl+R")
        )
        self.act_rename.setToolTip(
            "Rename the disc or folder picked in the Discs list."
        )
        disc_menu.addAction("D&uplicate Disc", lambda: self.disc_view.duplicate_disc())
        self.act_delete = disc_menu.addAction("&Delete…", self.delete_picked)
        self.act_delete.setShortcuts(
            [QKeySequence("Ctrl+Backspace"), QKeySequence("Ctrl+Delete")]
        )  # (⌘⌫ on a Mac; Backspace is the key called Delete there)
        self.act_delete.setToolTip(
            "Delete the disc or folder picked in the Discs list: at once if it is empty,"
            " else after asking."
        )
        disc_menu.addSeparator()
        self.act_burn = disc_menu.addAction(
            "&Burn Disc…", self.burn_current_disc, QKeySequence("Ctrl+B")
        )
        self.act_print_labels = disc_menu.addAction(
            "&Print Labels…", lambda: self.print_labels(), QKeySequence("Ctrl+P")
        )
        self.act_print_labels.setToolTip(
            "Print the track lists of discs on sticker paper (A4, Letter, ...), as labels for the case."
        )
        disc_menu.addSeparator()
        self.act_add_selected = disc_menu.addAction(
            "&Add Selected Tracks", self.add_selected_to_disc
        )
        self.act_add_selected.setShortcuts(
            [
                QKeySequence("Ctrl+Return"),
                QKeySequence("Ctrl+Enter"),  # (the keypad's)
                QKeySequence("Ctrl+D"),
            ]
        )  # (Return on its own loads the highlighted track into the player)
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
        self.act_shortcuts = help_menu.addAction(
            "&Keyboard Shortcuts", self.show_shortcuts, QKeySequence("Ctrl+/")
        )
        help_menu.addSeparator()
        about = help_menu.addAction("&About Dubcrate", self.show_about)
        about.setMenuRole(QAction.MenuRole.AboutRole)
        self.act_notices = help_menu.addAction(
            "Third-Party &Notices…", self.show_notices
        )
        self.act_show_log = help_menu.addAction("Show &Log File", self.show_log_file)
        self.act_show_log.setToolTip(
            "Opens the folder with Dubcrate's log, to attach to a bug report."
        )
        self.act_updates = help_menu.addAction(
            "Check for &Updates…", lambda: self.updates.start(manual=True)
        )
        self.act_updates.setToolTip(
            "Asks GitHub whether a newer Dubcrate has been released. Nothing is downloaded or installed."
        )

    # -- theme / about ----------------------------------------------------

    def set_theme(self, value: str) -> None:
        settings.set_theme(value)
        theme.set_theme_preference(value)
        for key, action in self._theme_actions.items():
            action.setChecked(key == value)

    def _on_theme_changed(self) -> None:
        # the trees' icons are baked in when they are built (and nothing else changes)
        self.refresh_sources(reload=False)
        self.refresh_discs()

    def _sync_wave_actions(self) -> None:
        current = settings.get_wave_style()
        for key, action in self._wave_actions.items():
            action.setChecked(key == current)

    def focus_search(self) -> None:
        """Put the cursor in the search box, with what was typed selected to type over."""
        if (
            self.center.currentIndex() != 0
        ):  # a disc is open: searching is for the library
            self.tree.setCurrentIndex(find_selection(self.tree.model(), ("all",)))
            self.center.setCurrentIndex(0)
        self.search.setFocus()
        self.search.selectAll()

    def leave_search(self, *, pick_first: bool = False) -> None:
        """Out of the search box into the list on show (the tracks, or the open disc), where Space
        plays and pauses again. The search itself stays as typed.

        Escape just moves the focus. Enter (`pick_first`) also picks the list's first row, after
        applying a search that is still waiting for its pause, so it is the first of what is shown;
        it neither loads nor plays it (Return in the list does that)."""
        shown = (
            self.disc_view.table
            if self.center.currentIndex() == 1
            else self.track_table
        )
        if pick_first and self._search_timer.isActive():
            self._search_timer.stop()
            self.refresh_tracks()
        shown.setFocus()
        if pick_first and shown.model().rowCount() > 0:
            shown.selectRow(0)

    def show_help(self) -> None:
        show_help_dialog(self)

    def show_shortcuts(self) -> None:
        """The list of shortcuts, in a window that stays open beside your work. Built each time,
        from the menus as they are now."""
        if self.shortcuts_dialog is not None:
            self.shortcuts_dialog.close()
        self.shortcuts_dialog = ShortcutsDialog(self)
        self.shortcuts_dialog.setWindowModality(Qt.WindowModality.NonModal)
        self.shortcuts_dialog.show()
        self.shortcuts_dialog.raise_()

    def show_about(self) -> None:
        AboutDialog(self, self.updates, icon_path()).exec()

    def show_notices(self) -> None:
        NoticesDialog(self).exec()

    def show_log_file(self) -> None:
        """Open the folder with the log (made first, if nothing has been logged yet)."""
        folder = logsetup.log_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

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
        """Open the burn window for the open disc."""
        disc_id = self.disc_view.current_disc_id()
        if disc_id is None:
            self._notify("Burn Disc", "Open a disc and add some tracks first.")
            return
        self.burn_dialog = BurnDialog(
            self._lib, disc_id, self, burner_factory=self._burner_factory
        )
        self.burn_dialog.burned.connect(self._on_burned)
        self.burn_dialog.finished.connect(lambda _r: self.disc_view.reload())
        self.burn_dialog.open()

    def print_labels(self, disc_id: int | None = None) -> None:
        """Open the label printing window with a disc ticked: the one given, else the open one."""
        if disc_id is None:
            disc_id = self.disc_view.current_disc_id()
        self.label_dialog = LabelDialog(self._lib, self, picked=disc_id)
        self.label_dialog.open()

    def _on_burned(self, disc_id: int) -> None:
        self.refresh_discs()  # the disc's tooltip now says when it was burned
        name = self._lib.disc(disc_id).name
        self.statusBar().showMessage(
            f"Burn finished: “{name}” is marked as burned", 10000
        )

    def _notify(self, title: str, text: str) -> None:
        QMessageBox.information(self, title, text)

    def _warn(self, title: str, text: str) -> None:
        QMessageBox.warning(self, title, text)

    # -- sources ----------------------------------------------------------

    def refresh_sources(
        self, select: tuple | None = None, *, reload: bool = True
    ) -> None:
        """Rebuild the library tree and select `select` (default: what was selected).

        With `reload` False (a theme change) nothing is reloaded unless what was selected is gone.
        """
        target = select or self._library_selection
        expanded = self._expanded_keys(self.tree)
        model = build_source_model(self._lib)
        self.tree.setModel(model)
        tree_header = self.tree.header()
        tree_header.setStretchLastSection(False)
        tree_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        tree_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        if len(self._lib.sources()) != 1:
            self.tree.expandToDepth(
                0
            )  # each source opened; with one, its folders stay shut
        self._restore_expanded(self.tree, expanded)
        index = find_selection(model, target)
        replaced = not index.isValid()  # what was selected is gone (a removed source)
        if replaced:
            index = find_selection(model, ("all",))
        self.tree.setCurrentIndex(index)  # (before connecting: this is not a change)
        self.tree.selectionModel().currentChanged.connect(self._on_tree_current_changed)
        if reload or replaced:
            self._on_tree_current_changed(
                index, QModelIndex()
            )  # (the same index isn't a change for Qt)

    def _expanded_keys(self, view: QTreeView) -> set[tuple]:
        model = view.model()
        keys: set[tuple] = set()
        if model is None:
            return keys
        stack = [model.index(row, 0) for row in range(model.rowCount())]
        while stack:
            index = stack.pop()
            if view.isExpanded(index):
                keys.add(index.data(SELECTION_ROLE))
            stack += [
                model.index(row, 0, index) for row in range(model.rowCount(index))
            ]
        return keys

    @staticmethod
    def _restore_expanded(view: QTreeView, keys: set[tuple]) -> None:
        for key in keys:  # folders that were open stay open
            index = find_selection(view.model(), key)
            if index.isValid():
                view.setExpanded(index, True)

    def _on_tree_current_changed(
        self, current: QModelIndex, _previous: QModelIndex
    ) -> None:
        current = current.siblingAtColumn(0)  # (a click on the count column)
        selection = current.data(SELECTION_ROLE) if current.isValid() else None
        if selection is None:
            return
        self._library_selection = selection
        if self.source_tabs.current() != "library":
            return  # (the Explorer is showing: this is picked again when its tab is left)
        self._selection = selection
        self.center.setCurrentIndex(0)  # a library item shows its tracks
        self.tracks_title.setText(current.data(NAME_ROLE))
        self.refresh_tracks()
        self._update_source_actions()

    def _on_tree_clicked(self, index: QModelIndex) -> None:
        """Clicking what is already picked in the library shows its tracks again: with a disc
        open in the middle, picking the same playlist is not a change for Qt, so say it here."""
        selection = index.siblingAtColumn(0).data(SELECTION_ROLE)
        if selection is not None and selection == self._selection:
            self.center.setCurrentIndex(0)

    # -- the left tabs ----------------------------------------------------

    def _on_left_tab(self, key: str) -> None:
        """The Library tab shows the library tree's pick in the track list; the Explorer shows
        the folder picked in its own tree."""
        self.source_pages.setCurrentWidget(self._left_pages[key])
        if self._persist:
            settings.set_left_tab(key)
        if key == "library":
            current = self.tree.currentIndex()
            if current.isValid():
                self._on_tree_current_changed(current, QModelIndex())
            return
        if key == "explorer":
            self.explorer_tree.refresh_roots()  # (a drive may have been plugged in since)
            path = self._explorer_path or self.explorer_tree.current_path()
        else:  # a pinned folder: its folder (or one inside it) is read again each time
            self._pin_trees[key].refresh_roots()  # (and a folder made inside it since)
            path = self._pin_trees[key].current_path()
        if path:
            self._show_explorer_folder(path)
            return
        self._selection = ("explorer", "")
        self.center.setCurrentIndex(0)
        self.tracks_title.setText("Explorer")
        self.refresh_tracks()
        self.tracks_count.setText("Pick a folder on the left")

    def _on_explorer_folder(self, path: str) -> None:
        self._explorer_path = path
        if self._persist:
            settings.set_explorer_path(path)
        if self.source_tabs.current() == "explorer":
            self._show_explorer_folder(path)

    # -- pinned folders ---------------------------------------------------

    def pin_folder(self, path: str) -> None:
        """Give a folder a tab of its own, below the Explorer. The window stays where it is."""
        path = str(Path(path))
        name = Path(path).name or path
        if path in self._pins:
            self.statusBar().showMessage(f"“{name}” is already pinned", 4000)
            return
        self._add_pinned_tab(path)
        self._save_pins()
        self.statusBar().showMessage(f"Pinned “{name}”", 4000)

    def unpin_folder(self, path: str) -> None:
        """Take a folder's tab away (the folder itself is not touched)."""
        key = settings.PIN_PREFIX + str(Path(path))
        if key not in self._pin_trees:
            return
        self._pins.remove(key[len(settings.PIN_PREFIX) :])
        del self._pin_trees[key]
        self.source_tabs.remove_tab(key)  # (picks another tab if this one was showing)
        page = self._left_pages.pop(key)
        self.source_pages.removeWidget(page)
        page.deleteLater()
        self._save_pins()

    def _add_pinned_tab(self, path: str) -> None:
        key = settings.PIN_PREFIX + path
        if key in self._pin_trees:
            return
        name = Path(path).name or path
        # (the tab is named for the folder, so the tree lists what is inside it, not the folder again)
        tree = ExplorerTree(contents_of=Path(path))
        tree.folder_selected.connect(lambda p, k=key: self._on_pinned_folder(k, p))
        tree.pin_requested.connect(self.pin_folder)
        tree.unpin_requested.connect(self.unpin_folder)
        tree.refresh_requested.connect(self._on_refresh_folder)
        page = self._folder_page(name, tree)
        self.source_pages.addWidget(page)
        self._left_pages[key] = page
        self._pin_trees[key] = tree
        self._pins.append(path)
        self.source_tabs.add_tab(
            key,
            name,
            pinned_glyph,
            f"{path}\nDrag to reorder, right-click to unpin",
            movable=True,
        )

    def _save_pins(self) -> None:
        pinned = set(self._pins)
        self.explorer_tree.set_pinned(pinned)
        for tree in self._pin_trees.values():
            tree.set_pinned(pinned)
        if self._persist:
            settings.set_pinned_folders(self._pins)

    def _on_refresh_folder(self, path: str) -> None:
        """Refresh on the folder that is showing reads its files again too."""
        if self._selection == ("explorer", path):
            self._show_explorer_folder(path)

    def _on_pinned_folder(self, key: str, path: str) -> None:
        if self.source_tabs.current() == key:
            self._show_explorer_folder(path)

    def move_pin(self, path: str, offset: int) -> None:
        """Move a pinned folder's tab up (negative) or down; pins stay below Library and Explorer."""
        path = str(Path(path))
        if path not in self._pins:
            return
        old = self._pins.index(path)
        new = max(0, min(len(self._pins) - 1, old + offset))
        if new == old:
            return
        self._pins.insert(new, self._pins.pop(old))
        self.source_tabs.move_tab(settings.PIN_PREFIX + path, new - old)
        self._save_pins()

    def _on_tabs_dragged(self) -> None:
        """A pinned tab was dragged to a new place: keep the pins in the strip's order."""
        prefix = settings.PIN_PREFIX
        order = self.source_tabs.keys()
        self._pins = [k[len(prefix) :] for k in order if k in self._pin_trees]
        self._save_pins()

    def _tab_menu(self, key: str) -> QMenu | None:
        if not key.startswith(settings.PIN_PREFIX):
            return None
        path = key[len(settings.PIN_PREFIX) :]
        place = self._pins.index(path)
        menu = QMenu(self)
        menu.addAction("Move Up", lambda: self.move_pin(path, -1)).setEnabled(place > 0)
        menu.addAction("Move Down", lambda: self.move_pin(path, 1)).setEnabled(
            place < len(self._pins) - 1
        )
        menu.addSeparator()
        menu.addAction("Unpin Folder", lambda: self.unpin_folder(path))
        return menu

    def _on_tab_menu(self, key: str, where) -> None:
        menu = self._tab_menu(key)
        if menu is not None:
            menu.exec(where)

    def _show_explorer_folder(self, path: str) -> None:
        """Show what is known of a folder at once, and read it again in the background."""
        self._selection = ("explorer", path)
        self.center.setCurrentIndex(0)
        self.tracks_title.setText(Path(path).name or path)
        self.refresh_tracks()
        self._scan_explorer_folder(path)

    def _scan_explorer_folder(self, path: str) -> None:
        node = self._lib.explorer_node(path)
        known = (
            {t.path: t for t in self._lib.tracks(node_id=node)}
            if node is not None
            else {}
        )  # (the worker must not touch the library)
        name = Path(path).name or path
        self.statusBar().showMessage(f"Reading {name}…")

        def work() -> DirectoryScan:
            try:
                return scan_directory(Path(path), known)
            except PermissionError:
                raise TaskError(
                    f"Dubcrate isn't allowed to read “{name}”. On a Mac, allow it under "
                    "System Settings > Privacy & Security > Files & Folders."
                ) from None
            except (FileNotFoundError, NotADirectoryError):
                raise TaskError(f"“{name}” is no longer there.") from None
            except OSError as exc:
                raise TaskError(
                    f"Couldn't read “{name}”: {exc.strerror or exc}"
                ) from None

        run_async(
            work,
            lambda scan: self._explorer_scanned(path, scan),
            self._explorer_failed,
        )

    def _explorer_scanned(self, path: str, scan: DirectoryScan) -> None:
        self._lib.sync_directory(path, scan.tracks)
        self._sync_disc_markers()  # (new rows may be the twin of a track on a disc)
        if scan.errors:
            self.statusBar().showMessage(
                f"Couldn't read {len(scan.errors)} file(s) in {Path(path).name or path}",
                10000,
            )
        else:
            self.statusBar().clearMessage()
        if self._selection == ("explorer", path):
            self.refresh_tracks()

    def _explorer_failed(self, message: str) -> None:
        self.statusBar().showMessage(message, 15000)

    # -- the discs (right pane) -------------------------------------------

    def refresh_discs(self) -> None:
        """Rebuild the disc tree after a disc or folder changed (names, counts, filing).

        It leaves the track list and what is shown in the middle alone.
        """
        expanded = self._expanded_keys(self.disc_tree)
        open_id = self.disc_view.current_disc_id()
        model = build_disc_model(self._lib, open_id)
        model.tracks_dropped.connect(self._on_tracks_dropped)
        model.tracks_dropped_in_dir.connect(self._on_tracks_dropped_in_dir)
        model.rows_dropped.connect(self._on_rows_dropped)
        model.items_moved.connect(self.disc_view.move_items)
        model.renamed.connect(self._on_tree_renamed)
        self.disc_tree.setModel(model)
        header = self.disc_tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)  # the + button
        header.setMinimumSectionSize(COLUMN_WIDTH)
        header.resizeSection(2, COLUMN_WIDTH)
        self._restore_expanded(self.disc_tree, expanded)
        index = (
            find_selection(model, self._disc_pick) if self._disc_pick else QModelIndex()
        )
        if not index.isValid() and open_id is not None:
            index = find_selection(model, ("disc", open_id))
        self._disc_pick = index.data(SELECTION_ROLE) if index.isValid() else None
        if index.isValid():
            self.disc_tree.setCurrentIndex(index)  # (before connecting: not a change)
        self.disc_tree.selectionModel().currentChanged.connect(
            self._on_disc_current_changed
        )
        if open_id is None and self.center.currentIndex() == 1:
            self.center.setCurrentIndex(
                0
            )  # the open disc was deleted: back to the library

    def _on_disc_current_changed(
        self, current: QModelIndex, _previous: QModelIndex
    ) -> None:
        selection = (
            current.siblingAtColumn(0).data(SELECTION_ROLE)
            if current.isValid()
            else None
        )
        self._disc_pick = selection
        self._open_pick(selection)

    def _navigate_to(self, disc_id: int, dir_id: object) -> None:
        """A step of the path above the tracks was clicked: go to that folder of the disc (or
        the disc itself), picking its row in the Discs list."""
        selection = (
            ("disc_dir", disc_id, dir_id) if dir_id is not None else ("disc", disc_id)
        )
        index = find_selection(self.disc_tree.model(), selection)
        if index.isValid():
            parent = index.parent()
            while parent.isValid():
                self.disc_tree.setExpanded(parent, True)
                parent = parent.parent()
            self.disc_tree.setCurrentIndex(index)
        self._disc_pick = selection
        self._open_pick(selection)

    def _on_disc_clicked(self, index: QModelIndex) -> None:
        """One click opens a disc, even if it is the one already picked (with the library
        showing in the middle, that is not a change for Qt)."""
        self._open_pick(index.siblingAtColumn(0).data(SELECTION_ROLE))

    def _open_pick(self, selection: tuple | None) -> None:
        if selection and selection[0] in ("disc", "disc_dir"):
            disc_id = selection[1]
            dir_id = selection[2] if selection[0] == "disc_dir" else None
            if (
                self.disc_view.current_disc_id() != disc_id
                or self.disc_view.current_dir_id() != dir_id
            ):
                # opening a disc makes it the target
                self.disc_view.set_disc(disc_id, dir_id)
            self.center.setCurrentIndex(1)

    def open_disc(self, disc_id: int) -> None:
        """Open a disc in the middle (and select it in the disc tree)."""
        self.disc_view.set_disc(disc_id)
        self.refresh_discs()
        index = find_selection(self.disc_tree.model(), ("disc", disc_id))
        if index.isValid():
            self.disc_tree.setCurrentIndex(index)
        self.center.setCurrentIndex(1)  # (even if it was already the selected one)

    def _on_tracks_dropped(self, disc_id: int, track_ids: list) -> None:
        self.disc_view.add_tracks(track_ids, disc_id=disc_id)

    def _on_tracks_dropped_in_dir(
        self, disc_id: int, dir_id: int, track_ids: list
    ) -> None:
        """Library tracks dropped on a folder of an MP3 disc go into that folder."""
        self.disc_view.add_tracks(track_ids, disc_id=disc_id, dir_id=dir_id)

    def _on_rows_dropped(self, disc_id: int, dir_id: object, rows: list) -> None:
        """Tracks of the open disc dropped on a folder (or on the disc, for its root)."""
        if disc_id == self.disc_view.current_disc_id():
            self.disc_view.move_rows_to_dir(rows, dir_id)  # type: ignore[arg-type]

    def _folder_of(self, pick: tuple | None) -> int | None:
        """The folder a new disc beside `pick` goes in: the folder itself, or the one
        holding a disc; the top level if there is no pick."""
        if pick is None:
            return None
        if pick[0] == "disc_folder":
            return pick[1]
        return self._lib.disc(pick[1]).folder_id  # a disc, or a folder on one

    def _folder_here(self) -> int | None:
        """The folder a new disc or folder goes in: the one picked in the disc tree, or the
        one holding the picked disc, else the top level."""
        return self._folder_of(self._disc_pick)

    def _on_plus_clicked(self, pick: tuple) -> None:
        if pick[0] == "disc_dir":  # on a folder of an MP3 disc: a folder beside it
            beside = next(
                d.parent_id for d in self._lib.disc_dirs(pick[1]) if d.id == pick[2]
            )
            self.new_disc_dir(pick[1], beside)
        else:
            self.new_disc(self._folder_of(pick))

    def _on_new_folder_clicked(self, pick: tuple) -> None:
        """The folder button: a folder inside the disc (its root) or inside the folder picked."""
        self.new_disc_dir(pick[1], pick[2] if pick[0] == "disc_dir" else None)

    def new_disc_dir(self, disc_id: int, parent_id: int | None = None) -> None:
        """Make a folder on an MP3 disc and put its name in the tree up for editing."""
        dir_id = self.disc_view.new_dir(disc_id, parent_id)
        if dir_id is None:
            return
        self.edit_in_tree(("disc_dir", disc_id, dir_id))

    def new_disc(
        self, folder_id: int | None | object = ..., kind: DiscKind = "audio"
    ) -> None:
        """Make a disc (an audio CD, or an MP3 CD) and put its name in the tree up for editing."""
        folder = self._folder_here() if folder_id is ... else folder_id
        self.edit_in_tree(("disc", self.disc_view.new_disc(folder, kind)))

    def new_disc_folder(self, parent_id: int | None | object = ...) -> None:
        """Make a folder and put its name in the tree up for editing."""
        parent = self._folder_here() if parent_id is ... else parent_id
        self.edit_in_tree(("disc_folder", self.disc_view.new_folder(parent)))

    def edit_in_tree(self, selection: tuple) -> None:
        """Pick a disc or folder in the disc tree and start editing its name there."""
        index = find_selection(self.disc_tree.model(), selection)
        if not index.isValid():
            return
        parent = index.parent()
        while parent.isValid():  # (it may be inside a folder that is closed)
            self.disc_tree.setExpanded(parent, True)
            parent = parent.parent()
        self.disc_tree.setFocus()
        self.disc_tree.setCurrentIndex(index)
        self.disc_tree.scrollTo(index)
        self.disc_tree.edit(index)

    def picked_disc_item(self) -> tuple | None:
        """What is picked in the Discs list, ("disc", id) | ("disc_folder", id) | ("disc_dir",
        disc id, folder id); with nothing picked, the open disc."""
        index = self.disc_tree.currentIndex()
        selection = (
            index.siblingAtColumn(0).data(SELECTION_ROLE) if index.isValid() else None
        )
        if selection is None and self.disc_view.current_disc_id() is not None:
            selection = ("disc", self.disc_view.current_disc_id())
        return selection

    def rename_picked(self) -> None:
        selection = self.picked_disc_item()
        if selection is not None:
            self.edit_in_tree(selection)

    def delete_picked(self) -> None:
        selection = self.picked_disc_item()
        if selection is not None:
            self.disc_view.delete_item(selection)

    def _on_tree_renamed(self, selection: tuple, name: str) -> None:
        # Apply it once the editor has finished: rebuilding the tree inside its own
        # setData would pull the editor out from under Qt.
        QTimer.singleShot(0, lambda: self._rename(selection, name))

    def _rename(self, selection: tuple, name: str) -> None:
        if selection[0] == "disc":
            self.disc_view.rename_disc(selection[1], name)
        elif selection[0] == "disc_dir":
            self.disc_view.rename_dir(selection[1], selection[2], name)
        else:
            self.disc_view.rename_folder(selection[1], name)

    def disc_tree_menu(self, index: QModelIndex) -> QMenu:
        """What a right-click offers: on a disc, on a folder, or on empty space."""
        selection = (
            index.siblingAtColumn(0).data(SELECTION_ROLE) if index.isValid() else None
        )
        kind = selection[0] if selection else None
        menu = QMenu(self)
        if kind == "disc":
            disc_id = selection[1]
            menu.addAction("Open", lambda: self.open_disc(disc_id))
            menu.addAction("Rename", lambda: self.edit_in_tree(("disc", disc_id)))
            menu.addAction("Duplicate", lambda: self.disc_view.duplicate_disc(disc_id))
            if self._lib.disc(disc_id).kind == "mp3":
                menu.addAction("New Folder on Disc", lambda: self.new_disc_dir(disc_id))
            if self._lib.disc(disc_id).kind == "audio":
                menu.addAction(
                    "Make MP3 CD", lambda: self.disc_view.set_disc_kind(disc_id, "mp3")
                )
            else:
                menu.addAction(
                    "Make Audio CD",
                    lambda: self.disc_view.set_disc_kind(disc_id, "audio"),
                )
            menu.addSeparator()
            menu.addAction("Burn…", lambda: self._open_and_burn(disc_id))
            menu.addAction("Print Labels…", lambda: self.print_labels(disc_id))
            menu.addSeparator()
            menu.addAction("Delete…", lambda: self.disc_view.delete_disc(disc_id))
        elif kind == "disc_dir":
            disc_id, dir_id = selection[1], selection[2]
            menu.addAction("Open", lambda: self._open_pick(selection))
            menu.addAction(
                "New Folder Inside", lambda: self.new_disc_dir(disc_id, dir_id)
            )
            menu.addAction("Rename", lambda: self.edit_in_tree(selection))
            menu.addSeparator()
            menu.addAction(
                "Delete Folder from Disc…",
                lambda: self.disc_view.delete_dir(disc_id, dir_id),
            )
        elif kind == "disc_folder":
            folder_id = selection[1]
            menu.addAction("New Disc Here", lambda: self.new_disc(folder_id))
            menu.addAction(
                "New MP3 Disc Here", lambda: self.new_disc(folder_id, kind="mp3")
            )
            menu.addAction("New Folder Here", lambda: self.new_disc_folder(folder_id))
            menu.addSeparator()
            menu.addAction(
                "Rename", lambda: self.edit_in_tree(("disc_folder", folder_id))
            )
            menu.addAction(
                "Delete Folder…", lambda: self.disc_view.delete_folder(folder_id)
            )
        else:  # empty space: the top level
            menu.addAction("New Disc", lambda: self.new_disc(None))
            menu.addAction("New MP3 Disc", lambda: self.new_disc(None, kind="mp3"))
            menu.addAction("New Folder", lambda: self.new_disc_folder(None))
        return menu

    def _show_disc_tree_menu(self, position) -> None:
        menu = self.disc_tree_menu(self.disc_tree.indexAt(position))
        menu.popup(self.disc_tree.viewport().mapToGlobal(position))

    def _open_and_burn(self, disc_id: int) -> None:
        self.open_disc(disc_id)
        self.burn_current_disc()

    def selected_source_id(self) -> int | None:
        """The source that Re-import and Remove act on: the one the pick is in. With a single
        source its row is not shown, and "All Tracks" is that source."""
        kind = self._selection[0]
        if kind == "source":
            return self._selection[1]
        if kind == "node":
            return self._selection[2]
        if kind == "all":
            sources = self._lib.sources()
            return sources[0].id if len(sources) == 1 else None
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
        log.info(
            "imported %s %s: %d new, %d updated, %d removed, %d notes",
            kind,
            path,
            summary.added,
            summary.updated,
            summary.removed,
            len(summary.notes),
        )
        self._set_busy(False)
        self.refresh_sources(select=("source", summary.source_id))
        self.disc_view.reload()  # a re-import can mark tracks on a disc as no longer in their source
        self.refresh_discs()
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
        self._library_selection = ("all",)
        if self.source_tabs.current() == "library":
            self._selection = ("all",)
        self.refresh_sources()
        self.disc_view.reload()
        self.refresh_discs()

    # -- tracks -----------------------------------------------------------

    def refresh_tracks(self) -> None:
        key, descending = self._sort
        filters: dict = {}
        explorer_node = None
        if self._selection[0] == "source":
            filters["source_id"] = self._selection[1]
        elif self._selection[0] == "node":
            filters["node_id"] = self._selection[1]
        elif self._selection[0] == "explorer":
            explorer_node = self._lib.explorer_node(self._selection[1])
            if explorer_node is not None:
                filters["node_id"] = explorer_node
        if self._selection[0] == "explorer" and explorer_node is None:
            tracks = []  # (a folder that has not been read yet, or none picked)
        else:
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

    def load_highlighted_track(self) -> None:
        """Return in the track list: load the highlighted track into the player, as a
        double-click does (the one with the focus, if several are selected)."""
        table = self.track_table
        current = table.currentIndex()
        selected = {i.row() for i in table.selectionModel().selectedRows()}
        row = (
            current.row()
            if current.isValid() and current.row() in selected
            else min(selected, default=None)
        )
        if row is not None:
            self.preview.load(self.track_model.track_at(row))

    def _on_track_double_clicked(self, index: QModelIndex) -> None:
        self.preview.load(self.track_model.track_at(index.row()))

    def _step_list(self) -> tuple[list[Track], QTableView]:
        """The tracks Previous / Next walk (the open disc's, in its order, else the track
        list's) and the table that shows them."""
        if (
            self.center.currentIndex() == 1
        ):  # a disc is open: step through it, in its order
            model = self.disc_view.model
            tracks = [model.entry_at(r).track for r in range(model.rowCount())]
            return tracks, self.disc_view.table
        return self.track_model.tracks(), self.track_table

    def _step_track(self, delta: int) -> None:
        """Load the track before or after the loaded one in the list (or the selected one)."""
        tracks, table = self._step_list()
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
            chosen = table.selectionModel().selectedRows()
            row = chosen[0].row() if chosen else (-1 if delta > 0 else len(tracks))
        target = row + delta
        if 0 <= target < len(tracks):
            table.selectRow(target)
            self.preview.load(tracks[target])

    def _play_on_after_end(self) -> None:
        """A track played to its end: go on with the next one in the list, and stop after the
        last. Only from a track that is in the list on show (so changing lists mid-song never
        starts something unexpected), skipping files that are missing."""
        tracks, table = self._step_list()
        current, requested = self.preview.track, self.preview.requested
        if current is None or requested is None or requested.id != current.id:
            return  # the user has already asked for something else
        row = next((i for i, t in enumerate(tracks) if t.id == current.id), None)
        if row is None:
            return
        target = next(
            (i for i in range(row + 1, len(tracks)) if not tracks[i].missing), None
        )
        if target is not None:
            table.selectRow(target)
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
        self.disc_view.add_tracks(self.selected_track_ids())

    def fit_all_to_disc(self) -> None:
        self.disc_view.add_tracks([t.id for t in self.track_model.tracks()], fit=True)

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
            self.disc_view.split([t.id for t in tracks], name.strip())

    def _sync_disc_markers(self) -> None:
        """Mark the tracks on the open disc (●) and on any other disc (○), and say which disc
        adds go to."""
        disc_id = self.disc_view.current_disc_id()
        # (a file is marked under every row it has: rekordbox's and the Explorer's)
        self.track_model.set_on_disc(
            self._lib.same_file_ids(self.disc_view.current_track_ids()),
            self._lib.same_file_ids(self._lib.tracks_on_discs(exclude=disc_id)),
        )
        if disc_id is None:
            self.target_label.setText("No disc open: adding starts a new one")
        else:
            self.target_label.setText(f"Adding to “{self._lib.disc(disc_id).name}”")

    # -- the transport keys -----------------------------------------------

    def eventFilter(self, watched, event) -> bool:
        """Space plays or pauses, Left and Right beat jump, wherever focus is in this window.

        The two track lists handle these keys themselves, but focus is often elsewhere (the
        trees, the disc's title, a button), and then nothing happened. A modal dialog, a
        menu or another window in front gets its own keys, and so does a widget that is
        being typed into (see `transport_key_is_taken`).
        """
        if event.type() == QEvent.Type.KeyPress and self._owns_keys():
            action = transport_action(event)
            if action and not transport_key_is_taken(
                QApplication.focusWidget(), action
            ):
                if action == "play_pause":
                    if not event.isAutoRepeat():  # (holding Space must not flicker)
                        self.preview.toggle()
                else:
                    self.preview.beat_jump(-1 if action == "jump_back" else 1)
                return True
        return super().eventFilter(watched, event)

    def _owns_keys(self) -> bool:
        return (
            QApplication.activeWindow() is self
            and QApplication.activePopupWidget() is None
        )

    # -- closing ----------------------------------------------------------

    def closeEvent(self, event) -> None:
        QApplication.instance().removeEventFilter(self)
        self.updates.close()
        self._search_timer.stop()
        self._preview_timer.stop()
        self.preview.shutdown()
        if self._persist:
            settings.set_bytes("window/geometry", self.saveGeometry())
            settings.set_bytes("window/splitter3", self.splitter.saveState())
        super().closeEvent(event)
