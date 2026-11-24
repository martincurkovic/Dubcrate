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
from dataclasses import dataclass, replace
from pathlib import Path
from typing import NamedTuple

from dubcrate.analysis import Analysis, Cue, Phrase
from dubcrate.waveform import Waveform

log = logging.getLogger(__name__)

HEAD_BYTES = (
    4096  # the first frame is well inside this, once an ID3v2 tag has been skipped
)
_MPEG1_RATES = (44100, 48000, 32000)
_RATE_DIVISOR = {3: 1, 2: 2, 0: 4}  # version bits: MPEG1, MPEG2, MPEG2.5
_DECODER_DELAY = (
    529  # what ffmpeg adds to the encoder delay when it trims (`start_pad + 528 + 1`)
)


class _Tag(NamedTuple):
    rate: int
    frame: int  # samples in a frame: the size of the Info frame itself
    maker: bytes  # "LAME", "Lavc", "Lavf" or what else the tag says (4 bytes, empty if it has none)
    delay: (
        int | None
    )  # the encoder delay it records, None when the tag has no extension to read it from


def _tag(head: bytes) -> _Tag | None:
    """What the first frame says about itself, or None if it is not an MP3 frame with an Info / Xing tag."""
    if len(head) < 4 or head[0] != 0xFF or head[1] & 0xE0 != 0xE0:
        return None
    version = (head[1] >> 3) & 3
    if version == 1 or (head[1] >> 1) & 3 != 1:  # reserved version, or not Layer III
        return None
    rate_index = (head[2] >> 2) & 3
    if rate_index == 3:
        return None
    rate = _MPEG1_RATES[rate_index] // _RATE_DIVISOR[version]
    mono = (head[3] >> 6) == 3
    side = (17 if mono else 32) if version == 3 else (9 if mono else 17)
    tag = 4 + (2 if not head[1] & 1 else 0) + side  # (a CRC adds two bytes)
    if head[tag : tag + 4] not in (b"Xing", b"Info"):
        return None
    frame = 1152 if version == 3 else 576
    flags = int.from_bytes(head[tag + 4 : tag + 8], "big")
    lame = tag + 8 + 4 * (flags & 1) + 4 * bool(flags & 2) + 100 * bool(flags & 4) + 4 * bool(flags & 8)  # fmt: skip
    maker = head[lame : lame + 4]
    if len(head) < lame + 24:
        return _Tag(rate, frame, maker, None)
    packed = head[lame + 21 : lame + 24]
    return _Tag(rate, frame, maker, (packed[0] << 4) | (packed[1] >> 4))


_KNOWN_MAKERS = (b"LAME", b"Lavc", b"Lavf")


def lead_samples(head: bytes, player: bool = False) -> tuple[int, int]:
    """(samples, sample rate) rekordbox's timeline is ahead of ffmpeg's for an MP3 starting `head` (its first
    bytes after any ID3v2 tag), or with `player`, the CDJ's. (0, 0): no Info / Xing frame, so no lead.

    The player counts the Info frame for any file that has one, an ffmpeg-made file too (rekordbox leaves that
    one out): **measured 2026-11-24, one file each**: a LAME file's cue, shifted by 51 ms, sounded right on the
    CDJ; an ffmpeg file's, shifted by 25 ms, sounded 5 frames early, about the Info frame (26 ms) and the
    player's rounding down to an MP3 frame (up to 26 ms) short."""
    found = _tag(head)
    if found is None or found.maker not in _KNOWN_MAKERS or found.delay is None:
        return (
            0,
            0,
        )  # no Info frame, or one with no delay we can read or by an encoder we haven't measured: leave it
    if (
        not found.delay
    ):  # (a handful of files, which did not agree with each other: left alone)
        return 0, 0
    # rekordbox counts a LAME file's Info frame as well (an ffmpeg file's it leaves out); both count the delay.
    counted = found.frame if found.maker == b"LAME" or player else 0
    return counted + found.delay + _DECODER_DELAY, found.rate


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


_held: dict[tuple[str, int, int, bool], int] = {}


_HELD_LIMIT = 20000  # files remembered at once (a changed file leaves a dead entry): then start again


def lead_or_none(path: str | os.PathLike[str], player: bool = False) -> int | None:
    """`lead_ms`, but None for an MP3 that can't be read just now (a drive that is unplugged): a caller that
    would write a time must not take that for "no lead". Kept per file until it changes."""
    where = Path(path)
    if where.suffix.lower() != ".mp3":
        return 0
    try:
        info = where.stat()
        key = (str(where), info.st_size, info.st_mtime_ns, player)
        if key not in _held:
            if len(_held) >= _HELD_LIMIT:
                _held.clear()
            samples, rate = lead_samples(_head(where), player)
            _held[key] = round(samples * 1000 / rate) if rate else 0
        return _held[key]
    except OSError:
        return None


def lead_ms(path: str | os.PathLike[str]) -> int:
    """How many milliseconds later rekordbox's time is than the audio ffmpeg renders for this file (0 for any
    file that isn't an MP3 with an Info / Xing frame, or that can't be read: never raises)."""
    return lead_or_none(path) or 0


def player_lead_ms(path: str | os.PathLike[str]) -> int:
    """How many milliseconds later the CDJ's time is than the audio for this file: `lead_ms`, with the Info
    frame counted for an ffmpeg-made file too (0 for a file with no Info frame, or one that can't be read)."""
    return lead_or_none(path, player=True) or 0


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


def analysis_to_audio(analysis: Analysis, lead: int) -> Analysis:
    """All of rekordbox's analysis of a track in audio time, so what the player draws and snaps to (the beat grid
    Q quantises on, the bar counter, beat jump, phrases, the detail waveform, the cues) lines up with the sound.
    The detail waveform loses its first `lead` milliseconds a whole column (1/150 s) at a time, so it can be up
    to 3 ms off; the whole-track overview is left as it is (50 ms is a fraction of a column of it)."""
    if not lead:
        return analysis
    shift = lead / 1000.0
    wave = analysis.waveform
    if wave is not None and wave.columns_per_second:
        drop = round(shift * wave.columns_per_second)
        wave = Waveform(wave.bands[drop:], wave.columns_per_second)
    return replace(
        analysis,
        waveform=wave,
        beat_times=analysis.beat_times - shift,
        cues=tuple(to_audio(analysis.cues, lead)),
        phrases=tuple(
            Phrase(max(0.0, p.start - shift), max(0.0, p.end - shift), p.label)
            for p in analysis.phrases
        ),
    )


def rekordbox_ms(audio_ms: int, lead: int) -> int:
    """A time in audio time as rekordbox has it."""
    return audio_ms + lead


@dataclass(frozen=True)
class Timing:
    """What `lead_ms` makes of a file and why, for `dubcrate cues timing`."""

    lead_ms: int
    group: str  # "lame", "ffmpeg", "no-info", "left-alone", "not-mp3" or "unreadable"
    note: str
    sure: bool  # the rule was measured on many files and matched them closely


def describe(path: str | os.PathLike[str]) -> Timing:
    """The offset for a file, which group of the measurements it falls in, and how far to trust it."""
    where = Path(path)
    if where.suffix.lower() != ".mp3":
        return Timing(0, "not-mp3", "not an MP3: formats other than MP3 were not measured, no offset", True)  # fmt: skip
    try:
        head = _head(where)
    except OSError:
        return Timing(0, "unreadable", "the file can't be read just now", False)
    found = _tag(head)
    if found is None:
        return Timing(0, "no-info", "no Info / Xing frame: rekordbox and the audio agree (measured: within 4 ms)", True)  # fmt: skip
    maker = found.maker.decode("latin1").strip() or "?"
    if found.maker not in _KNOWN_MAKERS or found.delay is None or not found.delay:
        why = (
            "encoder not measured"
            if found.maker not in _KNOWN_MAKERS
            else "no usable delay in its tag"
        )
        return Timing(0, "left-alone", f"{maker}, {why}: files like this did not agree with each other, so no offset", False)  # fmt: skip
    ms = lead_ms(where)
    if found.maker == b"LAME":
        return Timing(ms, "lame", f"LAME, delay {found.delay}: Info frame and delay both count (measured on about 400 files, 90% within 6 ms)", True)  # fmt: skip
    return Timing(ms, "ffmpeg", f"{maker}, delay {found.delay}: only the delay counts (measured: about 80% of these; the rest behave like LAME ones, about 25 ms more)", False)  # fmt: skip
