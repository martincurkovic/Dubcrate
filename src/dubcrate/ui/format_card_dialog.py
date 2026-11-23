"""Format a blank SD card for the CDJ-1000MK3, from scratch (Sync tab > Format a Card…).

Sets a card up without a trip through the player: erases it, makes it FAT16 named `DISK_IMG` with
the empty `PIONEER/CDJ` folder the player keeps its cues in (`card_format`), and leaves it mounted so
cues and waveforms can be synced to it at once. **It erases a disk**, so: only removable disks are
listed, each with its verdict (`card_format.problems`: plain SD, 2 GB or less, not a disk image,
not locked), the ones that can't be used are greyed out with the reason, the erase needs a tick in a
box and a press of a button that says what it does, and the disk is looked at once more, and checked
to still be the one picked, just before it is erased.

**Not yet tried on a real card or a player** (`dev_docs/card-sync.md`, "Formatting a card").
The disk listing and the format are injected so a test never touches a disk.
"""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from dubcrate import card_format
from dubcrate.card_format import CardDisk, Formatted

from .glyph_widgets import disk_glyph, glyph_icon, ink
from .source_tabs import sd_card_glyph
from .tasks import TaskError, run_async

DiskLister = Callable[[], list[CardDisk]]
Formatter = Callable[[str, CardDisk], Formatted]


def _real_lister() -> list[CardDisk]:
    return card_format.list_disks()


def _real_formatter(device: str, expect: CardDisk) -> Formatted:
    return card_format.format_card(device, expect=expect)


def short_reason(disk: CardDisk) -> str:
    """Why a disk can't be used, in a few words (`card_format.problems` has the full sentences)."""
    if not disk.whole:
        return "Not a whole card"
    if disk.system or not disk.removable:
        return "Not a removable card"
    if disk.virtual:
        return "A disk image"
    if not disk.writable:
        return "Write-protected: unlock the card"
    if disk.size <= 0:
        return "No card inside"
    return f"{disk.card_class}: only plain SD (2 GB or less) works"


def announce_ready(parent: QWidget | None, name: str) -> None:
    """The box shown over the window once the card is formatted and the Format a Card window has closed
    (tests and the gallery patch or photograph it: `conftest.py` refuses the real one)."""
    QMessageBox.information(
        parent,
        "Card Ready",
        f"“{name}” is formatted and ready for the CDJ-1000MK3.\n\n"
        "It is picked in the card list, so you can sync your discs to it now. "
        "Eject it before you take it out.",
    )


class FormatCardDialog(QDialog):
    """Pick a removable disk, say you understand, erase and format it. It closes itself when the card is
    ready and `announce_ready` says so, so the end of the job is unmistakable."""

    formatted = Signal(object)  # the card's mounted folder (a Path), once it is ready

    def __init__(
        self,
        parent: QWidget | None = None,
        list_disks: DiskLister | None = None,
        formatter: Formatter | None = None,
        device: str | None = None,
    ):
        super().__init__(parent)
        self._preferred = (
            device  # the card to pick if it is there (the list's Format… button)
        )
        self._list_disks = list_disks or _real_lister
        self._formatter = formatter or _real_formatter
        self._disks: list[CardDisk] = []
        self._busy = False
        self._done = False
        self._serial = 0  # a newer look at the disks makes an older one's answer stale
        self.setWindowTitle("Format a Card")
        self.setMinimumSize(520, 400)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        intro = QLabel(
            "Initialise card for use in a CDJ-1000MK3. Only plain SD cards of 2GB or less will work."
        )
        intro.setToolTip(
            "Erases the card, makes it FAT16 named DISK_IMG and adds the PIONEER folder the player keeps its"
            " files in. The CDJ-1000MK3 can't read SDHC or SDXC cards."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.status = QLabel()
        self.status.setObjectName("mutedLabel")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.list = QListWidget()
        self.list.setWordWrap(True)  # a reason is a sentence, not a word
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setUniformItemSizes(False)
        self.list.setIconSize(QSize(36, 36))
        self.list.setSpacing(3)  # (a gap between one drive and the next)
        self.list.setMinimumHeight(140)
        self.list.currentItemChanged.connect(lambda *_: self._update())
        layout.addWidget(self.list)

        self.warning = QLabel()
        self.warning.setWordWrap(True)
        warn_font = self.warning.font()
        warn_font.setBold(True)
        self.warning.setFont(warn_font)
        layout.addWidget(self.warning)
        self.understand = QCheckBox(
            "I understand that everything on this card will be erased"
        )
        self.understand.toggled.connect(lambda _on: self._update())
        layout.addWidget(self.understand)

        self.result = QLabel()
        self.result.setWordWrap(True)
        layout.addWidget(self.result)

        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setToolTip("Look for the card again")
        self.refresh_button.clicked.connect(self.refresh)
        buttons.addWidget(self.refresh_button)
        buttons.addStretch(1)
        self.close_button = QPushButton("Cancel")
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)
        self.format_button = QPushButton("Erase and Format")
        self.format_button.clicked.connect(self.start)
        buttons.addWidget(self.format_button)
        layout.addLayout(buttons)

        self._update()
        self.refresh()

    # -- the disks -----------------------------------------------------------------

    def refresh(self) -> None:
        """Look at the removable disks (on a worker: the system answers slowly) and list them."""
        if self._busy:
            return
        self._serial += 1
        serial = self._serial
        self.status.setText("Looking for cards…")

        def work() -> list[CardDisk]:
            try:
                return self._list_disks()
            except card_format.FormatError as exc:
                raise TaskError(str(exc)) from exc

        def done(disks: list[CardDisk]) -> None:
            if serial == self._serial:
                self._show(disks)

        def failed(text: str) -> None:
            if serial == self._serial:
                self._show([], text)

        run_async(work, done, failed)

    def _show(self, disks: list[CardDisk], problem: str = "") -> None:
        self._disks = disks
        self.list.clear()
        for disk in disks:
            why = card_format.problems(disk)
            line = f"{disk.name or 'Unnamed disk'}  ·  {card_format.size_text(disk.size)}  ({disk.device})"
            item = QListWidgetItem(
                f"{line}\n{short_reason(disk) if why else 'Ready to format'}"
            )
            item.setData(Qt.ItemDataRole.UserRole, disk.device)
            item.setSizeHint(QSize(0, 56))
            item.setIcon(
                glyph_icon(
                    disk_glyph if disk.system or not disk.removable else sd_card_glyph,
                    36,
                    ink(enabled=not why),
                )
            )
            if why:  # greyed out and not pickable, with the reason as its second line
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                item.setForeground(
                    self.palette().color(
                        QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text
                    )
                )
            self.list.addItem(item)
        usable = [i for i, d in enumerate(disks) if not card_format.problems(d)]
        if usable:
            wanted = next(
                (i for i in usable if disks[i].device == self._preferred), usable[0]
            )
            self.list.setCurrentRow(wanted)
        if problem:
            self.status.setText(problem)
        elif not disks:
            self.status.setText(
                "No removable disks found. Put the card in its reader and press Refresh."
            )
        else:
            n = len(usable)
            self.status.setText(
                f"{n} card{'s' if n != 1 else ''} can be formatted."
                if n
                else "None of these can be formatted: the reason is under each."
            )
        self._update()

    def chosen(self) -> CardDisk | None:
        item = self.list.currentItem()
        if item is None or not (item.flags() & Qt.ItemFlag.ItemIsEnabled):
            return None
        device = item.data(Qt.ItemDataRole.UserRole)
        return next((d for d in self._disks if d.device == device), None)

    def _update(self) -> None:
        disk = self.chosen()
        free = not self._busy and not self._done
        self.warning.setText(
            ""
            if disk is None
            else f"Everything on “{disk.name or disk.device}” ({card_format.size_text(disk.size)}, {disk.device}) will be erased."
        )
        self.understand.setEnabled(free and disk is not None)
        self.list.setEnabled(free)
        self.refresh_button.setEnabled(free)
        self.format_button.setVisible(not self._done)
        self.format_button.setEnabled(
            free and disk is not None and self.understand.isChecked()
        )
        self.close_button.setText("Close" if self._done else "Cancel")
        self.close_button.setEnabled(not self._busy)

    # -- erasing -------------------------------------------------------------------

    def start(self) -> None:
        """Erase and format the picked card (on a worker). Needs the tick; refuses anything else."""
        disk = self.chosen()
        if disk is None or not self.understand.isChecked() or self._busy or self._done:
            return
        self._busy = True
        self.result.setText("")
        self.status.setText(f"Formatting {disk.device}… don't take the card out.")
        self._update()

        def work() -> Formatted:
            try:
                return self._formatter(disk.device, disk)
            except card_format.FormatError as exc:
                raise TaskError(str(exc)) from exc

        def done(found: Formatted) -> None:
            self._busy = False
            self._done = True
            self.understand.setChecked(False)
            self.status.setText("")
            self.result.setText("")
            self._update()
            self.formatted.emit(Path(found.volume))
            self.accept()
            announce_ready(self.parent(), found.volume.name)

        def failed(text: str) -> None:
            self._busy = False
            self.understand.setChecked(False)
            self.status.setText("")
            self.result.setText(f"Couldn't format the card: {text}")
            self._update()

        run_async(work, done, failed)
