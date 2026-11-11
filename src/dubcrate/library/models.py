"""Plain data types shared by library sources and the store."""

from dataclasses import dataclass, field
from typing import Literal

SourceKind = Literal["folder", "rekordbox"]


@dataclass(frozen=True, kw_only=True)
class TrackRecord:
    """A track as a source describes it, before the store has assigned an id.

    `ext_id` is the source's own stable identifier: the absolute path for a
    folder, the content ID for rekordbox. Re-importing matches on it, so the
    store's track ids survive a re-import.
    """

    ext_id: str
    path: str
    title: str = ""
    artist: str = ""
    album: str = ""
    genre: str = ""
    year: int | None = None
    comment: str = ""
    bpm: float | None = None
    key: str = ""
    duration: float = 0.0  # seconds
    bitrate: int | None = None  # kbit/s
    sample_rate: int | None = None
    bit_depth: int | None = None
    rating: int | None = None
    file_size: int | None = None
    analysis_path: str | None = None  # rekordbox ANLZ file, for waveforms/cues later


@dataclass(frozen=True, kw_only=True)
class Track(TrackRecord):
    id: int
    source_id: int
    missing: bool = False  # file wasn't on disk at the last import


@dataclass(frozen=True)
class NodeRecord:
    """A browsable node: a rekordbox playlist/folder or a directory on disk.

    Any node may hold tracks; `is_folder` only says it groups other nodes.
    `parent_ext_id` is None for top-level nodes. A list of NodeRecords is
    ordered parents-first, and siblings keep their list order.
    """

    ext_id: str
    parent_ext_id: str | None
    name: str
    is_folder: bool
    track_ext_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Node:
    id: int
    source_id: int
    parent_id: int | None
    name: str
    is_folder: bool
    position: int
    track_count: int


@dataclass(frozen=True)
class Source:
    id: int
    kind: SourceKind
    path: str
    name: str
    imported_at: str
    track_count: int


@dataclass(frozen=True)
class ImportSummary:
    source_id: int
    added: int
    updated: int
    removed: int
    nodes: int
    notes: tuple[str, ...] = field(default=())
