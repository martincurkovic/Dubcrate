"""The window's side of the track finder (`track_finder`): where to save the page, and what to say after.

Disc > Export Track Finder… comes here. It writes one HTML page that says which disc a track is on; nothing
is read from or written to rekordbox, and no music file is touched.
"""

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from dubcrate import mp3, settings, track_finder
from dubcrate.library import Library
from dubcrate.track_finder import FinderError, Written

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


def export_track_finder(parent: QWidget, lib: Library) -> Written | None:
    """Write every disc as the page. Returns what was written, or None if it was cancelled or could not be
    done (the user has been told which)."""
    try:
        found = track_finder.collect(lib, notation=settings.get_key_notation())
    except FinderError as exc:
        QMessageBox.information(parent, TITLE, str(exc))
        return None
    path = ask_save_file(parent)
    if not path:
        return None
    try:
        written = track_finder.write_html(found, Path(path))
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
    left_out = track_finder.summary(found)
    if left_out:
        QMessageBox.information(parent, TITLE, f"{message}\n\n{left_out}")
    return written
