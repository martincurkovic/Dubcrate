"""Dubcrate's own SQLite library: imported sources, their tracks and browse trees.

Sources (a folder, a rekordbox library) are read-only inputs. Re-importing a
source syncs it in place: tracks are matched on `ext_id` so their ids stay
stable, tracks that vanished from the source are removed, and the browse tree
is rebuilt.
"""

import json
import logging
import os
import sqlite3
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from dubcrate import keys, mp3
from dubcrate.cd import DISC_80_SECTORS, MAX_TRACKS, disc_used_sectors
from dubcrate.library.models import (
    AddResult,
    DiscDir,
    DiscEntry,
    DiscFolder,
    DiscImage,
    DiscKind,
    DiscRef,
    DiscSummary,
    ImportSummary,
    Node,
    NodeRecord,
    Source,
    SourceKind,
    SplitResult,
    Track,
    TrackRecord,
    TrackUsage,
)
from dubcrate.library.paths import default_library_path
from dubcrate.planning import fit_tracks, split_sequential

log = logging.getLogger(__name__)
from dubcrate.waveform import Waveform, pack_preview, unpack_preview


def _max_tracks(kind: str) -> int:
    """Red Book allows 99 tracks on an audio CD; an MP3 CD holds 999 files in a folder."""
    return MAX_TRACKS if kind == "audio" else mp3.MAX_FILES_PER_FOLDER


def _limit_error(kind: str) -> str:
    if kind == "audio":
        return f"a disc holds at most {MAX_TRACKS} tracks"
    return f"a folder holds at most {mp3.MAX_FILES_PER_FOLDER} files"


@dataclass(frozen=True)
class _Slot:
    """A row of a disc as the store moves it about: a track and the CD text edits that
    belong to this place on this disc (None: the track's own)."""

    track_id: int
    title: str | None = None
    artist: str | None = None
    dir_id: int | None = None  # the folder on an MP3 disc; None: the root


# The Explorer tab's tracks live in a hidden source of their own: it is not listed with the
# sources and its tracks are not in "All Tracks", but its tracks are library tracks like any
# other, so a disc can hold them (`Library.sync_directory`, `prune_explorer`).
EXPLORER_PATH = "explorer:"
EXPLORER_NAME = "Explorer"
# What the Explorer has read is kept between sessions (a folder opens at once and only new or changed files
# are probed); past this many tracks that no disc uses, the oldest read are forgotten (`prune_explorer`).
MAX_EXPLORER_TRACKS = 20_000


class LibraryError(RuntimeError):
    pass


class DiscError(LibraryError):
    pass


_SCHEMA_V1 = """
CREATE TABLE source (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('folder', 'rekordbox')),
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    UNIQUE (kind, path)
);
CREATE TABLE track (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES source(id) ON DELETE CASCADE,
    ext_id TEXT NOT NULL,
    path TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    artist TEXT NOT NULL DEFAULT '',
    album TEXT NOT NULL DEFAULT '',
    genre TEXT NOT NULL DEFAULT '',
    year INTEGER,
    comment TEXT NOT NULL DEFAULT '',
    bpm REAL,
    key TEXT NOT NULL DEFAULT '',
    duration REAL NOT NULL DEFAULT 0,
    bitrate INTEGER,
    sample_rate INTEGER,
    bit_depth INTEGER,
    rating INTEGER,
    file_size INTEGER,
    analysis_path TEXT,
    missing INTEGER NOT NULL DEFAULT 0,
    UNIQUE (source_id, ext_id)
);
CREATE INDEX track_source ON track(source_id);
CREATE TABLE node (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES source(id) ON DELETE CASCADE,
    ext_id TEXT NOT NULL,
    parent_id INTEGER REFERENCES node(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    is_folder INTEGER NOT NULL,
    position INTEGER NOT NULL,
    UNIQUE (source_id, ext_id)
);
CREATE TABLE node_track (
    node_id INTEGER NOT NULL REFERENCES node(id) ON DELETE CASCADE,
    track_id INTEGER NOT NULL REFERENCES track(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    PRIMARY KEY (node_id, position)
);
CREATE INDEX node_track_track ON node_track(track_id);
"""

_SCHEMA_V2 = """
ALTER TABLE track ADD COLUMN cd_sectors INTEGER;
ALTER TABLE track ADD COLUMN stale INTEGER NOT NULL DEFAULT 0;
CREATE TABLE disc (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    capacity_sectors INTEGER NOT NULL,
    pregap_sectors INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE disc_track (
    disc_id INTEGER NOT NULL REFERENCES disc(id) ON DELETE CASCADE,
    track_id INTEGER NOT NULL REFERENCES track(id) ON DELETE RESTRICT,
    position INTEGER NOT NULL,
    PRIMARY KEY (disc_id, position)
);
CREATE INDEX disc_track_track ON disc_track(track_id)
"""

_SCHEMA_V3 = """
CREATE TABLE burn (
    id INTEGER PRIMARY KEY,
    disc_id INTEGER REFERENCES disc(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    capacity_sectors INTEGER NOT NULL,
    pregap_sectors INTEGER NOT NULL,
    used_sectors INTEGER NOT NULL,
    drive TEXT NOT NULL DEFAULT '',
    speed INTEGER,
    simulated INTEGER NOT NULL DEFAULT 0,
    burned_at TEXT NOT NULL
);
CREATE INDEX burn_disc ON burn(disc_id);
CREATE TABLE burn_track (
    burn_id INTEGER NOT NULL REFERENCES burn(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    track_id INTEGER REFERENCES track(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    sectors INTEGER NOT NULL,
    path TEXT NOT NULL,
    PRIMARY KEY (burn_id, position)
)
"""

_SCHEMA_V4 = """
ALTER TABLE track ADD COLUMN label TEXT NOT NULL DEFAULT '';
ALTER TABLE track ADD COLUMN remixer TEXT NOT NULL DEFAULT '';
ALTER TABLE track ADD COLUMN composer TEXT NOT NULL DEFAULT '';
ALTER TABLE track ADD COLUMN my_tags TEXT NOT NULL DEFAULT '';
ALTER TABLE track ADD COLUMN play_count INTEGER;
ALTER TABLE track ADD COLUMN date_added TEXT NOT NULL DEFAULT ''
"""

_SCHEMA_V5 = """
ALTER TABLE disc_track ADD COLUMN title TEXT;
ALTER TABLE disc_track ADD COLUMN artist TEXT
"""

_SCHEMA_V6 = """
CREATE TABLE disc_folder (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    parent_id INTEGER REFERENCES disc_folder(id) ON DELETE CASCADE
);
ALTER TABLE disc ADD COLUMN folder_id INTEGER REFERENCES disc_folder(id) ON DELETE SET NULL;
ALTER TABLE disc ADD COLUMN last_burned_at TEXT
"""


def _migrate_to_v6(conn: sqlite3.Connection) -> None:
    """Discs get folders and a "last burned" note; burn records stop being a thing.

    A burn record was a frozen copy of a disc. Now a disc is just a playlist, so: a disc
    that was burned remembers when, and a burn whose disc had been deleted becomes a disc
    of its own (what it had burned, edits included), so that no history is thrown away.
    """
    for statement in _SCHEMA_V6.split(";"):
        if statement.strip():
            conn.execute(statement)
    conn.execute(
        "UPDATE disc SET last_burned_at = (SELECT MAX(burned_at) FROM burn"
        " WHERE burn.disc_id = disc.id AND burn.simulated = 0)"
    )
    orphans = conn.execute(
        "SELECT * FROM burn WHERE disc_id IS NULL AND simulated = 0 ORDER BY id"
    ).fetchall()
    for burn in orphans:
        disc_id = conn.execute(
            "INSERT INTO disc (name, capacity_sectors, pregap_sectors, created_at,"
            " updated_at, last_burned_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                burn["name"], burn["capacity_sectors"], burn["pregap_sectors"],
                burn["burned_at"], burn["burned_at"], burn["burned_at"],
            ),
        ).lastrowid  # fmt: skip
        rows = conn.execute(
            "SELECT bt.track_id, bt.title, bt.artist, t.title AS own_title,"
            " t.artist AS own_artist FROM burn_track bt JOIN track t ON t.id = bt.track_id"
            " WHERE bt.burn_id = ? ORDER BY bt.position",
            (burn["id"],),
        ).fetchall()  # (tracks that have since left the library can't be put back)
        for position, row in enumerate(rows):
            conn.execute(
                "INSERT INTO disc_track (disc_id, track_id, position, title, artist)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    disc_id, row["track_id"], position,
                    row["title"] if row["title"] != row["own_title"] else None,
                    row["artist"] if row["artist"] != row["own_artist"] else None,
                ),
            )  # fmt: skip
    conn.execute("DROP TABLE burn_track")
    conn.execute("DROP TABLE burn")


_SCHEMA_V7 = """
ALTER TABLE disc ADD COLUMN kind TEXT NOT NULL DEFAULT 'audio';
ALTER TABLE disc ADD COLUMN mp3_bitrate INTEGER
"""

_SCHEMA_V8 = """
CREATE TABLE disc_dir (
    id INTEGER PRIMARY KEY,
    disc_id INTEGER NOT NULL REFERENCES disc(id) ON DELETE CASCADE,
    parent_id INTEGER REFERENCES disc_dir(id) ON DELETE CASCADE,
    name TEXT NOT NULL
);
CREATE INDEX disc_dir_disc ON disc_dir(disc_id);
ALTER TABLE disc_track ADD COLUMN dir_id INTEGER REFERENCES disc_dir(id) ON DELETE SET NULL
"""

_SCHEMA_V9 = """
ALTER TABLE source ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0
"""

_SCHEMA_V10 = """
ALTER TABLE track ADD COLUMN loudness_lufs REAL;
ALTER TABLE track ADD COLUMN true_peak_db REAL
"""

_SCHEMA_V11 = """
ALTER TABLE track ADD COLUMN waveform BLOB
"""

# The Preview strip of a folder track, computed when it is played, kept across sessions. Keyed by the
# file (`path_key` and size), NOT by track id: the Explorer forgets its tracks at every start-up and a
# re-import can give a file a new id, and neither should cost the strip.
_SCHEMA_V12 = """
CREATE TABLE preview_cache (
    path_key TEXT PRIMARY KEY,
    file_size INTEGER NOT NULL,
    bands BLOB NOT NULL
)
"""

# Memory cues the user edited in Dubcrate (never written to rekordbox, `dev_docs/rekordbox-cue-writes.md`). Keyed
# by the file (`path_key`), like the preview cache, so they survive a re-import and are the same whichever row
# (rekordbox's, a folder's, the Explorer's) the file comes under. A track with a `memory_cue_set` row has
# its memory cues replaced by the `memory_cue` rows (there may be none: all deleted).
_SCHEMA_V13 = """
CREATE TABLE memory_cue_set (
    path_key TEXT PRIMARY KEY,
    edited_at TEXT NOT NULL
);
CREATE TABLE memory_cue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path_key TEXT NOT NULL,
    time_ms INTEGER NOT NULL,
    label TEXT NOT NULL DEFAULT ''
);
CREATE INDEX memory_cue_path ON memory_cue (path_key)
"""

# Memory loops saved in Dubcrate (MEMORY pressed with a loop on). Kept apart from `memory_cue` on purpose: they
# are added to a track's cues, never replace rekordbox's loops, and are not written to rekordbox (yet), so
# nothing that reads `memory_cue` can send one there as a plain cue. Keyed by `path_key` like the cues.
_SCHEMA_V14 = """
CREATE TABLE memory_loop (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path_key TEXT NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    label TEXT NOT NULL DEFAULT ''
);
CREATE INDEX memory_loop_path ON memory_loop (path_key)
"""

# What each sync left on a card, per disc (by its table of contents, the way the card tells discs apart): the cues
# Dubcrate wanted that are on the card afterwards (track, position in absolute frames, a loop's end or 0). Import Cues
# compares the card with it: a cue in it that is gone from the card was deleted on the player, and a cue on the card
# that is in it but not in Dubcrate any more was deleted or moved in Dubcrate (so it is not offered back as new).
# `card_baseline_disc` says a disc has a baseline even when it has no cues.
_SCHEMA_V15 = """
CREATE TABLE card_baseline_disc (
    disc_key TEXT PRIMARY KEY,
    saved_at TEXT NOT NULL
);
CREATE TABLE card_baseline (
    disc_key TEXT NOT NULL,
    track INTEGER NOT NULL,
    position INTEGER NOT NULL,
    end_pos INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX card_baseline_key ON card_baseline (disc_key)
"""

# Memory loops deleted in Dubcrate that rekordbox may still have: (start ms, end ms) per file, the loop to hide from the
# track's cues and to soft delete in rekordbox (`rekordbox_write`, like a memory cue). A loop saved in Dubcrate that
# rekordbox does not have yet is just removed from `memory_loop`: nothing is recorded here for it.
_SCHEMA_V16 = """
CREATE TABLE memory_loop_removed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path_key TEXT NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL
);
CREATE INDEX memory_loop_removed_path ON memory_loop_removed (path_key)
"""

# The table of contents the drive read back from a disc just burned (`burn_check`), with the TOC that was planned for it
# (JSON: [track starts, lead-out] in absolute frames). A drive can lay a disc out a frame differently from the plan, and
# the card knows a disc by its TOC, so Sync uses `actual` while `planned` is still what the disc would be planned as
# (an edited disc no longer matches `planned`, so its record is ignored).
_SCHEMA_V17 = """
CREATE TABLE disc_burned_toc (
    disc_id INTEGER PRIMARY KEY REFERENCES disc(id) ON DELETE CASCADE,
    planned TEXT NOT NULL,
    actual TEXT NOT NULL,
    saved_at TEXT NOT NULL
)
"""

# Schema 17 kept one TOC per disc; a playlist can be burned more than once (another machine, another gap setting), and
# each real disc has its own TOC, so a disc has a list: every TOC known to belong to a physical copy of it, `source`
# 'burn' (read back right after a burn) or 'manual' (read in the TOC Manager), each with the TOC planned when it was
# recorded (an edited disc no longer matches it, and the record is then ignored). One row per (disc, actual TOC).
# `enabled` 0: kept in the list but not used by Sync (a TOC that is wrong or out of date).
_SCHEMA_V18 = """
CREATE TABLE disc_toc (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    disc_id INTEGER NOT NULL REFERENCES disc(id) ON DELETE CASCADE,
    planned TEXT NOT NULL,
    actual TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('burn', 'manual')),
    saved_at TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    UNIQUE (disc_id, actual)
);
INSERT INTO disc_toc (disc_id, planned, actual, source, saved_at)
    SELECT disc_id, planned, actual, 'burn', saved_at FROM disc_burned_toc;
DROP TABLE disc_burned_toc
"""

# Schema 19: the image an MP3 disc was burned from (`DiscImage`): the CDJ's ID for the disc has the image's creation
# date in it, so it is kept to make the same ID again. One per disc (a new burn replaces it).
_SCHEMA_V19 = """
CREATE TABLE disc_image (
    disc_id INTEGER PRIMARY KEY REFERENCES disc(id) ON DELETE CASCADE,
    created TEXT NOT NULL,
    label TEXT NOT NULL,
    file_count INTEGER NOT NULL,
    folders INTEGER NOT NULL,
    last_file_start INTEGER NOT NULL,
    sectors INTEGER NOT NULL,
    saved_at TEXT NOT NULL
)
"""


def _allow_source_kind(conn: sqlite3.Connection, before: str, after: str) -> None:
    """Relax the `source.kind` check from `before` to `after` (the lists inside `CHECK (kind IN (...))`).

    SQLite can't change a CHECK, and rebuilding `source` would, with foreign keys on, delete every track through the
    cascade. A check that is only RELAXED can be changed in the stored table definition (SQLite's own documented way:
    `PRAGMA writable_schema`), which touches no row. The schema version is bumped so every connection reads it again."""
    version = conn.execute("PRAGMA schema_version").fetchone()[0]
    conn.execute("PRAGMA writable_schema = ON")
    try:
        changed = conn.execute(
            "UPDATE sqlite_master SET sql = replace(sql, ?, ?) WHERE type = 'table' AND name = 'source'",
            (f"CHECK (kind IN ({before}))", f"CHECK (kind IN ({after}))"),
        ).rowcount
        if changed != 1:
            raise RuntimeError(
                "the library's source table has an unexpected definition"
            )
    finally:
        conn.execute("PRAGMA writable_schema = OFF")
    conn.execute(f"PRAGMA schema_version = {version + 1}")


def _migrate_to_v20(conn: sqlite3.Connection) -> None:
    """A source may be a rekordbox USB stick (`rekordbox_usb`)."""
    _allow_source_kind(
        conn, "'folder', 'rekordbox'", "'folder', 'rekordbox', 'rekordbox_usb'"
    )


def _migrate_to_v21(conn: sqlite3.Connection) -> None:
    """A source may be a rekordbox XML file (`rekordbox_xml`)."""
    _allow_source_kind(
        conn,
        "'folder', 'rekordbox', 'rekordbox_usb'",
        "'folder', 'rekordbox', 'rekordbox_usb', 'rekordbox_xml'",
    )


_MIGRATIONS = (
    _SCHEMA_V1, _SCHEMA_V2, _SCHEMA_V3, _SCHEMA_V4, _SCHEMA_V5, _migrate_to_v6,
    _SCHEMA_V7, _SCHEMA_V8, _SCHEMA_V9, _SCHEMA_V10, _SCHEMA_V11, _SCHEMA_V12,
    _SCHEMA_V13, _SCHEMA_V14, _SCHEMA_V15, _SCHEMA_V16, _SCHEMA_V17, _SCHEMA_V18,
    _SCHEMA_V19, _migrate_to_v20, _migrate_to_v21,
)  # fmt: skip

MAX_CACHED_PREVIEWS = 20_000  # about 40 MB; past it the oldest are forgotten
SCHEMA_VERSION = len(_MIGRATIONS)


@dataclass(frozen=True)
class KnownToc:
    """A TOC recorded for a disc: what the drive read from a physical copy, with the TOC planned at the time."""

    id: int
    planned: tuple[list[int], int]
    actual: tuple[list[int], int]
    source: str  # 'burn' (read back after a burn) or 'manual' (TOC Manager)
    saved_at: str
    enabled: bool = True  # False: kept, but Sync ignores it


def _toc_json(toc: tuple[Sequence[int], int]) -> str:
    return json.dumps([list(toc[0]), toc[1]])


def _toc_from_json(text: str) -> tuple[list[int], int]:
    starts, lead_out = json.loads(text)
    return [int(n) for n in starts], int(lead_out)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def path_key(path: str) -> str:
    """What two paths to the same file have in common, so rekordbox's copy of a track and the
    Explorer's can be told to be one. A Mac keeps a name's case and Unicode form (é as one
    character, or as e and an accent) but counts those as the same file, so both are folded."""
    return unicodedata.normalize("NFC", path).casefold()


_TRACK_FIELDS = (
    "path", "title", "artist", "album", "genre", "year", "comment", "bpm", "key",
    "duration", "bitrate", "sample_rate", "bit_depth", "rating", "file_size",
    "analysis_path", "label", "remixer", "composer", "my_tags", "play_count", "date_added",
)  # fmt: skip

_SORTS = {
    "title": "t.title COLLATE NOCASE",
    "artist": "t.artist COLLATE NOCASE",
    "album": "t.album COLLATE NOCASE",
    "genre": "t.genre COLLATE NOCASE",
    "bpm": "t.bpm",
    "key": "key_order(t.key), t.key COLLATE NOCASE",
    "duration": "t.duration",
    "year": "t.year",
    "rating": "t.rating",
    "path": "t.path COLLATE NOCASE",
    "comment": "t.comment COLLATE NOCASE",
    "bitrate": "t.bitrate",
    "sample_rate": "t.sample_rate",
    "label": "t.label COLLATE NOCASE",
    "remixer": "t.remixer COLLATE NOCASE",
    "composer": "t.composer COLLATE NOCASE",
    "my_tags": "t.my_tags COLLATE NOCASE",
    "play_count": "t.play_count",
    "date_added": "t.date_added",
}


def _like_escape(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class Library:
    """Handle to the SQLite library. Not thread-safe; open one per thread."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        # the rekordbox rows by file, and the connection's `total_changes` they were read at (see below)
        self._rekordbox_files: tuple[int, dict[str, sqlite3.Row]] | None = None

    @classmethod
    def open(cls, path: Path | str | None = None) -> "Library":
        """Open (creating if needed) the library at `path`, or the default one.

        Pass ":memory:" for a throwaway library.
        """
        target = default_library_path() if path is None else path
        if str(target) != ":memory:":
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(target))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.create_function("key_order", 1, keys.sort_value, deterministic=True)
        if str(target) != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
        library = cls(conn)
        library._migrate()
        return library

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"library schema v{version} is newer than this Dubcrate (v{SCHEMA_VERSION})"
            )
        if version < SCHEMA_VERSION:
            log.info("library schema v%d -> v%d", version, SCHEMA_VERSION)
        for target, script in enumerate(_MIGRATIONS, start=1):
            if version < target:
                with self._conn:
                    if callable(script):
                        script(self._conn)
                    else:
                        for statement in script.split(";"):
                            if statement.strip():
                                self._conn.execute(statement)
                    self._conn.execute(f"PRAGMA user_version = {target}")

    # -- sources ---------------------------------------------------------

    def sources(self) -> list[Source]:
        """The imported sources (not the Explorer's hidden one)."""
        rows = self._conn.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM track WHERE source_id = s.id AND stale = 0) AS n"
            " FROM source s WHERE s.hidden = 0 ORDER BY s.name COLLATE NOCASE, s.id"
        )
        return [
            Source(r["id"], r["kind"], r["path"], r["name"], r["imported_at"], r["n"])
            for r in rows
        ]

    def track_paths(self, source_id: int) -> list[tuple[int, str]]:
        """(id, file path) of every track of a source, for a worker to look at the files (the library stays here)."""
        return [
            (r["id"], r["path"])
            for r in self._conn.execute(
                "SELECT id, path FROM track WHERE source_id = ?", (source_id,)
            )
        ]

    def set_missing(self, source_id: int, missing_ids: Iterable[int]) -> int:
        """Mark exactly `missing_ids` of the source's tracks as "file not found" and the rest as found (what an
        import does for each file as it reads it). Returns how many tracks changed. A drive that is unplugged or
        plugged in again is the reason: the files are not looked at here, the caller did that."""
        missing = set(missing_ids)
        with self._conn:
            rows = self._conn.execute(
                "SELECT id, missing FROM track WHERE source_id = ?", (source_id,)
            ).fetchall()
            changed = [(int(r["id"] in missing), r["id"]) for r in rows if bool(r["missing"]) != (r["id"] in missing)]  # fmt: skip
            self._conn.executemany("UPDATE track SET missing = ? WHERE id = ?", changed)
        return len(changed)

    def move_source(self, source_id: int, new_path: str) -> None:
        """A source whose folder is now somewhere else (a USB stick that came back under another name): its path and
        every track's path under it change, **and so do the things kept by a file's path** (memory cue and loop edits,
        the kept preview waveforms), which would otherwise be left behind. Raises `LibraryError` if another source
        of the kind is already at `new_path`. A key that already exists at the new place is left alone."""
        row = self._conn.execute(
            "SELECT kind, path FROM source WHERE id = ?", (source_id,)
        ).fetchone()
        if row is None:
            raise LibraryError(f"no source {source_id}")
        old = row["path"]
        if new_path == old:
            return
        if self._conn.execute(
            "SELECT 1 FROM source WHERE kind = ? AND path = ?", (row["kind"], new_path)
        ).fetchone():
            raise LibraryError("another source is already there")
        with self._conn:
            for track in self._conn.execute(
                "SELECT id, path FROM track WHERE source_id = ?", (source_id,)
            ).fetchall():
                path = track["path"]
                if not path.startswith(old):
                    continue
                moved = new_path + path[len(old) :]
                self._conn.execute("UPDATE track SET path = ? WHERE id = ?", (moved, track["id"]))  # fmt: skip
                before, after = path_key(path), path_key(moved)
                for table in ("memory_cue_set", "memory_cue", "memory_loop", "memory_loop_removed", "preview_cache"):  # fmt: skip
                    self._conn.execute(
                        f"UPDATE OR IGNORE {table} SET path_key = ? WHERE path_key = ?",
                        (after, before),
                    )
            self._conn.execute("UPDATE source SET path = ? WHERE id = ?", (new_path, source_id))  # fmt: skip
        log.info("source %s moved from %s to %s", source_id, old, new_path)

    def remove_source(self, source_id: int, *, force: bool = False) -> None:
        """Delete a source and its tracks.

        Refuses if any of its tracks are on a disc, unless `force`, which
        takes them off those discs first.
        """
        with self._conn:
            on_discs = self._conn.execute(
                "SELECT COUNT(DISTINCT dt.disc_id) FROM disc_track dt"
                " JOIN track t ON t.id = dt.track_id WHERE t.source_id = ?",
                (source_id,),
            ).fetchone()[0]
            if on_discs and not force:
                raise LibraryError(f"source has tracks on {on_discs} disc(s)")
            self._conn.execute(
                "DELETE FROM disc_track WHERE track_id IN"
                " (SELECT id FROM track WHERE source_id = ?)",
                (source_id,),
            )
            self._conn.execute("DELETE FROM source WHERE id = ?", (source_id,))
        log.info("removed source %s (%s disc(s) lost tracks)", source_id, on_discs)

    def replace_source(
        self,
        kind: SourceKind,
        path: str,
        name: str,
        tracks: Iterable[TrackRecord],
        nodes: Iterable[NodeRecord] = (),
        notes: Iterable[str] = (),
    ) -> ImportSummary:
        """Create or sync the source at (`kind`, `path`) in one transaction."""
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._conn:
            row = self._conn.execute(
                "SELECT id FROM source WHERE kind = ? AND path = ?", (kind, path)
            ).fetchone()
            if row:
                source_id = row["id"]
                self._conn.execute(
                    "UPDATE source SET name = ?, imported_at = ? WHERE id = ?",
                    (name, now, source_id),
                )
            else:
                source_id = self._conn.execute(
                    "INSERT INTO source (kind, path, name, imported_at) VALUES (?, ?, ?, ?)",
                    (kind, path, name, now),
                ).lastrowid
            assert source_id is not None

            existing = {
                r["ext_id"]: r["id"]
                for r in self._conn.execute(
                    "SELECT ext_id, id FROM track WHERE source_id = ?", (source_id,)
                )
            }
            ids: dict[str, int] = {}
            added = updated = 0
            for rec in tracks:
                track_id, is_new = self._write_track(source_id, rec, existing)
                if is_new:
                    added += 1
                elif rec.ext_id not in ids:
                    updated += 1
                ids[rec.ext_id] = track_id

            # Tracks that left the source are deleted, unless a disc uses them:
            # those are kept (marked stale) so no disc silently loses a track.
            gone = [i for ext, i in existing.items() if ext not in ids]
            kept, dropped = self._drop_gone(source_id, gone)
            notes = list(notes)
            if kept:
                notes.append(
                    f"kept {len(kept)} track(s) no longer in the source: they're on discs"
                )

            node_count = self._replace_nodes(source_id, list(nodes), ids)
        log.info(
            "source %s %s (%s): %d added, %d updated, %d removed, %d kept on discs",
            source_id,
            name,
            kind,
            added,
            updated,
            len(dropped),
            len(kept),
        )
        return ImportSummary(
            source_id, added, updated, len(dropped), node_count, tuple(notes)
        )

    def _write_track(
        self, source_id: int, rec: TrackRecord, existing: dict[str, int]
    ) -> tuple[int, bool]:
        """Add `rec` to the source, or update its row: matched on `ext_id` through `existing`
        (which grows), so a track keeps its id. Returns (id, whether it is new)."""
        values = [getattr(rec, f) for f in _TRACK_FIELDS]
        missing = int(not os.path.exists(rec.path))
        if rec.ext_id in existing:
            track_id = existing[rec.ext_id]
            assignments = ", ".join(f"{f} = ?" for f in _TRACK_FIELDS)
            # A changed file size means the measured CD length, loudness and waveform are out of date.
            self._conn.execute(
                "UPDATE track SET cd_sectors = CASE WHEN file_size IS ? THEN cd_sectors END,"
                " loudness_lufs = CASE WHEN file_size IS ? THEN loudness_lufs END,"
                " true_peak_db = CASE WHEN file_size IS ? THEN true_peak_db END,"
                " waveform = CASE WHEN file_size IS ? THEN waveform END,"
                f" {assignments}, missing = ?, stale = 0 WHERE id = ?",
                [rec.file_size] * 4 + [*values, missing, track_id],
            )
            return track_id, False
        columns = ", ".join(_TRACK_FIELDS)
        placeholders = ", ".join("?" for _ in _TRACK_FIELDS)
        track_id = self._conn.execute(
            f"INSERT INTO track (source_id, ext_id, missing, {columns})"
            f" VALUES (?, ?, ?, {placeholders})",
            [source_id, rec.ext_id, missing, *values],
        ).lastrowid
        assert track_id is not None
        existing[rec.ext_id] = track_id
        return track_id, True

    def _drop_gone(
        self, source_id: int, gone: list[int]
    ) -> tuple[list[int], list[int]]:
        """Delete the tracks that left a source, except those a disc uses: they are kept and
        marked stale. Returns (kept, dropped) track ids."""
        on_discs = {
            r[0]
            for r in self._conn.execute(
                "SELECT DISTINCT dt.track_id FROM disc_track dt"
                " JOIN track t ON t.id = dt.track_id WHERE t.source_id = ?",
                (source_id,),
            )
        }
        kept = [i for i in gone if i in on_discs]
        dropped = [i for i in gone if i not in on_discs]
        self._conn.executemany(
            "UPDATE track SET stale = 1 WHERE id = ?", [(i,) for i in kept]
        )
        self._conn.executemany(
            "DELETE FROM track WHERE id = ?", [(i,) for i in dropped]
        )
        return kept, dropped

    # -- the Explorer's folders ------------------------------------------

    def _explorer_source(self) -> int:
        row = self._conn.execute(
            "SELECT id FROM source WHERE kind = 'folder' AND path = ?", (EXPLORER_PATH,)
        ).fetchone()
        if row:
            return row["id"]
        source_id = self._conn.execute(
            "INSERT INTO source (kind, path, name, imported_at, hidden)"
            " VALUES ('folder', ?, ?, ?, 1)",
            (
                EXPLORER_PATH,
                EXPLORER_NAME,
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        ).lastrowid
        assert source_id is not None
        return source_id

    def explorer_node(self, directory: str) -> int | None:
        """The node (the list of tracks) the Explorer has for `directory`, if it has read it."""
        row = self._conn.execute(
            "SELECT n.id FROM node n JOIN source s ON s.id = n.source_id"
            " WHERE s.path = ? AND n.ext_id = ?",
            (EXPLORER_PATH, directory),
        ).fetchone()
        return row["id"] if row else None

    def sync_directory(self, directory: str, tracks: Iterable[TrackRecord]) -> int:
        """Store what the Explorer found directly inside `directory` and return its node id.

        Only this folder is touched. As with `replace_source`, a track keeps its id when it is
        read again (matched on its path), and one that has left the folder is deleted unless a
        disc uses it, in which case it is kept and marked stale.
        """
        with self._conn:
            source_id = self._explorer_source()
            node_id = self.explorer_node(directory)
            if node_id is None:
                node_id = self._conn.execute(
                    "INSERT INTO node (source_id, ext_id, parent_id, name, is_folder, position)"
                    " VALUES (?, ?, NULL, ?, 0, 0)",
                    (source_id, directory, os.path.basename(directory) or directory),
                ).lastrowid
                assert node_id is not None
            before = {
                r[0]
                for r in self._conn.execute(
                    "SELECT track_id FROM node_track WHERE node_id = ?", (node_id,)
                )
            }
            existing = {
                r["ext_id"]: r["id"]
                for r in self._conn.execute(
                    "SELECT ext_id, id FROM track WHERE source_id = ?", (source_id,)
                )
            }
            members: list[int] = []
            twins = self._rekordbox_rows_by_file()
            for rec in tracks:
                # a file that is also in rekordbox takes rekordbox's tempo and key, which were
                # worked out from the audio; the tags only fill in what rekordbox has not got
                twin = twins.get(path_key(rec.path))
                if twin is not None:
                    rec = replace(
                        rec, bpm=twin["bpm"] or rec.bpm, key=twin["key"] or rec.key
                    )
                track_id, _is_new = self._write_track(source_id, rec, existing)
                if track_id not in members:
                    members.append(track_id)
            self._drop_gone(source_id, [i for i in before if i not in members])
            self._conn.execute("DELETE FROM node_track WHERE node_id = ?", (node_id,))
            self._conn.executemany(
                "INSERT INTO node_track (node_id, track_id, position) VALUES (?, ?, ?)",
                [(node_id, tid, pos) for pos, tid in enumerate(members)],
            )
        return node_id

    def prune_explorer(self, keep: int = MAX_EXPLORER_TRACKS) -> int:
        """Forget the oldest of what the Explorer has read, keeping the newest `keep` tracks and every track
        that is on a disc. Returns how many tracks went. (What was read is only a cache, kept so a folder
        opens at once next session: opening a folder reads it again anyway, probing only what changed.)"""
        with self._conn:
            row = self._conn.execute(
                "SELECT id FROM source WHERE path = ? AND hidden = 1", (EXPLORER_PATH,)
            ).fetchone()
            if row is None:
                return 0
            dropped = self._conn.execute(
                "DELETE FROM track WHERE source_id = ?"
                " AND id NOT IN (SELECT track_id FROM disc_track)"
                " AND id NOT IN (SELECT id FROM track WHERE source_id = ?"
                " ORDER BY id DESC LIMIT ?)",
                (row["id"], row["id"], max(0, keep)),
            ).rowcount
            self._conn.execute(
                "DELETE FROM node WHERE source_id = ?"
                " AND id NOT IN (SELECT node_id FROM node_track)",
                (row["id"],),
            )
        return dropped

    def _replace_nodes(
        self, source_id: int, nodes: list[NodeRecord], track_ids: dict[str, int]
    ) -> int:
        self._conn.execute("DELETE FROM node WHERE source_id = ?", (source_id,))
        node_ids: dict[str, int] = {}
        sibling_count: dict[str | None, int] = {}
        for node in nodes:
            parent = node.parent_ext_id if node.parent_ext_id in node_ids else None
            position = sibling_count.get(parent, 0)
            sibling_count[parent] = position + 1
            node_id = self._conn.execute(
                "INSERT INTO node (source_id, ext_id, parent_id, name, is_folder, position)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    source_id,
                    node.ext_id,
                    node_ids.get(parent) if parent else None,
                    node.name,
                    int(node.is_folder),
                    position,
                ),
            ).lastrowid
            assert node_id is not None
            node_ids[node.ext_id] = node_id
            members = [track_ids[e] for e in node.track_ext_ids if e in track_ids]
            self._conn.executemany(
                "INSERT INTO node_track (node_id, track_id, position) VALUES (?, ?, ?)",
                [(node_id, tid, pos) for pos, tid in enumerate(members)],
            )
        return len(nodes)

    # -- browsing --------------------------------------------------------

    def nodes(self, source_id: int) -> list[Node]:
        """All nodes of a source, parents before children, siblings in order."""
        rows = self._conn.execute(
            "SELECT n.*, (SELECT COUNT(*) FROM node_track WHERE node_id = n.id) AS n_tracks"
            " FROM node n WHERE n.source_id = ?",
            (source_id,),
        ).fetchall()
        children: dict[int | None, list[sqlite3.Row]] = {}
        for r in rows:
            children.setdefault(r["parent_id"], []).append(r)
        ordered: list[Node] = []

        def walk(parent: int | None) -> None:
            for r in sorted(children.get(parent, []), key=lambda r: r["position"]):
                ordered.append(
                    Node(
                        r["id"],
                        r["source_id"],
                        r["parent_id"],
                        r["name"],
                        bool(r["is_folder"]),
                        r["position"],
                        r["n_tracks"],
                    )
                )
                walk(r["id"])

        walk(None)
        return ordered

    def _track_query(
        self,
        select: str,
        source_id: int | None,
        node_id: int | None,
        search: str | None,
        include_stale: bool = False,
    ) -> tuple[str, list]:
        sql = f"SELECT {select} FROM track t"
        where: list[str] = [] if include_stale else ["t.stale = 0"]
        params: list = []
        if node_id is not None:
            sql += " JOIN node_track nt ON nt.track_id = t.id"
            where.append("nt.node_id = ?")
            params.append(node_id)
        if source_id is not None:
            where.append("t.source_id = ?")
            params.append(source_id)
        elif (
            node_id is None
        ):  # "All Tracks": the Explorer's tracks are not part of the library
            where.append("t.source_id NOT IN (SELECT id FROM source WHERE hidden = 1)")
        for term in (search or "").split():
            like = f"%{_like_escape(term)}%"
            where.append(
                "(t.title LIKE ? ESCAPE '\\' OR t.artist LIKE ? ESCAPE '\\'"
                " OR t.album LIKE ? ESCAPE '\\' OR t.genre LIKE ? ESCAPE '\\')"
            )
            params += [like] * 4
        if where:
            sql += " WHERE " + " AND ".join(where)
        return sql, params

    def count_tracks(
        self,
        *,
        source_id: int | None = None,
        node_id: int | None = None,
        search: str | None = None,
        include_stale: bool = False,
    ) -> int:
        sql, params = self._track_query(
            "COUNT(*)", source_id, node_id, search, include_stale
        )
        return self._conn.execute(sql, params).fetchone()[0]

    def tracks(
        self,
        *,
        source_id: int | None = None,
        node_id: int | None = None,
        search: str | None = None,
        sort: str | None = None,
        descending: bool = False,
        limit: int | None = None,
        offset: int = 0,
        include_stale: bool = False,
    ) -> list[Track]:
        """Tracks matching the filters.

        With `node_id` and no `sort` they come back in the node's own order
        (a rekordbox playlist's running order, a directory's file order).
        Otherwise the default is artist, then title.
        """
        if sort is not None and sort not in _SORTS:
            raise ValueError(f"unknown sort {sort!r}; choose from {sorted(_SORTS)}")
        sql, params = self._track_query(
            "t.*", source_id, node_id, search, include_stale
        )
        direction = "DESC" if descending else "ASC"
        if sort:
            sql += f" ORDER BY {_SORTS[sort]} {direction}, t.id"
        elif node_id is not None:
            sql += f" ORDER BY nt.position {direction}"
        else:
            sql += f" ORDER BY {_SORTS['artist']} {direction}, {_SORTS['title']} {direction}, t.id"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params += [limit, offset]
        return [self._to_track(r) for r in self._conn.execute(sql, params)]

    def track(self, track_id: int) -> Track | None:
        row = self._conn.execute(
            "SELECT * FROM track WHERE id = ?", (track_id,)
        ).fetchone()
        return self._to_track(row) if row else None

    @staticmethod
    def _to_track(r: sqlite3.Row) -> Track:
        fields = {f: r[f] for f in _TRACK_FIELDS}
        return Track(
            id=r["id"],
            source_id=r["source_id"],
            ext_id=r["ext_id"],
            missing=bool(r["missing"]),
            stale=bool(r["stale"]),
            cd_sectors=r["cd_sectors"],
            loudness_lufs=r["loudness_lufs"],
            true_peak_db=r["true_peak_db"],
            waveform=r["waveform"],
            **fields,
        )

    # -- discs -----------------------------------------------------------

    def set_cd_sectors(self, track_id: int, sectors: int) -> None:
        """Record a track's measured (or rendered) length on a CD."""
        with self._conn:
            self._conn.execute(
                "UPDATE track SET cd_sectors = ? WHERE id = ?", (sectors, track_id)
            )

    def set_loudness(self, track_id: int, lufs: float, true_peak_db: float) -> None:
        """Record a track's measured loudness (see `loudness.py`). It is forgotten when the file's
        size changes at a re-import, like the measured CD length."""
        with self._conn:
            self._conn.execute(
                "UPDATE track SET loudness_lufs = ?, true_peak_db = ? WHERE id = ?",
                (lufs, true_peak_db, track_id),
            )

    def set_waveform(self, track_id: int, values: Sequence[int]) -> None:
        """Record a track's CDJ waveform (`wave_model.column_values`: 100 values, 1 to 15). It is
        forgotten when the file's size changes at a re-import, like the loudness."""
        if len(values) != 100 or not all(0 <= v <= 15 for v in values):
            raise ValueError("A waveform is 100 values, 0 to 15.")
        with self._conn:
            self._conn.execute(
                "UPDATE track SET waveform = ? WHERE id = ?",
                (bytes(values), track_id),
            )

    def set_preview(self, path: str, file_size: int | None, waveform: Waveform) -> None:
        """Keep a file's whole-track Preview waveform. Not kept for a file whose size isn't known
        (the cache can't tell if it changed). Past `MAX_CACHED_PREVIEWS` the oldest are forgotten."""
        if not file_size:
            return
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO preview_cache (path_key, file_size, bands) VALUES (?, ?, ?)",
                (path_key(path), file_size, pack_preview(waveform)),
            )
            self._conn.execute(
                "DELETE FROM preview_cache WHERE rowid <= (SELECT MAX(rowid) FROM preview_cache) - ?",
                (MAX_CACHED_PREVIEWS,),
            )

    # -- memory cues edited in Dubcrate ----------------------------------------------

    def memory_cues(self, path: str) -> list[tuple[int, str]] | None:
        """The memory cues edited for the file at `path` as (milliseconds, label), in time order; None if it has
        not been edited (rekordbox's are then the ones in use). An edited track may have none."""
        key = path_key(path)
        if self._conn.execute("SELECT 1 FROM memory_cue_set WHERE path_key = ?", (key,)).fetchone() is None:  # fmt: skip
            return None
        rows = self._conn.execute(
            "SELECT time_ms, label FROM memory_cue WHERE path_key = ? ORDER BY time_ms, id",
            (key,),
        ).fetchall()
        return [(row["time_ms"], row["label"]) for row in rows]

    def set_memory_cues(self, path: str, cues: Iterable[tuple[int, str]]) -> None:
        """Make these (milliseconds, label) the file's memory cues, replacing rekordbox's and any earlier edit.
        Times are held to 0 or more and the same time is kept once."""
        key = path_key(path)
        unique: dict[int, str] = {}
        for ms, label in cues:
            unique.setdefault(max(0, int(ms)), label)
        with self._conn:
            self._conn.execute("DELETE FROM memory_cue WHERE path_key = ?", (key,))
            self._conn.execute(
                "INSERT OR REPLACE INTO memory_cue_set (path_key, edited_at) VALUES (?, ?)",
                (key, _now()),
            )
            self._conn.executemany(
                "INSERT INTO memory_cue (path_key, time_ms, label) VALUES (?, ?, ?)",
                [(key, ms, label) for ms, label in sorted(unique.items())],
            )

    def clear_memory_cues(self, path: str) -> None:
        """Forget the edits to the file's memory cues: rekordbox's are used again."""
        self.clear_memory_cue_key(path_key(path))

    def clear_memory_cue_key(self, key: str) -> None:
        """`clear_memory_cues` for a file known by its `path_key`."""
        with self._conn:
            self._conn.execute("DELETE FROM memory_cue WHERE path_key = ?", (key,))
            self._conn.execute("DELETE FROM memory_cue_set WHERE path_key = ?", (key,))

    def memory_cue_sets(self) -> dict[str, list[tuple[int, str]]]:
        """Every file with edited memory cues, by its `path_key`: (milliseconds, label), in time order."""
        found: dict[str, list[tuple[int, str]]] = {
            row["path_key"]: []
            for row in self._conn.execute("SELECT path_key FROM memory_cue_set")
        }
        for row in self._conn.execute(
            "SELECT path_key, time_ms, label FROM memory_cue ORDER BY time_ms, id"
        ):
            found.setdefault(row["path_key"], []).append((row["time_ms"], row["label"]))
        return found

    # -- what a sync left on a card (the baseline for Import Cues) ---------------------

    @staticmethod
    def _disc_key(key: tuple[int, ...]) -> str:
        return ",".join(str(n) for n in key)

    def card_baseline(self, key: tuple[int, ...]) -> list[tuple[int, int, int]] | None:
        """The cues a sync left on the card for the disc with this ID key (`DiscId.key`): (track, position, end),
        in the order saved. None: no sync has been recorded for it (an empty list is a sync that left no cues)."""
        text = self._disc_key(key)
        if self._conn.execute("SELECT 1 FROM card_baseline_disc WHERE disc_key = ?", (text,)).fetchone() is None:  # fmt: skip
            return None
        rows = self._conn.execute(
            "SELECT track, position, end_pos FROM card_baseline WHERE disc_key = ? ORDER BY rowid",
            (text,),
        ).fetchall()
        return [(r["track"], r["position"], r["end_pos"]) for r in rows]

    def set_card_baseline(
        self, key: tuple[int, ...], cues: Iterable[tuple[int, int, int]]
    ) -> None:
        """Record what a sync left on the card for this disc, replacing the last one: (track, position, end)."""
        text = self._disc_key(key)
        with self._conn:
            self._conn.execute("DELETE FROM card_baseline WHERE disc_key = ?", (text,))
            self._conn.execute(
                "INSERT OR REPLACE INTO card_baseline_disc (disc_key, saved_at) VALUES (?, ?)",
                (text, _now()),
            )
            self._conn.executemany(
                "INSERT INTO card_baseline (disc_key, track, position, end_pos) VALUES (?, ?, ?, ?)",
                [(text, int(t), int(p), int(e)) for t, p, e in cues],
            )

    # -- memory loops saved in Dubcrate ------------------------------------------------

    def memory_loops(self, path: str) -> list[tuple[int, int, str]]:
        """The memory loops saved here for the file at `path` as (start ms, end ms, label), in time order."""
        rows = self._conn.execute(
            "SELECT start_ms, end_ms, label FROM memory_loop WHERE path_key = ? ORDER BY start_ms, id",
            (path_key(path),),
        ).fetchall()
        return [(r["start_ms"], r["end_ms"], r["label"]) for r in rows]

    def set_memory_loops(
        self, path: str, loops: Iterable[tuple[int, int, str]]
    ) -> None:
        """Make these (start ms, end ms, label) the file's saved memory loops. A loop has to end after it
        starts (others are dropped) and the same start and end is kept once."""
        key = path_key(path)
        unique: dict[tuple[int, int], str] = {}
        for start, end, label in loops:
            start = max(0, int(start))
            if int(end) > start:
                unique.setdefault((start, int(end)), label)
        with self._conn:
            self._conn.execute("DELETE FROM memory_loop WHERE path_key = ?", (key,))
            self._conn.executemany(
                "INSERT INTO memory_loop (path_key, start_ms, end_ms, label) VALUES (?, ?, ?, ?)",
                [(key, s, e, label) for (s, e), label in sorted(unique.items())],
            )

    def clear_memory_loops(self, path: str) -> None:
        """Forget the file's saved memory loops, and the deletions waiting for rekordbox (Reset)."""
        self.set_memory_loops(path, [])
        self.set_memory_loops_removed(path, [])

    def memory_loops_removed(self, path: str) -> list[tuple[int, int]]:
        """The memory loops deleted in Dubcrate for the file, as (start ms, end ms), that rekordbox may still have."""
        rows = self._conn.execute(
            "SELECT start_ms, end_ms FROM memory_loop_removed WHERE path_key = ? ORDER BY start_ms, id",
            (path_key(path),),
        ).fetchall()
        return [(r["start_ms"], r["end_ms"]) for r in rows]

    def set_memory_loops_removed(
        self, path: str, loops: Iterable[tuple[int, int]]
    ) -> None:
        """Make these (start ms, end ms) the file's deleted memory loops: each once, in time order."""
        key = path_key(path)
        unique = sorted({(max(0, int(s)), int(e)) for s, e in loops if int(e) > int(s)})
        with self._conn:
            self._conn.execute(
                "DELETE FROM memory_loop_removed WHERE path_key = ?", (key,)
            )
            self._conn.executemany(
                "INSERT INTO memory_loop_removed (path_key, start_ms, end_ms) VALUES (?, ?, ?)",
                [(key, s, e) for s, e in unique],
            )

    def memory_loop_removal_sets(self) -> dict[str, list[tuple[int, int]]]:
        """Every file with deleted memory loops waiting for rekordbox, by its `path_key`: (start ms, end ms)."""
        found: dict[str, list[tuple[int, int]]] = {}
        for row in self._conn.execute(
            "SELECT path_key, start_ms, end_ms FROM memory_loop_removed ORDER BY start_ms, id"
        ):
            found.setdefault(row["path_key"], []).append(
                (row["start_ms"], row["end_ms"])
            )
        return found

    def clear_memory_loop_removal_key(self, key: str) -> None:
        """Forget the deleted memory loops of a file known by its `path_key` (rekordbox has them gone now)."""
        with self._conn:
            self._conn.execute(
                "DELETE FROM memory_loop_removed WHERE path_key = ?", (key,)
            )

    def clear_memory_loop_key(self, key: str) -> None:
        """`clear_memory_loops` for a file known by its `path_key`."""
        with self._conn:
            self._conn.execute("DELETE FROM memory_loop WHERE path_key = ?", (key,))

    def memory_loop_sets(self) -> dict[str, list[tuple[int, int, str]]]:
        """Every file with memory loops saved in Dubcrate, by its `path_key`: (start ms, end ms, label), in time order."""
        found: dict[str, list[tuple[int, int, str]]] = {}
        for row in self._conn.execute(
            "SELECT path_key, start_ms, end_ms, label FROM memory_loop ORDER BY start_ms, id"
        ):
            found.setdefault(row["path_key"], []).append(
                (row["start_ms"], row["end_ms"], row["label"])
            )
        return found

    def previews_for(self, tracks: Iterable[Track]) -> dict[int, Waveform]:
        """The kept Preview waveform of each of these tracks that has one, by track id. A track whose
        file has changed size since it was kept has none (the old one is stale); a bad row is skipped."""
        wanted: dict[str, list[Track]] = {}
        for track in tracks:
            if track.file_size:
                wanted.setdefault(path_key(track.path), []).append(track)
        found: dict[int, Waveform] = {}
        keys = list(wanted)
        for start in range(0, len(keys), 500):
            chunk = keys[start : start + 500]
            marks = ", ".join("?" for _ in chunk)
            for row in self._conn.execute(
                f"SELECT path_key, file_size, bands FROM preview_cache WHERE path_key IN ({marks})",
                chunk,
            ):
                try:
                    wave = unpack_preview(row["bands"])
                except ValueError:
                    continue
                for track in wanted[row["path_key"]]:
                    if track.file_size == row["file_size"]:
                        found[track.id] = wave
        return found

    def tracks_by_ids(self, track_ids: Iterable[int]) -> list[Track]:
        """Tracks for `track_ids`, in that order (repeats allowed). Raises on unknown ids."""
        wanted = list(track_ids)
        unique = list(dict.fromkeys(wanted))
        found: dict[int, Track] = {}
        for start in range(0, len(unique), 500):
            chunk = unique[start : start + 500]
            marks = ", ".join("?" for _ in chunk)
            for r in self._conn.execute(
                f"SELECT * FROM track WHERE id IN ({marks})", chunk
            ):
                found[r["id"]] = self._to_track(r)
        unknown = [i for i in unique if i not in found]
        if unknown:
            raise LibraryError(f"unknown track id(s): {unknown[:5]}")
        return [found[i] for i in wanted]

    def create_disc(
        self,
        name: str,
        *,
        capacity_sectors: int = DISC_80_SECTORS,
        pregap_sectors: int = 0,
        folder_id: int | None = None,
        kind: DiscKind = "audio",
        mp3_bitrate: int | None = None,
    ) -> int:
        """Make a disc. An "mp3" disc is a data disc: no gaps, and its sectors are 2048-byte
        data sectors (`capacity_sectors` is still the disc's size, 360,000 for 80 minutes)."""
        if kind not in ("audio", "mp3"):
            raise DiscError(f"unknown disc kind {kind!r}")
        if folder_id is not None:
            self._folder_row(folder_id)
        if kind == "mp3":
            pregap_sectors = 0
            mp3_bitrate = mp3_bitrate or mp3.DEFAULT_BITRATE
        else:
            mp3_bitrate = None
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO disc (name, capacity_sectors, pregap_sectors, folder_id,"
                " created_at, updated_at, kind, mp3_bitrate) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    name,
                    capacity_sectors,
                    pregap_sectors,
                    folder_id,
                    now,
                    now,
                    kind,
                    mp3_bitrate,
                ),
            )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def _disc_row(self, disc_id: int) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT * FROM disc WHERE id = ?", (disc_id,)
        ).fetchone()
        if row is None:
            raise DiscError(f"no disc with id {disc_id}")
        return row

    def _summarize(self, row: sqlite3.Row) -> DiscSummary:
        entries = self.disc_entries(row["id"])
        used = disc_used_sectors((e.sectors for e in entries), row["pregap_sectors"])
        if row["kind"] == "mp3":
            used += mp3.FOLDER_OVERHEAD_SECTORS * len(self.disc_dirs(row["id"]))
        return DiscSummary(
            row["id"], row["name"], row["capacity_sectors"], row["pregap_sectors"],
            len(entries), used, row["created_at"], row["folder_id"], row["last_burned_at"],
            row["kind"], row["mp3_bitrate"],
        )  # fmt: skip

    def discs(self) -> list[DiscSummary]:
        rows = self._conn.execute("SELECT * FROM disc ORDER BY id").fetchall()
        return [self._summarize(r) for r in rows]

    def disc(self, disc_id: int) -> DiscSummary:
        return self._summarize(self._disc_row(disc_id))

    def update_disc(
        self,
        disc_id: int,
        *,
        name: str | None = None,
        capacity_sectors: int | None = None,
        pregap_sectors: int | None = None,
        mp3_bitrate: int | None = None,
    ) -> None:
        row = self._disc_row(disc_id)
        if row["kind"] == "mp3":
            pregap_sectors = 0
        else:
            mp3_bitrate = None  # only an MP3 disc has one
        with self._conn:
            self._conn.execute(
                "UPDATE disc SET name = ?, capacity_sectors = ?, pregap_sectors = ?,"
                " mp3_bitrate = ?, updated_at = ? WHERE id = ?",
                (
                    row["name"] if name is None else name,
                    row["capacity_sectors"]
                    if capacity_sectors is None
                    else capacity_sectors,
                    row["pregap_sectors"] if pregap_sectors is None else pregap_sectors,
                    row["mp3_bitrate"] if mp3_bitrate is None else mp3_bitrate,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    disc_id,
                ),
            )

    def set_disc_kind(
        self, disc_id: int, kind: DiscKind, mp3_bitrate: int | None = None
    ) -> None:
        """Turn a disc into an audio CD or an MP3 CD. Its tracks stay as they are and are
        measured the new way (so it can go from over-length to fitting, or back). Going to
        audio makes it gapless; going to MP3 uses `mp3_bitrate` (default 320)."""
        row = self._disc_row(disc_id)
        if kind not in ("audio", "mp3"):
            raise DiscError(f"unknown disc kind {kind!r}")
        if kind == row["kind"]:
            return
        with self._conn:
            if kind == "audio" and self.disc_dirs(disc_id):
                self._write_disc_slots(disc_id, self._flattened(disc_id))
                self._conn.execute("DELETE FROM disc_dir WHERE disc_id = ?", (disc_id,))
            self._conn.execute(
                "UPDATE disc SET kind = ?, mp3_bitrate = ?, pregap_sectors = 0,"
                " updated_at = ? WHERE id = ?",
                (
                    kind,
                    (mp3_bitrate or mp3.DEFAULT_BITRATE) if kind == "mp3" else None,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    disc_id,
                ),
            )

    # -- folders on an MP3 disc -------------------------------------------

    def disc_dirs(self, disc_id: int) -> list[DiscDir]:
        """The folders on an MP3 disc (none on any other), parents before their children."""
        rows = self._conn.execute(
            "SELECT * FROM disc_dir WHERE disc_id = ? ORDER BY id", (disc_id,)
        )
        return [DiscDir(r["id"], r["disc_id"], r["parent_id"], r["name"]) for r in rows]

    def dir_file_counts(self, disc_id: int) -> dict[int | None, int]:
        """How many tracks are in each folder of an MP3 disc (None: the root)."""
        rows = self._conn.execute(
            "SELECT dir_id, COUNT(*) AS n FROM disc_track WHERE disc_id = ? GROUP BY dir_id",
            (disc_id,),
        )
        return {r["dir_id"]: r["n"] for r in rows}

    def _dir_row(self, disc_id: int, dir_id: int) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT * FROM disc_dir WHERE id = ? AND disc_id = ?", (dir_id, disc_id)
        ).fetchone()
        if row is None:
            raise DiscError(f"disc {disc_id} has no folder with id {dir_id}")
        return row

    @staticmethod
    def _dir_depth(parents: dict[int, int | None], dir_id: int | None) -> int:
        """How many levels deep a folder is: 0 for the root, 1 directly in it."""
        depth = 0
        while dir_id is not None:
            depth, dir_id = depth + 1, parents[dir_id]
        return depth

    def _check_dir_name(
        self,
        disc_id: int,
        parent_id: int | None,
        name: str,
        exclude: int | None = None,
    ) -> str:
        """The name to keep, or an error: not empty, and not another folder's in the same place
        (compared as it will be written on the disc, where case doesn't tell names apart)."""
        name = name.strip()
        if not name:
            raise DiscError("a folder needs a name")
        written = mp3.dir_name(name).casefold()
        for other in self.disc_dirs(disc_id):
            if (
                other.id != exclude
                and other.parent_id == parent_id
                and mp3.dir_name(other.name).casefold() == written
            ):
                raise DiscError(f"there is already a folder called “{other.name}” here")
        return name

    def create_disc_dir(
        self, disc_id: int, name: str, parent_id: int | None = None
    ) -> int:
        """Make a folder on an MP3 disc, inside folder `parent_id` (default: the root).

        The player's limits are kept: 99 folders on a disc (the root counts as one) and 8
        levels (the root counts as the first)."""
        row = self._disc_row(disc_id)
        if row["kind"] != "mp3":
            raise DiscError("only an MP3 CD has folders")
        if parent_id is not None:
            self._dir_row(disc_id, parent_id)
        dirs = self.disc_dirs(disc_id)
        if len(dirs) + 1 >= mp3.MAX_FOLDERS:
            raise DiscError(
                f"a disc holds at most {mp3.MAX_FOLDERS} folders, counting the root"
            )
        parents = {d.id: d.parent_id for d in dirs}
        if self._dir_depth(parents, parent_id) + 2 > mp3.MAX_DEPTH:
            raise DiscError(
                f"folders can go at most {mp3.MAX_DEPTH} levels deep, counting the root"
            )
        name = self._check_dir_name(disc_id, parent_id, name)
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO disc_dir (disc_id, parent_id, name) VALUES (?, ?, ?)",
                (disc_id, parent_id, name),
            )
            self._touch_disc(disc_id)
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def rename_disc_dir(self, disc_id: int, dir_id: int, name: str) -> None:
        row = self._dir_row(disc_id, dir_id)
        name = self._check_dir_name(disc_id, row["parent_id"], name, exclude=dir_id)
        with self._conn:
            self._conn.execute(
                "UPDATE disc_dir SET name = ? WHERE id = ?", (name, dir_id)
            )
            self._touch_disc(disc_id)

    def move_disc_dir(self, disc_id: int, dir_id: int, parent_id: int | None) -> None:
        """Move a folder of an MP3 disc, with everything in it, to be inside folder `parent_id`
        (None: the root). Not into itself or a folder inside it, not where a folder of the same
        name already is, and not so deep that the player's limit of levels is passed."""
        row = self._dir_row(disc_id, dir_id)
        if parent_id is not None:
            self._dir_row(disc_id, parent_id)
        if row["parent_id"] == parent_id:
            return  # already there
        subtree = self.disc_dir_subtree(disc_id, dir_id)
        if parent_id in subtree:
            raise DiscError("a folder can't go inside itself")
        dirs = self.disc_dirs(disc_id)
        parents = {d.id: d.parent_id for d in dirs}
        height = max(self._dir_depth(parents, d) for d in subtree) - self._dir_depth(
            parents, dir_id
        )  # (the levels below the folder itself)
        if self._dir_depth(parents, parent_id) + 2 + height > mp3.MAX_DEPTH:
            raise DiscError(
                f"folders can go at most {mp3.MAX_DEPTH} levels deep, counting the root"
            )
        self._check_dir_name(disc_id, parent_id, row["name"], exclude=dir_id)
        with self._conn:
            self._conn.execute(
                "UPDATE disc_dir SET parent_id = ? WHERE id = ?", (parent_id, dir_id)
            )
            self._touch_disc(disc_id)
        log.info("moved folder %s of MP3 disc %s into %s", dir_id, disc_id, parent_id)

    def disc_dir_subtree(self, disc_id: int, dir_id: int) -> set[int]:
        """A folder of an MP3 disc and every folder inside it, as ids."""
        self._dir_row(disc_id, dir_id)
        dirs = self.disc_dirs(disc_id)
        found = {dir_id}
        grew = True
        while grew:
            more = {d.id for d in dirs if d.parent_id in found} - found
            found |= more
            grew = bool(more)
        return found

    def disc_dir_contents(self, disc_id: int, dir_id: int) -> tuple[int, int]:
        """(tracks, folders) inside a folder of an MP3 disc, at any depth: what deleting it takes."""
        subtree = self.disc_dir_subtree(disc_id, dir_id)
        tracks = sum(1 for s in self._slots(disc_id) if s.dir_id in subtree)
        return tracks, len(subtree) - 1

    def delete_disc_dir(self, disc_id: int, dir_id: int) -> None:
        """Delete a folder of an MP3 disc with everything in it: its tracks come off the disc
        and so do its folders, at any depth. The tracks stay in the library."""
        subtree = self.disc_dir_subtree(disc_id, dir_id)
        keep = [s for s in self._slots(disc_id) if s.dir_id not in subtree]
        with self._conn:
            self._write_disc_slots(disc_id, keep)
            self._conn.execute(
                "DELETE FROM disc_dir WHERE id = ?", (dir_id,)
            )  # (its folders go too)
            self._touch_disc(disc_id)
        log.info(
            "deleted folder %s of MP3 disc %s (%d in it)", dir_id, disc_id, len(subtree)
        )

    def move_to_dir(
        self, disc_id: int, positions: Iterable[int], dir_id: int | None
    ) -> None:
        """Put the tracks at `positions` (their place in the whole list) into folder `dir_id`
        of an MP3 disc, or into the root with None. Their order is kept."""
        self._disc_row(disc_id)
        if dir_id is not None:
            self._dir_row(disc_id, dir_id)
        slots = self._slots(disc_id)
        moving = set(positions)
        bad = sorted(p for p in moving if not 0 <= p < len(slots))
        if bad:
            raise DiscError(f"no track at position(s) {bad}")
        arriving = sum(1 for p in moving if slots[p].dir_id != dir_id)
        there = sum(1 for s in slots if s.dir_id == dir_id)
        if there + arriving > mp3.MAX_FILES_PER_FOLDER:
            raise DiscError(_limit_error("mp3"))
        slots = [
            replace(s, dir_id=dir_id) if i in moving else s for i, s in enumerate(slots)
        ]
        with self._conn:
            self._write_disc_slots(disc_id, slots)

    def _flattened(self, disc_id: int) -> list[_Slot]:
        """The disc's slots, one folder after another (the root's first, then each folder A to Z,
        its own folders after its tracks), with no folder left: the order a player lists them in."""
        slots = self._slots(disc_id)
        dirs = self.disc_dirs(disc_id)
        out: list[_Slot] = []

        def walk(here: int | None) -> None:
            out.extend(replace(s, dir_id=None) for s in slots if s.dir_id == here)
            for child in sorted(
                (d for d in dirs if d.parent_id == here),
                key=lambda d: d.name.casefold(),
            ):
                walk(child.id)

        walk(None)
        return out

    def _touch_disc(self, disc_id: int) -> None:
        self._conn.execute(
            "UPDATE disc SET updated_at = ? WHERE id = ?",
            (datetime.now(UTC).isoformat(timespec="seconds"), disc_id),
        )

    def delete_disc(self, disc_id: int) -> None:
        name = self._disc_row(disc_id)["name"]
        with self._conn:
            self._conn.execute("DELETE FROM disc WHERE id = ?", (disc_id,))
        log.info("deleted disc %s “%s”", disc_id, name)

    def disc_entries(self, disc_id: int) -> list[DiscEntry]:
        disc = self._disc_row(disc_id)
        kind, bitrate = disc["kind"], disc["mp3_bitrate"]
        rows = self._conn.execute(
            "SELECT dt.position AS disc_position, dt.title AS dt_title,"
            " dt.artist AS dt_artist, dt.dir_id AS dt_dir, t.* FROM disc_track dt"
            " JOIN track t ON t.id = dt.track_id WHERE dt.disc_id = ? ORDER BY dt.position",
            (disc_id,),
        )
        return [
            DiscEntry(
                r["disc_position"],
                self._to_track(r),
                r["dt_title"],
                r["dt_artist"],
                kind,
                bitrate,
                r["dt_dir"],
            )
            for r in rows
        ]

    def _slots(self, disc_id: int) -> list[_Slot]:
        rows = self._conn.execute(
            "SELECT track_id, title, artist, dir_id FROM disc_track WHERE disc_id = ?"
            " ORDER BY position",
            (disc_id,),
        )
        return [
            _Slot(r["track_id"], r["title"], r["artist"], r["dir_id"]) for r in rows
        ]

    def _write_disc_tracks(self, disc_id: int, track_ids: list[int]) -> None:
        """Replace a disc's track list with plain tracks (no CD text edits)."""
        self._write_disc_slots(disc_id, [_Slot(tid) for tid in track_ids])

    def _write_disc_slots(self, disc_id: int, slots: list[_Slot]) -> None:
        """Replace a disc's rows; positions are always 0..n-1."""
        self._conn.execute("DELETE FROM disc_track WHERE disc_id = ?", (disc_id,))
        self._conn.executemany(
            "INSERT INTO disc_track (disc_id, track_id, position, title, artist, dir_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                (disc_id, s.track_id, pos, s.title, s.artist, s.dir_id)
                for pos, s in enumerate(slots)
            ],
        )
        self._conn.execute(
            "UPDATE disc SET updated_at = ? WHERE id = ?",
            (datetime.now(UTC).isoformat(timespec="seconds"), disc_id),
        )

    def add_to_disc(
        self,
        disc_id: int,
        track_ids: Iterable[int],
        *,
        position: int | None = None,
        fit: bool = False,
        strict: bool = False,
        dir_id: int | None = None,
    ) -> AddResult:
        """Add tracks to a disc, at `position` (default: the end), in folder `dir_id` of an
        MP3 disc (default: the root). `position` counts within that folder.

        By default everything is added, even past capacity, so a planner can
        show the overflow. With `fit`, only tracks that still fit are added
        (see `fit_tracks`; `strict` stops at the first one that doesn't).
        Going over 99 tracks (an audio CD), or 999 in a folder (an MP3 CD), is never allowed.
        """
        row = self._disc_row(disc_id)
        if dir_id is not None:
            self._dir_row(disc_id, dir_id)
        candidates = self.tracks_by_ids(track_ids)
        current = self.disc_entries(disc_id)
        slots = self._slots(disc_id)
        here = [i for i, s in enumerate(slots) if s.dir_id == dir_id]
        limit = _max_tracks(row["kind"])
        if fit:
            accepted, rejected = fit_tracks(
                [t.sectors_on(row["kind"], row["mp3_bitrate"]) for t in candidates],
                used=disc_used_sectors(
                    (e.sectors for e in current), row["pregap_sectors"]
                ),
                count=len(here),
                capacity=row["capacity_sectors"],
                pregap=row["pregap_sectors"],
                max_tracks=limit,
                strict=strict,
            )
        else:
            accepted, rejected = list(range(len(candidates))), []
        if len(here) + len(accepted) > limit:
            raise DiscError(_limit_error(row["kind"]))
        at = self._insertion_point(slots, here, position)
        slots[at:at] = [_Slot(candidates[i].id, dir_id=dir_id) for i in accepted]
        with self._conn:
            self._write_disc_slots(disc_id, slots)
        return AddResult(
            tuple(candidates[i].id for i in accepted),
            tuple(candidates[i].id for i in rejected),
        )

    @staticmethod
    def _insertion_point(slots: list, here: list[int], position: int | None) -> int:
        """Where in the whole list the `position`th place of a folder is (`here`: the indexes
        of the folder's slots). Past its last track means just after it."""
        local = len(here) if position is None else max(0, min(position, len(here)))
        if local < len(here):
            return here[local]
        return here[-1] + 1 if here else len(slots)

    def remove_from_disc(self, disc_id: int, positions: Iterable[int]) -> None:
        self._disc_row(disc_id)
        drop = set(positions)
        entries = self.disc_entries(disc_id)
        bad = sorted(drop - {e.position for e in entries})
        if bad:
            raise DiscError(f"no track at position(s) {bad}")
        slots = self._slots(disc_id)
        with self._conn:
            self._write_disc_slots(
                disc_id, [s for i, s in enumerate(slots) if i not in drop]
            )

    def move_to_disc(
        self,
        from_disc: int,
        positions: Iterable[int],
        to_disc: int,
        dir_id: int | None = None,
    ) -> int:
        """Move the tracks at `positions` of one disc to the end of another (into folder
        `dir_id` of an MP3 disc, else its root), keeping their CD text edits and their order.
        Like `add_to_disc` it lets a disc go past its length (the caller can warn), but never
        past the track limit. Returns how many moved."""
        if from_disc == to_disc:
            raise DiscError("those tracks are already on this disc")
        self._disc_row(from_disc)
        target = self._disc_row(to_disc)
        if dir_id is not None:
            self._dir_row(to_disc, dir_id)
        source = self._slots(from_disc)
        moving = sorted(set(positions))
        bad = [p for p in moving if not 0 <= p < len(source)]
        if bad:
            raise DiscError(f"no track at position(s) {bad}")
        landing = self._slots(to_disc)
        if len([s for s in landing if s.dir_id == dir_id]) + len(moving) > _max_tracks(
            target["kind"]
        ):
            raise DiscError(_limit_error(target["kind"]))
        landing.extend(replace(source[p], dir_id=dir_id) for p in moving)
        gone = set(moving)
        with self._conn:
            self._write_disc_slots(to_disc, landing)
            self._write_disc_slots(
                from_disc, [s for i, s in enumerate(source) if i not in gone]
            )
        return len(moving)

    def disc_snapshot(self, *disc_ids: int) -> dict[int, list[_Slot]]:
        """What is on these discs now, for `restore_discs` (the undo of an add or a move)."""
        return {d: self._slots(d) for d in disc_ids}

    def restore_discs(self, snapshot: dict[int, list[_Slot]]) -> None:
        """Put the discs back as `disc_snapshot` found them, CD text edits and folders included."""
        with self._conn:
            for disc_id, slots in snapshot.items():
                self._write_disc_slots(disc_id, slots)

    def move_in_disc(self, disc_id: int, from_position: int, to_position: int) -> None:
        self._disc_row(disc_id)
        slots = self._slots(disc_id)
        if not (0 <= from_position < len(slots) and 0 <= to_position < len(slots)):
            raise DiscError(f"positions must be between 0 and {len(slots) - 1}")
        slots.insert(to_position, slots.pop(from_position))
        with self._conn:
            self._write_disc_slots(disc_id, slots)

    def reorder_disc(
        self,
        disc_id: int,
        positions: Iterable[int],
        to_position: int,
        *,
        dir_id: int | None = None,
    ) -> None:
        """Move the tracks at `positions` so they start at `to_position`.

        `to_position` is an insertion point in the list as it is before the
        move, which is what a drag-and-drop gives you. The moved tracks keep
        their relative order. Positions count within folder `dir_id` of an MP3
        disc (default: the root, which is the whole list on any other disc).
        """
        self._disc_row(disc_id)
        slots = self._slots(disc_id)
        here = [i for i, s in enumerate(slots) if s.dir_id == dir_id]
        mine = [slots[i] for i in here]
        moving = sorted(set(positions))
        if not moving or moving[0] < 0 or moving[-1] >= len(mine):
            raise DiscError(f"positions must be between 0 and {len(mine) - 1}")
        at = max(0, min(to_position, len(mine)))
        before = sum(1 for p in moving if p < at)
        moved = [mine[p] for p in moving]
        rest = [s for i, s in enumerate(mine) if i not in set(moving)]
        rest[at - before : at - before] = moved
        for index, slot in zip(here, rest, strict=True):
            slots[index] = (
                slot  # the folder keeps the places it had; only their order changes
            )
        with self._conn:
            self._write_disc_slots(disc_id, slots)

    def set_entry_text(
        self, disc_id: int, position: int, field: str, text: str
    ) -> None:
        """Change what is written on the CD for the track at `position`: its "title"
        (the track name) or "artist". This is for this disc only; the library track,
        and so rekordbox and the audio file, are untouched.

        Empty text, or the track's own text, clears the edit.
        """
        if field not in ("title", "artist"):
            raise ValueError(f"field must be title or artist, not {field!r}")
        self._disc_row(disc_id)
        entries = self.disc_entries(disc_id)
        if not 0 <= position < len(entries):
            raise DiscError(f"no track at position {position}")
        text = text.strip()
        own = getattr(entries[position].track, field)
        value = None if not text or text == own else text
        with self._conn:
            self._conn.execute(
                f"UPDATE disc_track SET {field} = ? WHERE disc_id = ? AND position = ?",
                (value, disc_id, position),
            )
            self._conn.execute(
                "UPDATE disc SET updated_at = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(timespec="seconds"), disc_id),
            )

    def split_into_discs(
        self,
        track_ids: Iterable[int],
        name: str,
        *,
        capacity_sectors: int = DISC_80_SECTORS,
        pregap_sectors: int = 0,
        kind: DiscKind = "audio",
        mp3_bitrate: int | None = None,
    ) -> SplitResult:
        """Cut an ordered track list into as many discs as it takes.

        Discs are named "<name> 1", "<name> 2", ... (just "<name>" if one is
        enough). Tracks longer than an empty disc are skipped and returned.
        """
        tracks = self.tracks_by_ids(track_ids)
        if kind == "mp3":
            pregap_sectors = 0
        groups, too_long = split_sequential(
            [t.sectors_on(kind, mp3_bitrate) for t in tracks],
            capacity=capacity_sectors,
            pregap=pregap_sectors,
            max_tracks=_max_tracks(kind),
        )
        disc_ids: list[int] = []
        for number, group in enumerate(groups, start=1):
            disc_id = self.create_disc(
                name if len(groups) == 1 else f"{name} {number}",
                capacity_sectors=capacity_sectors,
                pregap_sectors=pregap_sectors,
                kind=kind,
                mp3_bitrate=mp3_bitrate,
            )
            with self._conn:
                self._write_disc_tracks(disc_id, [tracks[i].id for i in group])
            disc_ids.append(disc_id)
        return SplitResult(tuple(disc_ids), tuple(tracks[i].id for i in too_long))

    # -- disc folders -----------------------------------------------------

    def _folder_row(self, folder_id: int) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT * FROM disc_folder WHERE id = ?", (folder_id,)
        ).fetchone()
        if row is None:
            raise DiscError(f"no disc folder with id {folder_id}")
        return row

    def disc_folders(self) -> list[DiscFolder]:
        rows = self._conn.execute("SELECT * FROM disc_folder ORDER BY id")
        return [DiscFolder(r["id"], r["name"], r["parent_id"]) for r in rows]

    def create_disc_folder(self, name: str, parent_id: int | None = None) -> int:
        name = name.strip()
        if not name:
            raise DiscError("a folder needs a name")
        if parent_id is not None:
            self._folder_row(parent_id)
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO disc_folder (name, parent_id) VALUES (?, ?)",
                (name, parent_id),
            )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def rename_disc_folder(self, folder_id: int, name: str) -> None:
        self._folder_row(folder_id)
        name = name.strip()
        if not name:
            raise DiscError("a folder needs a name")
        with self._conn:
            self._conn.execute(
                "UPDATE disc_folder SET name = ? WHERE id = ?", (name, folder_id)
            )

    def move_disc_folder(self, folder_id: int, parent_id: int | None) -> None:
        """Put a folder inside another (or at the top level with None)."""
        self._folder_row(folder_id)
        if parent_id is not None:
            self._folder_row(parent_id)
            parents = {f.id: f.parent_id for f in self.disc_folders()}
            here: int | None = parent_id
            while (
                here is not None
            ):  # walk up: the new parent must not be inside this folder
                if here == folder_id:
                    raise DiscError("a folder can't go inside itself")
                here = parents[here]
        with self._conn:
            self._conn.execute(
                "UPDATE disc_folder SET parent_id = ? WHERE id = ?",
                (parent_id, folder_id),
            )

    def disc_folder_subtree(self, folder_id: int) -> tuple[set[int], list[int]]:
        """(the folder and every folder inside it, the discs filed in any of them), as ids."""
        self._folder_row(folder_id)
        folders = self.disc_folders()
        found = {folder_id}
        grew = True
        while grew:
            more = {f.id for f in folders if f.parent_id in found} - found
            found |= more
            grew = bool(more)
        return found, [d.id for d in self.discs() if d.folder_id in found]

    def disc_folder_contents(self, folder_id: int) -> tuple[int, int]:
        """(discs, folders) inside a folder, at any depth: what deleting it takes with it."""
        folders, discs = self.disc_folder_subtree(folder_id)
        return len(discs), len(folders) - 1

    def delete_disc_folder(self, folder_id: int) -> None:
        """Delete a folder with everything in it: its discs and its folders, at any depth.
        The tracks on those discs stay in the library (a disc only lists tracks)."""
        folders, discs = self.disc_folder_subtree(folder_id)
        with self._conn:
            self._conn.executemany(
                "DELETE FROM disc WHERE id = ?", [(d,) for d in discs]
            )
            self._conn.execute(
                "DELETE FROM disc_folder WHERE id = ?", (folder_id,)
            )  # (its folders go too)
        log.info(
            "deleted disc folder %s with %d folder(s) and %d disc(s) in it",
            folder_id,
            len(folders),
            len(discs),
        )

    def move_disc(self, disc_id: int, folder_id: int | None) -> None:
        """Put a disc in a folder (or at the top level with None)."""
        self._disc_row(disc_id)
        if folder_id is not None:
            self._folder_row(folder_id)
        with self._conn:
            self._conn.execute(
                "UPDATE disc SET folder_id = ? WHERE id = ?", (folder_id, disc_id)
            )

    # -- burned, and copies ------------------------------------------------

    def mark_burned(self, disc_id: int, when: str | None = None) -> None:
        """Note that the disc was burned. It is only a note: the disc is as editable as
        ever, and nothing is copied."""
        self._disc_row(disc_id)
        when = when or datetime.now(UTC).isoformat(timespec="seconds")
        with self._conn:
            self._conn.execute(
                "UPDATE disc SET last_burned_at = ? WHERE id = ?", (when, disc_id)
            )

    def add_disc_toc(
        self,
        disc_id: int,
        planned: tuple[Sequence[int], int],
        actual: tuple[Sequence[int], int],
        source: str = "burn",
    ) -> None:
        """Record a TOC a physical copy of the disc has (read from the drive), beside the TOC planned for the disc now.

        The same TOC again just refreshes its record. Records made for a different plan are dropped: the disc has
        been edited since, so they describe discs that are no longer this one."""
        self._disc_row(disc_id)
        was, now = _toc_json(planned), _toc_json(actual)
        with self._conn:
            self._conn.execute(
                "DELETE FROM disc_toc WHERE disc_id = ? AND planned != ?",
                (disc_id, was),
            )
            self._conn.execute(
                "INSERT INTO disc_toc (disc_id, planned, actual, source, saved_at) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT (disc_id, actual) DO UPDATE SET planned = excluded.planned,"
                " source = excluded.source, saved_at = excluded.saved_at, enabled = 1",
                (disc_id, was, now, source, _now()),
            )

    def set_disc_toc_enabled(self, toc_id: int, enabled: bool) -> None:
        """Switch a recorded TOC on or off: Sync ignores one that is off (wrong, or out of date), and it stays listed."""
        with self._conn:
            self._conn.execute(
                "UPDATE disc_toc SET enabled = ? WHERE id = ?", (int(enabled), toc_id)
            )

    def remove_disc_toc(self, toc_id: int) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM disc_toc WHERE id = ?", (toc_id,))

    def disc_tocs(self, disc_id: int) -> list[KnownToc]:
        """Every TOC recorded for the disc, newest first (whether or not it still matches the disc: `planned` says)."""
        rows = self._conn.execute(
            "SELECT id, planned, actual, source, saved_at, enabled FROM disc_toc WHERE disc_id = ?"
            " ORDER BY saved_at DESC, id DESC",
            (disc_id,),
        ).fetchall()
        return [
            KnownToc(
                r["id"],
                _toc_from_json(r["planned"]),
                _toc_from_json(r["actual"]),
                r["source"],
                r["saved_at"],
                bool(r["enabled"]),
            )
            for r in rows
        ]

    def set_disc_image(self, disc_id: int, image: DiscImage) -> None:
        """Keep the image this MP3 disc was burned from (replacing an earlier burn's)."""
        self._disc_row(disc_id)
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO disc_image (disc_id, created, label, file_count, folders, last_file_start,"
                " sectors, saved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    disc_id,
                    image.created,
                    image.label,
                    image.file_count,
                    image.folders,
                    image.last_file_start,
                    image.sectors,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )

    def clear_disc_image(self, disc_id: int) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM disc_image WHERE disc_id = ?", (disc_id,))

    def disc_image(self, disc_id: int) -> DiscImage | None:
        row = self._conn.execute(
            "SELECT created, label, file_count, folders, last_file_start, sectors FROM disc_image WHERE disc_id = ?",
            (disc_id,),
        ).fetchone()
        return None if row is None else DiscImage(*tuple(row))

    def duplicate_disc(self, disc_id: int, name: str | None = None) -> int:
        """A copy of a disc: its tracks (with any CD text edits), size, gaps and folder.
        The copy has not been burned."""
        row = self._disc_row(disc_id)
        copy = self.create_disc(
            name or f"{row['name']} (copy)",
            capacity_sectors=row["capacity_sectors"],
            pregap_sectors=row["pregap_sectors"],
            folder_id=row["folder_id"],
            kind=row["kind"],
            mp3_bitrate=row["mp3_bitrate"],
        )
        with self._conn:
            made: dict[int, int] = {}
            for d in self.disc_dirs(disc_id):  # parents come before their children
                made[d.id] = self._conn.execute(
                    "INSERT INTO disc_dir (disc_id, parent_id, name) VALUES (?, ?, ?)",
                    (copy, made.get(d.parent_id), d.name),
                ).lastrowid
            self._write_disc_slots(
                copy,
                [replace(s, dir_id=made.get(s.dir_id)) for s in self._slots(disc_id)],
            )
        return copy

    def tracks_on_discs(self, exclude: int | None = None) -> set[int]:
        """The ids of every track that is on at least one disc, leaving out `exclude` (a disc's
        id): "on some other disc" when that is the open one."""
        rows = self._conn.execute(
            "SELECT DISTINCT track_id FROM disc_track WHERE disc_id IS NOT ?",
            (exclude,),
        )
        return {r["track_id"] for r in rows}

    def track_usage(self, track_ids: Iterable[int]) -> dict[int, TrackUsage]:
        """The discs each track is already on; tracks on none are left out. A track counts as
        used when the same file (`path_key`) is on a disc under another row, such as the
        rekordbox copy of an Explorer file."""
        ids = list(dict.fromkeys(track_ids))
        key_of: dict[int, str] = {}
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            marks = ",".join("?" * len(chunk))
            for r in self._conn.execute(
                f"SELECT id, path FROM track WHERE id IN ({marks})", chunk
            ):
                key_of[r["id"]] = path_key(r["path"])
        if not key_of:
            return {}
        wanted = set(key_of.values())
        discs: dict[str, dict[int, DiscRef]] = {}
        for r in self._conn.execute(
            "SELECT t.path, d.id, d.name, d.last_burned_at FROM disc_track dt"
            " JOIN track t ON t.id = dt.track_id JOIN disc d ON d.id = dt.disc_id"
            " ORDER BY d.id"
        ):
            key = path_key(r["path"])
            if key in wanted:
                discs.setdefault(key, {})[r["id"]] = DiscRef(
                    r["id"], r["name"], r["last_burned_at"]
                )
        return {
            tid: TrackUsage(tuple(discs[key_of[tid]].values()))
            for tid in ids
            if tid in key_of and key_of[tid] in discs
        }

    # -- the same file under two rows ------------------------------------

    def _rekordbox_rows_by_file(self) -> dict[str, sqlite3.Row]:
        """The rekordbox tracks (not stale), by `path_key`; the first if two share a file.

        Held until the connection has changed any row (`total_changes`: every insert, update and delete through
        it counts, so a re-import, a stale flag or a new track can't leave this out of date): a window asks
        for a twin once per visible row, and building this for the whole library each time was 0.2 s a refresh."""
        changes = self._conn.total_changes
        if self._rekordbox_files is not None and self._rekordbox_files[0] == changes:
            return self._rekordbox_files[1]
        found: dict[str, sqlite3.Row] = {}
        for r in self._conn.execute(
            "SELECT t.* FROM track t JOIN source s ON s.id = t.source_id"
            " WHERE s.kind IN ('rekordbox', 'rekordbox_usb', 'rekordbox_xml') AND t.stale = 0 ORDER BY t.id"
        ):
            found.setdefault(path_key(r["path"]), r)
        self._rekordbox_files = (changes, found)
        return found

    def rekordbox_twin(self, track: Track) -> Track | None:
        """The rekordbox track that is the same file as `track` (matched on its path), or None.
        A rekordbox track has no twin of its own."""
        row = self._rekordbox_rows_by_file().get(path_key(track.path))
        return (
            self._to_track(row) if row is not None and row["id"] != track.id else None
        )

    def same_file_ids(self, track_ids: Iterable[int]) -> set[int]:
        """`track_ids` and the ids of every other track that is the same file, so a mark on a
        track can show on its twin."""
        ids = set(track_ids)
        if not ids:
            return ids
        wanted: set[str] = set()
        listed = list(ids)
        for start in range(0, len(listed), 500):
            chunk = listed[start : start + 500]
            marks = ",".join("?" * len(chunk))
            wanted.update(
                path_key(r["path"])
                for r in self._conn.execute(
                    f"SELECT path FROM track WHERE id IN ({marks})", chunk
                )
            )
        return ids | {
            r["id"]
            for r in self._conn.execute("SELECT id, path FROM track")
            if path_key(r["path"]) in wanted
        }
