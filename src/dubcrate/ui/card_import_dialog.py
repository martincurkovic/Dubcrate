"""The review before what changed on a card is brought into Dubcrate: per disc and track, every change, ticked.

`ask_import(parent, findings)` is the only real dialog (`conftest.py` refuses it; tests use `CardImportDialog`).
The rows are what `card_import.changes_on_card` found: cues and loops set on the player (new), the player's hot
cues as memory cues (listed, **not ticked**), and, once a sync has been recorded for a disc, cues deleted or moved
on the player. Nothing is imported until the user presses the button; unticking a disc, a track or one row leaves it
out. A ticked deletion deletes that memory cue (or a loop saved in Dubcrate) in Dubcrate, and in rekordbox if
saving there is allowed; it never touches a music file.
"""

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dubcrate.card_import import (
    ADD,
    HOT,
    LOOP,
    MOVE,
    REMOVE,
    REMOVE_LOOP,
    DiscFinding,
    ImportChoice,
    Row,
    TrackFinding,
    choice_from,
)

ROLE = Qt.ItemDataRole.UserRole


def clock(ms: int) -> str:
    minutes, rest = divmod(ms, 60000)
    return f"{minutes}:{rest / 1000:06.3f}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _span(start: int, end: int) -> str:
    return f"{clock(start)} → {clock(end)}"


def _range(start: int, end: int) -> str:
    return f"{clock(start)}–{clock(end)}"


def describe(row: Row) -> tuple[str, str]:
    """A row's words and its time column."""
    if row.kind == ADD:
        return "New memory cue", clock(row.ms)
    if row.kind == LOOP:
        return "New loop", _span(row.ms, row.end_ms)
    if row.kind == HOT:
        letter = "ABC"[row.slot - 1] if 1 <= row.slot <= 3 else str(row.slot)
        return f"Hot cue {letter} on the player, as a memory cue", clock(row.ms)
    if row.kind == REMOVE:
        return "Deleted on the player", clock(row.ms)
    if row.kind == REMOVE_LOOP:
        return "Loop deleted on the player", _span(row.ms, row.end_ms)
    if row.kind == MOVE:
        return "Moved on the player", f"{clock(row.old_ms)} → {clock(row.ms)}"
    return (
        "Loop moved on the player",
        f"{_range(row.old_ms, row.old_end_ms)}  →  {_range(row.ms, row.end_ms)}",
    )


class CardImportDialog(QDialog):
    def __init__(
        self, findings: Sequence[DiscFinding], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import changes from the card")
        self.resize(640, 480)
        layout = QVBoxLayout(self)
        self.intro = QLabel(
            "These are the changes on the card that Dubcrate doesn't have: cues and loops set on the player,"
            " and cues deleted or moved there since the last sync. Tick the ones to bring in. New ones become"
            " memory cues and loops in Dubcrate, and in rekordbox if you have allowed that (Settings > Library);"
            " a deletion deletes that memory cue. The player's hot cues are listed but not ticked."
        )
        self.intro.setWordWrap(True)
        layout.addWidget(self.intro)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Disc, track and change", "Time"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.tree, 1)
        self.notes = QLabel()
        self.notes.setObjectName("mutedLabel")
        self.notes.setWordWrap(True)
        layout.addWidget(self.notes)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.import_button = self.buttons.addButton(
            "Import", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._filling = True
        for disc in findings:
            self._add_disc(disc)
        self.tree.expandAll()
        for d in range(self.tree.topLevelItemCount()):
            for t in range(self.tree.topLevelItem(d).childCount()):
                self._update_parents(self.tree.topLevelItem(d).child(t).child(0))
        self._filling = False
        self.tree.itemChanged.connect(self._changed)
        self._write_notes(findings)
        self._update()

    # -- building ----------------------------------------------------------------

    def _add_disc(self, disc: DiscFinding) -> None:
        top = QTreeWidgetItem(self.tree, [disc.path or disc.name, ""])
        top.setFlags(top.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        top.setCheckState(0, Qt.CheckState.Checked)
        for found in disc.tracks:
            self._add_track(top, found)

    def _add_track(self, parent: QTreeWidgetItem, found: TrackFinding) -> None:
        name = f"{found.number}. {found.title}"
        if found.artist:
            name += f" - {found.artist}"
        item = QTreeWidgetItem(parent, [name, ""])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Checked)
        for row in found.rows:
            words, when = describe(row)
            leaf = QTreeWidgetItem(item, [words, when])
            leaf.setFlags(leaf.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            leaf.setCheckState(
                0, Qt.CheckState.Checked if row.ticked else Qt.CheckState.Unchecked
            )
            leaf.setData(0, ROLE, (found.track, row))
        if found.over_limit:
            more = QTreeWidgetItem(
                item,
                [
                    f"{_plural(found.over_limit, 'more change')} left out (room for ten)",
                    "",
                ],
            )
            more.setFlags(Qt.ItemFlag.ItemIsEnabled)

    def _write_notes(self, findings: Sequence[DiscFinding]) -> None:
        lines = []
        kept = sum(d.kept for d in findings)
        if kept:
            lines.append(
                f"{_plural(kept, 'cue')} deleted on the player can't be deleted here (hot cues, and loops that are"
                " in rekordbox): left as they are."
            )
        without = [d.path or d.name for d in findings if not d.has_baseline]
        if without:
            lines.append(
                "Deletions and moves are noticed from the next sync on"
                + (f" ({', '.join(without)})." if len(without) < len(findings) else ".")
            )
        self.notes.setText(" ".join(lines))

    # -- what is ticked -----------------------------------------------------------

    def _rows(self):
        for d in range(self.tree.topLevelItemCount()):
            disc = self.tree.topLevelItem(d)
            for t in range(disc.childCount()):
                track = disc.child(t)
                for c in range(track.childCount()):
                    yield track.child(c)

    def chosen(self) -> list[ImportChoice]:
        """What is ticked, a track once, in the order shown (a track with nothing ticked is left out)."""
        ticked: dict[int, tuple[object, list[Row]]] = {}
        for leaf in self._rows():
            data = leaf.data(0, ROLE)
            if data is not None and leaf.checkState(0) == Qt.CheckState.Checked:
                track, row = data
                ticked.setdefault(track.id, (track, []))[1].append(row)
        choices = [choice_from(track, rows) for track, rows in ticked.values()]
        return [c for c in choices if not c.empty]

    def _count(self) -> int:
        return sum(
            1
            for leaf in self._rows()
            if leaf.data(0, ROLE) is not None
            and leaf.checkState(0) == Qt.CheckState.Checked
        )

    def _update_parents(self, item: QTreeWidgetItem | None) -> None:
        """Bring the track and disc above `item` up to date: ticked, unticked or partly, from what is under them."""
        parent = item.parent() if item is not None else None
        while parent is not None:
            states = {
                parent.child(i).checkState(0)
                for i in range(parent.childCount())
                if parent.child(i).flags() & Qt.ItemFlag.ItemIsUserCheckable
            }
            parent.setCheckState(
                0,
                next(iter(states))
                if len(states) == 1
                else Qt.CheckState.PartiallyChecked,
            )
            parent = parent.parent()

    def _changed(self, item: QTreeWidgetItem, *_: object) -> None:
        """A tick on a disc or track ticks everything under it; a parent shows partly ticked when its rows differ."""
        if self._filling:
            return
        self._filling = True
        try:
            state = item.checkState(0)
            if item.childCount() and state != Qt.CheckState.PartiallyChecked:
                self._set_below(item, state)
            self._update_parents(item)
        finally:
            self._filling = False
        self._update()

    def _set_below(self, item: QTreeWidgetItem, state: Qt.CheckState) -> None:
        for i in range(item.childCount()):
            child = item.child(i)
            if child.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                child.setCheckState(0, state)
                self._set_below(child, state)

    def _update(self) -> None:
        count = self._count()
        self.import_button.setText(f"Import {_plural(count, 'change')}")
        self.import_button.setEnabled(count > 0)


def ask_import(
    parent: QWidget | None, findings: Sequence[DiscFinding]
) -> list[ImportChoice] | None:
    """Show the review; what was ticked, or None if cancelled."""
    dialog = CardImportDialog(findings, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.chosen()
