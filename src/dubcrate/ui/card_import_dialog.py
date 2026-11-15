"""The review before memory cues from a card are brought into Dubcrate: every new cue, ticked, per disc and track.

`ask_import(parent, findings)` is the only real dialog (`conftest.py` refuses it; tests use `CardImportDialog`).
Nothing is imported until the user presses the button; unticking a disc, a track or one cue leaves it out.
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

from dubcrate.card_import import DiscFinding, TrackFinding
from dubcrate.library.models import Track

ROLE = Qt.ItemDataRole.UserRole


def clock(ms: int) -> str:
    minutes, rest = divmod(ms, 60000)
    return f"{minutes}:{rest / 1000:06.3f}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


class CardImportDialog(QDialog):
    def __init__(
        self, findings: Sequence[DiscFinding], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import memory cues from the card")
        self.resize(560, 460)
        layout = QVBoxLayout(self)
        self.intro = QLabel(
            "These memory cues are on the card and not in Dubcrate. They were set on the player."
            " Tick the ones to bring in: they become memory cues in Dubcrate, and in rekordbox if"
            " you have allowed that (Settings > Library). Nothing is deleted anywhere."
        )
        self.intro.setWordWrap(True)
        layout.addWidget(self.intro)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Disc, track and cue", "Time"])
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
        for ms in found.cues:
            row = QTreeWidgetItem(item, ["New memory cue", clock(ms)])
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(0, Qt.CheckState.Checked)
            row.setData(0, ROLE, (found.track, ms))
        if found.over_limit:
            more = QTreeWidgetItem(
                item,
                [
                    f"{_plural(found.over_limit, 'more cue')} left out: a track has room for ten",
                    "",
                ],
            )
            more.setFlags(Qt.ItemFlag.ItemIsEnabled)

    def _write_notes(self, findings: Sequence[DiscFinding]) -> None:
        loops = sum(d.loops for d in findings)
        hot = sum(d.hot_cues for d in findings)
        skipped = []
        if hot:
            skipped.append(_plural(hot, "hot cue") + " set on the player")
        if loops:
            skipped.append(_plural(loops, "loop"))
        lines = [f"Not imported: {', '.join(skipped)}."] if skipped else []
        lines.append(
            "A cue moved or deleted on the player is not noticed: a moved one shows here as new."
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

    def chosen(self) -> list[tuple[Track, list[int]]]:
        """The ticked cues as (track, milliseconds), a track once, in the order shown."""
        found: dict[int, tuple[Track, list[int]]] = {}
        for row in self._rows():
            data = row.data(0, ROLE)
            if data is not None and row.checkState(0) == Qt.CheckState.Checked:
                track, ms = data
                found.setdefault(track.id, (track, []))[1].append(ms)
        return list(found.values())

    def _changed(self, item: QTreeWidgetItem, *_: object) -> None:
        """A tick on a disc or track ticks everything under it; a parent shows partly ticked when its cues differ."""
        if self._filling:
            return
        self._filling = True
        try:
            state = item.checkState(0)
            if item.childCount() and state != Qt.CheckState.PartiallyChecked:
                self._set_below(item, state)
            parent = item.parent()
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
        count = sum(len(times) for _, times in self.chosen())
        self.import_button.setText(f"Import {_plural(count, 'cue')}")
        self.import_button.setEnabled(count > 0)


def ask_import(
    parent: QWidget | None, findings: Sequence[DiscFinding]
) -> list[tuple[Track, list[int]]] | None:
    """Show the review; the ticked cues, or None if cancelled."""
    dialog = CardImportDialog(findings, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.chosen()
