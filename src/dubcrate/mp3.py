"""MP3 discs: sizes in data sectors, estimates, and the names files get on the disc (pure).

An MP3 CD is a data disc, so what limits it is bytes, not minutes. The planner works in
"sectors" for both kinds of disc, and for an MP3 disc a sector is a 2048-byte data sector.
A disc's capacity in sectors is the same number as for an audio CD of the same size (360,000
for an 80 minute disc, about 737 MB), so the Settings > Discs size applies to both.
"""

import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

DATA_SECTOR_BYTES = 2048
BITRATES = (
    128,
    192,
    256,
    320,
)  # the ceilings Settings offers, in kbit/s (constant bitrate)
DEFAULT_BITRATE = 320
# The volume descriptors, path tables and so on, measured with DiscRecording's own estimate for a
# folder holding one tiny file (178 sectors, 2 of them the file).
FILESYSTEM_OVERHEAD_SECTORS = 176
# Every file costs its directory entries (ISO 9660 and Joliet) and is padded to a sector.
FILE_OVERHEAD_SECTORS = 2
# Room for the ID3 tag we write, and for an estimate being a little off.
TAG_BYTES = 4096
ESTIMATE_MARGIN = 1.02
MAX_NAME_CHARS = 60  # Joliet allows 64 in all, including ".mp3"

# What the CDJs read (from Pioneer's specification): 999 files in a folder, 99 folders on a disc
# (the root counting as one), and folders nested 8 levels deep (the root counting as the first).
MAX_FILES_PER_FOLDER = 999
MAX_FOLDERS = 99
MAX_DEPTH = 8
FOLDER_OVERHEAD_SECTORS = 4  # a folder's directory entries, in ISO 9660 and in Joliet

STANDARD_BITRATES = (32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
# Formats that are already lossy: below the ceiling, their own bitrate says how much the audio
# can use, so it is not inflated. Anything else (FLAC, WAV, AIFF, ALAC ...) goes at the ceiling.
LOSSY_CODECS = frozenset(
    {"mp3", "mp2", "aac", "vorbis", "opus", "ac3", "eac3", "wmav1", "wmav2"}
)

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def is_mp3(path: str | Path) -> bool:
    """Whether a file is an MP3 (and so goes on the disc as it is, with fresh tags)."""
    return Path(path).suffix.lower() == ".mp3"


def data_sectors(size_bytes: int) -> int:
    """Sectors a file of `size_bytes` takes on the disc."""
    return math.ceil(size_bytes / DATA_SECTOR_BYTES) + FILE_OVERHEAD_SECTORS


def encoded_bytes(duration: float, bitrate_kbps: int) -> int:
    """Conservative size of `duration` seconds encoded as constant-bitrate MP3."""
    return math.ceil(duration * bitrate_kbps * 125 * ESTIMATE_MARGIN) + TAG_BYTES


def cbr_target(source_bitrate: int | None, ceiling: int) -> int:
    """The constant bitrate a lossy source goes on an MP3 disc at: `ceiling` (320 unless the
    disc says less), or, for a source below it, the next standard rate up (never worse than the
    source, never more than the ceiling): 245 kbit/s becomes 256, 128 stays 128."""
    if not source_bitrate or source_bitrate >= ceiling:
        return ceiling
    return next(
        (r for r in STANDARD_BITRATES if source_bitrate <= r <= ceiling), ceiling
    )


def track_sectors(
    path: str | Path,
    *,
    duration: float,
    source_bitrate: int | None,
    disc_bitrate: int,
) -> int:
    """Sectors a library track will take on an MP3 disc: its length at the constant bitrate it
    will have there. This is an estimate that errs high (an MP3 that is already constant
    bitrate is copied and so takes a little less; the staged files' real sizes are checked at
    burn time). Only an MP3's own bitrate is trusted to be below the ceiling: any other format
    might be lossless, which always goes at the ceiling.
    """
    rate = cbr_target(source_bitrate, disc_bitrate) if is_mp3(path) else disc_bitrate
    return data_sectors(encoded_bytes(duration, rate))


@dataclass(frozen=True)
class Mp3Scan:
    """What the frames of an MP3 file say about its bitrate."""

    cbr: bool  # every audio frame has the same bitrate
    bitrate: int  # kbit/s: that bitrate, or (not CBR) the average
    sample_rate: int
    frames: int


_BITRATES_MPEG1 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0)
_BITRATES_MPEG2 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0)
_SAMPLE_RATES = {
    3: (44100, 48000, 32000),
    2: (22050, 24000, 16000),
    0: (11025, 12000, 8000),
}


def _frame_at(data: bytes, pos: int) -> tuple[int, int, int] | None:
    """(bitrate in kbit/s, sample rate, frame length in bytes) of the Layer III frame that
    starts at `pos`, or None if there isn't one."""
    if pos + 4 > len(data):
        return None
    h = data[pos : pos + 4]
    if h[0] != 0xFF or h[1] & 0xE0 != 0xE0:
        return None
    version, layer = (h[1] >> 3) & 3, (h[1] >> 1) & 3
    index, rate_index, pad = h[2] >> 4, (h[2] >> 2) & 3, (h[2] >> 1) & 1
    if version == 1 or layer != 1 or index in (0, 15) or rate_index == 3:
        return None
    bitrate = (_BITRATES_MPEG1 if version == 3 else _BITRATES_MPEG2)[index]
    rate = _SAMPLE_RATES[version][rate_index]
    return bitrate, rate, (144 if version == 3 else 72) * bitrate * 1000 // rate + pad


def _resync(data: bytes, start: int) -> int | None:
    """The next place a run of audio frames starts: a frame header that has another one right
    after it (so a stray 0xFF in a tag or in junk is not taken for one). None if there is no more
    audio, as after the last frame, where an ID3v1 or APE tag is only trailing data."""
    pos = data.find(b"\xff", start)
    while pos != -1:
        frame = _frame_at(data, pos)
        if frame is not None and _frame_at(data, pos + frame[2]) is not None:
            return pos
        pos = data.find(b"\xff", pos + 1)
    return None


def scan_mp3(path: str | Path) -> Mp3Scan | None:
    """Read every frame header of an MP3 file; None if it doesn't look like Layer III audio.

    This is how constant bitrate is told from variable (VBR and ABR files change bitrate from
    frame to frame), which a player's tag can't be trusted for: many CBR files have none, and
    some encoders write the wrong one. The file's Xing/Info/VBRI frame is not audio and is left
    out. Reading a file is all it does: it never changes it.
    """
    data = Path(path).read_bytes()
    pos = 0
    if (
        data[:3] == b"ID3" and len(data) >= 10
    ):  # an ID3v2 tag first; its size is 4 bytes of 7 bits
        size = (data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14 | (data[8] & 0x7F) << 7 | (data[9] & 0x7F)  # fmt: skip
        pos = 10 + size + (10 if data[5] & 0x10 else 0)  # (a footer is 10 bytes more)
    rates: list[int] = []
    sample_rate = 0
    first = True
    while pos is not None and pos + 4 <= len(data):
        frame = _frame_at(data, pos)
        if frame is None:  # junk between frames, or the tags after the last one
            pos = _resync(data, pos + 1)
            continue
        bitrate, rate, length = frame
        body = data[pos : pos + min(length, 64)]
        if first and any(tag in body for tag in (b"Xing", b"Info", b"VBRI")):
            pass  # the encoder's own header frame, not audio
        else:
            rates.append(bitrate)
            sample_rate = rate
        first = False
        pos += length
    if not rates:
        return None
    cbr = len(set(rates)) == 1
    return Mp3Scan(
        cbr,
        rates[0] if cbr else round(sum(rates) / len(rates)),
        sample_rate,
        len(rates),
    )


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return " ".join(_UNSAFE.sub(" ", text).split()).strip(" .")


def file_name(number: int, artist: str, title: str) -> str:
    """`01 Artist - Title.mp3`: the number keeps the order on players that sort by name, and
    the rest is cut to what Joliet allows. Characters a file system dislikes are removed."""
    label = " - ".join(part for part in (_clean(artist), _clean(title)) if part)
    prefix = f"{number:02d} "
    room = MAX_NAME_CHARS - len(prefix) - len(".mp3")
    label = label[:room].rstrip(" .-") or "Track"
    return f"{prefix}{label}.mp3"


def dir_name(name: str) -> str:
    """A folder's name as it is written on the disc: what a file system allows, Joliet's length."""
    return _clean(name)[:MAX_NAME_CHARS].strip(" .") or "Folder"


def format_size(sectors: int) -> str:
    """Sectors as megabytes, the way CDs are labelled (an 80 minute disc is "703 MB", the
    "700 MB" on the box)."""
    return f"{sectors * DATA_SECTOR_BYTES / (1024 * 1024):.0f} MB"


def format_amount(sectors: int, kind: str = "audio") -> str:
    """A disc's size or length for people: MM:SS:FF for an audio CD, megabytes for an MP3 CD."""
    from dubcrate.cd import format_msf

    return format_size(sectors) if kind == "mp3" else format_msf(sectors)


def format_span(sectors: int, kind: str = "audio") -> str:
    """Like `format_amount`, but a length as M:SS (what the window shows)."""
    from dubcrate.cd import format_clock

    return format_size(sectors) if kind == "mp3" else format_clock(sectors)


def volume_name(title: str) -> str:
    """The disc's name as a volume label (Joliet keeps 16 characters)."""
    return _clean(title)[:16].strip() or "MP3 DISC"
