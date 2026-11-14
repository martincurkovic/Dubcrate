"""The CDJ-1000MK3's memory card (`PIONEER/CDJ/`): read and write its files (Qt-free).

The card holds, for each audio CD the player has seen, the memory cues, loops, hot cues and a
coarse waveform of that disc. The format is Pioneer's, undocumented; everything here was measured
from a real card (see `dev_docs/cdj1000mk3-card.md`, which says what is measured and what is a
guess) and not from any specification. Big-endian throughout.

Two kinds of file, both `*.PDJ`, both a run of chunks (`tag(4)`, `u32`, payload) after `PIONEER `:

* `CDJINDX0.PDJ`, the index: a header and one 24-byte entry per disc, the name of its file
  (`CDJ00001`) and the disc's 16-byte ID.
* `CDJ0000n.PDJ`, one per disc: `INFO`, `DID ` (the ID again), `CN11` (memory cues and loops),
  `HC11` (hot cues A, B and C), `W001` / `W002` (waveform records), `CSUM`.

**The disc ID is the disc's table of contents, not its audio**: the track count, the lead-out and
the starts of track 2 and of the last track, all in absolute CD frames (minutes, seconds and frames
as `(m * 60 + s) * 75 + f`, so track 1 starts at 150, the two seconds of lead-in the disc's TOC
counts). `DiscId.from_toc` computes it, so a disc can be prepared for before it is burned.

A cue's position is in the same absolute frames: the track's start (from the TOC) plus the time in
the track. A cue 5 s into track 1 is `150 + 5 * 75`.

Nothing here touches a card or a drive: callers give bytes or paths. The one thing not known is
whether the player accepts a file *written* here; parsing and writing are checked against files the
player wrote (`tests/test_cdj_card.py`), byte for byte.
"""

import os
import re
import shutil
import struct
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

MAGIC = b"PIONEER "
CARD_DIR = ("PIONEER", "CDJ")
INDEX_NAME = "CDJINDX0.PDJ"
FRAMES_PER_SECOND = 75
LEAD_IN_FRAMES = 150  # track 1 of every disc starts here (00:02.00)

MAX_CUES = 100  # memory cues and loops together, per disc (the player's manual)
HOT_CUE_SLOTS = 3  # A, B, C
WAVE_COLUMNS = 50  # a W001 record's bytes; a W002 record packs twice as many nibbles
WAVE_MAX_LEVEL = 14  # the highest amplitude seen (a level is 0 to 15 in its nibble)

KIND_CUE = 0x0010  # a memory cue, and also what a hot cue slot says
KIND_LOOP = 0x0020
LOOP_FLAG = (
    0x00010000  # the last field of a loop entry (0 for a cue); meaning not known
)

INDEX_FLAGS = 0x0000  # the ID's flags word, in the index
FILE_FLAGS = 0x0020  # and in a disc's own file; meaning not known

_CUE = struct.Struct(">IHIIHI")  # index, kind, position, end, track, flag
_HOT = struct.Struct(">HHIIHIH")  # slot, kind, position, end, track, extra, tail
_ID = struct.Struct(
    ">HHIII"
)  # tracks, flags, lead-out, track 2 start, last track start
_RECORD = 56  # a waveform record: u16 track, 4 reserved bytes, 50 data bytes
_HOT_BYTES = HOT_CUE_SLOTS * _HOT.size
_INDEX_ENTRY = 8 + _ID.size


class CardFormatError(RuntimeError):
    """A card file isn't one this module understands (an expected error: say so plainly)."""


def checksum(data: bytes) -> int:
    """The `CSUM` value: the sum of 16-bit big-endian words, low 16 bits."""
    words = struct.unpack(f">{len(data) // 2}H", data[: len(data) // 2 * 2])
    return sum(words) & 0xFFFF


def frames(minutes: int, seconds: int, frame: int = 0) -> int:
    """An absolute CD position, as the TOC prints it (`77:55.58`), in frames."""
    return (minutes * 60 + seconds) * FRAMES_PER_SECOND + frame


def toc_from_lengths(lengths: Sequence[int], gap: int = 0) -> tuple[list[int], int]:
    """Track starts and the lead-out, in absolute frames, for tracks of these lengths in sectors.

    Track 1 starts at `LEAD_IN_FRAMES`; `gap` frames of silence sit between one track and the next.
    This is what a burn with that gap gives the disc's TOC (a gapless burn is `gap=0`). **Not yet
    compared with the TOC of a disc Dubcrate burned**: do that (`drutil toc`) before trusting it.
    """
    starts: list[int] = []
    position = LEAD_IN_FRAMES
    for n, length in enumerate(lengths):
        if n:
            position += gap
        starts.append(position)
        position += length
    return starts, position


def toc_from_drutil(text: str) -> tuple[list[int], int]:
    """Track starts and the lead-out, in absolute frames, from the text of macOS's `drutil toc`."""

    def find(pattern: str) -> list[int]:
        return [frames(*(int(g) for g in m)) for m in re.findall(pattern, text)]

    starts = find(r"Track\s+\d+:\s+(\d+):(\d+)\.(\d+)")
    lead_out = find(r"Lead-out:\s+(\d+):(\d+)\.(\d+)")
    if not starts or len(lead_out) != 1:
        raise CardFormatError("That doesn't look like the output of `drutil toc`.")
    return starts, lead_out[0]


@dataclass(frozen=True)
class DiscId:
    """The 16 bytes that identify a disc to the player: its TOC, abbreviated."""

    track_count: int
    flags: int
    lead_out: int
    second_start: int  # where track 2 starts
    last_start: int  # where the last track starts

    @classmethod
    def from_toc(
        cls, track_starts: Sequence[int], lead_out: int, flags: int = INDEX_FLAGS
    ) -> "DiscId":
        """The ID of a disc from its TOC: track starts and lead-out, in absolute frames.

        Needs two tracks or more: what the ID holds for a one-track disc was not measured.
        """
        if len(track_starts) < 2:
            raise CardFormatError(
                "The player's ID for a disc of one track hasn't been measured, "
                "so no card entry can be made for it."
            )
        if not 2 <= len(track_starts) <= 99:
            raise CardFormatError("An audio CD has at most 99 tracks.")
        return cls(
            len(track_starts), flags, lead_out, track_starts[1], track_starts[-1]
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "DiscId":
        if len(data) != _ID.size:
            raise CardFormatError("A disc ID is 16 bytes.")
        return cls(*_ID.unpack(data))

    def to_bytes(self) -> bytes:
        return _ID.pack(
            self.track_count,
            self.flags,
            self.lead_out,
            self.second_start,
            self.last_start,
        )

    @property
    def key(self) -> tuple[int, int, int, int]:
        """What identifies the disc: the ID without its flags word."""
        return (self.track_count, self.lead_out, self.second_start, self.last_start)

    def with_flags(self, flags: int) -> "DiscId":
        return replace(self, flags=flags)


@dataclass
class CueEntry:
    """A memory cue or a loop (a `CN11` entry, 20 bytes). Positions are absolute frames."""

    index: int  # 0, 1, 2, ... in the order they were set
    kind: int  # KIND_CUE or KIND_LOOP
    position: int  # the cue, or the loop's start
    end: int = 0  # a loop's end; 0 for a cue
    track: int = 1
    flag: int = 0  # 0 for a cue, LOOP_FLAG for the loop seen

    @classmethod
    def memory(cls, index: int, position: int, track: int) -> "CueEntry":
        return cls(index, KIND_CUE, position, 0, track, 0)

    @classmethod
    def loop(cls, index: int, start: int, end: int, track: int) -> "CueEntry":
        return cls(index, KIND_LOOP, start, end, track, LOOP_FLAG)

    @property
    def is_loop(self) -> bool:
        return self.kind == KIND_LOOP

    def pack(self) -> bytes:
        return _CUE.pack(
            self.index, self.kind, self.position, self.end, self.track, self.flag
        )


@dataclass
class HotCue:
    """One of the three hot cue slots (20 bytes in `HC11`); an unset slot is `None`, not this."""

    slot: int  # 1 = A, 2 = B, 3 = C
    position: int  # absolute frames
    track: int = 1
    kind: int = KIND_CUE
    end: int = 0
    extra: int = 0
    tail: int = 0

    def pack(self) -> bytes:
        return _HOT.pack(
            self.slot,
            self.kind,
            self.position,
            self.end,
            self.track,
            self.extra,
            self.tail,
        )


@dataclass
class WaveRecord:
    """One track's waveform record in `W001` (50 amplitudes, a byte each, 0 to 14) or `W002`
    (the same waveform at twice the resolution, 100 nibbles packed two to a byte, high first).

    The player fills it in as the track plays: columns not yet played are 0.
    """

    track: int
    data: bytes  # 50 bytes
    reserved: bytes = b"\0\0\0\0"

    def __post_init__(self) -> None:
        if len(self.data) != WAVE_COLUMNS:
            raise CardFormatError(f"A waveform record holds {WAVE_COLUMNS} bytes.")

    @classmethod
    def coarse(cls, track: int, levels: Sequence[int]) -> "WaveRecord":
        """A `W001` record from 50 amplitudes."""
        if len(levels) != WAVE_COLUMNS or not all(0 <= v <= 255 for v in levels):
            raise CardFormatError(f"A coarse waveform is {WAVE_COLUMNS} values.")
        return cls(track, bytes(levels))

    @classmethod
    def fine(cls, track: int, levels: Sequence[int]) -> "WaveRecord":
        """A `W002` record from 100 amplitudes (0 to 15 each)."""
        if len(levels) != 2 * WAVE_COLUMNS or not all(0 <= v <= 15 for v in levels):
            raise CardFormatError(
                f"A fine waveform is {2 * WAVE_COLUMNS} values, 0 to 15."
            )
        return cls(track, bytes(a << 4 | b for a, b in zip(levels[::2], levels[1::2])))

    def nibbles(self) -> list[int]:
        """The 100 amplitudes of a `W002` record."""
        return [n for byte in self.data for n in (byte >> 4, byte & 15)]

    def pack(self) -> bytes:
        return struct.pack(">H", self.track) + self.reserved + self.data


@dataclass
class TrackFile:
    """A disc's own file, `CDJ0000n.PDJ`."""

    disc_id: DiscId
    info: int = 2  # the u32 of the INFO chunk; always 2 so far, meaning not known
    cues: list[CueEntry] = field(default_factory=list)
    hot_cues: list[HotCue | None] = field(
        default_factory=lambda: [None] * HOT_CUE_SLOTS
    )
    coarse: list[WaveRecord] = field(default_factory=list)  # W001
    fine: list[WaveRecord] = field(default_factory=list)  # W002
    pad: bytes = b"\0\0"  # the two bytes after the checksum: junk in the player's files

    @classmethod
    def new(cls, disc_id: DiscId) -> "TrackFile":
        """An empty file for a disc: no cues, no waveform yet."""
        return cls(disc_id.with_flags(FILE_FLAGS))

    @classmethod
    def parse(cls, data: bytes, *, verify: bool = True) -> "TrackFile":
        r = _Reader(data)
        r.expect(MAGIC)
        info = r.header(b"INFO")
        if r.header(b"DID ") != 16:
            raise CardFormatError("The disc ID chunk isn't 16 bytes long.")
        disc_id = DiscId.from_bytes(r.take(16))

        cn_length = r.header(b"CN11")
        cues: list[CueEntry] = []
        if cn_length:
            body = _Reader(r.take(cn_length))
            count = body.u16()
            if cn_length != 2 + _CUE.size * count:
                raise CardFormatError("The cue list's length doesn't match its count.")
            cues = [CueEntry(*_CUE.unpack(body.take(_CUE.size))) for _ in range(count)]

        r.header(b"HC11")  # 0 or 60: not needed to read it, set again on writing
        hot_bytes = r.take(_HOT_BYTES)
        hot_cues: list[HotCue | None] = []
        for n in range(HOT_CUE_SLOTS):
            raw = hot_bytes[n * _HOT.size : (n + 1) * _HOT.size]
            if not any(raw):
                hot_cues.append(None)
                continue
            slot, kind, position, end, track, extra, tail = _HOT.unpack(raw)
            hot_cues.append(HotCue(slot, position, track, kind, end, extra, tail))

        waves = []
        for tag in (b"W001", b"W002"):
            length = r.header(tag)
            if length % _RECORD:
                raise CardFormatError(
                    f"The {tag.decode()} waveform chunk is cut short."
                )
            body = _Reader(r.take(length))
            records = []
            for _ in range(length // _RECORD):
                track = body.u16()
                reserved = body.take(4)
                records.append(WaveRecord(track, body.take(WAVE_COLUMNS), reserved))
            waves.append(records)

        csum_at = r.pos
        if r.header(b"CSUM") != 2:
            raise CardFormatError("The checksum chunk is the wrong size.")
        stored = r.u16()
        pad = r.take(2)
        if r.pos != len(data):
            raise CardFormatError("There is more in the file after its checksum.")
        if verify and checksum(data[:csum_at]) != stored:
            raise CardFormatError("The file's checksum doesn't match its contents.")
        return cls(disc_id, info, cues, hot_cues, waves[0], waves[1], pad)

    def to_bytes(self) -> bytes:
        if len(self.cues) > MAX_CUES:
            raise CardFormatError(f"A disc holds at most {MAX_CUES} cues and loops.")
        if len(self.hot_cues) != HOT_CUE_SLOTS:
            raise CardFormatError("There are three hot cue slots, A, B and C.")
        out = bytearray(MAGIC)
        out += b"INFO" + struct.pack(">I", self.info)
        out += b"DID " + struct.pack(">I", 16) + self.disc_id.to_bytes()

        if self.cues:
            body = struct.pack(">H", len(self.cues)) + b"".join(
                c.pack() for c in self.cues
            )
        else:
            body = b""
        out += b"CN11" + struct.pack(">I", len(body)) + body

        hot = b"".join(h.pack() if h else bytes(_HOT.size) for h in self.hot_cues)
        used = any(self.hot_cues)  # the chunk's u32: 60 once a slot is used, else 0
        out += b"HC11" + struct.pack(">I", _HOT_BYTES if used else 0) + hot

        for tag, records in ((b"W001", self.coarse), (b"W002", self.fine)):
            body = b"".join(rec.pack() for rec in records)
            out += tag + struct.pack(">I", len(body)) + body

        out += b"CSUM" + struct.pack(">I", 2) + struct.pack(">H", checksum(bytes(out)))
        return bytes(out) + self.pad


@dataclass
class IndexEntry:
    name: str  # the file's name without ".PDJ", e.g. "CDJ00001"
    disc_id: DiscId

    @property
    def file_name(self) -> str:
        return self.name + ".PDJ"


@dataclass
class IndexFile:
    """`CDJINDX0.PDJ`: which file belongs to which disc."""

    entries: list[IndexEntry] = field(default_factory=list)
    version: int = 1  # the u32 after INDX; 1 so far, meaning not known

    @classmethod
    def parse(cls, data: bytes) -> "IndexFile":
        r = _Reader(data)
        r.expect(MAGIC)
        version = r.header(b"INDX")
        if r.take(4) != b"CD  ":
            raise CardFormatError(
                "This index is for another kind of disc than an audio CD."
            )
        count = r.u32()
        if len(data) - r.pos != count * _INDEX_ENTRY:
            raise CardFormatError("The index's length doesn't match its disc count.")
        entries = []
        for _ in range(count):
            name = r.take(8).decode("ascii", errors="replace")
            entries.append(IndexEntry(name, DiscId.from_bytes(r.take(_ID.size))))
        return cls(entries, version)

    def to_bytes(self) -> bytes:
        out = bytearray(MAGIC)
        out += b"INDX" + struct.pack(">I", self.version) + b"CD  "
        out += struct.pack(">I", len(self.entries))
        for entry in self.entries:
            out += entry.name.encode("ascii") + entry.disc_id.to_bytes()
        return bytes(out)

    def find(self, disc_id: DiscId) -> IndexEntry | None:
        """The entry for a disc: same TOC, whatever its flags."""
        return next((e for e in self.entries if e.disc_id.key == disc_id.key), None)

    def next_name(self) -> str:
        """A file name not in use: one after the highest `CDJ0000n`.

        How the player numbers after a disc is deleted from the card was not measured.
        """
        numbers = [int(e.name[3:]) for e in self.entries if e.name[3:].isdigit()]
        return f"CDJ{max(numbers, default=0) + 1:05d}"

    def add(self, disc_id: DiscId) -> IndexEntry:
        """Add a disc (an entry with the index's flags word) unless it is there; return its entry."""
        if (found := self.find(disc_id)) is not None:
            return found
        entry = IndexEntry(self.next_name(), disc_id.with_flags(INDEX_FLAGS))
        self.entries.append(entry)
        return entry


def card_dir(path: Path) -> Path:
    """The folder with `CDJINDX0.PDJ` for a card's root, its `PIONEER` folder or that folder."""
    for candidate in (path, path / CARD_DIR[0] / CARD_DIR[1], path / CARD_DIR[1]):
        if (candidate / INDEX_NAME).is_file():
            return candidate
    raise CardFormatError(
        f"No {INDEX_NAME} found at {path}: is this the player's card?"
    )


def find_cards(volumes: Iterable[Path]) -> list[Path]:
    """The volumes that hold a CDJ card (a `PIONEER/CDJ` folder with its index), in the order given."""
    found = []
    for volume in volumes:
        try:
            card_dir(volume)
        except (CardFormatError, OSError):
            continue
        found.append(volume)
    return found


def read_card(path: Path) -> tuple[IndexFile, dict[str, TrackFile | None]]:
    """Read a card (or a copy of its `PIONEER/CDJ` folder): the index and each disc's file.

    A disc in the index with no file yet (the player makes the file later) maps to `None`.
    """
    folder = card_dir(path)
    index = IndexFile.parse((folder / INDEX_NAME).read_bytes())
    files: dict[str, TrackFile | None] = {}
    for entry in index.entries:
        file = folder / entry.file_name
        files[entry.name] = (
            TrackFile.parse(file.read_bytes()) if file.is_file() else None
        )
    return index, files


def write_card(
    path: Path,
    index: IndexFile,
    files: Mapping[str, TrackFile],
    *,
    create: bool = False,
) -> None:
    """Write disc files, then the index, into a card's `PIONEER/CDJ` folder and check them.

    `files` maps an index name (`CDJ00001`) to its file; only those are written. Each goes through
    a temporary name and a rename, so an interrupted write leaves the old file; the index is last,
    so an entry never points at a file that was not written (an entry with no file is a state the
    player makes itself). Everything is read back and compared. `create` makes `PIONEER/CDJ` on a
    card that has none: **the player has not been shown a folder made this way**.
    Only these files in that folder are touched. **The card must still be ejected through the system
    before it is pulled** (a FAT card pulled early can be left unreadable): the callers say so.
    """
    try:
        folder = card_dir(path)
    except CardFormatError:
        if not create:
            raise
        folder = path / CARD_DIR[0] / CARD_DIR[1]
        folder.mkdir(parents=True, exist_ok=True)
    names = {e.name for e in index.entries}
    if unknown := sorted(set(files) - names):
        raise CardFormatError(
            f"{unknown[0]} is not in the index, so it can't be written."
        )
    for name, file in files.items():
        _replace(folder / f"{name}.PDJ", file.to_bytes())
    _replace(folder / INDEX_NAME, index.to_bytes())
    if hasattr(os, "sync"):
        os.sync()  # (a file's fsync leaves the card's directory and FAT in the cache: flush those too)


def _replace(target: Path, data: bytes) -> None:
    temporary = target.with_name(target.name + ".TMP")
    try:
        with open(temporary, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    if target.read_bytes() != data:
        raise CardFormatError(
            f"{target.name} read back differently from what was written."
        )


def backup_card(path: Path, into: Path) -> Path:
    """Copy a card's `PIONEER/CDJ` files (a few kilobytes) to a new `card-DATE-TIME` folder in `into`.

    Returns that folder. Nothing on the card is changed; this is what a sync does before it writes.
    """
    folder = card_dir(path)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    destination = into / f"card-{stamp}"
    n = 1
    while destination.exists():  # two backups in one second
        n += 1
        destination = into / f"card-{stamp}-{n}"
    destination.mkdir(parents=True)
    for file in sorted(folder.glob("*.PDJ")):
        shutil.copy2(file, destination / file.name)
    return destination


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise CardFormatError("The file ends too soon.")
        chunk = self.data[self.pos : self.pos + n]
        self.pos += n
        return chunk

    def u16(self) -> int:
        return struct.unpack(">H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack(">I", self.take(4))[0]

    def expect(self, tag: bytes) -> None:
        if self.take(len(tag)) != tag:
            raise CardFormatError("Not a file the player wrote (the start is wrong).")

    def header(self, tag: bytes) -> int:
        """A chunk's tag, which must be this one, and its u32."""
        if self.take(4) != tag:
            raise CardFormatError(f"Expected the {tag.decode().strip()} chunk here.")
        return self.u32()
