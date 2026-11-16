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
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from dubcrate import loudness, settings
from dubcrate.build import (
    BuildError,
    BurnPlan,
    apply_levels,
    disc_problems,
    format_amount,
    media_problems,
    plan_burn,
    remember_rendered,
    render_plan,
)
from dubcrate.burn import (
    Burner,
    BurnError,
    BurnOptions,
    DataDisc,
    Drive,
    MediaInfo,
    backend_problem,
    choose_drive,
    default_backend,
    get_burner,
)
from dubcrate.burn_check import check_burn
from dubcrate.cancel import CancelToken
from dubcrate.cd import SECTORS_PER_SECOND, format_clock
from dubcrate.drive_toc import can_read_toc
from dubcrate.export import ExportError, ExportResult, export_plan
from dubcrate.library import Library
from dubcrate.library.levels import measure_loudness, needs_loudness, store_loudness
from dubcrate.library.models import DiscKind
from dubcrate.render import RenderError

from .tasks import TaskError, run_async

BurnerFactory = Callable[[], Burner]


def ask_export_folder(parent: QWidget | None) -> str:
    """Ask where to put the export; "" if cancelled. (Tests patch this: a real chooser blocks.)"""
    return QFileDialog.getExistingDirectory(parent, "Export Disc To", str(Path.home()))


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
    return get_burner(default_backend())


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
    labels_requested = Signal(
        int
    )  # "Print Labels…" after a real burn: the disc's id (this window has closed itself)
    _log = Signal(str)  # a line from the worker thread, delivered on this one
    _writing = Signal()  # the worker is past the point where a burn can be stopped

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
        # Whether this computer can burn at all (no cdrdao, or not a Mac): said up front, not when
        # the worker fails. A burner a test hands in is taken as able.
        self._backend_problem = (
            "" if burner_factory else (backend_problem(default_backend()) or "")
        )
        self._burner: Burner | None = None
        self._media: MediaInfo | None = None
        self._media_error = ""
        self._drives: list[Drive] = []  # what the burner saw at the last check
        self._device: str | None = (
            None  # the drive chosen (its id); None until the first check
        )
        self._busy = False
        self._burn_cancel: CancelToken | None = (
            None  # set while a burn is being prepared, for Cancel
        )
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
        # What the disc is without level matching; `_plan` is this, or this with a gain on each track
        self._base_plan = self._plan
        self._measuring = False
        self._levels_cancel: CancelToken | None = None
        self._levels_error = ""

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
        # the drive to burn on: only shown when the burner sees two or more (one drive: nothing to choose)
        self.drive_combo = QComboBox()
        self.drive_combo.setToolTip(
            "Which drive to burn on (remembered). The disc checked and burned is the one in this drive."
        )
        self.drive_combo.activated.connect(self._drive_picked)
        self.drive_combo.hide()
        drive_row = QHBoxLayout()
        drive_row.addWidget(self.drive_combo)
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

        # Normalising the levels (audio CDs only; see loudness.py): on or off as the Settings say, per burn
        self.levels_check = QCheckBox("Normalise")
        self.levels_combo = QComboBox()
        for preset in loudness.PRESETS:
            self.levels_combo.addItem(preset.label, preset.key)
        self.levels_combo.setCurrentIndex(
            max(0, self.levels_combo.findData(settings.get_levels_choice()))
        )
        self.levels_label = QLabel()
        self.levels_label.setObjectName("usageLabel")
        self.levels_label.setWordWrap(True)
        self.levels_label.setVisible(False)
        self._has_levels = self._kind == "audio" and self._base_plan is not None
        self.levels_check.setChecked(self._has_levels and settings.get_levels_enabled())
        self.levels_check.toggled.connect(lambda _on: self._apply_levels())
        self.levels_combo.activated.connect(lambda _i: self._apply_levels())
        levels_row = QHBoxLayout()
        levels_row.addWidget(self.levels_check)
        levels_row.addWidget(self.levels_combo, 1)

        form = QFormLayout()
        form.addRow("Speed:", self.speed_combo)
        if self._has_levels:
            form.addRow("Levels:", levels_row)
            form.addRow(self.levels_label)
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
        self.export_button = QPushButton("Export…")
        self.export_button.setAutoDefault(False)
        self.export_button.setToolTip(
            "Write the disc as files (WAVs and a cdrdao table of contents, or the MP3 folder)"
            " to burn with any other program"
        )
        self.export_button.clicked.connect(self.start_export)
        self.labels_button = QPushButton("Print Labels…")
        self.labels_button.setAutoDefault(False)
        self.labels_button.setToolTip(
            "Close this window and open the label printing window with this disc ticked"
        )
        self.labels_button.clicked.connect(self._print_labels)
        self.labels_button.hide()  # (only after a real burn has finished)
        self.close_button = QPushButton("Close")
        self.close_button.setAutoDefault(False)
        self.close_button.clicked.connect(self.reject)
        self.cancel_levels_button = QPushButton("Cancel Measuring")
        self.cancel_levels_button.setAutoDefault(False)
        self.cancel_levels_button.setToolTip(
            "Stop measuring the levels: what is measured is kept, and the burn waits until you untick Normalise or measure the rest"
        )
        self.cancel_levels_button.clicked.connect(self._cancel_levels)
        self.cancel_levels_button.hide()
        self.cancel_burn_button = QPushButton("Cancel")
        self.cancel_burn_button.setAutoDefault(False)
        self.cancel_burn_button.setToolTip(
            "Stop preparing the disc. Nothing has been written to the disc yet, so it stays blank. "
            "Once writing starts it can't be stopped."
        )
        self.cancel_burn_button.clicked.connect(self._cancel_burn)
        self.cancel_burn_button.hide()
        self._writing.connect(self._writing_started)
        buttons = QHBoxLayout()
        buttons.addWidget(self.export_button)
        buttons.addWidget(self.labels_button)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_levels_button)
        buttons.addWidget(self.cancel_burn_button)
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
        if self._has_levels:
            self._apply_levels()  # (the Settings may have it on already)
        if self._plan is not None and not self._backend_problem:
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
        if self._backend_problem:
            problems.append(self._backend_problem)
        if self._levels_error:
            problems.append(self._levels_error)
        if self._media_error:
            problems.append(self._media_error)
        if self._media is not None:
            problems += media_problems(self._used, self._media, self._kind)
        return problems

    def _update(self) -> None:
        problems = self._all_problems()
        self.problems_label.setText("\n".join(f"• {p}" for p in problems))
        self.problems_label.setVisible(bool(problems))
        working = self._busy or self._measuring
        ready = (
            self._plan is not None
            and not problems
            and self._media is not None
            and self.confirm_check.isChecked()
            and not working
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
            self.levels_check,
        ):
            widget.setEnabled(not working and not self._finished)
        self.levels_combo.setEnabled(
            not working and not self._finished and self.levels_check.isChecked()
        )
        self.close_button.setEnabled(not working)
        self.drive_combo.setEnabled(not working and not self._finished)
        self.cancel_levels_button.setVisible(self._measuring)
        self.cancel_burn_button.setVisible(self._burn_cancel is not None)
        self.export_button.setEnabled(self._plan is not None and not working)
        self.labels_button.setVisible(self._finished)
        self.labels_button.setEnabled(not working)
        if self._backend_problem:
            text, level = (
                (
                    "Dubcrate can't burn on this computer yet. Export… writes the disc as"
                    " files that another burning program can burn."
                ),
                "critical",
            )
            self.check_button.setEnabled(False)
        elif self._plan is None:
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

    # -- levels -----------------------------------------------------------

    def _say_levels(self, text: str, level: str) -> None:
        """The line under the Levels row (hidden while it has nothing to say)."""
        self._set_level(self.levels_label, text, level)
        self.levels_label.setVisible(bool(text))

    def _apply_levels(self) -> None:
        """Set `_plan` to the disc with or without level matching, as the controls say. Matching needs
        each track measured first: that is done on a worker, once per file (it is remembered)."""
        if not self._has_levels or self._base_plan is None or self._busy:
            return
        self._levels_error = ""
        if not self.levels_check.isChecked():
            self._plan = self._base_plan
            self._say_levels("", "normal")
            self._update()
            return
        ids = list(dict.fromkeys(t.track_id for t in self._base_plan.tracks))
        todo = [t for t in self._lib.tracks_by_ids(ids) if needs_loudness(t)]
        if not todo:
            self._levels_ready(ids, [])
            return
        self._measuring = True
        token = self._levels_cancel = CancelToken()
        self.cancel_levels_button.setEnabled(True)
        self.progress.setRange(0, len(todo))
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self._set_level(
            self.levels_label, f"Measuring levels… 0 of {len(todo)}", "normal"
        )
        self._update()

        def report(done: int, total: int) -> None:
            self._set_level(
                self.levels_label, f"Measuring levels… {done} of {total}", "normal"
            )
            self.progress.setValue(done)

        run_async(
            _expected_failures(
                lambda report: measure_loudness(todo, report, cancel=token)
            ),
            lambda result: self._levels_measured(ids, result),
            self._levels_failed,
            report,
            on_cancel=self._levels_cancelled,
        )

    def _cancel_levels(self) -> None:
        """The Cancel Measuring button: stop between tracks (what is measured stays measured)."""
        if self._levels_cancel is not None:
            self._levels_cancel.cancel()
            self.cancel_levels_button.setEnabled(False)
            self._set_level(self.levels_label, "Cancelling…", "normal")

    def _levels_cancelled(self, partial) -> None:
        """Stopped part way: keep the levels measured, burn without matching until it is settled."""
        if partial:
            store_loudness(self._lib, partial[0])
        self._measuring = False
        self._levels_cancel = None
        self.progress.setVisible(False)
        self._plan = self._base_plan
        self._levels_error = (
            "measuring the levels was cancelled (untick Normalise to burn without it, "
            "or change the setting to measure the rest)"
        )
        self._say_levels("", "normal")
        self._update()

    def _levels_measured(self, ids: list[int], result) -> None:
        found, problems = result
        store_loudness(self._lib, found)
        self._levels_cancel = None
        self._measuring = False
        self.progress.setVisible(False)
        self._levels_ready(ids, problems)

    def _levels_failed(self, text: str) -> None:
        self._measuring = False
        self.progress.setVisible(False)
        self._plan = self._base_plan
        self._levels_error = (
            f"couldn't measure the levels: {text} (untick Normalise to burn without it)"
        )
        self._say_levels("", "normal")
        self._update()

    def _levels_ready(self, ids: list[int], problems: list[str]) -> None:
        """Every track that can be is measured: put a gain on each and say what that is."""
        assert self._base_plan is not None
        measured = {
            t.id: t.loudness
            for t in self._lib.tracks_by_ids(ids)
            if t.loudness is not None
        }
        self._plan = apply_levels(
            self._base_plan,
            measured,
            loudness.preset_for(self.levels_combo.currentData()).target,
        )
        note = self._plan.levels_note
        held = "held back" in note or bool(problems)
        self._say_levels(note, "warning" if held else "normal")
        for problem in problems:
            logger.warning("levels: %s", problem)
        self._update()

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
        picked, remembered = self._device, settings.get_burn_drive()

        def work() -> tuple[list[Drive], Drive | None, MediaInfo]:
            drives = burner.drives()
            drive = choose_drive(
                drives, picked, remembered
            )  # (none: media_info says why there is no drive)
            return drives, drive, burner.media_info(drive.id if drive else None)

        def done(found: tuple[list[Drive], Drive | None, MediaInfo]) -> None:
            drives, drive, media = found
            self._busy = False
            self._set_drives(drives, drive)
            self._media = media
            self._update()

        def failed(text: str) -> None:
            self._busy = False
            self._media_error = text
            self._update()

        run_async(_expected_failures(work), done, failed)

    def _set_drives(self, drives: list[Drive], chosen: Drive | None) -> None:
        """Show what the burner saw: the combo only when there is a choice to make."""
        self._drives = drives
        self._device = chosen.id if chosen else None
        self.drive_combo.blockSignals(True)
        self.drive_combo.clear()
        for drive in drives:
            self.drive_combo.addItem(drive.name, drive.id)
        if chosen is not None:
            self.drive_combo.setCurrentIndex(
                max(0, self.drive_combo.findData(chosen.id))
            )
        self.drive_combo.blockSignals(False)
        self.drive_combo.setVisible(len(drives) >= 2)

    def _drive_picked(self, index: int) -> None:
        """Another drive chosen: remember it by name and look at the disc in it."""
        if not 0 <= index < len(self._drives):
            return
        drive = self._drives[index]
        self._device = drive.id
        settings.set_burn_drive(drive.name)
        logger.info("burn drive: %s (%s)", drive.name, drive.id)
        self.check_drive()

    def _is_first_drive(self) -> bool:
        """Whether the drive to burn on is the first one: the system's disc tools (`drive_toc`, `eject`) read
        and open that one, so the check after a burn is only made for it."""
        return not self._drives or self._device == self._drives[0].id

    # -- burning ----------------------------------------------------------

    def start_burn(self) -> None:
        if not self.burn_button.isEnabled() or self._plan is None:
            return
        plan = self._plan
        burner = self._burner_instance()
        media = self._media
        speed = self.speed_combo.currentData()
        simulate = self.simulate_check.isChecked()
        # an audio burn keeps the disc in the drive so its table of contents can be checked against the
        # plan (burn_check), and ejects it itself afterwards
        wants_check = not simulate and plan.kind != "mp3" and can_read_toc()
        check = wants_check and self._is_first_drive()
        options = BurnOptions(
            speed=speed,
            simulate=simulate,
            pregap_sectors=plan.pregap_sectors,
            eject=not check,
            device=self._device,
        )
        self.log.clear()
        self._append(
            f"{'Test burn' if simulate else 'Burning'} “{plan.name}”"
            f"{' (MP3 CD)' if plan.kind == 'mp3' else ''} at {speed}x"
        )
        if plan.levels_note:
            self._append(plan.levels_note)
        if wants_check and not check:
            self._append(
                "The disc is not checked after the burn here: only the first drive can be, and it is ejected as"
                " usual."
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
        cancel = self._burn_cancel = (
            CancelToken()
        )  # (so Cancel shows while the disc is prepared)
        self._busy = True
        self._update()
        emit = self._log.emit  # thread-safe: queued to this thread

        def say(text: str) -> None:
            logger.info("burn: %s", text)
            emit(text)

        @_expected_failures
        def work():
            with TemporaryDirectory(prefix="dubcrate-burn-") as tmp:
                rendered = render_plan(plan, Path(tmp), say, cancel)
                if media is not None:  # the rendered lengths are the exact ones
                    problems = media_problems(rendered.used_sectors, media, plan.kind)
                    if problems:
                        raise TaskError("; ".join(problems))
                # the point of no return: a Cancel from here on does nothing (writing can't be stopped)
                cancel.commit()
                self._writing.emit()
                if isinstance(rendered.disc, DataDisc):
                    burner.burn_data(rendered.disc, options, say)
                else:
                    burner.burn(rendered.disc, options, say)
                    if check:  # (never raises: a good burn stays a good burn)
                        check_burn(
                            [t.sectors for t in rendered.disc.tracks],
                            plan.pregap_sectors,
                            say,
                        )
            return rendered.lengths, rendered.waveforms

        run_async(
            work,
            lambda found: self._burn_done(found[0], simulate, found[1]),
            self._burn_failed,
            on_cancel=lambda _partial: self._burn_cancelled(),
        )

    def _cancel_burn(self) -> None:
        """The Cancel button: stop preparing the disc (before the writing starts)."""
        if self._burn_cancel is not None and self._burn_cancel.cancel():
            self.cancel_burn_button.setEnabled(False)
            self._append("Cancelling…")

    def _writing_started(self) -> None:
        """Writing has begun: it can't be stopped, so the Cancel button goes."""
        self._burn_cancel = None
        self._update()

    def _burn_cancelled(self) -> None:
        self._burn_cancel = None
        self._after_attempt()
        self.cancel_burn_button.setEnabled(True)
        self._append("Cancelled. Nothing was written to the disc.")
        self._set_level(
            self.status_label,
            "The burn was cancelled before anything was written. The disc is still blank.",
            "normal",
        )
        logger.info("burn of disc %s cancelled before writing", self._disc_id)
        self._update()

    def _after_attempt(self) -> None:
        self._burn_cancel = None
        self._busy = False
        self.progress.setVisible(False)
        self.confirm_check.setChecked(False)  # ask again before the next one

    def _burn_done(
        self,
        lengths: dict[int, int],
        simulate: bool,
        waveforms: dict[int, list[int]] | None = None,
    ) -> None:
        # what the render found out stays: the exact lengths, and the waveforms Sync would write
        remember_rendered(self._lib, lengths, waveforms or {})
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

    def _print_labels(self) -> None:
        """Print Labels… after a burn: close this window (so the label window isn't opened over a modal one) and ask
        the main window to open the labels with this disc ticked."""
        disc_id = self._disc_id
        self.accept()
        self.labels_requested.emit(disc_id)

    def _burn_failed(self, text: str) -> None:
        self._after_attempt()
        self._append(f"Failed: {text}")
        self._set_level(
            self.status_label,
            "The burn failed. The disc is not marked as burned.",
            "critical",
        )
        self._update()

    # -- exporting --------------------------------------------------------

    def start_export(self) -> None:
        """Write the disc as files for another burning program (see `export.py`)."""
        if not self.export_button.isEnabled() or self._plan is None:
            return
        parent = ask_export_folder(self)
        if not parent:
            return
        plan = self._plan
        self.log.clear()
        self._append(f"Exporting “{plan.name}” to {parent}")
        if plan.levels_note:
            self._append(plan.levels_note)
        logger.info("export of disc %s “%s” to %s", self._disc_id, plan.name, parent)
        self.status_label.setText("")
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self._busy = True
        self._update()
        emit = self._log.emit

        def say(text: str) -> None:
            logger.info("export: %s", text)
            emit(text)

        def work() -> ExportResult:
            try:
                return export_plan(plan, Path(parent), say)
            except (ExportError, BuildError, RenderError) as exc:
                raise TaskError(str(exc)) from exc

        run_async(work, self._export_done, self._export_failed)

    def _export_done(self, result: ExportResult) -> None:
        remember_rendered(
            self._lib, result.lengths, result.waveforms
        )  # (lengths are exact)
        self._busy = False
        self.progress.setVisible(False)
        self._append(
            f"Done. Open {result.readme.name} in that folder to see how to burn it."
        )
        self._set_level(self.status_label, f"Exported to {result.folder}.", "normal")
        self._update()

    def _export_failed(self, text: str) -> None:
        self._busy = False
        self.progress.setVisible(False)
        self._append(f"Failed: {text}")
        self._set_level(self.status_label, "The export failed.", "critical")
        self._update()

    # -- closing ----------------------------------------------------------

    def reject(self) -> None:
        if (
            self._busy or self._measuring
        ):  # a burn can't be taken back halfway: wait for it
            return
        super().reject()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            return  # never let Return start a burn
        super().keyPressEvent(event)
