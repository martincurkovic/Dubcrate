"""Dubcrate's own SQLite library: imported sources, their tracks and browse trees.

Sources (a folder, a rekordbox library) are read-only inputs. Re-importing a
source syncs it in place: tracks are matched on `ext_id` so their ids stay
stable, tracks that vanished from the source are removed, and the browse tree
is rebuilt.
"""

import os
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from dubcrate.cd import DISC_80_SECTORS, MAX_TRACKS, disc_used_sectors
from dubcrate.library.models import (
    AddResult,
    BurnRecord,
    BurnTrack,
    DiscEntry,
    DiscSummary,
    DuplicateResult,
    ImportSummary,
    Node,
    NodeRecord,
    Source,
    SourceKind,
    SplitResult,
    Track,
    TrackRecord,
)
from dubcrate.library.paths import default_library_path
from dubcrate.planning import fit_tracks, split_sequential


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

_MIGRATIONS = (_SCHEMA_V1, _SCHEMA_V2, _SCHEMA_V3)
SCHEMA_VERSION = len(_MIGRATIONS)

_TRACK_FIELDS = (
    "path", "title", "artist", "album", "genre", "year", "comment", "bpm", "key",
    "duration", "bitrate", "sample_rate", "bit_depth", "rating", "file_size",
    "analysis_path",
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
    ) -> int:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO disc (name, capacity_sectors, pregap_sectors, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (name, capacity_sectors, pregap_sectors, now, now),
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
            len(entries), used, row["created_at"],
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
    ) -> None:
        row = self._disc_row(disc_id)
        with self._conn:
            self._conn.execute(
                "UPDATE disc SET name = ?, capacity_sectors = ?, pregap_sectors = ?,"
                " updated_at = ? WHERE id = ?",
                (
                    row["name"] if name is None else name,
                    row["capacity_sectors"]
                    if capacity_sectors is None
                    else capacity_sectors,
                    row["pregap_sectors"] if pregap_sectors is None else pregap_sectors,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    disc_id,
                ),
            )

    def delete_disc(self, disc_id: int) -> None:
        self._disc_row(disc_id)
        with self._conn:
            self._conn.execute("DELETE FROM disc WHERE id = ?", (disc_id,))

    def disc_entries(self, disc_id: int) -> list[DiscEntry]:
        rows = self._conn.execute(
            "SELECT dt.position AS disc_position, t.* FROM disc_track dt"
            " JOIN track t ON t.id = dt.track_id WHERE dt.disc_id = ? ORDER BY dt.position",
            (disc_id,),
        )
        return [DiscEntry(r["disc_position"], self._to_track(r)) for r in rows]

    def _write_disc_tracks(self, disc_id: int, track_ids: list[int]) -> None:
        """Replace a disc's track list; positions are always 0..n-1."""
        self._conn.execute("DELETE FROM disc_track WHERE disc_id = ?", (disc_id,))
        self._conn.executemany(
            "INSERT INTO disc_track (disc_id, track_id, position) VALUES (?, ?, ?)",
            [(disc_id, tid, pos) for pos, tid in enumerate(track_ids)],
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
                [t.length_sectors for t in candidates],
                used=disc_used_sectors(
                    (e.sectors for e in current), row["pregap_sectors"]
                ),
                count=len(current),
                capacity=row["capacity_sectors"],
                pregap=row["pregap_sectors"],
                strict=strict,
            )
        else:
            accepted, rejected = list(range(len(candidates))), []
        if len(current) + len(accepted) > MAX_TRACKS:
            raise DiscError(f"a disc holds at most {MAX_TRACKS} tracks")
        ids = [e.track.id for e in current]
        at = len(ids) if position is None else max(0, min(position, len(ids)))
        ids[at:at] = [candidates[i].id for i in accepted]
        with self._conn:
            self._write_disc_tracks(disc_id, ids)
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
        with self._conn:
            self._write_disc_tracks(
                disc_id, [e.track.id for e in entries if e.position not in drop]
            )

    def move_in_disc(self, disc_id: int, from_position: int, to_position: int) -> None:
        self._disc_row(disc_id)
        ids = [e.track.id for e in self.disc_entries(disc_id)]
        if not (0 <= from_position < len(ids) and 0 <= to_position < len(ids)):
            raise DiscError(f"positions must be between 0 and {len(ids) - 1}")
        ids.insert(to_position, ids.pop(from_position))
        with self._conn:
            self._write_disc_tracks(disc_id, ids)

    def reorder_disc(
        self, disc_id: int, positions: Iterable[int], to_position: int
    ) -> None:
        """Move the tracks at `positions` so they start at `to_position`.

        `to_position` is an insertion point in the list as it is before the
        move, which is what a drag-and-drop gives you. The moved tracks keep
        their relative order.
        """
        self._disc_row(disc_id)
        ids = [e.track.id for e in self.disc_entries(disc_id)]
        moving = sorted(set(positions))
        if not moving or moving[0] < 0 or moving[-1] >= len(ids):
            raise DiscError(f"positions must be between 0 and {len(ids) - 1}")
        at = max(0, min(to_position, len(ids)))
        before = sum(1 for p in moving if p < at)
        moved = [ids[p] for p in moving]
        rest = [t for i, t in enumerate(ids) if i not in set(moving)]
        rest[at - before : at - before] = moved
        with self._conn:
            self._write_disc_tracks(disc_id, rest)

    def split_into_discs(
        self,
        track_ids: Iterable[int],
        name: str,
        *,
        capacity_sectors: int = DISC_80_SECTORS,
        pregap_sectors: int = 0,
    ) -> SplitResult:
        """Cut an ordered track list into as many discs as it takes.

        Discs are named "<name> 1", "<name> 2", ... (just "<name>" if one is
        enough). Tracks longer than an empty disc are skipped and returned.
        """
        tracks = self.tracks_by_ids(track_ids)
        groups, too_long = split_sequential(
            [t.length_sectors for t in tracks],
            capacity=capacity_sectors,
            pregap=pregap_sectors,
        )
        disc_ids: list[int] = []
        for number, group in enumerate(groups, start=1):
            disc_id = self.create_disc(
                name if len(groups) == 1 else f"{name} {number}",
                capacity_sectors=capacity_sectors,
                pregap_sectors=pregap_sectors,
            )
            with self._conn:
                self._write_disc_tracks(disc_id, [tracks[i].id for i in group])
            disc_ids.append(disc_id)
        return SplitResult(tuple(disc_ids), tuple(tracks[i].id for i in too_long))

    # -- burn records ----------------------------------------------------

    def record_burn(
        self,
        disc_id: int,
        *,
        drive: str = "",
        speed: int | None = None,
        simulated: bool = False,
    ) -> int:
        """Save a snapshot of the disc as it is now; returns the burn id.

        The planned disc stays editable: later changes to it don't touch the
        snapshot.
        """
        row = self._disc_row(disc_id)
        entries = self.disc_entries(disc_id)
        used = disc_used_sectors((e.sectors for e in entries), row["pregap_sectors"])
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO burn (disc_id, name, capacity_sectors, pregap_sectors,"
                " used_sectors, drive, speed, simulated, burned_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    disc_id, row["name"], row["capacity_sectors"],
                    row["pregap_sectors"], used, drive, speed, int(simulated), now,
                ),
            )  # fmt: skip
            burn_id = cursor.lastrowid
            assert burn_id is not None
            self._conn.executemany(
                "INSERT INTO burn_track (burn_id, position, track_id, title, artist,"
                " sectors, path) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        burn_id, e.position, e.track.id, e.track.title,
                        e.track.artist, e.sectors, e.track.path,
                    )
                    for e in entries
                ],
            )  # fmt: skip
        return burn_id

    def _burn_tracks(self, burn_id: int) -> tuple[BurnTrack, ...]:
        rows = self._conn.execute(
            "SELECT * FROM burn_track WHERE burn_id = ? ORDER BY position", (burn_id,)
        )
        return tuple(
            BurnTrack(
                r["position"], r["track_id"], r["title"], r["artist"],
                r["sectors"], r["path"],
            )
            for r in rows
        )  # fmt: skip

    def _to_burn(self, r: sqlite3.Row) -> BurnRecord:
        return BurnRecord(
            r["id"], r["disc_id"], r["name"], r["capacity_sectors"],
            r["pregap_sectors"], r["used_sectors"], r["drive"], r["speed"],
            bool(r["simulated"]), r["burned_at"], self._burn_tracks(r["id"]),
        )  # fmt: skip

    def burns(self, disc_id: int | None = None) -> list[BurnRecord]:
        """Burn records, newest first; only one disc's if `disc_id` is given."""
        if disc_id is None:
            rows = self._conn.execute("SELECT * FROM burn ORDER BY id DESC")
        else:
            rows = self._conn.execute(
                "SELECT * FROM burn WHERE disc_id = ? ORDER BY id DESC", (disc_id,)
            )
        return [self._to_burn(r) for r in rows.fetchall()]

    def burn(self, burn_id: int) -> BurnRecord:
        row = self._conn.execute(
            "SELECT * FROM burn WHERE id = ?", (burn_id,)
        ).fetchone()
        if row is None:
            raise DiscError(f"no burn with id {burn_id}")
        return self._to_burn(row)

    def delete_burn(self, burn_id: int) -> None:
        self.burn(burn_id)
        with self._conn:
            self._conn.execute("DELETE FROM burn WHERE id = ?", (burn_id,))

    def duplicate_burn(self, burn_id: int, name: str | None = None) -> DuplicateResult:
        """Make a new editable disc from a burn record, to re-burn or tweak.

        Tracks that have left the library since are skipped and reported.
        """
        record = self.burn(burn_id)
        # track_id is NULLed when its track is deleted, so non-null means it's still here.
        kept = [t for t in record.tracks if t.track_id is not None]
        skipped = tuple(t for t in record.tracks if t.track_id is None)
        disc_id = self.create_disc(
            name or f"{record.name} (copy)",
            capacity_sectors=record.capacity_sectors,
            pregap_sectors=record.pregap_sectors,
        )
        with self._conn:
            self._write_disc_tracks(
                disc_id, [t.track_id for t in kept if t.track_id is not None]
            )
        return DuplicateResult(disc_id, skipped)
