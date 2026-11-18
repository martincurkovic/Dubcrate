"""The windows' side of exporting discs as playlists (`playlist_export`): where to put it, and what to say after.

Disc > Export Playlists (every disc) and the Discs list's right-click > Export Playlist (one disc, or a disc
folder) both come here. A rekordbox XML is always one file; an M3U is one file for a single disc and a new
folder of files for several. Nothing is read from or written to rekordbox, only the files chosen here.
"""

from pathlib import Path
from typing import Literal

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from dubcrate import mp3, playlist_export
from dubcrate.library import Library
from dubcrate.playlist_export import PlaylistError, Written

Format = Literal["xml", "m3u"]

FORMAT_NAMES = {"xml": "rekordbox XML", "m3u": "M3U"}
TITLE = "Export Playlists"


def ask_save_file(parent: QWidget | None, title: str, name: str, fmt: Format) -> str:
    """Ask for the file to write ("" if cancelled). (Tests patch this: a real chooser blocks.)"""
    suffix, kind = (
        (".xml", "rekordbox XML (*.xml)") if fmt == "xml" else (".m3u8", "M3U (*.m3u8)")
    )
    path, _ = QFileDialog.getSaveFileName(
        parent, title, str(Path.home() / f"{name}{suffix}"), kind
    )
    if path and not path.lower().endswith((suffix, ".m3u")):
        path += suffix
    return path


def ask_save_folder(parent: QWidget | None) -> str:
    """Ask where to make the folder of M3U files ("" if cancelled). (Tests patch this.)"""
    return QFileDialog.getExistingDirectory(
        parent, "Export Playlists To", str(Path.home())
    )


def _file_name(name: str) -> str:
    return mp3.dir_name(name)


def export_playlists(
    parent: QWidget,
    lib: Library,
    fmt: Format,
    *,
    disc_id: int | None = None,
    folder_id: int | None = None,
) -> Written | None:
    """Export one disc, one disc folder, or (neither given) every disc. Returns what was written, or None if
    it was cancelled or could not be done (the user has been told which)."""
    try:
        collected = playlist_export.collect(lib, disc_id=disc_id, folder_id=folder_id)
    except PlaylistError as exc:
        QMessageBox.information(parent, TITLE, str(exc))
        return None
    single = collected.playlists == 1 and disc_id is not None
    try:
        if fmt == "xml" or single:
            if disc_id is not None:
                name = lib.disc(disc_id).name
            elif folder_id is not None:
                name = collected.root.folders[0].name
            else:
                name = "Dubcrate Playlists"
            path = ask_save_file(
                parent,
                f"Export {FORMAT_NAMES[fmt]}",
                _file_name(name),
                fmt,
            )
            if not path:
                return None
            written = (
                playlist_export.write_xml(collected, Path(path))
                if fmt == "xml"
                else playlist_export.write_m3u(collected, Path(path))
            )
        else:
            folder = ask_save_folder(parent)
            if not folder:
                return None
            written = playlist_export.write_m3u_folder(collected, Path(folder))
    except PlaylistError as exc:
        QMessageBox.warning(parent, TITLE, str(exc))
        return None
    message = (
        f"Exported {written.playlists} playlist(s), {written.tracks} track(s), "
        f"to {written.path}"
    )
    status = getattr(parent, "statusBar", None)
    if status is not None:
        status().showMessage(message, 10000)
    left_out = playlist_export.summary(collected)
    if left_out:
        QMessageBox.information(parent, TITLE, f"{message}\n\n{left_out}")
    return written
