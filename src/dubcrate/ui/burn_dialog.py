"""Burn a planned disc: check the drive and the disc in it, confirm, watch it go.

The window never burns by itself. A burn needs the user to tick "a blank CD-R is
in the drive" and press Burn, and ticking is asked for again after every attempt.

Threading: rendering and burning run on a worker (`run_async`) and never touch
the library, which belongs to the GUI thread. The disc is snapshotted first
(`plan_burn`), and once the worker is done this thread stores the measured
lengths and the burn record.
"""

import logging
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from dubcrate import settings
from dubcrate.build import (
    BuildError,
    BurnPlan,
    disc_problems,
    format_amount,
    media_problems,
    plan_burn,
    render_plan,
)
from dubcrate.burn import (
    Burner,
    BurnError,
    BurnOptions,
    DataDisc,
    MediaInfo,
    get_burner,
)
from dubcrate.cd import SECTORS_PER_SECOND, format_clock
from dubcrate.library import Library
from dubcrate.library.models import DiscKind
from dubcrate.render import RenderError

from .tasks import TaskError, run_async

BurnerFactory = Callable[[], Burner]

# (`logger`, since the dialog's own `self.log` is the progress box)
logger = logging.getLogger(__name__)


def _expected_failures(fn: Callable) -> Callable:
    """Run `fn` on a worker so that the failures a user can cause (no drive, a bad
    file, a disc that won't take it) are reported as plain messages. Anything
    else is a bug, and still gets logged with its traceback."""

    @wraps(fn)
    def run(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (BurnError, BuildError, RenderError) as exc:
            raise TaskError(str(exc)) from exc

    return run


def _backend() -> Burner:
    return get_burner("discrecording")


def describe_media(
    media: MediaInfo | None, planned_sectors: int, kind: DiscKind = "audio"
) -> tuple[str, str]:
    """(text, level) for the drive line. `level` is "normal", "warning" or "critical"."""
    if media is None:
        return "Looking for the drive…", "normal"
    drive = media.drive or "The drive"
    if media.present is False:
        return f"{drive}: no disc inside.", "critical"
    parts = [drive]
    if media.blank is False:
        parts.append("this disc doesn't look blank")
    elif media.blank:
        parts.append("blank disc")
    if media.free_sectors:
        parts.append(f"holds {format_amount(media.free_sectors, kind)}")
        level = "critical" if planned_sectors > media.free_sectors else "normal"
    else:
        parts.append("couldn't read the disc's size, so going by the planned size")
        level = "warning"
    if media.blank is False and level == "normal":
        level = "warning"
    return " · ".join(parts), level


class BurnDialog(QDialog):
    burned = Signal(
        int
    )  # the id of the disc that was burned (it is only marked as such)
    _log = Signal(str)  # a line from the worker thread, delivered on this one

    def __init__(
        self,
        lib: Library,
        disc_id: int,
        parent=None,
        burner_factory: BurnerFactory | None = None,
    ):
        super().__init__(parent)
        self._lib = lib
        self._disc_id = disc_id
        self._burner_factory = burner_factory or _backend
        self._burner: Burner | None = None
        self._media: MediaInfo | None = None
        self._media_error = ""
        self._busy = False
        self._finished = False  # a real burn is done: this disc is no longer blank
        self.setWindowTitle("Burn Disc")
        self.setMinimumWidth(520)

        summary = lib.disc(disc_id)
        self._used = summary.used_sectors
        self._kind = summary.kind
        self._problems = disc_problems(lib, disc_id)
        self._plan: BurnPlan | None = None
        if not self._problems:
            self._plan = plan_burn(lib, disc_id)

        title = QLabel(summary.name)
        title.setObjectName("panelTitle")
        n = summary.track_count
        tracks = f"{n} track{'s' if n != 1 else ''}"
        if summary.kind == "mp3":
            text = (
                f"MP3 CD · {tracks} · {format_amount(summary.used_sectors, 'mp3')}"
                f" of {format_amount(summary.capacity_sectors, 'mp3')}"
                f" · constant bitrate, up to {summary.mp3_bitrate} kbit/s"
            )
        else:
            gap = (
                "gapless"
                if summary.pregap_sectors == 0
                else f"{summary.pregap_sectors // SECTORS_PER_SECOND} s between tracks"
            )
            text = (
                f"{tracks} · {format_clock(summary.used_sectors)}"
                f" of {format_clock(summary.capacity_sectors)} · {gap}"
            )
        self.disc_label = QLabel(text)
        self.disc_label.setObjectName("mutedLabel")

        self.problems_label = QLabel()
        self.problems_label.setObjectName("usageLabel")
        self.problems_label.setProperty("level", "critical")
        self.problems_label.setWordWrap(True)

        self.drive_label = QLabel()
        self.drive_label.setObjectName("usageLabel")
        self.drive_label.setWordWrap(True)
        self.check_button = QPushButton("Check Drive")
        self.check_button.setAutoDefault(False)
        self.check_button.clicked.connect(self.check_drive)
        drive_row = QHBoxLayout()
        drive_row.addWidget(self.drive_label, 1)
        drive_row.addWidget(self.check_button)

        self.speed_combo = QComboBox()
        for speed in settings.BURN_SPEEDS:
            self.speed_combo.addItem(f"{speed}x", speed)
        default = settings.get_burn_speed()
        self.speed_combo.setCurrentIndex(max(0, self.speed_combo.findData(default)))
        self.speed_combo.setToolTip(
            f"Your default is {default}x (File > Settings > Burning)."
            " Changing it here affects this burn only."
        )
        self.simulate_check = QCheckBox("Test only: don't write the disc (laser off)")
        self.confirm_check = QCheckBox("A blank CD-R is in the drive")
        self.confirm_check.toggled.connect(lambda _on: self._update())
        self.simulate_check.toggled.connect(lambda _on: self._update())

        form = QFormLayout()
        form.addRow("Speed:", self.speed_combo)
        form.addRow(self.simulate_check)
        form.addRow(self.confirm_check)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(140)
        self.log.setPlaceholderText("Progress shows here once the burn starts.")
        self._log.connect(self._append)

        self.status_label = QLabel()
        self.status_label.setObjectName("usageLabel")
        self.status_label.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1)
        self.progress.setVisible(False)

        self.burn_button = QPushButton("Burn")
        self.burn_button.setDefault(False)  # Return must not start a burn
        self.burn_button.setAutoDefault(False)
        self.burn_button.clicked.connect(self.start_burn)
        self.close_button = QPushButton("Close")
        self.close_button.setAutoDefault(False)
        self.close_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        buttons.addWidget(self.burn_button)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(title)
        layout.addWidget(self.disc_label)
        layout.addWidget(self.problems_label)
        layout.addLayout(drive_row)
        layout.addLayout(form)
        layout.addWidget(self.log, 1)
        layout.addWidget(self.progress)
        layout.addWidget(self.status_label)
        layout.addLayout(buttons)

        self._update()
        if self._plan is not None:
            self.check_drive()

    # -- state ------------------------------------------------------------

    def _set_level(self, label: QLabel, text: str, level: str) -> None:
        label.setText(text)
        if label.property("level") != level:
            label.setProperty("level", level)
            label.style().unpolish(label)
            label.style().polish(label)

    def _all_problems(self) -> list[str]:
        problems = list(self._problems)
        if self._media_error:
            problems.append(self._media_error)
        if self._media is not None:
            problems += media_problems(self._used, self._media, self._kind)
        return problems

    def _update(self) -> None:
        problems = self._all_problems()
        self.problems_label.setText("\n".join(f"• {p}" for p in problems))
        self.problems_label.setVisible(bool(problems))
        ready = (
            self._plan is not None
            and not problems
            and self._media is not None
            and self.confirm_check.isChecked()
            and not self._busy
            and not self._finished
        )
        self.burn_button.setEnabled(ready)
        self.burn_button.setText(
            "Test Burn" if self.simulate_check.isChecked() else "Burn"
        )
        for widget in (
            self.speed_combo,
            self.simulate_check,
            self.confirm_check,
            self.check_button,
        ):
            widget.setEnabled(not self._busy and not self._finished)
        self.close_button.setEnabled(not self._busy)
        if self._plan is None:
            text, level = (
                "The drive isn't checked until the disc is ready to burn.",
                "normal",
            )
            self.check_button.setEnabled(False)
        elif self._media is None and not self._media_error:
            text, level = describe_media(None, self._used, self._kind)
        elif self._media is None:
            text, level = "Couldn't reach the drive.", "critical"
        else:
            text, level = describe_media(self._media, self._used, self._kind)
        self._set_level(self.drive_label, text, level)

    def _append(self, text: str) -> None:
        self.log.appendPlainText(text)

    def _burner_instance(self) -> Burner:
        if self._burner is None:
            self._burner = self._burner_factory()
        return self._burner

    # -- the drive --------------------------------------------------------

    def check_drive(self) -> None:
        """Ask the drive what is in it (on a worker: it can take a moment)."""
        if self._busy:
            return
        burner = self._burner_instance()
        self._media = None
        self._media_error = ""
        self._busy = True
        self._update()

        def done(media: MediaInfo) -> None:
            self._busy = False
            self._media = media
            self._update()

        def failed(text: str) -> None:
            self._busy = False
            self._media_error = text
            self._update()

        run_async(_expected_failures(lambda: burner.media_info(None)), done, failed)

    # -- burning ----------------------------------------------------------

    def start_burn(self) -> None:
        if not self.burn_button.isEnabled() or self._plan is None:
            return
        plan = self._plan
        burner = self._burner_instance()
        media = self._media
        speed = self.speed_combo.currentData()
        simulate = self.simulate_check.isChecked()
        options = BurnOptions(
            speed=speed, simulate=simulate, pregap_sectors=plan.pregap_sectors
        )
        self.log.clear()
        self._append(
            f"{'Test burn' if simulate else 'Burning'} “{plan.name}”"
            f"{' (MP3 CD)' if plan.kind == 'mp3' else ''} at {speed}x"
        )
        logger.info(
            "%s of disc %s “%s” at %sx",
            "test burn" if simulate else "burn",
            self._disc_id,
            plan.name,
            speed,
        )
        self.status_label.setText("")
        self.progress.setRange(0, 0)  # busy
        self.progress.setVisible(True)
        self._busy = True
        self._update()
        emit = self._log.emit  # thread-safe: queued to this thread

        def say(text: str) -> None:
            logger.info("burn: %s", text)
            emit(text)

        @_expected_failures
        def work():
            with TemporaryDirectory(prefix="dubcrate-burn-") as tmp:
                rendered = render_plan(plan, Path(tmp), say)
                if media is not None:  # the rendered lengths are the exact ones
                    problems = media_problems(rendered.used_sectors, media, plan.kind)
                    if problems:
                        raise TaskError("; ".join(problems))
                if isinstance(rendered.disc, DataDisc):
                    burner.burn_data(rendered.disc, options, say)
                else:
                    burner.burn(rendered.disc, options, say)
            return rendered.lengths

        run_async(
            work,
            lambda lengths: self._burn_done(lengths, simulate),
            self._burn_failed,
        )

    def _after_attempt(self) -> None:
        self._busy = False
        self.progress.setVisible(False)
        self.confirm_check.setChecked(False)  # ask again before the next one

    def _burn_done(self, lengths: dict[int, int], simulate: bool) -> None:
        for track_id, sectors in lengths.items():
            self._lib.set_cd_sectors(track_id, sectors)
        self._after_attempt()
        logger.info("%s finished", "test burn" if simulate else "burn")
        if simulate:
            self._append("Test burn finished. Nothing was written to the disc.")
            self.status_label.setText(
                "Test burn finished. The disc is not marked as burned."
            )
            self._update()
            return
        self._lib.mark_burned(
            self._disc_id
        )  # a note on the disc: it stays fully editable
        self._finished = True
        self._append("Done. The disc is marked as burned.")
        self.status_label.setText("Burn finished. The disc is marked as burned.")
        self._update()
        self.burned.emit(self._disc_id)

    def _burn_failed(self, text: str) -> None:
        self._after_attempt()
        self._append(f"Failed: {text}")
        self._set_level(
            self.status_label,
            "The burn failed. The disc is not marked as burned.",
            "critical",
        )
        self._update()

    # -- closing ----------------------------------------------------------

    def reject(self) -> None:
        if self._busy:  # a burn can't be taken back halfway: wait for it
            return
        super().reject()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            return  # never let Return start a burn
        super().keyPressEvent(event)
