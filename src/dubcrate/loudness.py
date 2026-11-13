"""Level matching for audio CDs: measure a track's loudness, work out one gain for it (Qt-free).

The idea is the one DJs want from a normaliser and nothing more: tracks on a disc play at about the
same level. So each track gets ONE fixed gain, `target - measured loudness` (integrated loudness in
LUFS, ITU-R BS.1770 / EBU R128), applied as a plain volume change in `render.render_track`. There is no
compression and no limiter: dynamics, transients and groove are untouched. A gain is never allowed to
push the true peak over `PEAK_CEILING_DB`, so a quiet track with big peaks is lifted less than its
target asks for and is reported as held back. Peak normalising is deliberately not offered (one stray
spike leaves a loud track quiet), nor `loudnorm`'s dynamic mode or `dynaudnorm` (they change dynamics).

Measuring decodes the track the way `render_track` will (stereo, 44.1 kHz), so a mono file is measured
as the stereo it will become. It runs ffmpeg's `ebur128` filter, at hundreds of times real time.
"""

import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from dubcrate.render import RenderError, _require

DEFAULT_TARGET_LUFS = (
    -10
)  # loud masters come down a little, quiet tracks come up as far as their peaks allow
TARGET_RANGE = (-24, -6)  # what the target can be set to
# A gain never lifts the true peak above this (dBTP). Not 0: the true-peak reading is an estimate (about a tenth
# of a dB), the 16-bit conversion adds a bit of dither, a DAC can overshoot between samples, and a track decoded from
# a lossy file can already have overs. -0.3 is the usual mastering ceiling.
PEAK_CEILING_DB = -0.3
MAX_GAIN_DB = 12.0  # nor lifts anything more than this, so a very quiet file's noise isn't blown up
SILENT_LUFS = (
    -70.0
)  # at or below this a track is silence (ebur128's absolute gate): no gain


@dataclass(frozen=True)
class Loudness:
    """What was measured for one track."""

    lufs: float  # integrated loudness
    true_peak_db: float  # dBTP


@dataclass(frozen=True)
class Gain:
    """The gain for one track: what is applied, what the target wanted, and whether it was held back."""

    db: float
    wanted_db: float

    @property
    def limited(self) -> bool:
        return self.db < self.wanted_db - 0.05


def parse_ebur128(output: str) -> Loudness | None:
    """The loudness and true peak from ebur128's closing summary, or None if it has none
    (nothing was decoded) or the file is silent."""
    integrated = re.search(
        r"Integrated loudness:\s*\n\s*I:\s*(-?[\d.]+|-inf)\s*LUFS", output
    )
    peak = re.search(r"True peak:\s*\n\s*Peak:\s*(-?[\d.]+|-inf)\s*dBFS", output)
    if not integrated or not peak:
        return None
    if "inf" in integrated.group(1) or "inf" in peak.group(1):
        return Loudness(SILENT_LUFS, SILENT_LUFS)
    return Loudness(float(integrated.group(1)), float(peak.group(1)))


def measure(path: Path) -> Loudness:
    """Measure the file at `path` (it is only read). Raises `RenderError`, or `ToolMissingError`
    (a `RenderError`) when ffmpeg can't be found."""
    cmd = [
        _require("ffmpeg"), "-nostdin", "-hide_banner", "-nostats", "-v", "info",
        "-i", str(path), "-map", "0:a:0", "-vn",
        # the same decode as render_track: 44.1 kHz stereo
        "-af", "aresample=44100,aformat=channel_layouts=stereo,ebur128=peak=true:framelog=quiet",
        "-f", "null", "-",
    ]  # fmt: skip
    proc = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
    )
    found = parse_ebur128(proc.stderr)
    if proc.returncode != 0 or found is None:
        detail = proc.stderr.strip().splitlines()
        raise RenderError(
            f"couldn't measure the loudness of {path}: {detail[-1] if detail else 'no audio decoded'}"
        )
    return found


def gain_for(loud: Loudness, target_lufs: float) -> Gain:
    """The gain that brings `loud` to `target_lufs` without the true peak passing the ceiling or the
    gain passing `MAX_GAIN_DB`. A silent track gets none."""
    if loud.lufs <= SILENT_LUFS:
        return Gain(0.0, 0.0)
    wanted = target_lufs - loud.lufs
    headroom = PEAK_CEILING_DB - loud.true_peak_db
    # A track that wants lifting is lifted as far as headroom allows, but never turned DOWN for lack of
    # it (one whose peaks already pass the ceiling just stays as it is); one that wants lowering is lowered.
    db = max(0.0, min(wanted, headroom, MAX_GAIN_DB)) if wanted > 0 else wanted
    return Gain(round(db, 2), round(wanted, 2))


def describe_gains(gains: Iterable[Gain], unmeasured: int = 0) -> str:
    """One line for the burn window: the range of gains and what could not be done."""
    gains = list(gains)
    if not gains:
        return "No tracks were measured."
    low, high = min(g.db for g in gains), max(g.db for g in gains)
    parts = [
        f"gains {low:+.1f} to {high:+.1f} dB" if low != high else f"gain {low:+.1f} dB",
        f"across {len(gains)} track{'s' if len(gains) != 1 else ''}",
    ]
    held = sum(g.limited for g in gains)
    text = " ".join(parts)
    if held:
        text += f" · {held} held back by peak headroom"
    if unmeasured:
        text += f" · {unmeasured} not measured (left as they are)"
    return text
