"""Red Book (audio CD) constants, sector maths and CD-TEXT string handling."""

import math
import unicodedata
from collections.abc import Iterable

SAMPLE_RATE = 44_100
CHANNELS = 2
BYTES_PER_SAMPLE_FRAME = 4  # 16-bit stereo
SAMPLES_PER_SECTOR = 588  # 44100 / 75
SECTOR_BYTES = SAMPLES_PER_SECTOR * BYTES_PER_SAMPLE_FRAME  # 2352
SECTORS_PER_SECOND = 75

# Nominal disc capacities in sectors (frames). 80 min discs hold 360,000.
DISC_74_SECTORS = 74 * 60 * SECTORS_PER_SECOND
DISC_80_SECTORS = 80 * 60 * SECTORS_PER_SECOND

# CD-TEXT is limited; titles beyond this are truncated rather than risk an
# oversized pack list. The Red Book allows 160 chars per field, but the whole
# block shares a small budget, so stay well under.
CDTEXT_MAX_FIELD_CHARS = 80


def samples_to_sectors(samples: int) -> int:
    """Number of whole sectors needed to hold `samples` sample frames."""
    return -(-samples // SAMPLES_PER_SECTOR)


def format_msf(sectors: int) -> str:
    """Format a sector count as MM:SS:FF (frames out of 75)."""
    minutes, rest = divmod(sectors, 60 * SECTORS_PER_SECOND)
    seconds, frames = divmod(rest, SECTORS_PER_SECOND)
    return f"{minutes:02d}:{seconds:02d}:{frames:02d}"


def cdtext_clean(value: str, max_chars: int = CDTEXT_MAX_FIELD_CHARS) -> str:
    """Reduce `value` to characters a single-byte CD-TEXT block can carry.

    Keeps ASCII and Latin-1 characters as-is (é, ü, ñ ...). Anything outside
    Latin-1 is decomposed and stripped of combining marks, and what still does
    not fit becomes '?'. Whitespace runs collapse to one space; other control
    characters and double quotes are dropped.
    """
    out: list[str] = []
    for ch in value:
        if ch.isspace():
            out.append(" ")
            continue
        if ch == '"' or unicodedata.category(ch).startswith("C"):
            continue
        if ord(ch) <= 0xFF:
            out.append(ch)
            continue
        folded = unicodedata.normalize("NFKD", ch)
        folded = "".join(c for c in folded if not unicodedata.combining(c))
        out.append("".join(c if ord(c) <= 0xFF else "?" for c in folded))
    return " ".join("".join(out).split())[:max_chars]


def cdtext_toc_escape(value: str) -> str:
    """Escape an already-cleaned string for use inside a cdrdao TOC literal.

    Anything outside printable ASCII, plus backslash and double quote, is
    written as a three-digit octal escape of its Latin-1 byte.
    """
    parts: list[str] = []
    for ch in value:
        code = ord(ch)
        if 0x20 <= code <= 0x7E and ch not in '"\\':
            parts.append(ch)
        else:
            parts.append(f"\\{code:03o}")
    return "".join(parts)


# The Red Book allows at most 99 tracks on a disc.
MAX_TRACKS = 99


def estimate_sectors(duration: float) -> int:
    """Conservative length of an unmeasured track, in sectors.

    Tag durations can be whole seconds (rekordbox stores integers), so add one
    second of margin to avoid planning a disc that turns out to be too long.
    """
    return math.ceil(duration * SECTORS_PER_SECOND) + SECTORS_PER_SECOND


def measured_sectors(duration: float) -> int:
    """Length in sectors from a precise duration (e.g. ffprobe's)."""
    return math.ceil(duration * SECTORS_PER_SECOND)


def disc_used_sectors(lengths: Iterable[int], pregap_sectors: int = 0) -> int:
    """Sectors a disc consumes: track lengths plus the pregap before tracks 2+.

    Track 1's pregap is the mandatory lead-in, which isn't part of capacity.
    """
    lengths = list(lengths)
    return sum(lengths) + max(len(lengths) - 1, 0) * pregap_sectors
