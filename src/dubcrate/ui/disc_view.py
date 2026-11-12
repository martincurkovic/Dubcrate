"""A disc, in the middle of the window: its tracks as they will be written on the CD, how
full it is, and everything for managing discs and the folders they are filed in.

A disc is just a playlist. Opening one (clicking it in the tree) shows it here and makes it
the *target*: the disc that tracks are added to (Return, the Add button, or dragging onto it).
The target stays the same while you browse your library, so you can pick tracks and add them.
"""

from html import escape

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from dubcrate import settings
from dubcrate.cd import MAX_TRACKS, SECTORS_PER_SECOND
from dubcrate.library import Library
from dubcrate.library.lengths import needs_measuring, probe_lengths
from dubcrate.library.models import DiscKind
from dubcrate.library.store import LibraryError
from dubcrate.mp3 import format_span

from . import theme
from .capacity_bar import CapacitySummary
from .models import DiscModel
from .overlaps import overlap_message
from .tasks import run_async
from .track_table import transport_action


def _things(n: int, noun: str) -> str:
    """ "1 disc", "3 discs", "no folders": for the question before a delete."""
    return f"{n} {noun}{'s' if n != 1 else ''}" if n else f"no {noun}s"


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
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and not (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):  # (with ⌘ it is the shortcut that deletes the disc, not the tracks)
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
    # a step of the path above the tracks was clicked: (disc id, folder id or None for the disc)
    navigate_requested = Signal(int, object)
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
        self._dir_id: int | None = (
            None  # the folder of an MP3 disc being shown; None: the root
        )
        self._mp3_declined: set[int] = (
            set()
        )  # discs where "make it an MP3 CD?" got a no

        self.title = QLabel()
        self.title.setObjectName("panelTitle")
        self.title.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.title.linkActivated.connect(self._on_crumb)
        self._crumbs: list[str] = []  # the path shown, as plain names
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
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(
            lambda pos: self.table_menu().popup(self.table.viewport().mapToGlobal(pos))
        )
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

    def current_dir_id(self) -> int | None:
        """The folder of the open (MP3) disc whose tracks are shown; None for the root."""
        return self._dir_id

    def set_disc(self, disc_id: int | None, dir_id: int | None = None) -> None:
        """Open a disc: show it, and make it the one that tracks are added to. For an MP3 disc,
        `dir_id` is the folder to show and add to (None: the disc's root)."""
        self._disc_id = disc_id
        self._dir_id = dir_id
        self._show_disc()

    def _set_title(self, name: str, disc_id: int) -> None:
        """The disc's name, and when a folder is open the path to it, each step but the last a
        link back to that level (the disc's name goes to its root)."""
        chain = self._dir_chain(disc_id, self._dir_id)
        self._crumbs = [name, *(d.name for d in chain)]
        if not chain:
            self.title.setTextFormat(Qt.TextFormat.PlainText)
            self.title.setText(name)
            return
        palette = theme.current_palette()
        # (the colour goes in the text: the app's stylesheet leaves a label's link palette alone)
        color = palette.get("link") or palette["accent"]
        steps = [f'<a href="root">{escape(name)}</a>']
        steps += [f'<a href="dir:{d.id}">{escape(d.name)}</a>' for d in chain[:-1]]
        steps.append(escape(chain[-1].name))  # where you are: not a link
        self.title.setTextFormat(Qt.TextFormat.RichText)
        self.title.setText(
            f"<style>a {{ color: {color}; }}</style>" + " / ".join(steps)
        )

    def crumb_names(self) -> list[str]:
        """The path shown above the tracks as plain names: the disc, then each folder."""
        return list(self._crumbs)

    def _on_crumb(self, href: str) -> None:
        if self._disc_id is not None:
            dir_id = None if href == "root" else int(href.removeprefix("dir:"))
            self.navigate_requested.emit(self._disc_id, dir_id)

    def _dir_chain(self, disc_id: int, dir_id: int | None) -> list:
        """The folders from the top of the disc down to `dir_id`."""
        names = {d.id: d for d in self._lib.disc_dirs(disc_id)}
        chain = []
        while dir_id is not None and dir_id in names:
            chain.append(names[dir_id])
            dir_id = names[dir_id].parent_id
        return chain[::-1]

    def _dir_path(self, disc_id: int, dir_id: int | None) -> str:
        """A folder's place on its disc, as "House / Deep" ("" for the root)."""
        names = {d.id: d for d in self._lib.disc_dirs(disc_id)}
        parts = []
        while dir_id is not None and dir_id in names:
            parts.append(names[dir_id].name)
            dir_id = names[dir_id].parent_id
        return " / ".join(reversed(parts))

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
            if self._dir_id not in {
                None,
                *(d.id for d in self._lib.disc_dirs(disc_id)),
            }:
                self._dir_id = None  # that folder is gone
            entries = self._lib.disc_entries(disc_id)
            shown = [e for e in entries if e.dir_id == self._dir_id]
            self.model.set_entries(shown)
            self.summary.show_disc(summary, entries)  # the bar is the whole disc
            self._set_title(summary.name, disc_id)
            n = summary.track_count
            here = (
                f"  ·  {len(shown)} here"
                if summary.kind == "mp3" and len(shown) != n
                else ""
            )
            self.count.setText(
                f"{'MP3 CD  ·  ' if summary.kind == 'mp3' else ''}"
                f"{n} track{'s' if n != 1 else ''}{here}  ·  {format_span(summary.used_sectors, summary.kind)}"
                f" of {format_span(summary.capacity_sectors, summary.kind)}"
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

    def _create(
        self, name: str, folder_id: int | None = None, kind: DiscKind = "audio"
    ) -> int:
        """A new disc with the size (and, for audio, the gap) chosen in Settings."""
        return self._lib.create_disc(
            name,
            capacity_sectors=settings.get_disc_minutes() * 60 * SECTORS_PER_SECOND,
            pregap_sectors=settings.get_pregap_seconds() * SECTORS_PER_SECOND,
            folder_id=folder_id,
            kind=kind,
            mp3_bitrate=settings.get_mp3_bitrate() if kind == "mp3" else None,
        )

    def new_disc(self, folder_id: int | None = None, kind: DiscKind = "audio") -> int:
        """Make a disc (in `folder_id`) with a name that is free, and open it. The window
        then puts its name in the tree up for editing."""
        taken = {d.name for d in self._lib.discs()}
        base = "MP3 Disc" if kind == "mp3" else "Disc"
        n = len(taken) + 1
        while f"{base} {n}" in taken:
            n += 1
        disc_id = self._create(f"{base} {n}", folder_id, kind)
        self.set_disc(disc_id)
        self.discs_changed.emit()
        self.opened.emit(disc_id)
        return disc_id

    def rename_disc(self, disc_id: int, name: str) -> None:
        name = name.strip()
        if not name or name == self._lib.disc(disc_id).name:
            return
        self._lib.update_disc(disc_id, name=name)
        if disc_id == self._disc_id:
            self._show_disc()
        self.discs_changed.emit()

    def delete_disc(self, disc_id: int | None = None, ask: bool = True) -> None:
        disc_id = self._disc_id if disc_id is None else disc_id
        if disc_id is None:
            return
        name = self._lib.disc(disc_id).name
        if ask:
            answer = QMessageBox.question(
                self,
                "Delete Disc",
                f'Delete "{name}"?\nThe tracks stay in your library; only this disc is removed.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._lib.delete_disc(disc_id)
        self.reload()
        self.discs_changed.emit()

    def is_empty(self, selection: tuple) -> bool:
        """Whether a row of the Discs list holds nothing: a disc with no tracks and no folders, a
        folder with no discs or folders in it, a folder on an MP3 disc with no tracks or folders."""
        kind = selection[0]
        if kind == "disc":
            return not self._lib.disc(selection[1]).track_count and not self._lib.disc_dirs(selection[1])  # fmt: skip
        if kind == "disc_folder":
            return not any(d.folder_id == selection[1] for d in self._lib.discs()) and not any(f.parent_id == selection[1] for f in self._lib.disc_folders())  # fmt: skip
        if kind == "disc_dir":
            disc_id, dir_id = selection[1], selection[2]
            return not self._lib.dir_file_counts(disc_id).get(dir_id) and not any(d.parent_id == dir_id for d in self._lib.disc_dirs(disc_id))  # fmt: skip
        return False

    def delete_item(self, selection: tuple) -> None:
        """Delete a disc, a folder of discs, or a folder on an MP3 disc: straight away if it is
        empty, else after asking (Yes is the default answer, so Return confirms)."""
        ask = not self.is_empty(selection)
        match selection[0]:
            case "disc":
                self.delete_disc(selection[1], ask=ask)
            case "disc_folder":
                self.delete_folder(selection[1], ask=ask)
            case "disc_dir":
                self.delete_dir(selection[1], selection[2], ask=ask)

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

    def new_folder(self, parent_id: int | None = None) -> int:
        """Make a folder with a name that is free; the window then puts it up for editing."""
        taken = {f.name for f in self._lib.disc_folders()}
        name, n = "New Folder", 2
        while name in taken:
            name, n = f"New Folder {n}", n + 1
        folder_id = self._lib.create_disc_folder(name, parent_id)
        self.discs_changed.emit()
        return folder_id

    def rename_folder(self, folder_id: int, name: str) -> None:
        name = name.strip()
        current = next(f for f in self._lib.disc_folders() if f.id == folder_id).name
        if name and name != current:
            self._lib.rename_disc_folder(folder_id, name)
            self.discs_changed.emit()

    def delete_folder(self, folder_id: int, ask: bool = True) -> None:
        """Delete a folder of discs and everything in it: its discs, and its folders at any depth.
        The tracks on those discs stay in the library."""
        name = next(f for f in self._lib.disc_folders() if f.id == folder_id).name
        discs, folders = self._lib.disc_folder_contents(folder_id)
        if ask:
            answer = QMessageBox.question(
                self,
                "Delete Folder",
                f'Delete the folder "{name}"?\n'
                f"This also deletes {_things(discs, 'disc')} and {_things(folders, 'folder')} in it."
                "\nThe tracks stay in your library.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._lib.delete_disc_folder(folder_id)
        self.reload()  # (the open disc may have been in it)
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
            self._lib.reorder_disc(self._disc_id, rows, target, dir_id=self._dir_id)
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
        dir_id: int | None = None,
    ) -> None:
        """Add tracks to a disc: `disc_id`, else the open one (making one if there is none).

        Adding to a disc by name (a drop onto it in the tree) leaves the open disc as it is.
        With a `disc_id` that is not the open disc, `dir_id` is the folder of that MP3 disc they
        go to (the open disc takes them in the folder being shown).
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
                self._dir_id = None
            # into the folder being shown, if this is the disc being shown
            folder = (
                self._dir_id if disc_id is None else dir_id
            )  # (a drop names its own)
            try:
                result = self._lib.add_to_disc(
                    target,
                    track_ids,
                    position=position,
                    fit=fit,
                    strict=strict,
                    dir_id=folder,
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
            self._offer_mp3(target)

        self._measured(track_ids, add)

    def _offer_mp3(self, disc_id: int) -> None:
        """An audio CD that has just gone over what a CD holds: offer to make it an MP3 CD.
        Asked once per disc a session, so saying no isn't nagged about."""
        summary = self._lib.disc(disc_id)
        too_many = summary.track_count > MAX_TRACKS
        if summary.kind != "audio" or not (summary.over or too_many):
            return
        if disc_id in self._mp3_declined:
            return
        why = (
            f"has {summary.track_count} tracks, and an audio CD holds at most {MAX_TRACKS}"
            if too_many
            else f"is {format_span(-summary.free_sectors)} too long for an audio CD"
        )
        answer = QMessageBox.question(
            self,
            "Make this an MP3 CD?",
            f"“{summary.name}” {why}.\n\nMake it an MP3 CD instead? An MP3 CD is a data disc of"
            " MP3 files that holds hours of music, but it only plays in players that read MP3"
            " discs (the CDJ-1000MK3 does). Files that are already MP3 go on as they are; the"
            " rest are encoded.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.set_disc_kind(disc_id, "mp3")
        else:
            self._mp3_declined.add(disc_id)

    def set_disc_kind(self, disc_id: int, kind: DiscKind) -> None:
        """Make a disc an audio CD or an MP3 CD. Its tracks stay (an audio CD has no folders, so
        a disc's folders are dissolved, its tracks listed folder by folder)."""
        if kind == "audio" and self._lib.disc_dirs(disc_id):
            answer = QMessageBox.question(
                self,
                "Make this an audio CD?",
                "An audio CD has no folders. The folders on this disc will be removed and its"
                " tracks put in one list, folder by folder.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._lib.set_disc_kind(
            disc_id,
            kind,
            settings.get_mp3_bitrate() if kind == "mp3" else None,
        )
        self._mp3_declined.discard(disc_id)
        if disc_id == self._disc_id:
            self._show_disc()
        self.discs_changed.emit()
        name = self._lib.disc(disc_id).name
        self.message.emit(
            f"“{name}” is now {'an MP3 CD' if kind == 'mp3' else 'an audio CD'}"
        )

    # -- folders on an MP3 disc ---------------------------------------------

    def new_dir(self, disc_id: int, parent_id: int | None = None) -> int | None:
        """Make a folder on an MP3 disc with a name that is free there; the window then puts it
        up for editing. None (and a message) if the player's limits say no."""
        taken = {
            d.name.casefold()
            for d in self._lib.disc_dirs(disc_id)
            if d.parent_id == parent_id
        }
        name, n = "New Folder", 2
        while name.casefold() in taken:
            name, n = f"New Folder {n}", n + 1
        try:
            dir_id = self._lib.create_disc_dir(disc_id, name, parent_id)
        except LibraryError as exc:
            self._warn("Can't make a folder", str(exc))
            return None
        self.discs_changed.emit()
        return dir_id

    def rename_dir(self, disc_id: int, dir_id: int, name: str) -> None:
        current = next(d for d in self._lib.disc_dirs(disc_id) if d.id == dir_id).name
        if not name.strip() or name.strip() == current:
            return
        try:
            self._lib.rename_disc_dir(disc_id, dir_id, name)
        except LibraryError as exc:
            self._warn("Can't rename the folder", str(exc))
        self.reload()
        self.discs_changed.emit()

    def delete_dir(self, disc_id: int, dir_id: int, ask: bool = True) -> None:
        """Delete a folder of an MP3 disc and everything in it: its tracks come off the disc,
        and its folders go, at any depth. The tracks stay in the library."""
        gone = next(d for d in self._lib.disc_dirs(disc_id) if d.id == dir_id)
        tracks, folders = self._lib.disc_dir_contents(disc_id, dir_id)
        if ask:
            answer = QMessageBox.question(
                self,
                "Delete Folder",
                f'Delete the folder "{gone.name}" from the disc?\n'
                f"This also takes {_things(tracks, 'track')} off the disc, and"
                f" deletes {_things(folders, 'folder')} in it.\nThe tracks stay in your library.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        within = self._lib.disc_dir_subtree(disc_id, dir_id)
        try:
            self._lib.delete_disc_dir(disc_id, dir_id)
        except LibraryError as exc:
            self._warn("Can't delete the folder", str(exc))
            return
        if disc_id == self._disc_id and self._dir_id in within:
            self._dir_id = (
                gone.parent_id
            )  # what was shown goes with it: back up to where it was
        self.reload()
        self.discs_changed.emit()

    def move_rows_to_dir(self, rows: list[int], dir_id: int | None) -> None:
        """Put the shown tracks at `rows` into a folder of the open MP3 disc (None: its root)."""
        if self._disc_id is None or not rows:
            return
        positions = [self.model.entry_at(r).position for r in rows]
        try:
            self._lib.move_to_dir(self._disc_id, positions, dir_id)
        except LibraryError as exc:
            self._warn("Can't move those tracks", str(exc))
        self._show_disc()
        self.discs_changed.emit()

    def move_selected_to_dir(self, dir_id: int | None) -> None:
        rows = sorted(i.row() for i in self.table.selectionModel().selectedRows())
        self.move_rows_to_dir(rows, dir_id)

    def table_menu(self) -> QMenu:
        """What a right-click on the track list offers."""
        menu = QMenu(self)
        selected = bool(self.table.selectionModel().selectedRows())
        if self._disc_id is not None:
            dirs = self._lib.disc_dirs(self._disc_id)
            if dirs:
                move = menu.addMenu("Move to Folder")
                move.setEnabled(selected)
                if self._dir_id is not None:
                    move.addAction(
                        "(The disc's root)", lambda: self.move_selected_to_dir(None)
                    )
                for d in sorted(
                    dirs, key=lambda d: self._dir_path(self._disc_id, d.id).casefold()
                ):
                    if d.id != self._dir_id:
                        move.addAction(
                            self._dir_path(self._disc_id, d.id),
                            lambda _c=False, i=d.id: self.move_selected_to_dir(i),
                        )
        remove = menu.addAction("Remove from Disc", self.remove_selected)
        remove.setEnabled(selected)
        return menu

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
