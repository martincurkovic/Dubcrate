"""TOC Manager: the table of contents the CDJ's card knows a playlist's discs by (right-click an audio disc > TOC Manager…).

A card entry is a disc's TOC, and a playlist can have physical copies whose TOC differs a little from the plan (burned
on another machine or with another gap setting). **Read from Disc in Drive** reads the TOC of the disc in the drive and
keeps it for the playlist if it is plausibly a copy (`toc_manager.candidate_problems`: the same number of tracks, every
track's length close to the planned one); Sync then writes the cues under each enabled TOC. Every TOC is listed with a
tick box: an unticked one stays in the list but Sync ignores it (a TOC that is wrong or out of date), and a TOC recorded
for a playlist that has been edited since is greyed out and can only be removed. Burns record their own read-back TOC
here too. With none enabled the plan's own TOC is used.

The drive reader and the length probe are injected so a test never touches a drive.
"""

from collections.abc import Callable, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dubcrate import cdj_card as cc
from dubcrate import toc_manager
from dubcrate.card_discs import Toc, disc_toc
from dubcrate.card_report import msf
from dubcrate.drive_toc import DriveTocError, read_drive_toc
from dubcrate.library import Library
from dubcrate.library.lengths import needs_measuring, probe_lengths
from dubcrate.library.store import KnownToc

from . import theme
from .tasks import TaskError, run_async

COL_TOC, COL_SOURCE, COL_SAVED, COL_DIFFERENCE = range(4)
KNOWN_ROLE = Qt.ItemDataRole.UserRole  # the KnownToc on a row
SOURCES = {"burn": "Read after a burn", "manual": "Read from a disc"}


def difference_text(planned: Toc, actual: Toc) -> str:
    """How a disc's TOC differs from the plan, in a few words, for the list."""
    if (list(planned[0]), planned[1]) == (list(actual[0]), actual[1]):
        return "Same as planned"
    delta = actual[1] - planned[1]
    if delta == 0:
        return "Same length, tracks placed differently"
    n = abs(delta)
    unit = "frame" if n == 1 else "frames"
    how = "longer" if delta > 0 else "shorter"
    span = f" ({n / cc.FRAMES_PER_SECOND:.1f} s)" if n >= cc.FRAMES_PER_SECOND else ""
    return f"{n} {unit}{span} {how} than planned"


def saved_text(stamp: str) -> str:
    return stamp.replace("T", " ").split("+")[0]


class TocManagerDialog(QDialog):
    """List a playlist's known TOCs, switch them on and off, remove them, and read a new one from the drive."""

    changed = (
        Signal()
    )  # the list of TOCs (or which are on) changed: Sync should look at the discs again

    def __init__(
        self,
        lib: Library,
        disc_id: int,
        parent: QWidget | None = None,
        read_toc: Callable[[], tuple[Sequence[int], int]] | None = None,
    ):
        super().__init__(parent)
        self._lib = lib
        self._disc_id = disc_id
        self._read_toc = read_toc or read_drive_toc
        self._busy = False
        self._filling = False
        name = lib.disc(disc_id).name
        self.setWindowTitle(f"TOC Manager: {name}")
        self.setMinimumSize(720, 380)
        self.resize(860, 420)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        intro = QLabel(
            "The CDJ's card knows a disc by its table of contents (TOC). A copy of this playlist burned on another "
            "machine or with other settings can have a slightly different one. Put the disc in the drive and read "
            "it here; Sync then writes the cues for every ticked TOC."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.plan_label = QLabel()
        self.plan_label.setObjectName("mutedLabel")
        self.plan_label.setWordWrap(True)
        layout.addWidget(self.plan_label)

        self.tree = QTreeWidget()
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setHeaderLabels(
            ["TOC", "Source", "Recorded", "Compared with the plan"]
        )
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COL_TOC, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_SOURCE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_SAVED, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_DIFFERENCE, QHeaderView.ResizeMode.Stretch)
        self.tree.setMinimumHeight(120)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.currentItemChanged.connect(lambda *_: self._update())
        layout.addWidget(self.tree, 1)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(self.status.fontMetrics().lineSpacing() * 3)
        self.status.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.read_button = QPushButton("Read from Disc in Drive")
        self.read_button.setToolTip(
            "Reads the table of contents of the audio CD in the drive. It is kept for this playlist only if the "
            "disc's tracks are about as long as the playlist's."
        )
        self.read_button.clicked.connect(self.read_from_drive)
        buttons.addWidget(self.read_button)
        self.remove_button = QPushButton("Remove")
        self.remove_button.setToolTip(
            "Forget the picked TOC (untick it instead to keep it but have Sync ignore it)"
        )
        self.remove_button.clicked.connect(self.remove_picked)
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.accept)
        self.close_button.setDefault(True)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        self.fill()

    # -- the list ------------------------------------------------------------

    def _planned(self) -> Toc | None:
        entries = self._lib.disc_entries(self._disc_id)
        if len(entries) < 2 or any(not e.track.length_exact for e in entries):
            return None
        return disc_toc(entries, self._lib.disc(self._disc_id).pregap_sectors)

    def fill(self) -> None:
        """Show the plan's TOC and every TOC recorded for the disc."""
        planned = self._planned()
        known_tocs = self._lib.disc_tocs(self._disc_id)
        in_use = planned is not None and any(
            k.enabled and k.planned == planned for k in known_tocs
        )
        self._filling = True
        self.tree.clear()
        if planned is not None:
            self.tree.addTopLevelItem(self._plan_row(planned, in_use))
        for known in known_tocs:
            self.tree.addTopLevelItem(self._row(known, planned))
        self._filling = False
        if planned is None:
            self.plan_label.setText(
                "Some tracks have no measured length yet: reading a disc measures them first."
            )
        else:
            on = sum(1 for k in known_tocs if k.enabled and k.planned == planned)
            self.plan_label.setText(
                f"As planned: {len(planned[0])} tracks, {msf(planned[1])}. "
                + (
                    f"Sync writes to the {on} ticked TOC{'s' if on != 1 else ''} below."
                    if on
                    else "No TOC below is ticked, so Sync uses the plan's."
                )
            )
        self._update()

    def _plan_row(self, planned: Toc, in_use: bool) -> QTreeWidgetItem:
        """The TOC the playlist has as planned, first and greyed: it has no tick box and can't be removed. Sync uses
        it only while no TOC below is ticked."""
        item = QTreeWidgetItem(
            [
                f"{len(planned[0])} tracks, {msf(planned[1])}",
                "As planned",
                "",
                "Used when no TOC below is ticked"
                if in_use
                else "In use: no TOC below is ticked",
            ]
        )
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        for column in range(self.tree.columnCount()):
            item.setForeground(column, QColor(theme.current_palette()["text_disabled"]))
        item.setToolTip(
            COL_TOC,
            "The TOC the playlist has as planned now. It can't be removed: Sync writes to it when no TOC below is ticked.",
        )
        return item

    def _row(self, known: KnownToc, planned: Toc | None) -> QTreeWidgetItem:
        current = planned is not None and known.planned == planned
        item = QTreeWidgetItem(
            [
                f"{len(known.actual[0])} tracks, {msf(known.actual[1])}",
                SOURCES.get(known.source, known.source),
                saved_text(known.saved_at),
                difference_text(known.planned, known.actual),
            ]
        )
        item.setData(COL_TOC, KNOWN_ROLE, known)
        flags = item.flags() | Qt.ItemFlag.ItemIsUserCheckable
        if not current:  # the playlist was edited since: it describes a disc that is no longer this one
            flags &= ~Qt.ItemFlag.ItemIsUserCheckable
            item.setText(COL_DIFFERENCE, "Playlist edited since: not used")
            item.setCheckState(COL_TOC, Qt.CheckState.Unchecked)
            item.setDisabled(False)
            for column in range(self.tree.columnCount()):
                item.setForeground(
                    column, QColor(theme.current_palette()["text_disabled"])
                )
        else:
            item.setCheckState(
                COL_TOC,
                Qt.CheckState.Checked if known.enabled else Qt.CheckState.Unchecked,
            )
        item.setFlags(flags)
        item.setToolTip(
            COL_TOC,
            "Tick: Sync writes the cues under this TOC. Untick it if it is wrong or out of date.",
        )
        return item

    def _picked(self) -> KnownToc | None:
        item = self.tree.currentItem()
        return None if item is None else item.data(COL_TOC, KNOWN_ROLE)

    def _update(self) -> None:
        self.read_button.setEnabled(not self._busy)
        self.remove_button.setEnabled(not self._busy and self._picked() is not None)

    def _item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._filling or column != COL_TOC:
            return
        known: KnownToc = item.data(COL_TOC, KNOWN_ROLE)
        on = item.checkState(COL_TOC) == Qt.CheckState.Checked
        if on == known.enabled:
            return
        self._lib.set_disc_toc_enabled(known.id, on)
        self._say(
            "Sync will write the cues under this TOC."
            if on
            else "Sync will ignore this TOC. It stays in the list.",
            "normal",
        )
        self.fill()
        self.changed.emit()

    def remove_picked(self) -> None:
        known = self._picked()
        if known is None:
            return
        self._lib.remove_disc_toc(known.id)
        self._say("Removed.", "normal")
        self.fill()
        self.changed.emit()

    # -- reading a disc ------------------------------------------------------

    def _say(self, text: str, level: str) -> None:
        self.status.setText(text)
        if self.status.property("level") != level:
            self.status.setProperty("level", level)
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)

    def read_from_drive(self) -> None:
        """Measure any track the playlist has no exact length for, read the disc in the drive, check it, keep it."""
        if self._busy:
            return
        entries = self._lib.disc_entries(self._disc_id)
        todo = [e.track for e in entries if needs_measuring(e.track)]
        self._busy = True
        self._update()
        self._say(
            "Measuring the playlist's tracks…" if todo else "Reading the disc…",
            "normal",
        )

        def read() -> tuple[Sequence[int], int]:
            try:
                return self._read_toc()
            except DriveTocError as exc:  # (no disc, not an audio CD…: said as it is)
                raise TaskError(str(exc)) from exc

        def read_disc() -> None:
            self._say("Reading the disc…", "normal")
            run_async(read, self._read_done, self._failed)

        def measured(found) -> None:
            lengths, problems = found
            for (
                track_id,
                sectors,
            ) in lengths.items():  # (the library is the GUI thread's)
                self._lib.set_cd_sectors(track_id, sectors)
            if problems:
                self._failed(
                    "Couldn't measure the playlist's tracks: " + "; ".join(problems[:2])
                )
                return
            read_disc()

        if todo:
            run_async(lambda: probe_lengths(todo), measured, self._failed)
        else:
            read_disc()

    def _failed(self, text: str) -> None:
        self._busy = False
        self._say(text, "critical")
        self._update()

    def _read_done(self, actual) -> None:
        self._busy = False
        starts, lead_out = list(actual[0]), actual[1]
        already = any(
            k.actual == (starts, lead_out) for k in self._lib.disc_tocs(self._disc_id)
        )
        try:
            toc_manager.record(self._lib, self._disc_id, (starts, lead_out))
        except toc_manager.TocError as exc:
            self._say(str(exc), "critical")
            self._update()
            return
        self._say(
            "That TOC was already in the list: it is ticked again."
            if already
            else f"Recorded: {len(starts)} tracks, {msf(lead_out)}. Sync writes the cues under it too.",
            "normal",
        )
        self.fill()
        self.changed.emit()
