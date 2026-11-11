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

from dubcrate.library.models import (
    ImportSummary,
    Node,
    NodeRecord,
    Source,
    SourceKind,
    Track,
    TrackRecord,
)
from dubcrate.library.paths import default_library_path

SCHEMA_VERSION = 1

_SCHEMA = """
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
        if version < 1:
            with self._conn:
                for statement in _SCHEMA.split(";"):
                    if statement.strip():
                        self._conn.execute(statement)
                self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # -- sources ---------------------------------------------------------

    def sources(self) -> list[Source]:
        rows = self._conn.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM track WHERE source_id = s.id) AS n"
            " FROM source s ORDER BY s.name COLLATE NOCASE, s.id"
        )
        return [
            Source(r["id"], r["kind"], r["path"], r["name"], r["imported_at"], r["n"])
            for r in rows
        ]

    def remove_source(self, source_id: int) -> None:
        with self._conn:
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
                    self._conn.execute(
                        f"UPDATE track SET {assignments}, missing = ? WHERE id = ?",
                        [*values, missing, track_id],
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

            stale = [i for ext, i in existing.items() if ext not in ids]
            self._conn.executemany(
                "DELETE FROM track WHERE id = ?", [(i,) for i in stale]
            )

            node_count = self._replace_nodes(source_id, list(nodes), ids)
        return ImportSummary(
            source_id, added, updated, len(stale), node_count, tuple(notes)
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
    ) -> tuple[str, list]:
        sql = f"SELECT {select} FROM track t"
        where: list[str] = []
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
    ) -> int:
        sql, params = self._track_query("COUNT(*)", source_id, node_id, search)
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
    ) -> list[Track]:
        """Tracks matching the filters.

        With `node_id` and no `sort` they come back in the node's own order
        (a rekordbox playlist's running order, a directory's file order).
        Otherwise the default is artist, then title.
        """
        if sort is not None and sort not in _SORTS:
            raise ValueError(f"unknown sort {sort!r}; choose from {sorted(_SORTS)}")
        sql, params = self._track_query("t.*", source_id, node_id, search)
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
            **fields,
        )
