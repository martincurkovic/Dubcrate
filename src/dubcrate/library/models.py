"""Plain data types shared by library sources and the store."""

from dataclasses import dataclass, field
from typing import Literal

from dubcrate.cd import estimate_sectors

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
    # More of what rekordbox knows (empty for a folder, which has none of it):
    label: str = ""
    remixer: str = ""
    composer: str = ""
    my_tags: str = ""  # rekordbox "My Tag" names, joined with ", "
    play_count: int | None = None  # None when never played
    date_added: str = ""  # when it was added to rekordbox, "YYYY-MM-DD"


@dataclass(frozen=True, kw_only=True)
class Track(TrackRecord):
    id: int
    source_id: int
    missing: bool = False  # file wasn't on disk at the last import
    # No longer in its source but kept because a disc still uses it.
    stale: bool = False
    cd_sectors: int | None = None  # measured length once known

    @property
    def length_sectors(self) -> int:
        """Length on a CD: measured if we have it, else a conservative estimate."""
        if self.cd_sectors is not None:
            return self.cd_sectors
        return estimate_sectors(self.duration)

    @property
    def length_exact(self) -> bool:
        return self.cd_sectors is not None


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


@dataclass(frozen=True)
class DiscSummary:
    """A disc being planned: its settings and how full it is."""

    id: int
    name: str
    capacity_sectors: int
    pregap_sectors: int
    track_count: int
    used_sectors: int
    created_at: str

    @property
    def free_sectors(self) -> int:
        return self.capacity_sectors - self.used_sectors

    @property
    def over(self) -> bool:
        return self.used_sectors > self.capacity_sectors


@dataclass(frozen=True)
class DiscEntry:
    """One place on a disc: a library track, and what is written on the CD for it.

    The artist and title burned into the CD-TEXT can be edited for this disc alone: the
    overrides live with the disc, and the library track (so rekordbox and the file) is
    never touched. None means "use the track's own".
    """

    position: int  # 0-based
    track: Track
    title_override: str | None = None
    artist_override: str | None = None

    @property
    def title(self) -> str:
        """The track name for the CD."""
        return self.track.title if self.title_override is None else self.title_override

    @property
    def artist(self) -> str:
        """The artist for the CD."""
        return (
            self.track.artist if self.artist_override is None else self.artist_override
        )

    @property
    def edited(self) -> bool:
        return self.title_override is not None or self.artist_override is not None

    @property
    def sectors(self) -> int:
        return self.track.length_sectors


@dataclass(frozen=True)
class AddResult:
    added: tuple[int, ...]  # track ids
    rejected: tuple[int, ...]  # track ids that didn't fit (fit mode only)


@dataclass(frozen=True)
class SplitResult:
    disc_ids: tuple[int, ...]
    too_long: tuple[int, ...]  # track ids longer than an empty disc


@dataclass(frozen=True)
class BurnTrack:
    """One track as it was burned. A copy, so it outlives the library track."""

    position: int  # 0-based
    track_id: int | None  # None once the library track is gone
    title: str
    artist: str
    sectors: int
    path: str


@dataclass(frozen=True)
class BurnRecord:
    """A frozen snapshot of one burn: what went on the disc, when, and how."""

    id: int
    disc_id: int | None  # None once the planned disc is deleted
    name: str
    capacity_sectors: int
    pregap_sectors: int
    used_sectors: int
    drive: str
    speed: int | None  # x, None for the drive's default
    simulated: bool
    burned_at: str
    tracks: tuple[BurnTrack, ...] = ()

    @property
    def track_count(self) -> int:
        return len(self.tracks)


@dataclass(frozen=True)
class DuplicateResult:
    disc_id: int
    skipped: tuple[BurnTrack, ...]  # tracks no longer in the library


@dataclass(frozen=True)
class DiscRef:
    id: int
    name: str


@dataclass(frozen=True)
class BurnRef:
    id: int
    name: str
    burned_at: str


@dataclass(frozen=True)
class TrackUsage:
    """Where a track is already used: planned discs and burned discs."""

    discs: tuple[DiscRef, ...] = ()
    burns: tuple[BurnRef, ...] = ()
