"""Dubcrate's own SQLite library: imported sources, their tracks and browse trees.

Sources (a folder, a rekordbox library) are read-only inputs. Re-importing a
source syncs it in place: tracks are matched on `ext_id` so their ids stay
stable, tracks that vanished from the source are removed, and the browse tree
is rebuilt.
"""

import os
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from dubcrate import mp3
from dubcrate.cd import DISC_80_SECTORS, MAX_TRACKS, disc_used_sectors
from dubcrate.library.models import (
    AddResult,
    DiscEntry,
    DiscFolder,
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


def _max_tracks(kind: str) -> int:
    """Red Book allows 99 tracks on an audio CD; a data disc has no such limit."""
    return MAX_TRACKS if kind == "audio" else 1 << 30


@dataclass(frozen=True)
class _Slot:
    """A row of a disc as the store moves it about: a track and the CD text edits that
    belong to this place on this disc (None: the track's own)."""

    track_id: int
    title: str | None = None
    artist: str | None = None


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

_MIGRATIONS = (
    _SCHEMA_V1, _SCHEMA_V2, _SCHEMA_V3, _SCHEMA_V4, _SCHEMA_V5, _migrate_to_v6,
    _SCHEMA_V7,
)  # fmt: skip
SCHEMA_VERSION = len(_MIGRATIONS)

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
    "key": "t.key COLLATE NOCASE",
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
        rows = self._conn.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM track WHERE source_id = s.id AND stale = 0) AS n"
            " FROM source s ORDER BY s.name COLLATE NOCASE, s.id"
        )
        return [
            Source(r["id"], r["kind"], r["path"], r["name"], r["imported_at"], r["n"])
            for r in rows
        ]

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
            columns = ", ".join(_TRACK_FIELDS)
            placeholders = ", ".join("?" for _ in _TRACK_FIELDS)
            assignments = ", ".join(f"{f} = ?" for f in _TRACK_FIELDS)
            for rec in tracks:
                values = [getattr(rec, f) for f in _TRACK_FIELDS]
                missing = int(not os.path.exists(rec.path))
                if rec.ext_id in existing:
                    track_id = existing[rec.ext_id]
                    # A changed file size means the measured CD length is out of date.
                    self._conn.execute(
                        "UPDATE track SET cd_sectors = CASE WHEN file_size IS ?"
                        f" THEN cd_sectors END, {assignments}, missing = ?, stale = 0"
                        " WHERE id = ?",
                        [rec.file_size, *values, missing, track_id],
                    )
                    updated += rec.ext_id not in ids
                else:
                    track_id = self._conn.execute(
                        f"INSERT INTO track (source_id, ext_id, missing, {columns})"
                        f" VALUES (?, ?, ?, {placeholders})",
                        [source_id, rec.ext_id, missing, *values],
                    ).lastrowid
                    assert track_id is not None
                    existing[rec.ext_id] = track_id
                    added += 1
                ids[rec.ext_id] = track_id

            # Tracks that left the source are deleted, unless a disc uses them:
            # those are kept (marked stale) so no disc silently loses a track.
            gone = [i for ext, i in existing.items() if ext not in ids]
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
            notes = list(notes)
            if kept:
                notes.append(
                    f"kept {len(kept)} track(s) no longer in the source: they're on discs"
                )

            node_count = self._replace_nodes(source_id, list(nodes), ids)
        return ImportSummary(
            source_id, added, updated, len(dropped), node_count, tuple(notes)
        )

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
            **fields,
        )

    # -- discs -----------------------------------------------------------

    def set_cd_sectors(self, track_id: int, sectors: int) -> None:
        """Record a track's measured (or rendered) length on a CD."""
        with self._conn:
            self._conn.execute(
                "UPDATE track SET cd_sectors = ? WHERE id = ?", (sectors, track_id)
            )

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

    def delete_disc(self, disc_id: int) -> None:
        self._disc_row(disc_id)
        with self._conn:
            self._conn.execute("DELETE FROM disc WHERE id = ?", (disc_id,))

    def disc_entries(self, disc_id: int) -> list[DiscEntry]:
        disc = self._disc_row(disc_id)
        kind, bitrate = disc["kind"], disc["mp3_bitrate"]
        rows = self._conn.execute(
            "SELECT dt.position AS disc_position, dt.title AS dt_title,"
            " dt.artist AS dt_artist, t.* FROM disc_track dt"
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
            )
            for r in rows
        ]

    def _slots(self, disc_id: int) -> list[_Slot]:
        rows = self._conn.execute(
            "SELECT track_id, title, artist FROM disc_track WHERE disc_id = ?"
            " ORDER BY position",
            (disc_id,),
        )
        return [_Slot(r["track_id"], r["title"], r["artist"]) for r in rows]

    def _write_disc_tracks(self, disc_id: int, track_ids: list[int]) -> None:
        """Replace a disc's track list with plain tracks (no CD text edits)."""
        self._write_disc_slots(disc_id, [_Slot(tid) for tid in track_ids])

    def _write_disc_slots(self, disc_id: int, slots: list[_Slot]) -> None:
        """Replace a disc's rows; positions are always 0..n-1."""
        self._conn.execute("DELETE FROM disc_track WHERE disc_id = ?", (disc_id,))
        self._conn.executemany(
            "INSERT INTO disc_track (disc_id, track_id, position, title, artist)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (disc_id, s.track_id, pos, s.title, s.artist)
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
    ) -> AddResult:
        """Add tracks to a disc, at `position` (default: the end).

        By default everything is added, even past capacity, so a planner can
        show the overflow. With `fit`, only tracks that still fit are added
        (see `fit_tracks`; `strict` stops at the first one that doesn't).
        Going over 99 tracks is never allowed.
        """
        row = self._disc_row(disc_id)
        candidates = self.tracks_by_ids(track_ids)
        current = self.disc_entries(disc_id)
        if fit:
            accepted, rejected = fit_tracks(
                [t.sectors_on(row["kind"], row["mp3_bitrate"]) for t in candidates],
                used=disc_used_sectors(
                    (e.sectors for e in current), row["pregap_sectors"]
                ),
                count=len(current),
                capacity=row["capacity_sectors"],
                pregap=row["pregap_sectors"],
                max_tracks=_max_tracks(row["kind"]),
                strict=strict,
            )
        else:
            accepted, rejected = list(range(len(candidates))), []
        if len(current) + len(accepted) > _max_tracks(row["kind"]):
            raise DiscError(f"a disc holds at most {MAX_TRACKS} tracks")
        slots = self._slots(disc_id)
        at = len(slots) if position is None else max(0, min(position, len(slots)))
        slots[at:at] = [_Slot(candidates[i].id) for i in accepted]
        with self._conn:
            self._write_disc_slots(disc_id, slots)
        return AddResult(
            tuple(candidates[i].id for i in accepted),
            tuple(candidates[i].id for i in rejected),
        )

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

    def move_in_disc(self, disc_id: int, from_position: int, to_position: int) -> None:
        self._disc_row(disc_id)
        slots = self._slots(disc_id)
        if not (0 <= from_position < len(slots) and 0 <= to_position < len(slots)):
            raise DiscError(f"positions must be between 0 and {len(slots) - 1}")
        slots.insert(to_position, slots.pop(from_position))
        with self._conn:
            self._write_disc_slots(disc_id, slots)

    def reorder_disc(
        self, disc_id: int, positions: Iterable[int], to_position: int
    ) -> None:
        """Move the tracks at `positions` so they start at `to_position`.

        `to_position` is an insertion point in the list as it is before the
        move, which is what a drag-and-drop gives you. The moved tracks keep
        their relative order.
        """
        self._disc_row(disc_id)
        slots = self._slots(disc_id)
        moving = sorted(set(positions))
        if not moving or moving[0] < 0 or moving[-1] >= len(slots):
            raise DiscError(f"positions must be between 0 and {len(slots) - 1}")
        at = max(0, min(to_position, len(slots)))
        before = sum(1 for p in moving if p < at)
        moved = [slots[p] for p in moving]
        rest = [s for i, s in enumerate(slots) if i not in set(moving)]
        rest[at - before : at - before] = moved
        with self._conn:
            self._write_disc_slots(disc_id, rest)

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

    def delete_disc_folder(self, folder_id: int) -> None:
        """Delete a folder. What was in it (discs and folders) moves up a level: this
        never deletes a disc."""
        row = self._folder_row(folder_id)
        parent = row["parent_id"]
        with self._conn:
            self._conn.execute(
                "UPDATE disc SET folder_id = ? WHERE folder_id = ?", (parent, folder_id)
            )
            self._conn.execute(
                "UPDATE disc_folder SET parent_id = ? WHERE parent_id = ?",
                (parent, folder_id),
            )
            self._conn.execute("DELETE FROM disc_folder WHERE id = ?", (folder_id,))

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
            self._write_disc_slots(copy, self._slots(disc_id))
        return copy

    def track_usage(self, track_ids: Iterable[int]) -> dict[int, TrackUsage]:
        """The discs each track is already on; tracks on none are left out."""
        ids = list(dict.fromkeys(track_ids))
        discs: dict[int, dict[int, DiscRef]] = {}
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            marks = ",".join("?" * len(chunk))
            for r in self._conn.execute(
                "SELECT DISTINCT dt.track_id, d.id, d.name, d.last_burned_at"
                " FROM disc_track dt JOIN disc d ON d.id = dt.disc_id"
                f" WHERE dt.track_id IN ({marks}) ORDER BY d.id",
                chunk,
            ):
                discs.setdefault(r["track_id"], {})[r["id"]] = DiscRef(
                    r["id"], r["name"], r["last_burned_at"]
                )
        return {
            tid: TrackUsage(tuple(discs[tid].values())) for tid in ids if tid in discs
        }
