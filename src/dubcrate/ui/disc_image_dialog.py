"""Disc ID: what the CDJ's ID for an MP3 disc is made from (right-click an MP3 disc > Disc ID…).

The player knows an MP3 disc by an ID that has the disc image's creation date in it, so Sync can make the card's entry
for a disc before the player has seen it only if Dubcrate kept what the image was made from. A burn made here keeps it;
for a disc burned before that, or elsewhere, **Read from Disc in Drive** (or **Read from Image File…**, for a disc.iso
Dubcrate exported) reads it off the disc and keeps it, if the disc is plausibly this playlist's and the ID can be
reproduced (`image_manager.candidate_problems`). Remove forgets it (Sync then needs the player to have played the disc).

The reader and the file chooser are injected so a test never touches a drive or a dialog.
"""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from dubcrate import image_manager, iso
from dubcrate.drive_toc import DriveTocError, data_disc_device
from dubcrate.library import Library
from dubcrate.library.models import DiscImage

from .tasks import TaskError, run_async


def ask_open_image(parent: QWidget | None) -> Path | None:
    """Pick a disc image file (a real dialog: tests patch it)."""
    path, _ = QFileDialog.getOpenFileName(
        parent, "Choose the Disc Image", "", "Disc images (*.iso);;All files (*)"
    )
    return Path(path) if path else None


def read_drive_image() -> iso.ImageSummary:
    """What the disc in the drive says about itself (on a worker: a drive can be slow to answer)."""
    try:
        return iso.read_image(data_disc_device())
    except (DriveTocError, iso.IsoError) as exc:
        raise TaskError(str(exc)) from exc


def read_image_file(path: Path) -> iso.ImageSummary:
    try:
        return iso.read_image(path)
    except iso.IsoError as exc:
        raise TaskError(str(exc)) from exc


def when_text(stamp: str) -> str:
    try:
        return datetime.fromisoformat(stamp).strftime("%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return stamp


class DiscImageDialog(QDialog):
    """Show the image an MP3 disc was burned from, read it from a disc or a file, or forget it."""

    changed = Signal()  # what is recorded changed: Sync should look at the discs again

    def __init__(
        self,
        lib: Library,
        disc_id: int,
        parent: QWidget | None = None,
        read_drive: Callable[[], iso.ImageSummary] | None = None,
        choose_file: Callable[[QWidget | None], Path | None] | None = None,
    ):
        super().__init__(parent)
        self._lib = lib
        self._disc_id = disc_id
        self._read_drive = read_drive or read_drive_image
        self._choose_file = choose_file or ask_open_image
        self._busy = False
        self.setWindowTitle(f"Disc ID: {lib.disc(disc_id).name}")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        intro = QLabel(
            "The CDJ knows an MP3 disc by an ID made from the disc's image, and the image's creation date is in it. "
            "With that kept, Sync can add this disc's waveforms and cues to the card before the player has seen the "
            "disc. A disc burned here keeps it by itself; for one burned before, or elsewhere, put the disc in the "
            "drive and read it here."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self._values: dict[str, QLabel] = {}
        for key, title in (
            ("created", "Image made"),
            ("label", "Label"),
            ("files", "MP3 files"),
            ("folders", "Folders"),
            ("last", "Last file starts at"),
            ("size", "Image size"),
        ):
            value = QLabel()
            value.setObjectName("imageValue")
            self._values[key] = value
            form.addRow(title + ":", value)
        layout.addLayout(form)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(self.status.fontMetrics().lineSpacing() * 3)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.read_button = QPushButton("Read from Disc in Drive")
        self.read_button.setToolTip(
            "Reads the MP3 disc in the drive (its descriptors and folders, not the music) and keeps what it says for this "
            "playlist, if the disc is plausibly this playlist's."
        )
        self.read_button.clicked.connect(self.read_from_drive)
        buttons.addWidget(self.read_button)
        self.file_button = QPushButton("Read from Image File…")
        self.file_button.setToolTip(
            "The same, from a disc image file such as the disc.iso Dubcrate exports."
        )
        self.file_button.clicked.connect(self.read_from_file)
        buttons.addWidget(self.file_button)
        self.remove_button = QPushButton("Remove")
        self.remove_button.setToolTip(
            "Forget it: Sync then needs the player to have played the disc once"
        )
        self.remove_button.clicked.connect(self.remove)
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.accept)
        self.close_button.setDefault(True)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self.fill()

    # -- what is recorded ----------------------------------------------------

    def fill(self) -> None:
        image: DiscImage | None = self._lib.disc_image(self._disc_id)
        if image is None:
            for value in self._values.values():
                value.setText("Not recorded")
            self._say(
                "Nothing is recorded for this disc, so the card needs the player to have played it once before Sync "
                "can add to it.",
                "normal",
            )
        else:
            self._values["created"].setText(when_text(image.created))
            self._values["label"].setText(image.label)
            self._values["files"].setText(str(image.file_count))
            self._values["folders"].setText(str(image.folders))
            self._values["last"].setText(f"sector {image.last_file_start:,}")
            self._values["size"].setText(f"{image.sectors:,} sectors")
            self._say(
                "Recorded. Sync makes the card's entry for this disc from it, if the disc's files are still the "
                "ones that were burned.",
                "normal",
            )
        self._update()

    def _update(self) -> None:
        self.read_button.setEnabled(not self._busy)
        self.file_button.setEnabled(not self._busy)
        self.remove_button.setEnabled(
            not self._busy and self._lib.disc_image(self._disc_id) is not None
        )

    def _say(self, text: str, level: str) -> None:
        self.status.setText(text)
        if self.status.property("level") != level:
            self.status.setProperty("level", level)
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)

    def remove(self) -> None:
        self._lib.clear_disc_image(self._disc_id)
        self.fill()
        self._say("Forgotten.", "normal")
        self.changed.emit()

    # -- reading a disc ------------------------------------------------------

    def read_from_drive(self) -> None:
        self._read(self._read_drive, "Reading the disc…")

    def read_from_file(self) -> None:
        path = self._choose_file(self)
        if path is None:
            return
        self._read(lambda: read_image_file(path), "Reading the image…")

    def _read(self, job: Callable[[], iso.ImageSummary], message: str) -> None:
        if self._busy:
            return
        self._busy = True
        self._update()
        self._say(message, "normal")
        run_async(job, self._read_done, self._failed)

    def _failed(self, text: str) -> None:
        self._busy = False
        self._say(text, "critical")
        self._update()

    def _read_done(self, found: iso.ImageSummary) -> None:
        self._busy = False
        try:
            image = image_manager.record(self._lib, self._disc_id, found)
        except image_manager.ImageError as exc:
            self._say(str(exc), "critical")
            self._update()
            return
        self.fill()
        self._say(
            f"Recorded: made {when_text(image.created)}, {image.file_count} files in {image.folders} "
            f"folder{'s' if image.folders != 1 else ''}. Sync can make the card's entry for this disc now.",
            "normal",
        )
        self.changed.emit()
