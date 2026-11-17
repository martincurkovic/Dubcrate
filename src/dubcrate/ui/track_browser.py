"""The Sync tab's middle: the tracks of the disc picked in the list on the right, and the waveform of the track picked here.

A read-only browser (`TrackBrowser`), so a disc can be looked through without leaving the Sync tab: one
row for each track with its place, title, artist and length on the disc. **Only the picked track's
waveform is drawn**, at the bottom, as the CDJ's display draws it (`wave_strip.CdjWaveStrip`: 100 columns
by 7 rows, from the bottom, with what a sync does with it written under it), with the **Analyse
Waveforms** button beside it. Above the tracks are the disc's name, the one line of what a sync would do
and any warnings. `SyncView` feeds it (`show_disc` / `show_message`) and connects the button.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dubcrate.card_discs import (
    WAVE_CARD,
    WAVE_NONE,
    WAVE_PENDING,
    WAVE_WRITE,
    WaveRow,
)

from .wave_strip import CARD, NONE, PENDING, WRITE, CdjWaveStrip

COL_NUMBER, COL_TITLE, COL_ARTIST, COL_TIME = range(4)
HEADINGS = ["#", "Title", "Artist", "Time"]
STRIP_STATES = {
    WAVE_WRITE: WRITE,
    WAVE_CARD: CARD,
    WAVE_PENDING: PENDING,
    WAVE_NONE: NONE,
}


def unsized(label: QLabel) -> QLabel:
    """A label whose text never decides how wide its pane is: a long disc name, track name or note
    would otherwise push the pane wider and squeeze its neighbours."""
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label


def time_text(seconds: float) -> str:
    whole = round(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


class TrackBrowser(QWidget):
    """The picked disc's tracks, and the picked track's waveform."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.rows: list[WaveRow] = []
        self._disc_key: object = (
            None  # which disc the rows are of: a refresh keeps the picked track
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        self.title = unsized(QLabel("Tracks"))
        self.title.setObjectName("panelTitle")
        layout.addWidget(self.title)
        self.message = unsized(
            QLabel()
        )  # the disc's one line, or why there is no disc to show
        self.message.setObjectName("mutedLabel")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.warnings = unsized(QLabel())
        self.warnings.setWordWrap(True)
        self.warnings.hide()
        layout.addWidget(self.warnings)

        self.table = QTreeWidget()
        self.table.setColumnCount(len(HEADINGS))
        self.table.setHeaderLabels(HEADINGS)
        self.table.setRootIsDecorated(False)
        self.table.setUniformRowHeights(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COL_NUMBER, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_TITLE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_ARTIST, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(COL_TIME, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnWidth(COL_ARTIST, 160)
        self.table.currentItemChanged.connect(lambda *_: self._show_wave())
        layout.addWidget(self.table, 1)

        # the waveform of the picked track, at the bottom (its name is the highlighted row above it)
        self.analyse_note = unsized(QLabel())
        self.analyse_note.setObjectName("mutedLabel")
        layout.addWidget(self.analyse_note)
        self.strip = CdjWaveStrip(None, NONE)
        layout.addWidget(self.strip)
        self.wave_note = unsized(QLabel())
        self.wave_note.setObjectName("mutedLabel")
        layout.addWidget(self.wave_note)
        self.show_message("")

    # -- what is shown ---------------------------------------------------------------

    def show_message(self, text: str, title: str = "Tracks") -> None:
        """No disc to show (or one that can't be synced): `text` says why, and the list is empty."""
        self._disc_key = None
        self.rows = []
        self.table.clear()
        self.title.setText(title)
        self.message.setText(text)
        self.message.setVisible(bool(text))  # (nothing to say: no empty line)
        self.warnings.hide()
        self._show_wave()

    def show_disc(
        self,
        key: object,
        title: str,
        summary: str,
        rows: list[WaveRow],
        warnings: list[str] = (),
    ) -> None:
        """The disc's tracks. The track that was picked stays picked if this is the same disc (`key`)."""
        keep = self.selected_row().number if self.selected_row() and key == self._disc_key else None  # fmt: skip
        self._disc_key = key
        self.rows = list(rows)
        self.title.setText(title)
        self.message.setText(summary)
        self.message.setVisible(bool(summary))
        self.warnings.setVisible(bool(warnings))
        self.warnings.setText("\n".join(warnings))
        self.table.blockSignals(True)
        self.table.clear()
        items: dict[int, QTreeWidgetItem] = {}
        for row in self.rows:
            item = QTreeWidgetItem(
                [
                    str(row.number),
                    row.name or row.title,
                    row.artist,
                    time_text(row.seconds) if row.seconds else "",
                ]
            )
            item.setData(COL_NUMBER, Qt.ItemDataRole.UserRole, row.number)
            for column in (COL_NUMBER, COL_TIME):
                item.setTextAlignment(
                    column, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
            self.table.addTopLevelItem(item)
            items[row.number] = item
        self.table.blockSignals(False)
        if items:  # (the track that was picked, else the first)
            self.table.setCurrentItem(items.get(keep) or next(iter(items.values())))
        self._show_wave()

    def selected_row(self) -> WaveRow | None:
        item = self.table.currentItem()
        if item is None:
            return None
        number = item.data(COL_NUMBER, Qt.ItemDataRole.UserRole)
        return next((r for r in self.rows if r.number == number), None)

    def _show_wave(self) -> None:
        """The picked track's waveform, with what a sync does with it."""
        row = self.selected_row()
        if row is None:
            self.strip.set_values(None, NONE)
            self.wave_note.setText("")
            return
        self.strip.set_values(row.values or None, STRIP_STATES[row.state], row.marks)
        self.wave_note.setText(row.note)
