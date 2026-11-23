"""The window's side of the track finder (`track_finder`): where to save the page, and what to say after.

Disc > Export Track Finder… comes here. It writes one HTML page that says which disc a track is on; nothing
is read from or written to rekordbox, and no music file is touched.
"""

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QFileDialog, QMessageBox, QWidget

from dubcrate import mp3, settings, track_finder
from dubcrate.cancel import CancelToken
from dubcrate.library import Library
from dubcrate.track_finder import FinderError, Written
from dubcrate.ui.tasks import run_async

TITLE = "Export Track Finder"
DEFAULT_NAME = "Dubcrate Track Finder"


def ask_save_file(parent: QWidget | None) -> str:
    """Ask for the file to write ("" if cancelled). The system chooser asks before replacing a file.
    (Tests patch this: a real chooser blocks.)"""
    path, _ = QFileDialog.getSaveFileName(
        parent,
        TITLE,
        str(Path.home() / f"{mp3.dir_name(DEFAULT_NAME)}.html"),
        "Web page (*.html)",
    )
    if path and not path.lower().endswith((".html", ".htm")):
        path += ".html"
    return path


HOWTO = (
    "To use it on an iPhone or iPad:\n"
    "1. AirDrop the file to the device.\n"
    "2. It opens as a plain preview, which can't run it: tap Open in Sitecase.\n"
    "3. Sitecase loads the page and keeps it, so it works with no signal.\n\n"
    "Sitecase is one app that opens local web pages; any app that runs HTML and JavaScript will do. "
    "The Files app and Safari on their own won't. After you change your discs, export again and "
    "open the new file the same way.\n\n"
    "You can see this again under Help > Track Finder on a Phone…"
)


def show_how_to_open(
    parent: QWidget | None,
    path: Path | None = None,
    summary: str = "",
    notes: str = "",
    *,
    offer_hide: bool = False,
) -> None:
    """Say how to get the page onto a phone (and, after an export, where it was saved and anything that was
    left out). With `offer_hide` it has a "Don't show this again" tick, which is remembered
    (`settings.get_hide_finder_howto`); with a `path` a Show in Folder button. (Tests patch this: a box blocks.)"""
    box = QMessageBox(QMessageBox.Icon.Information, TITLE, "", QMessageBox.StandardButton.Ok, parent)  # fmt: skip
    box.setText(
        "\n\n".join(t for t in (summary or "The track finder on a phone", notes) if t)
    )
    box.setInformativeText(HOWTO)
    folder = box.addButton("Show in Folder", QMessageBox.ButtonRole.ActionRole) if path else None  # fmt: skip
    box.setDefaultButton(QMessageBox.StandardButton.Ok)
    tick = None
    if offer_hide:
        tick = QCheckBox("Don't show this again")
        box.setCheckBox(tick)
    box.exec()
    if tick is not None and tick.isChecked():
        settings.set_hide_finder_howto(True)
    if folder is not None and path is not None and box.clickedButton() is folder:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))


_real_show_how_to_open = (
    show_how_to_open  # (a test puts it back after the suite's refusal)
)


def export_track_finder(parent: QWidget, lib: Library) -> Written | None:
    """Write every disc as the page. Returns what was written when it was done at once; None if it was
    cancelled, could not be done (the user has been told which), or is still running.

    Tracks with no detailed waveform yet (no rekordbox analysis, none kept) are analysed from their audio
    first, on a worker with progress and a Cancel button, and what is found is kept for next time. rekordbox's
    cues are read on the same worker. With nothing of either to do it is all done at once."""
    notation = settings.get_key_notation()
    try:  # is there anything to list? (before asking where to save it)
        track_finder.collect(lib, notation=notation, waveforms=False, memory_cues=False)
    except FinderError as exc:
        QMessageBox.information(parent, TITLE, str(exc))
        return None
    path = ask_save_file(parent)
    if not path:
        return None
    todo = track_finder.needs_analysis(lib)
    databases = track_finder.rekordbox_databases(lib)
    beats = track_finder.beat_sources(lib)
    if not todo and not databases:
        return _write(parent, lib, Path(path), notation)

    token = CancelToken()

    def job(report):
        found, problems = (
            track_finder.analyse_waves(todo, report, token) if todo else ({}, [])
        )
        tables, cue_problem = track_finder.read_cue_tables(databases)
        return found, problems, tables, cue_problem, track_finder.read_beat_grids(beats)

    def done(result) -> None:
        found, problems, tables, cue_problem, grids = result
        parent._set_busy(False)
        track_finder.store_waves(lib, todo, found)
        _write(parent, lib, Path(path), notation, tables, cue_problem, problems, grids)

    def cancelled(partial) -> None:
        parent._set_busy(False)
        if partial:  # what was analysed is good: keep it for the next export
            track_finder.store_waves(lib, todo, partial[0])
        parent.statusBar().showMessage(
            "Export cancelled: nothing was written (the waveforms analysed so far are kept).",
            10000,
        )

    def failed(text: str) -> None:
        parent._set_busy(False)
        QMessageBox.warning(parent, TITLE, text)

    parent._set_busy(
        True,
        "Analysing waveforms…" if todo else "Reading cues…",
        cancel=token,
    )
    run_async(job, done, failed, parent._on_progress, cancelled)
    return None


def _write(
    parent: QWidget,
    lib: Library,
    path: Path,
    notation: str,
    tables=None,
    cue_problem: str = "",
    problems: Sequence[str] = (),
    grids=None,
) -> Written | None:
    try:
        found = track_finder.collect(
            lib,
            notation=notation,
            cue_tables=tables,
            cue_problem=cue_problem,
            grids=grids,
        )
        written = track_finder.write_html(found, path)
    except FinderError as exc:
        QMessageBox.warning(parent, TITLE, str(exc))
        return None
    message = (
        f"Saved a finder for {written.tracks} track(s) on {written.discs} disc(s) "
        f"to {written.path}"
    )
    status = getattr(parent, "statusBar", None)
    if status is not None:
        status().showMessage(message, 10000)
    notes = [track_finder.summary(found)]
    if problems:
        shown = "; ".join(problems[:5]) + (
            f"; and {len(problems) - 5} more" if len(problems) > 5 else ""
        )
        notes.append(f"Could not analyse: {shown}")
    left_out = "\n".join(n for n in notes if n)
    if (
        not settings.get_hide_finder_howto()
    ):  # one box: where it is, what was left out, how to open it
        show_how_to_open(parent, written.path, message, left_out, offer_hide=True)
    elif left_out:
        QMessageBox.information(parent, TITLE, f"{message}\n\n{left_out}")
    return written
