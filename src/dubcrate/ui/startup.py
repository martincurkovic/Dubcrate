"""Bringing your rekordbox library up to date while the splash screen is showing.

Only a rekordbox library you have already imported is touched (importing is your choice, and
this never reads rekordbox on a first run), and folders are not re-scanned (that is an ffprobe
per file, far too slow for a launch). The reading happens on a worker, as for any import, while
a local event loop keeps the splash alive; the library is written here, on the GUI thread.

It is careful, because it runs without you asking: a library that is missing, locked or unreadable
is skipped (and the status bar says why), and a rekordbox that suddenly reports no tracks at all
is not believed, so a bad moment can never empty your library.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QEventLoop

from dubcrate import settings
from dubcrate.library import Library
from dubcrate.library.rekordbox import RekordboxLibrary, read_master_db

from .tasks import run_async

Say = Callable[[str], None]


def _tracks(n: int) -> str:
    return f"{n} track{'s' if n != 1 else ''}"


def _is(n: int) -> str:
    return "is" if n == 1 else "are"


@dataclass
class StartupImport:
    added: int = 0
    removed: int = 0
    notes: list[str] = field(default_factory=list)

    def message(self) -> str:
        """What to tell the user once the window is up; empty if there is nothing to say."""
        parts = []
        if self.added and self.removed:
            parts.append(
                f"Brought in {self.added} new {_tracks(self.added).split(' ', 1)[1]} from rekordbox;"
                f" removed {_tracks(self.removed)} that {_is(self.removed)} gone"
            )
        elif self.added:
            parts.append(
                f"Brought in {self.added} new {_tracks(self.added).split(' ', 1)[1]} from rekordbox"
            )
        elif self.removed:
            parts.append(
                f"Removed {_tracks(self.removed)} that {_is(self.removed)} gone from rekordbox"
            )
        return "; ".join([*parts, *self.notes])


def _read(path: Path) -> tuple[RekordboxLibrary | None, str]:
    """Read a rekordbox library on a worker and wait for it, keeping the splash alive.

    Returns (library, "") or (None, why it failed).
    """
    outcome: dict = {}
    loop = QEventLoop()

    def done(result) -> None:
        outcome["library"] = result
        loop.quit()

    def failed(message: str) -> None:
        outcome["error"] = message
        loop.quit()

    # (The callbacks only run from the event loop, so they can't fire before `exec` starts.)
    run_async(lambda: read_master_db(path), done, failed)
    loop.exec()
    return outcome.get("library"), outcome.get("error", "")


def refresh_rekordbox_sources(lib: Library, say: Say) -> StartupImport:
    """Re-read every rekordbox library that has been imported, saying what it is doing."""
    result = StartupImport()
    for source in lib.sources():
        if source.kind != "rekordbox":
            continue
        path = Path(source.path)
        if not path.is_file():
            result.notes.append(f"“{source.name}” wasn't found, so it wasn't updated")
            continue
        say("Reading the rekordbox library…")
        data, error = _read(path)
        if data is None:
            result.notes.append(f"“{source.name}” couldn't be updated: {error}")
            continue
        if not data.tracks and source.track_count:
            result.notes.append(
                f"rekordbox reported no tracks, so “{source.name}” was kept as it was"
            )
            continue
        say("Updating your library…")
        summary = lib.replace_source(
            "rekordbox",
            str(data.source),
            source.name,
            data.tracks,
            data.nodes,
            data.notes,
        )
        result.added += summary.added
        result.removed += summary.removed
        result.notes += [n for n in summary.notes if n.startswith("kept ")]
    return result


def maybe_refresh(
    lib: Library, say: Say, *, skip: bool = False
) -> StartupImport | None:
    """`refresh_rekordbox_sources`, unless Settings or the command line say not to."""
    if skip or not settings.get_auto_import():
        return None
    return refresh_rekordbox_sources(lib, say)
