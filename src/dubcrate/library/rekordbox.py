"""Read a rekordbox 6/7 library (`master.db`) into track and playlist records.

Strictly read-only: the database is never opened in place. We copy `master.db`
(with its WAL sidecars, so recent uncommitted-to-main changes are included)
into a temp directory and read the copy, so a running rekordbox can't be
disturbed and nothing here can write to the real library.
"""

import shutil
import sys
import tempfile
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dubcrate.analysis import Cue
from dubcrate.library.models import NodeRecord, TrackRecord

Progress = Callable[[str], None]

# Playlist `Attribute` values in djmdPlaylist.
_ATTR_PLAYLIST = 0
_ATTR_FOLDER = 1
_ATTR_SMART = 4
# Internal rows rekordbox creates itself: the cloud-sync trial node and the
# "CUE Analysis Playlist". Neither is something the user built.
_SYSTEM_PLAYLIST_IDS = frozenset({"100000", "200000"})
_ROOT = "root"


class RekordboxError(RuntimeError):
    pass


@dataclass
class RekordboxLibrary:
    source: Path
    tracks: list[TrackRecord] = field(default_factory=list)
    nodes: list[NodeRecord] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def default_master_db() -> Path | None:
    """Locate the installed rekordbox library, if there is one."""
    if sys.platform == "darwin":
        candidate = Path.home() / "Library/Pioneer/rekordbox/master.db"
    elif sys.platform == "win32":
        appdata = Path.home() / "AppData/Roaming"
        candidate = appdata / "Pioneer/rekordbox/master.db"
    else:
        return None
    return candidate if candidate.is_file() else None


def _snapshot(master_db: Path, dest: Path) -> Path:
    """Copy master.db and its sidecars into `dest`; returns the copied db path."""
    target = dest / master_db.name
    shutil.copy2(master_db, target)
    for suffix in ("-wal", "-shm"):
        sidecar = master_db.with_name(master_db.name + suffix)
        if sidecar.is_file():
            shutil.copy2(sidecar, dest / sidecar.name)
    playlists_xml = master_db.with_name("masterPlaylists6.xml")
    if playlists_xml.is_file():  # pyrekordbox looks for it next to the db
        shutil.copy2(playlists_xml, dest / playlists_xml.name)
    return target


def _int_or_none(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _text(row: Any, name: str) -> str:
    """A text attribute of a row, '' if it is missing or empty (older databases lack some)."""
    value = getattr(row, name, None)
    return "" if value is None else str(value)


def track_from_content(row: Any) -> TrackRecord | None:
    """Map a djmdContent row to a TrackRecord; None if it has no file path."""
    path = getattr(row, "FolderPath", None)
    if not path:
        return None
    bpm = _int_or_none(row.BPM)  # stored as BPM x 100
    return TrackRecord(
        ext_id=str(row.ID),
        path=str(path),
        title=row.Title or Path(path).stem,
        artist=row.ArtistName or "",
        album=row.AlbumName or "",
        genre=row.GenreName or "",
        year=_int_or_none(row.ReleaseYear),
        comment=row.Commnt or "",
        bpm=bpm / 100 if bpm else None,
        key=row.KeyName or "",
        duration=float(row.Length or 0),
        bitrate=_int_or_none(row.BitRate),
        sample_rate=_int_or_none(row.SampleRate),
        bit_depth=_int_or_none(row.BitDepth),
        rating=row.Rating if row.Rating else None,
        file_size=_int_or_none(row.FileSize),
        analysis_path=row.AnalysisDataPath or None,
        label=_text(row, "LabelName"),
        remixer=_text(row, "RemixerName"),
        composer=_text(row, "ComposerName"),
        my_tags=", ".join(getattr(row, "MyTagNames", None) or []),
        play_count=_int_or_none(getattr(row, "DJPlayCount", None)),
        date_added=_text(row, "StockDate"),
    )


def build_nodes(
    playlists: Iterable[Any], songs: Iterable[Any], track_ids: set[str]
) -> tuple[list[NodeRecord], list[str]]:
    """Turn djmdPlaylist/djmdSongPlaylist rows into ordered NodeRecords.

    Skips deleted rows, rekordbox's own system nodes, and smart playlists
    (their members aren't stored, only a rule). Returns (nodes, notes).
    """
    kept: dict[str, Any] = {}
    smart_skipped = 0
    for p in playlists:
        if p.rb_local_deleted or str(p.ID) in _SYSTEM_PLAYLIST_IDS:
            continue
        if p.Attribute == _ATTR_SMART or (p.SmartList and p.Attribute != _ATTR_FOLDER):
            smart_skipped += 1
            continue
        if p.Attribute in (_ATTR_PLAYLIST, _ATTR_FOLDER):
            kept[str(p.ID)] = p

    members: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for s in songs:
        pid, cid = str(s.PlaylistID), str(s.ContentID)
        if s.rb_local_deleted or pid not in kept or cid not in track_ids:
            continue
        members[pid].append((s.TrackNo or 0, cid))

    children: dict[str | None, list[Any]] = defaultdict(list)
    for pid, p in kept.items():
        parent = str(p.ParentID)
        children[parent if parent in kept else None].append(p)

    nodes: list[NodeRecord] = []

    def walk(parent: str | None) -> None:
        for p in sorted(children[parent], key=lambda p: (p.Seq or 0, p.Name or "")):
            pid = str(p.ID)
            nodes.append(
                NodeRecord(
                    ext_id=pid,
                    parent_ext_id=parent,
                    name=p.Name or "(untitled)",
                    is_folder=p.Attribute == _ATTR_FOLDER,
                    track_ext_ids=tuple(c for _, c in sorted(members[pid])),
                )
            )
            walk(pid)

    walk(None)
    notes = []
    if smart_skipped:
        notes.append(
            f"skipped {smart_skipped} smart playlist(s): members are rule-based"
        )
    return nodes, notes


def read_master_db(
    path: Path | None = None, progress: Progress | None = None
) -> RekordboxLibrary:
    """Read the rekordbox library at `path` (default: the installed one)."""
    source = path or default_master_db()
    if source is None or not source.is_file():
        raise RekordboxError(
            f"no rekordbox master.db found at {path or 'the default location'}"
        )
    say = progress or (lambda _msg: None)
    try:
        from pyrekordbox.masterdb import MasterDatabase
    except ImportError as exc:  # pragma: no cover
        raise RekordboxError(f"pyrekordbox is unavailable: {exc}") from exc

    library = RekordboxLibrary(source=source)
    with tempfile.TemporaryDirectory(prefix="dubcrate-rb-") as tmp:
        say("copying library snapshot")
        copy = _snapshot(source, Path(tmp))
        try:
            db = MasterDatabase(path=copy)
        except Exception as exc:
            raise RekordboxError(f"could not open {source}: {exc}") from exc
        try:
            say("reading tracks")
            skipped_no_path = 0
            for row in db.get_content():
                if row.rb_local_deleted:
                    continue
                track = track_from_content(row)
                if track is None:
                    skipped_no_path += 1
                else:
                    library.tracks.append(track)
            if skipped_no_path:
                library.notes.append(
                    f"skipped {skipped_no_path} track(s) with no file path"
                )
            say("reading playlists")
            library.nodes, notes = build_nodes(
                db.get_playlist(),
                db.get_playlist_songs(),
                {t.ext_id for t in library.tracks},
            )
            library.notes += notes
        finally:
            db.close()
    return library


def cue_from_row(row: Any) -> Cue | None:
    """A `Cue` from a `djmdCue` row; None for a row that can't be used (no time, or deleted).

    rekordbox 6 and 7 keep cues **in the database**, not in the analysis files (those only carry
    them in a USB export): so the preview's cue markers, which read the analysis files, have shown
    none for a local library. `Kind` 0 is a memory cue and 1, 2, 3 ... a hot cue by number (A, B, C
    ...), **which was read off a real library and not from any documentation**, and `Kind` 5
    turned up on loops and as a near-copy of hot cue C: not understood. A row with an `OutMsec`
    after its `InMsec` is a loop, whatever its kind.
    """
    if getattr(row, "rb_local_deleted", 0):
        return None
    start = _int_or_none(getattr(row, "InMsec", None))
    if start is None or start < 0:
        return None
    number = _int_or_none(getattr(row, "Kind", 0)) or 0
    stop = _int_or_none(getattr(row, "OutMsec", None))
    is_loop = stop is not None and stop > start
    return Cue(
        kind="loop" if is_loop else ("hot" if number else "memory"),
        time=start / 1000.0,
        end=stop / 1000.0 if is_loop and stop is not None else None,
        slot=number or None,
        label=str(getattr(row, "Comment", "") or ""),
    )


def cues_by_content(rows: Iterable[Any]) -> dict[str, list[Cue]]:
    """Cues grouped by track (the content ID, a rekordbox track's `ext_id`), each in time order."""
    grouped: dict[str, list[Cue]] = defaultdict(list)
    for row in rows:
        if (cue := cue_from_row(row)) is not None:
            grouped[str(row.ContentID)].append(cue)
    return {k: sorted(v, key=lambda c: c.time) for k, v in grouped.items()}


def read_cues(path: Path | None = None) -> dict[str, list[Cue]]:
    """Every cue rekordbox holds, by track, from a snapshot copy (read-only, like `read_master_db`)."""
    source = path or default_master_db()
    if source is None or not source.is_file():
        raise RekordboxError(
            f"no rekordbox master.db found at {path or 'the default location'}"
        )
    try:
        from pyrekordbox.masterdb import MasterDatabase
    except ImportError as exc:  # pragma: no cover
        raise RekordboxError(f"pyrekordbox is unavailable: {exc}") from exc
    with tempfile.TemporaryDirectory(prefix="dubcrate-rb-") as tmp:
        copy = _snapshot(source, Path(tmp))
        try:
            db = MasterDatabase(path=copy)
        except Exception as exc:
            raise RekordboxError(f"could not open {source}: {exc}") from exc
        try:
            return cues_by_content(db.get_cue())
        finally:
            db.close()
