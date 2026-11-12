"""MP3 discs: sizes in data sectors, estimates, and the names files get on the disc (pure).

An MP3 CD is a data disc, so what limits it is bytes, not minutes. The planner works in
"sectors" for both kinds of disc, and for an MP3 disc a sector is a 2048-byte data sector.
A disc's capacity in sectors is the same number as for an audio CD of the same size (360,000
for an 80 minute disc, about 737 MB), so the Settings > Discs size applies to both.
"""

import math
import re
import unicodedata
from pathlib import Path

DATA_SECTOR_BYTES = 2048
BITRATES = (128, 192, 256, 320)  # kbit/s offered for tracks that have to be encoded
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


def track_sectors(
    path: str | Path,
    *,
    file_size: int | None,
    duration: float,
    source_bitrate: int | None,
    disc_bitrate: int,
) -> int:
    """Sectors a library track will take on an MP3 disc.

    An MP3 is copied as it is, so it takes its file's size (or, if that is unknown, what its
    own bitrate says). Anything else is encoded at the disc's bitrate.
    """
    if is_mp3(path):
        if file_size:
            return data_sectors(file_size + TAG_BYTES)
        return data_sectors(encoded_bytes(duration, source_bitrate or disc_bitrate))
    return data_sectors(encoded_bytes(duration, disc_bitrate))


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
