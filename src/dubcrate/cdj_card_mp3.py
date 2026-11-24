"""The CDJ-1000MK3's SD card files for MP3 discs (`PIONEER/MPJ/`), pure: read AND write.

Decoded on 2026-11-23 from files the player made for one MP3 disc burned by Dubcrate (15 files, three
folders), with cues, hot cues and loops set on the player and read back: **`dev_docs/cdj1000mk3-card.md`,
section 6, is the reference, and `tests/data/cdj_card/mp3/` holds the player's own files.** Everything here
is measured on that disc and one player; the tests read those files and write them back byte for byte, and
build the player's own memory cue, hot cue and loop entries from their numbers. Not yet known (and so not
built): how the player's disc ID is made past three of its fields, and how the player's time relates to
Dubcrate's audio time. **Nothing here writes to a card by itself.**

An MP3 disc has ONE `MPJ00001.PDJ` for the whole disc (not a file per track) and an index
`MPJINDX0.PDJ`. A track is named by its **(folder, file)**: folder 0 is the root, then one number per
folder in order; files count from 1 inside their folder. A cue is a **132-byte** entry that carries the
time in 1/75 s frames within the track, the MP3 **frame number** and the file's **byte offset**, because the
player seeks an MP3 by byte, so a cue written from Dubcrate needs the frame headers of the file as burned
(`frame_table`).
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from dubcrate import cdj_card as cc

ENTRY_SIZE = 132  # a memory cue, loop or hot cue
HOT_SLOTS = cc.HOT_CUE_SLOTS  # A, B, C
HOT_BLOCK = HOT_SLOTS * ENTRY_SIZE  # 396, always present in the file
RECORD = 56  # a waveform record: folder u16, file u16, 0 u16, 50 data bytes
MEDIA = b"MP3 "
INDEX_ENTRY = 8 + 16  # a file name and the 16-byte disc ID
HEADER = 0x18  # PIONEER INDX, u32, "MP3 ", u32 (the number of discs)

KIND_CUE = cc.KIND_CUE  # 0x10: a memory cue, and what a hot cue says
KIND_LOOP = cc.KIND_LOOP  # 0x20

# The last four bytes of an entry's body. They vary on a real card (0x0086ffff, 0x007effff and 0xffffffff
# were seen on cues, 0xffffffff on loops), so what they mean is NOT known; a new cue gets what the player
# wrote for the first memory cue it made, a loop what it wrote for loops.
TAIL_CUE = bytes.fromhex("0086ffff")
TAIL_LOOP = bytes.fromhex("ffffffff")

SAMPLES_PER_FRAME = 1152  # MPEG-1 Layer III
_BITRATES = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0)
_RATES = (44100, 48000, 32000, 0)


class Mp3CardError(cc.CardFormatError):
    """A file under `PIONEER/MPJ` isn't one this module understands (an expected error: say so plainly)."""


class Mp3FrameError(RuntimeError):
    """An MP3 file whose frames can't be placed exactly (an expected error: not MPEG-1 Layer III, or not clean)."""


# -- the disc's ID ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Mp3DiscId:
    """The 16 bytes the player keeps for a disc. **Made from the image** by `made_from` (the firmware's own
    recipe, 2026-11-24); the properties below read three fields back out: byte 3 the file count (15 on the
    disc seen), bytes 8 to 10 the start sector of the last file, bytes 11 to 13 the size of the image."""

    raw: bytes

    def __post_init__(self) -> None:
        if len(self.raw) != 16:
            raise Mp3CardError("A disc ID is 16 bytes.")

    @property
    def file_count(self) -> int:
        return self.raw[3]

    @property
    def last_file_start(self) -> int:
        return int.from_bytes(self.raw[8:11], "big")

    @property
    def image_sectors(self) -> int:
        return int.from_bytes(self.raw[11:14], "big")

    @classmethod
    def made_from(
        cls,
        svd: bytes,
        *,
        file_count: int,
        folder_count: int,
        last_file_start: int,
    ) -> Mp3DiscId:
        """The ID the player makes for an image, read off its firmware (2.20, `FUN_ac10916c` and `FUN_ac1094c2`,
        2026-11-24) and checked against the one real disc we have (every field, the date to the second against
        the burn log). `svd` is the image's Joliet Supplementary Volume Descriptor (the 2048-byte sector, type 2).
        Layout, big endian: a 16-bit sum of the descriptor's bytes 40 to 87 (the label as UCS-2, 8 unused bytes,
        the volume size both-endian); 7 bits of the folder count (the root left out) then 9 bits of the file
        count; a 32-bit number of seconds from the volume's creation date,
        `(((year % 100) * 372 + (month - 1) * 31 + day) * 86400) + h * 3600 + m * 60 + s`; the last file's first
        sector in 3 bytes; the volume's size in sectors in 3 bytes; two zero bytes. **The creation date is in the
        ID, so the image that is burned must be the one the ID was made from** (`build_iso` must be given the
        date, see `iso.py`). Not yet tried on a player: only compared with the ID it wrote for one disc."""
        if len(svd) < 830 or svd[0] != 2 or svd[1:6] != b"CD001":
            raise Mp3CardError("That is not a Joliet volume descriptor.")
        digits = svd[813:829]
        if not digits.isdigit():
            raise Mp3CardError("The volume's creation date is not a date.")
        year, month, day, hour, minute, second = (
            int(digits[a:b])
            for a, b in ((0, 4), (4, 6), (6, 8), (8, 10), (10, 12), (12, 14))
        )
        checksum = sum(svd[40:88]) & 0xFFFF
        counts = ((folder_count & 0x7F) << 9) | (file_count & 0x1FF)
        seconds = (
            ((year % 100) * 372 + (month - 1) * 31 + day) * 86400
            + hour * 3600
            + minute * 60
            + second
        ) & 0xFFFFFFFF
        size = int.from_bytes(svd[80:84], "little")
        raw = (
            checksum.to_bytes(2, "big")
            + counts.to_bytes(2, "big")
            + seconds.to_bytes(4, "big")
            + (last_file_start & 0xFFFFFF).to_bytes(3, "big")
            + (size & 0xFFFFFF).to_bytes(3, "big")
            + b"\x00\x00"
        )
        return cls(raw)

    def matches(
        self, file_count: int, last_file_start: int, image_sectors: int
    ) -> bool:
        """Whether this ID is for an image with these numbers (the three fields we understand)."""
        return (
            self.file_count == file_count
            and self.last_file_start == last_file_start
            and self.image_sectors == image_sectors
        )


# -- a cue, loop or hot cue entry -------------------------------------------------------------------


def _u16(data: bytes, at: int) -> int:
    return struct.unpack_from(">H", data, at)[0]


def _u32(data: bytes, at: int) -> int:
    return struct.unpack_from(">I", data, at)[0]


@dataclass
class Mp3Entry:
    """One memory cue, loop or hot cue: 132 bytes.

    A hot slot is `slot u16, kind u16, ...` and a `CN11` entry is `index u32, kind u16, ...` with the rest
    of the fields **two bytes later**, which is the only difference between them. The fields:

    - `position`, `end`, `length`: frames of 1/75 s **within the track** (`end` is a loop's end, 0 for a cue;
      `length` is the track's, `ceil(frames x 1152 / rate x 75)` counting the Info frame);
    - `frame`, `end_frame`: the MP3 frame numbers of `position` and `end`, counting the file's first frame
      (the Info frame) as 0; `byte`, `end_byte`: those frames' offsets in the file as burned (its ID3 tag
      included). The player seeks by these.
    - `folder`, `file`: which track (a byte, and a `u16`).

    `raw` is the 132 bytes as the player wrote them; an entry that was read keeps them and is written back
    exactly (the player leaves a cue's unused fields **stale**: they hold the last loop's numbers, and a
    reader must ignore them). An entry made here is written with zeros in those fields."""

    kind: int
    folder: int
    file: int
    position: int
    end: int
    length: int
    frame: int
    end_frame: int
    byte: int
    end_byte: int
    raw: bytes | None = None

    @property
    def is_loop(self) -> bool:
        return self.kind == KIND_LOOP

    @classmethod
    def parse(cls, raw: bytes, *, hot: bool) -> Mp3Entry:
        if len(raw) != ENTRY_SIZE:
            raise Mp3CardError("A cue entry is 132 bytes.")
        s = 0 if hot else 2
        kind = _u16(raw, 2 if hot else 4)
        if kind not in (KIND_CUE, KIND_LOOP):
            raise Mp3CardError(
                f"A cue entry of kind {kind:#06x} isn't one that is understood."
            )
        return cls(
            kind=kind,
            folder=raw[104 + s],
            file=_u16(raw, 18 + s),
            position=_u32(raw, 24 + s),
            end=_u32(raw, 28 + s),
            length=_u32(raw, 32 + s),
            frame=_u32(raw, 88 + s),
            end_frame=_u32(raw, 92 + s),
            byte=_u32(raw, 96 + s),
            end_byte=_u32(raw, 100 + s),
            raw=bytes(raw),
        )

    @classmethod
    def cue(cls, table: FrameTable, folder: int, file: int, position: int) -> Mp3Entry:
        """A memory cue or hot cue at `position` frames (1/75 s) into the track whose frames are `table`."""
        frame, byte = table.locate(position)
        return cls(
            KIND_CUE, folder, file, position, 0, table.length_frames, frame, 0, byte, 0
        )

    @classmethod
    def loop(
        cls, table: FrameTable, folder: int, file: int, start: int, end: int
    ) -> Mp3Entry:
        frame, byte = table.locate(start)
        end_frame, end_byte = table.locate(end)
        return cls(
            KIND_LOOP,
            folder,
            file,
            start,
            end,
            table.length_frames,
            frame,
            end_frame,
            byte,
            end_byte,
        )

    def pack(self, *, hot: bool, number: int) -> bytes:
        """The 132 bytes: as read if this entry was read, else built (`number` is the hot slot, 1 to 3,
        or the `CN11` index, the order the cues were set in)."""
        if self.raw is not None:
            return self.raw
        s = 0 if hot else 2
        body = bytearray(ENTRY_SIZE)
        if hot:
            struct.pack_into(">HH", body, 0, number, self.kind)
        else:
            struct.pack_into(">IH", body, 0, number, self.kind)
        struct.pack_into(">H", body, 16 + s, 2 if self.is_loop else 1)
        struct.pack_into(">H", body, 18 + s, self.file)
        if self.is_loop:
            struct.pack_into(">H", body, 20 + s, self.file)
        struct.pack_into(">I", body, 24 + s, self.position)
        struct.pack_into(">I", body, 28 + s, self.end)
        struct.pack_into(">I", body, 32 + s, self.length)
        if self.is_loop:
            struct.pack_into(">I", body, 36 + s, self.length)
        struct.pack_into(">I", body, 88 + s, self.frame)
        struct.pack_into(">I", body, 92 + s, self.end_frame)
        struct.pack_into(">I", body, 96 + s, self.byte)
        struct.pack_into(">I", body, 100 + s, self.end_byte)
        body[104 + s] = self.folder
        body[112 + s : 116 + s] = TAIL_LOOP if self.is_loop else TAIL_CUE
        return bytes(body)


# -- waveform records -------------------------------------------------------------------------------


@dataclass
class Mp3Wave:
    """One track's waveform record, in `W001` (50 amplitudes, a byte each) or `W002` (100 packed nibbles),
    keyed by **(folder, file)**, not a track number. The player fills one in as the track plays (columns
    not yet played are 0) and appends records in the order tracks were first touched."""

    folder: int
    file: int
    data: bytes  # 50 bytes
    third: int = 0  # always 0 so far

    def __post_init__(self) -> None:
        if len(self.data) != cc.WAVE_COLUMNS:
            raise Mp3CardError(f"A waveform record holds {cc.WAVE_COLUMNS} bytes.")

    @property
    def key(self) -> tuple[int, int]:
        return (self.folder, self.file)

    @property
    def columns_played(self) -> int:
        return sum(1 for b in self.data if b)

    def pack(self) -> bytes:
        return struct.pack(">HHH", self.folder, self.file, self.third) + self.data

    @classmethod
    def from_audio_records(
        cls, folder: int, file: int, coarse: cc.WaveRecord, fine: cc.WaveRecord
    ) -> tuple[Mp3Wave, Mp3Wave]:
        """The (`W001`, `W002`) records for a track, from the audio disc's records `wave_model.records`
        makes (the same model: it matches the player's own MP3 records about as well as its audio ones)."""
        return cls(folder, file, coarse.data), cls(folder, file, fine.data)


# -- the track file ---------------------------------------------------------------------------------


@dataclass
class Mp3TrackFile:
    """`MPJ00001.PDJ`: the cues, hot cues and waveforms of every track of one disc."""

    disc_id: Mp3DiscId
    info: int = 2
    cues: list[Mp3Entry] = field(
        default_factory=list
    )  # CN11, in the order set (index = the order set)
    hot: list[Mp3Entry | None] = field(default_factory=lambda: [None] * HOT_SLOTS)
    coarse: list[Mp3Wave] = field(default_factory=list)  # W001
    fine: list[Mp3Wave] = field(default_factory=list)  # W002
    cue_indexes: list[int] = field(
        default_factory=list
    )  # each cue's index as read (not renumbered)
    pad: bytes = b"\x00\x00"  # two bytes after the checksum: not understood, kept

    def __post_init__(self) -> None:
        if not self.cue_indexes:
            self.cue_indexes = list(range(len(self.cues)))

    @classmethod
    def new(cls, disc_id: Mp3DiscId) -> Mp3TrackFile:
        """An empty file for a disc: no cues, no waveform yet."""
        return cls(disc_id)

    @classmethod
    def parse(cls, data: bytes, *, verify: bool = True) -> Mp3TrackFile:
        pos = 0

        def take(n: int) -> bytes:
            nonlocal pos
            if pos + n > len(data):
                raise Mp3CardError("The file is cut short.")
            chunk = data[pos : pos + n]
            pos += n
            return chunk

        def header(tag: bytes) -> int:
            if take(4) != tag:
                raise Mp3CardError(f"Expected the {tag.decode()} chunk here.")
            return struct.unpack(">I", take(4))[0]

        if take(8) != cc.MAGIC:
            raise Mp3CardError("This isn't a CDJ card file.")
        info = header(b"INFO")
        if header(b"DID ") != 16:
            raise Mp3CardError("The disc ID chunk isn't 16 bytes long.")
        disc_id = Mp3DiscId(take(16))

        cn_length = header(b"CN11")
        cues: list[Mp3Entry] = []
        indexes: list[int] = []
        if cn_length:
            body = take(cn_length)
            count = _u16(body, 0)
            if cn_length != 2 + ENTRY_SIZE * count:
                raise Mp3CardError("The cue list's length doesn't match its count.")
            for i in range(count):
                raw = body[2 + ENTRY_SIZE * i : 2 + ENTRY_SIZE * (i + 1)]
                cues.append(Mp3Entry.parse(raw, hot=False))
                indexes.append(_u32(raw, 0))

        header(b"HC11")  # 0, or 396 once a slot is used: set again on writing
        block = take(HOT_BLOCK)
        hot: list[Mp3Entry | None] = []
        for n in range(HOT_SLOTS):
            raw = block[n * ENTRY_SIZE : (n + 1) * ENTRY_SIZE]
            hot.append(Mp3Entry.parse(raw, hot=True) if any(raw) else None)

        waves: list[list[Mp3Wave]] = []
        for tag in (b"W001", b"W002"):
            length = header(tag)
            if length % RECORD:
                raise Mp3CardError(f"The {tag.decode()} waveform chunk is cut short.")
            body = take(length)
            records = []
            for i in range(length // RECORD):
                folder, file, third = struct.unpack_from(">HHH", body, i * RECORD)
                records.append(
                    Mp3Wave(
                        folder, file, body[i * RECORD + 6 : (i + 1) * RECORD], third
                    )
                )
            waves.append(records)

        csum_at = pos
        if header(b"CSUM") != 2:
            raise Mp3CardError("The checksum chunk is the wrong size.")
        stored = struct.unpack(">H", take(2))[0]
        pad = take(2)
        if pos != len(data):
            raise Mp3CardError("There is more in the file after its checksum.")
        if verify and cc.checksum(data[:csum_at]) != stored:
            raise Mp3CardError("The file's checksum doesn't match its contents.")
        return cls(disc_id, info, cues, hot, waves[0], waves[1], indexes, pad)

    def to_bytes(self) -> bytes:
        if len(self.cues) > cc.MAX_CUES:
            raise Mp3CardError(f"A disc holds at most {cc.MAX_CUES} cues and loops.")
        if len(self.hot) != HOT_SLOTS:
            raise Mp3CardError("There are three hot cue slots, A, B and C.")
        if len(self.cue_indexes) != len(self.cues):
            raise Mp3CardError("Every cue needs its index.")
        out = bytearray(cc.MAGIC)
        out += b"INFO" + struct.pack(">I", self.info)
        out += b"DID " + struct.pack(">I", 16) + self.disc_id.raw

        if self.cues:
            body = struct.pack(">H", len(self.cues)) + b"".join(
                c.pack(hot=False, number=n) for c, n in zip(self.cues, self.cue_indexes)
            )
        else:
            body = b""
        out += b"CN11" + struct.pack(">I", len(body)) + body

        block = b"".join(
            h.pack(hot=True, number=n + 1) if h else bytes(ENTRY_SIZE)
            for n, h in enumerate(self.hot)
        )
        out += b"HC11" + struct.pack(">I", HOT_BLOCK if any(self.hot) else 0) + block

        for tag, records in ((b"W001", self.coarse), (b"W002", self.fine)):
            body = b"".join(r.pack() for r in records)
            out += tag + struct.pack(">I", len(body)) + body

        out += (
            b"CSUM" + struct.pack(">I", 2) + struct.pack(">H", cc.checksum(bytes(out)))
        )
        return bytes(out) + self.pad

    def next_cue_index(self) -> int:
        """The index for a cue added now: the order set, one past the highest so far (deletions leave gaps)."""
        return max(self.cue_indexes, default=-1) + 1


# -- the index --------------------------------------------------------------------------------------


@dataclass
class Mp3IndexEntry:
    name: str  # the file's name without ".PDJ", e.g. "MPJ00001"
    disc_id: Mp3DiscId

    @property
    def file_name(self) -> str:
        return self.name + ".PDJ"


@dataclass
class Mp3Index:
    """`MPJINDX0.PDJ`: which file belongs to which disc. No checksum; 24-byte entries as the audio index."""

    entries: list[Mp3IndexEntry] = field(default_factory=list)
    version: int = 1

    @classmethod
    def parse(cls, data: bytes) -> Mp3Index:
        if data[:8] != cc.MAGIC or data[8:12] != b"INDX" or len(data) < HEADER:
            raise Mp3CardError("This isn't a CDJ card index.")
        version = _u32(data, 12)
        if data[16:20] != MEDIA:
            raise Mp3CardError("This index is for another kind of disc than an MP3 CD.")
        count = _u32(data, 20)
        if len(data) - HEADER != count * INDEX_ENTRY:
            raise Mp3CardError("The index's length doesn't match its disc count.")
        entries = []
        for i in range(count):
            at = HEADER + i * INDEX_ENTRY
            entries.append(
                Mp3IndexEntry(
                    data[at : at + 8].decode("ascii", errors="replace"),
                    Mp3DiscId(data[at + 8 : at + 24]),
                )
            )
        return cls(entries, version)

    def to_bytes(self) -> bytes:
        out = bytearray(cc.MAGIC + b"INDX" + struct.pack(">I", self.version))
        out += MEDIA + struct.pack(">I", len(self.entries))
        for entry in self.entries:
            out += entry.name.encode("ascii").ljust(8, b"\0")[:8] + entry.disc_id.raw
        return bytes(out)

    def next_name(self) -> str:
        """The name for a new entry's file: one past the highest number there, as the player counts (`MPJ00002`)."""
        numbers = [int(e.name[3:]) for e in self.entries if e.name[3:].isdigit()]
        return f"MPJ{max(numbers, default=0) + 1:05d}"

    def find(
        self, file_count: int, last_file_start: int, image_sectors: int
    ) -> list[Mp3IndexEntry]:
        """The entries whose ID is for an image with these three numbers (usually one)."""
        return [
            e
            for e in self.entries
            if e.disc_id.matches(file_count, last_file_start, image_sectors)
        ]


def mpj_dir(path: Path) -> Path:
    """The `MPJ` folder, from a card's root, its `PIONEER` folder, or the `MPJ` folder itself."""
    path = Path(path)
    for candidate in (path, path / "MPJ", path / "PIONEER" / "MPJ"):
        if (candidate / "MPJINDX0.PDJ").is_file():
            return candidate
    raise Mp3CardError(
        f"No MP3 disc files (PIONEER/MPJ/MPJINDX0.PDJ) were found at {path}."
    )


def mp3_disc_count(path: Path) -> int:
    """How many MP3 discs the card has an entry for (0 if it has none or can't be read): the index only, a few
    bytes. The player counts them with the audio discs."""
    try:
        return len(
            Mp3Index.parse((mpj_dir(path) / "MPJINDX0.PDJ").read_bytes()).entries
        )
    except (Mp3CardError, OSError, struct.error):
        return 0


def read_mp3_card(
    path: Path, missing_ok: bool = False
) -> tuple[Mp3Index, dict[str, Mp3TrackFile | None]]:
    """The MP3 disc index and each disc's file (None for an entry with no file yet). Read only. With `missing_ok`
    a card that has no MP3 disc files at all gives an empty index (for making the first entry)."""
    try:
        folder = mpj_dir(path)
    except Mp3CardError:
        if missing_ok:
            return Mp3Index(), {}
        raise
    try:
        index = Mp3Index.parse((folder / "MPJINDX0.PDJ").read_bytes())
        files: dict[str, Mp3TrackFile | None] = {}
        for entry in index.entries:
            target = folder / entry.file_name
            files[entry.name] = (
                Mp3TrackFile.parse(target.read_bytes()) if target.is_file() else None
            )
    except (
        OSError,
        struct.error,
    ) as exc:  # (a card pulled mid-read, or a chunk too short for its count)
        raise Mp3CardError(
            f"The card's MP3 disc files couldn't be read: {exc}"
        ) from exc
    return index, files


# -- the frames of an MP3 file, as burned ------------------------------------------------------------


@dataclass(frozen=True)
class FrameTable:
    """Where each frame of an MP3 file starts, counting from its first frame (a file Dubcrate burns starts
    with ffmpeg's Info frame, which is frame 0 and counts: the player counts it as audio time). The offsets
    follow the file's own padding, so they can only be read from the file as it is on the disc (a closed
    formula was tried and is wrong for most files)."""

    offsets: tuple[int, ...]
    sample_rate: int

    @property
    def frames(self) -> int:
        return len(self.offsets)

    @property
    def length_frames(self) -> int:
        """The track's length in 1/75 s frames, as the player writes it: `ceil(frames x 1152 / rate x 75)`."""
        return -(-self.frames * SAMPLES_PER_FRAME * 75 // self.sample_rate)

    def locate(self, position: int) -> tuple[int, int]:
        """(MP3 frame number, byte offset) of a position in 1/75 s frames: `floor(position / 75 s / frame
        length)`, the frame that contains it (checked on every cue the player has written)."""
        frame = position * self.sample_rate // (75 * SAMPLES_PER_FRAME)
        frame = max(0, min(frame, self.frames - 1))
        return frame, self.offsets[frame]


def frame_table(path: Path) -> FrameTable:
    """Read every frame header of an MP3 file. Raises `Mp3FrameError` for anything that isn't a clean
    MPEG-1 Layer III file, since a cue's byte offset must be exact. Only reads the file."""
    data = Path(path).read_bytes()
    pos = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        size = (data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14 | (data[8] & 0x7F) << 7 | (data[9] & 0x7F)  # fmt: skip
        pos = 10 + size + (10 if data[5] & 0x10 else 0)
    offsets: list[int] = []
    rate = 0
    while pos + 4 <= len(data):
        header = int.from_bytes(data[pos : pos + 4], "big")
        version, layer = (header >> 19) & 3, (header >> 17) & 3
        bitrate = _BITRATES[(header >> 12) & 0xF]
        this_rate = _RATES[(header >> 10) & 3]
        if (
            header >> 21 != 0x7FF
            or version != 3
            or layer != 1
            or not bitrate
            or not this_rate
        ):
            break
        if rate and this_rate != rate:
            raise Mp3FrameError(
                f"{Path(path).name}: the sample rate changes inside the file."
            )
        rate = this_rate
        offsets.append(pos)
        pos += 144 * bitrate * 1000 // rate + ((header >> 9) & 1)
    if not offsets:
        raise Mp3FrameError(f"{Path(path).name}: no MPEG-1 Layer III audio found.")
    left = len(data) - pos
    if left > 128 + 10 and not (data[pos : pos + 3] in (b"TAG", b"APE")):
        # junk the player might count differently: refuse rather than write wrong offsets
        raise Mp3FrameError(
            f"{Path(path).name}: something other than a tag follows the audio."
        )
    return FrameTable(tuple(offsets), rate)


def entries_for(
    table: FrameTable,
    folder: int,
    file: int,
    cues: Sequence[int],
    loops: Sequence[tuple[int, int]] = (),
) -> list[Mp3Entry]:
    """Memory cues (positions in 1/75 s frames) and loops (start, end) for one track, as new entries."""
    return [Mp3Entry.cue(table, folder, file, p) for p in cues] + [
        Mp3Entry.loop(table, folder, file, a, b) for a, b in loops
    ]
