"""Read a rekordbox USB stick (`exportLibrary.db`, "OneLibrary") into track, playlist and cue records.

**Strictly read-only**, like `rekordbox.py`: the stick's database is copied into a temp folder and the copy is read, so
nothing here can write to the stick, and rekordbox (or another program that manages it) is not disturbed. A stick is
its own source kind (`rekordbox_usb`), which is why the code that WRITES to rekordbox (`rekordbox_write`, the editor)
only ever sees a computer's `master.db`: it asks for `kind == "rekordbox"`.

A stick is `ROOT/PIONEER/rekordbox/exportLibrary.db` (`.PIONEER` on a stick that has had its folder hidden),
with the music under `ROOT/Contents/` and the analysis files (beat grids, waveforms) under
`ROOT/PIONEER/USBANLZ/`. The database holds paths **from the stick's root** (`/Contents/Artist/Track.mp3`,
`/.PIONEER/USBANLZ/P002/0002041C/ANLZ0000.DAT`), so a stick's tracks get `ROOT` put in front, and `analysis.read_*`
take `ROOT` as their root. Only `exportLibrary.db` is read: a stick that has only the older `export.pdb` (the
format Deep Symmetry documents) is refused with a message. `dev_docs/rekordbox-usb.md`.
"""

import logging
import shutil
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from dubcrate.analysis import Cue
from dubcrate.cancel import CancelToken
from dubcrate.library.models import NodeRecord, TrackRecord
from dubcrate.library.rekordbox import (
    Progress,
    RekordboxError,
    RekordboxLibrary,
    close_database,
    hot_slot,
)

log = logging.getLogger(__name__)

DB_NAME = "exportLibrary.db"
PIONEER_NAMES = ("PIONEER", ".PIONEER")  # (visible, or hidden as on the owner's stick)

SYSTEM_PLAYLIST = "CUE Analysis Playlist"
_ATTR_PLAYLIST = 0
_ATTR_FOLDER = 1
_NO_PARENT = 0


def pioneer_folder(root: Path) -> Path | None:
    """The stick's Pioneer folder (`PIONEER` or `.PIONEER`) under `root`, or None."""
    for name in PIONEER_NAMES:
        candidate = root / name
        if candidate.is_dir():
            return candidate
    return None


def library_file(root: Path) -> Path | None:
    """`exportLibrary.db` of the stick at `root`, or None."""
    folder = pioneer_folder(root)
    candidate = folder / "rekordbox" / DB_NAME if folder else None
    return candidate if candidate is not None and candidate.is_file() else None


def stick_root(path: Path) -> Path | None:
    """The stick's root from what the user pointed at: the root, its Pioneer folder, the `rekordbox` folder or the
    database itself. None if `path` isn't a stick that has an `exportLibrary.db`."""
    path = Path(path).expanduser()
    if path.name == DB_NAME:
        path = path.parent
    if path.name == "rekordbox" and path.parent.name in PIONEER_NAMES:
        path = path.parent
    if path.name in PIONEER_NAMES:
        path = path.parent
    return path if library_file(path) is not None else None


def find_sticks(volumes: Iterable[Path]) -> list[Path]:
    """The drives among `volumes` that are rekordbox sticks with an `exportLibrary.db`."""
    found = []
    for volume in volumes:
        try:
            if library_file(volume) is not None:
                found.append(volume)
        except OSError:  # (a drive that is going away)
            continue
    return found


def _unsupported(root: Path) -> RekordboxError:
    folder = pioneer_folder(root)
    if folder is not None and (folder / "rekordbox" / "export.pdb").is_file():
        return RekordboxError(
            f"“{root.name}” was exported by an older rekordbox (it has export.pdb but no {DB_NAME}), "
            "which Dubcrate can't read yet."
        )
    return RekordboxError(
        f"“{root}” isn't a rekordbox USB stick (no PIONEER/rekordbox/{DB_NAME})."
    )


def _snapshot(db: Path, dest: Path) -> Path:
    """Copy the database (and any sidecars) into `dest`; returns the copy."""
    target = dest / db.name
    shutil.copy2(db, target)
    for suffix in ("-wal", "-shm"):
        sidecar = db.with_name(db.name + suffix)
        if sidecar.is_file():
            shutil.copy2(sidecar, dest / sidecar.name)
    return target


def _open(copy: Path, source: Path) -> Any:
    try:
        from pyrekordbox.onelibrary import OneLibrary
    except ImportError as exc:  # pragma: no cover
        raise RekordboxError(f"pyrekordbox is unavailable: {exc}") from exc
    try:
        return OneLibrary(path=copy)
    except Exception as exc:
        raise RekordboxError(f"could not open {source}: {exc}") from exc


def _int_or_none(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _name(related: Any) -> str:
    """The `name` of a related row (artist, genre, ...), '' if there is none."""
    return str(getattr(related, "name", "") or "") if related is not None else ""


def track_from_content(
    row: Any, root: Path, my_tags: Iterable[str] = ()
) -> TrackRecord | None:
    """A `TrackRecord` from a OneLibrary `content` row; None if it has no file path. The paths in the database
    are from the stick's root: `root` goes in front of the file's (and the analysis path is kept as the database has it,
    for `analysis.read_*(root, ...)`)."""
    path = getattr(row, "path", None)
    if not path:
        return None
    bpm = _int_or_none(row.bpmx100)  # BPM x 100
    date = getattr(row, "dateAdded", None)
    return TrackRecord(
        ext_id=str(row.content_id),
        path=str(root / str(path).lstrip("/")),
        title=row.title or Path(path).stem,
        artist=_name(row.artist),
        album=_name(row.album),
        genre=_name(row.genre),
        year=_int_or_none(row.releaseYear),
        comment=row.djComment or "",
        bpm=bpm / 100 if bpm else None,
        key=_name(row.key),
        duration=float(row.length or 0),
        bitrate=_int_or_none(row.bitrate),
        sample_rate=_int_or_none(row.samplingRate),
        bit_depth=_int_or_none(row.bitDepth),
        rating=row.rating if row.rating else None,
        file_size=_int_or_none(row.fileSize),
        analysis_path=row.analysisDataFilePath or None,
        label=_name(row.label),
        remixer=_name(row.remixer),
        composer=_name(row.composer),
        my_tags=", ".join(my_tags),
        play_count=_int_or_none(row.djPlayCount),
        date_added=date.date().isoformat() if hasattr(date, "date") else "",
    )


def build_nodes(
    playlists: Iterable[Any], songs: Iterable[Any], track_ids: set[str]
) -> tuple[list[NodeRecord], list[str]]:
    """OneLibrary `playlist` / `playlist_content` rows as ordered NodeRecords (folders and playlists)."""
    songs = list(songs)
    members_of = Counter(str(s.playlist_id) for s in songs)
    kept = {
        str(p.playlist_id): p
        for p in playlists
        if p.attribute in (_ATTR_PLAYLIST, _ATTR_FOLDER)
        # rekordbox's own empty "CUE Analysis Playlist" (master.db import skips it by its ID, which a stick lacks)
        and not (p.name == SYSTEM_PLAYLIST and not members_of[str(p.playlist_id)])
    }
    members: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for s in songs:
        pid, cid = str(s.playlist_id), str(s.content_id)
        if pid in kept and cid in track_ids:
            members[pid].append((s.sequenceNo or 0, cid))
    children: dict[str | None, list[Any]] = defaultdict(list)
    for p in kept.values():
        parent = str(p.playlist_id_parent)
        children[parent if parent in kept else None].append(p)

    nodes: list[NodeRecord] = []

    def walk(parent: str | None) -> None:
        for p in sorted(
            children[parent], key=lambda p: (p.sequenceNo or 0, p.name or "")
        ):
            pid = str(p.playlist_id)
            nodes.append(
                NodeRecord(
                    ext_id=pid,
                    parent_ext_id=parent,
                    name=p.name or "(untitled)",
                    is_folder=p.attribute == _ATTR_FOLDER,
                    track_ext_ids=tuple(c for _, c in sorted(members[pid])),
                )
            )
            walk(pid)

    walk(None)
    return nodes, []


def read_usb(
    path: Path,
    progress: Progress | None = None,
    cancel: CancelToken | None = None,
) -> RekordboxLibrary:
    """Read the stick at `path` (its root, or anything inside that `stick_root` understands). The result's `source`
    is the stick's root. If `cancel` is set it raises `Cancelled` every few hundred tracks."""
    root = stick_root(path)
    if root is None:
        raise _unsupported(Path(path).expanduser())
    db_file = library_file(root)
    assert db_file is not None
    say = progress or (lambda _msg: None)
    library = RekordboxLibrary(source=root)
    try:
        with tempfile.TemporaryDirectory(prefix="dubcrate-usb-") as tmp:
            say("copying the stick's library")
            db = _open(_snapshot(db_file, Path(tmp)), db_file)
            try:
                say("reading tracks")
                from pyrekordbox.onelibrary import (
                    Content,
                    MyTagContent,
                    Playlist,
                    PlaylistContent,
                )

                session = db.session
                tags: dict[int, list[str]] = defaultdict(list)
                for link in session.query(MyTagContent):
                    if link.myTag is not None and link.myTag.name:
                        tags[link.content_id].append(link.myTag.name)
                skipped = 0
                for count, row in enumerate(session.query(Content)):
                    if cancel is not None and count % 200 == 0:
                        cancel.check()
                    track = track_from_content(row, root, tags.get(row.content_id, ()))
                    if track is None:
                        skipped += 1
                    else:
                        library.tracks.append(track)
                if skipped:
                    library.notes.append(
                        f"skipped {skipped} track(s) with no file path"
                    )
                if cancel is not None:
                    cancel.check()
                say("reading playlists")
                library.nodes, notes = build_nodes(
                    session.query(Playlist),
                    session.query(PlaylistContent),
                    {t.ext_id for t in library.tracks},
                )
                library.notes += notes
            finally:
                close_database(db)
    except OSError as exc:  # (a stick pulled while it was being read)
        raise RekordboxError(
            f"could not read “{root.name}”: {exc.strerror or exc}"
        ) from exc
    return library


def cue_from_row(row: Any) -> Cue | None:
    """A `Cue` from a OneLibrary `cue` row (same kinds as `master.db`'s: 0 memory, hot cues 1 to 3 and 5 to 7),
    None for a row with no time. A row whose out point is after its in point is a loop, whatever its kind."""
    start_us = getattr(row, "inUsec", None)
    if start_us is None or start_us < 0:
        return None
    number = _int_or_none(getattr(row, "kind", 0)) or 0
    slot = hot_slot(number)
    stop_us = getattr(row, "outUsec", None)
    is_loop = stop_us is not None and stop_us > start_us
    return Cue(
        kind="loop" if is_loop else ("hot" if slot else "memory"),
        time=start_us / 1_000_000.0,
        end=stop_us / 1_000_000.0 if is_loop and stop_us is not None else None,
        slot=slot,
        label=str(getattr(row, "cueComment", "") or ""),
    )


def read_usb_cues(path: Path) -> dict[str, list[Cue]]:
    """Every cue the stick holds, by track (the content id, a track's `ext_id`), each in time order. Read-only."""
    root = stick_root(path)
    if root is None:
        raise _unsupported(Path(path).expanduser())
    db_file = library_file(root)
    assert db_file is not None
    grouped: dict[str, list[Cue]] = defaultdict(list)
    try:
        with tempfile.TemporaryDirectory(prefix="dubcrate-usb-") as tmp:
            db = _open(_snapshot(db_file, Path(tmp)), db_file)
            try:
                from pyrekordbox.onelibrary import Cue as CueRow

                for row in db.session.query(CueRow):
                    if (cue := cue_from_row(row)) is not None:
                        grouped[str(row.content_id)].append(cue)
            except Exception as exc:  # e.g. a database with no cue table
                raise RekordboxError(
                    f"could not read the cues on “{root.name}”: {exc}"
                ) from exc
            finally:
                close_database(db)
    except OSError as exc:
        raise RekordboxError(
            f"could not read “{root.name}”: {exc.strerror or exc}"
        ) from exc
    return {k: sorted(v, key=lambda c: c.time) for k, v in grouped.items()}
