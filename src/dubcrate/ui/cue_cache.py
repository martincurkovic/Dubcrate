"""rekordbox's cues for the windows that draw them: the overview, the zoomed view and the track list.

rekordbox keeps cues in its database (`djmdCue`), not in the analysis files. `RekordboxCueCache`
reads a database once on a worker (`library/rekordbox.py: read_cues`: a snapshot copy, never the
live file), keeps what it read, and reads again only when the database or its journal changes in
size or date. `cues_for(track)` answers at once from what is held; if the database has not been read
yet it starts the read and answers None, and `changed` says when there is something new to draw.
"""

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from dubcrate.analysis import Cue
from dubcrate.library import Library
from dubcrate.library.models import Track
from dubcrate.library.rekordbox import RekordboxError, read_cues

from .tasks import TaskError, run_async

log = logging.getLogger(__name__)

Cues = Mapping[str, Sequence[Cue]]  # by a rekordbox track's content ID (its `ext_id`)


def _stamp(master_db: Path) -> tuple[int, ...]:
    """Size and modified time of rekordbox's database and its journal."""
    stamp: list[int] = []
    for file in (master_db, master_db.with_name(master_db.name + "-wal")):
        try:
            info = file.stat()
        except OSError:
            stamp += [0, 0]
        else:
            stamp += [info.st_mtime_ns, info.st_size]
    return tuple(stamp)


def _read(master_db: Path) -> Cues:
    """On a worker: every cue in the database, a failure turned into plain words."""
    try:
        return read_cues(master_db)
    except RekordboxError as exc:
        raise TaskError(f"Couldn't read rekordbox's cues: {exc}") from exc


class RekordboxCueCache(QObject):
    changed = (
        Signal()
    )  # a database was (re)read: whatever is drawn from `cues_for` can be asked again
    message = Signal(str)  # for the status bar

    def __init__(self, lib: Library, parent: QObject | None = None):
        super().__init__(parent)
        self._lib = lib
        self._held: dict[Path, tuple[tuple[int, ...], Cues]] = {}
        self._reading: set[Path] = set()

    def cues_for(self, track: Track, *, announce: bool = True) -> Sequence[Cue] | None:
        """The track's rekordbox cues (empty: it has none, or isn't in rekordbox); None: still being read.

        If the read this starts fails, `message` says so only when `announce`: the track list asks
        without it (it must not talk over the status bar), the log has the failure either way.

        A file that is also in rekordbox (same path) takes rekordbox's cues, as it takes its waveform.
        """
        analysed = self._lib.rekordbox_twin(track) or track
        source = next(
            (s for s in self._lib.sources() if s.id == analysed.source_id), None
        )
        if source is None or source.kind != "rekordbox":
            return ()
        database = Path(source.path)
        held = self._held.get(database)
        stamp = _stamp(database)
        if held is not None and held[0] == stamp:
            return held[1].get(analysed.ext_id, ())
        if database not in self._reading:
            self._read_in_background(database, stamp, announce)
        # an out-of-date answer is still better than none while the new one is read
        return held[1].get(analysed.ext_id, ()) if held is not None else None

    def _read_in_background(
        self, database: Path, stamp: tuple[int, ...], announce: bool
    ) -> None:
        self._reading.add(
            database
        )  # (the stamp was taken first: a change during the read re-reads)

        def done(found: Cues) -> None:
            self._reading.discard(database)
            self._held[database] = (stamp, found)
            self.changed.emit()

        def failed(text: str) -> None:
            self._reading.discard(database)
            self._held[database] = (stamp, {})  # not retried until the files change
            if announce:
                self.message.emit(text)
            self.changed.emit()

        run_async(lambda: _read(database), done, failed)
