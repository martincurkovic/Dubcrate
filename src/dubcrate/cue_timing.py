"""Where rekordbox's times and the audio on a disc part company (Qt-free, reads files only).

rekordbox times an MP3 from the first byte of its first frame. ffmpeg, which renders the audio that is burned
(`render.render_track`), does not: it drops the Info / Xing frame at the start (a silent frame of 1152 samples
that LAME puts there) and the encoder and decoder delay its tag records (`delay + 529` samples). rekordbox
keeps the delay in its timeline, and for a file LAME wrote the Info frame too, but not for one ffmpeg wrote. So
a time from rekordbox (a cue, a beat, a loop) is **later than the same place in the audio on the disc by
`lead_ms(file)`**: about 51 ms for the usual LAME file, 25 ms for one ffmpeg made, none for a file with no
Info / Xing frame (**measured 2026-11-23 on 700 of the owner's MP3s: the median is right in every group, 90%
of the LAME files within 6 ms of it; the rare encoders are left alone**; `dev_docs/cue-timing.md`). Other
formats are taken as having no lead: not measured.

Dubcrate's cues are in **audio time** (the time the player, the preview and the card use): `to_audio` converts
rekordbox's on the way in, and `rekordbox_ms` the way out (`rekordbox_write`).
"""

import logging
import os
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from dubcrate.analysis import Cue

log = logging.getLogger(__name__)

HEAD_BYTES = (
    4096  # the first frame is well inside this, once an ID3v2 tag has been skipped
)
_MPEG1_RATES = (44100, 48000, 32000)
_RATE_DIVISOR = {3: 1, 2: 2, 0: 4}  # version bits: MPEG1, MPEG2, MPEG2.5
_DECODER_DELAY = (
    529  # what ffmpeg adds to the encoder delay when it trims (`start_pad + 528 + 1`)
)


def lead_samples(head: bytes) -> tuple[int, int]:
    """(samples, sample rate) rekordbox's timeline is ahead of ffmpeg's for an MP3 starting `head` (its first
    bytes after any ID3v2 tag). (0, 0): no Info / Xing frame, so no lead."""
    if len(head) < 4 or head[0] != 0xFF or head[1] & 0xE0 != 0xE0:
        return 0, 0
    version = (head[1] >> 3) & 3
    if version == 1 or (head[1] >> 1) & 3 != 1:  # reserved version, or not Layer III
        return 0, 0
    rate_index = (head[2] >> 2) & 3
    if rate_index == 3:
        return 0, 0
    rate = _MPEG1_RATES[rate_index] // _RATE_DIVISOR[version]
    mono = (head[3] >> 6) == 3
    side = (17 if mono else 32) if version == 3 else (9 if mono else 17)
    tag = 4 + (2 if not head[1] & 1 else 0) + side  # (a CRC adds two bytes)
    if head[tag : tag + 4] not in (b"Xing", b"Info"):
        return 0, 0
    frame = 1152 if version == 3 else 576
    flags = int.from_bytes(head[tag + 4 : tag + 8], "big")
    lame = tag + 8 + 4 * (flags & 1) + 4 * bool(flags & 2) + 100 * bool(flags & 4) + 4 * bool(flags & 8)  # fmt: skip
    maker = head[lame : lame + 4]
    if maker not in (b"LAME", b"Lavc", b"Lavf") or len(head) < lame + 24:
        return (
            0,
            0,
        )  # an Info frame with no delay we can read, or by an encoder we haven't measured: leave it
    packed = head[lame + 21 : lame + 24]
    delay = (packed[0] << 4) | (packed[1] >> 4)
    if (
        not delay
    ):  # (a handful of files, which did not agree with each other: left alone)
        return 0, 0
    # rekordbox counts a LAME file's Info frame as well (an ffmpeg file's it leaves out); both count the delay.
    counted = frame if maker == b"LAME" else 0
    return counted + delay + _DECODER_DELAY, rate


def _head(path: Path) -> bytes:
    with path.open("rb") as handle:
        first = handle.read(10)
        skip = 0
        if first[:3] == b"ID3" and len(first) == 10:
            skip = 10 + (
                ((first[6] & 0x7F) << 21)
                | ((first[7] & 0x7F) << 14)
                | ((first[8] & 0x7F) << 7)
                | (first[9] & 0x7F)
            )
        handle.seek(skip)
        data = handle.read(HEAD_BYTES)
    start = data.find(b"\xff")
    while 0 <= start < len(data) - 1 and data[start + 1] & 0xE0 != 0xE0:
        start = data.find(b"\xff", start + 1)
    return data[start:] if start >= 0 else b""


_held: dict[tuple[str, int, int], int] = {}


def lead_ms(path: str | os.PathLike[str]) -> int:
    """How many milliseconds later rekordbox's time is than the audio ffmpeg renders for this file (0 for any
    file that isn't an MP3 with an Info / Xing frame, or that can't be read: never raises). Kept per file
    until it changes."""
    where = Path(path)
    if where.suffix.lower() != ".mp3":
        return 0
    try:
        info = where.stat()
        key = (str(where), info.st_size, info.st_mtime_ns)
        if key not in _held:
            samples, rate = lead_samples(_head(where))
            _held[key] = round(samples * 1000 / rate) if rate else 0
        return _held[key]
    except OSError:
        return 0


def to_audio(cues: Sequence[Cue], lead: int) -> list[Cue]:
    """rekordbox's `cues` in audio time: every time (and a loop's end) `lead` milliseconds earlier, none before 0."""
    if not lead:
        return list(cues)
    shift = lead / 1000.0
    return [
        replace(
            c,
            time=max(0.0, c.time - shift),
            end=None if c.end is None else max(0.0, c.end - shift),
        )
        for c in cues
    ]


def rekordbox_ms(audio_ms: int, lead: int) -> int:
    """A time in audio time as rekordbox has it."""
    return audio_ms + lead
