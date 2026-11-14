"""Read the CDJ's waveform records for the test disc and work out what it measures (Qt-free, numpy only).

`wavetest.py` makes a disc of known signals; after the disc has been played on the player, its card
file holds a 50-value (`W001`) and a 100-value (`W002`) record per track. This module regenerates the
audio, measures each column the way a player *might* (peak or RMS or average, of the left, the right or
their sum), and says which measurement the player's values follow, where the column edges fall, what
level each value stands for, and whether the scale is the same for every track.

A value of zero is both "silent" and "not played yet", so a record that is all zero is treated as not
played, and the report says which tracks were played.
"""

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from dubcrate import cdj_card as cc
from dubcrate.wavetest import FINE, RATE, Segment, TrackSpec, render

STATS = (
    "peak_lr",  # the louder channel's peak
    "peak_mono",  # the peak of (L+R)/2
    "rms_mono",
    "rms_lr",  # RMS over both channels
    "meanabs_mono",
    "meanabs_lr",
)
FLOOR_DB = -120.0


def to_db(x: np.ndarray | float) -> np.ndarray | float:
    return 20.0 * np.log10(np.maximum(x, 10 ** (FLOOR_DB / 20)))


def levels_of(record: cc.WaveRecord, fine: bool) -> np.ndarray:
    """A record's values as an array: 50 for `W001`, 100 (the nibbles, high first) for `W002`."""
    raw = np.frombuffer(record.data, dtype=np.uint8)
    if not fine:
        return raw.astype(int)
    out = np.zeros(2 * len(raw), dtype=int)
    out[0::2], out[1::2] = raw >> 4, raw & 15
    return out


def column_stats(
    audio: np.ndarray, columns: int, offset: float = 0.0
) -> dict[str, np.ndarray]:
    """Each statistic for each of `columns` equal stretches of the track (amplitudes, 1.0 = full scale).

    `offset` moves the grid later by that many seconds (a column then starts `offset` after its
    nominal start), to find where the player's columns really begin."""
    x = audio.astype(np.float32) / 32768.0
    mono = (x[:, 0] + x[:, 1]) / 2
    n = len(x)
    out = {name: np.zeros(columns) for name in STATS}
    for c in range(columns):
        a = max(0, round(c * n / columns + offset * RATE))
        b = min(n, round((c + 1) * n / columns + offset * RATE))
        if b <= a:
            continue
        m, lr = mono[a:b], x[a:b]
        out["peak_lr"][c] = np.abs(lr).max()
        out["peak_mono"][c] = np.abs(m).max()
        out["rms_mono"][c] = math.sqrt(float(np.mean(m.astype(np.float64) ** 2)))
        out["rms_lr"][c] = math.sqrt(float(np.mean(lr.astype(np.float64) ** 2)))
        out["meanabs_mono"][c] = float(np.mean(np.abs(m)))
        out["meanabs_lr"][c] = float(np.mean(np.abs(lr)))
    return out


def _ranks(a: np.ndarray) -> np.ndarray:
    order = np.argsort(a, kind="stable")
    ranks = np.empty(len(a))
    ranks[order] = np.arange(len(a))
    for value in np.unique(a):  # ties share their average rank
        tied = a == value
        ranks[tied] = ranks[tied].mean()
    return ranks


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Rank correlation, -1 to 1 (0 if either is constant)."""
    if len(x) < 3 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return 0.0
    rx, ry = _ranks(np.asarray(x, dtype=float)), _ranks(np.asarray(y, dtype=float))
    return float(np.corrcoef(rx, ry)[0, 1])


def discordance(
    stat_db: np.ndarray, levels: np.ndarray, tolerance_db: float = 0.25
) -> float:
    """Of the column pairs whose levels differ, the share whose statistic runs the other way (by more
    than `tolerance_db`): 0 means the levels follow the statistic exactly, 0.5 means no relation."""
    lo = levels[:, None] < levels[None, :]
    wrong = stat_db[:, None] > stat_db[None, :] + tolerance_db
    pairs = lo.sum()
    return float((lo & wrong).sum() / pairs) if pairs else 0.0


@dataclass
class Played:
    """One track's values from the card, and whether it was played."""

    coarse: np.ndarray | None  # 50 values
    fine: np.ndarray | None  # 100 values

    @property
    def played(self) -> bool:
        return self.coarse is not None and bool(self.coarse.any() or self.fine.any())


def records_by_track(file: cc.TrackFile) -> dict[int, Played]:
    coarse = {r.track: levels_of(r, False) for r in file.coarse if r.track}
    fine = {r.track: levels_of(r, True) for r in file.fine if r.track}
    return {
        t: Played(coarse.get(t), fine.get(t)) for t in sorted(set(coarse) | set(fine))
    }


def columns_inside(
    columns: int, seconds: float, offset: float, segment: Segment, margin: float = 0.0
) -> list[int]:
    """The columns that lie wholly inside a segment (with the grid moved by `offset`)."""
    width = seconds / columns
    return [
        c
        for c in range(columns)
        if segment.start - 1e-9 <= c * width + offset - margin
        and (c + 1) * width + offset + margin <= segment.end + 1e-9
    ]


def pearson(x: np.ndarray, y: np.ndarray) -> float:
    """Ordinary correlation (0 if either is constant)."""
    if np.ptp(x) == 0 or np.ptp(y) == 0:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


def best_offset(
    audio: np.ndarray, spec: TrackSpec, values: np.ndarray, columns: int
) -> tuple[float, float]:
    """The shift (seconds) of the column grid that makes the player's values follow the audio best,
    searched over plus and minus one column in eighths of a column, with the score (correlation of the
    values with the peak in dB). Only content with sharp edges, such as the bursts of track 3, can tell
    one shift from another; the smallest shift wins a tie."""
    width = spec.seconds / columns
    best = (0.0, -2.0)
    for step in sorted(range(-8, 9), key=abs):
        offset = step * width / 8
        stat = np.maximum(to_db(column_stats(audio, columns, offset)["peak_lr"]), -90.0)
        score = pearson(stat, values.astype(float))
        if score > best[1] + 1e-6:
            best = (offset, score)
    return best


# -- a stand-in player, to test the fitter -------------------------------------------------------------


def simulate_player(
    audio: np.ndarray,
    thresholds_db: Sequence[float],
    statistic: str,
    columns: int,
    offset: float = 0.0,
) -> np.ndarray:
    """What a player that measures `statistic` per column and counts how many of `thresholds_db` (in
    ascending order) the level reaches would record: values 0 to len(thresholds)."""
    stat = to_db(column_stats(audio, columns, offset)[statistic])
    return np.searchsorted(np.asarray(thresholds_db), stat, side="right").astype(int)


# -- the report ------------------------------------------------------------------------------------------


# tracks that tell the statistics apart: steps and ramps of levels, and shapes of equal RMS but different peaks
SCALE_TRACKS = (1, 2, 9, 14, 15, 16, 17, 5, 6, 10)
ALIGN_TRACKS = (
    3,
)  # sharp-edged content (bursts at known times) is what shows where the columns begin


@dataclass
class Fit:
    """What the analysis found, as data (the report is the same, as words)."""

    played: list[int]
    statistic: str  # the statistic the values follow best
    discordance: dict[str, float]
    offsets: dict[int, float]
    thresholds_db: list[float]  # estimated, level k (1..) begins at thresholds[k-1]
    scale: dict[
        int, tuple[float, float, int]
    ]  # level -> (lowest dB, highest dB, columns)


def analyse(tracks: list[TrackSpec], file: cc.TrackFile) -> tuple[Fit, list[str]]:
    """Compare the player's records with the audio: the fit, and the report in words."""
    got = records_by_track(file)
    lines = [
        "Tracks played (a record with any value set): "
        + ", ".join(str(t) for t, p in got.items() if p.played)
    ]
    missing = [
        t.number for t in tracks if not got.get(t.number) or not got[t.number].played
    ]
    if missing:
        lines.append("Not played, or all zero: " + ", ".join(str(m) for m in missing))
    audio = {
        t.number: render(t)
        for t in tracks
        if got.get(t.number) and got[t.number].played
    }
    specs = {t.number: t for t in tracks}

    # 1. where the columns fall
    offsets: dict[int, float] = {}
    for number in ALIGN_TRACKS:
        if number in audio and got[number].fine is not None:
            offset, score = best_offset(
                audio[number], specs[number], got[number].fine, FINE
            )
            offsets[number] = offset
            lines.append(
                f"Track {number}: the fine columns fit best shifted {offset:+.3f} s (correlation {score:.3f})"
            )
    shift = float(np.median(list(offsets.values()))) if offsets else 0.0
    if not offsets:
        lines.append(
            "The alignment track (3) was not played: the columns are taken to begin at each track's start"
        )

    # 2. which statistic the values follow: columns pooled from the scale tracks, so that columns of
    #    different tracks (a quiet-RMS pulse train against a louder-RMS sine) can contradict a statistic
    pooled: dict[str, list[np.ndarray]] = {name: [] for name in STATS}
    pooled_levels: list[np.ndarray] = []
    for number in SCALE_TRACKS:
        if number not in audio or got[number].fine is None:
            continue
        stats = column_stats(audio[number], FINE, shift)
        for name in STATS:
            pooled[name].append(to_db(stats[name]))
        pooled_levels.append(got[number].fine)
    mean_disc: dict[str, float] = {}
    if pooled_levels:
        levels_all = np.concatenate(pooled_levels)
        for name in STATS:
            mean_disc[name] = discordance(np.concatenate(pooled[name]), levels_all)
    ranking = sorted(mean_disc, key=mean_disc.get)
    lines.append(
        "Statistics, best first (share of column pairs, pooled over the scale tracks, that the values put the wrong way round): "
        + ", ".join(f"{s} {mean_disc[s]:.3f}" for s in ranking)
    )
    best = ranking[0] if ranking else "peak_lr"

    # 3. what each value stands for, on the best statistic, from the clean (interior) columns
    seen: dict[int, list[float]] = {}
    for number in (1, 2, 9, 14, 19, 20):
        if number not in audio or got[number].fine is None:
            continue
        spec = specs[number]
        stat = to_db(column_stats(audio[number], FINE, shift)[best])
        if number in (9, 14):  # ramps: every column is its own level
            usable = range(FINE)
        else:  # staircases: only columns wholly inside one step
            usable = sorted(
                {
                    c
                    for seg in spec.segments
                    for c in columns_inside(FINE, spec.seconds, shift, seg, 0.0)
                }
            )
        for c in usable:
            seen.setdefault(int(got[number].fine[c]), []).append(float(stat[c]))
    scale = {k: (min(v), max(v), len(v)) for k, v in sorted(seen.items())}
    thresholds: list[float] = []
    levels = sorted(scale)
    for lo_level, hi_level in itertools.pairwise(levels):
        thresholds.append((scale[lo_level][1] + scale[hi_level][0]) / 2)
    lines.append(f"Scale on {best} (dB re full scale), from the steps and ramps:")
    for k in levels:
        lo, hi, n = scale[k]
        lines.append(f"  value {k:2d}: {lo:7.1f} to {hi:7.1f} dB over {n:3d} columns")
    if thresholds:
        lines.append(
            "Estimated boundaries between values (dB): "
            + ", ".join(f"{t:.1f}" for t in thresholds)
        )

    # 4. every segment, so the other questions can be read off
    lines.append("")
    lines.append(
        "Per segment: the median fine value [lowest-highest], and the segment's own peak and RMS (dBFS)"
    )
    for number in sorted(audio):
        spec = specs[number]
        lines.append(f"-- track {number}: {spec.title}")
        stats = column_stats(audio[number], FINE, shift)
        for seg in spec.segments:
            cols = columns_inside(FINE, spec.seconds, shift, seg, 0.0)
            if not cols or got[number].fine is None:
                continue
            vals = got[number].fine[cols]
            peak = float(to_db(stats["peak_lr"][cols].max()))
            rms = float(to_db(np.median(stats["rms_lr"][cols])))
            lines.append(
                f"   {seg.label:48s} {int(np.median(vals)):2d} [{int(vals.min())}-{int(vals.max())}]   peak {peak:6.1f}  rms {rms:6.1f}"
            )
    fit = Fit(
        [t for t, p in got.items() if p.played],
        best,
        mean_disc,
        offsets,
        thresholds,
        scale,
    )
    return fit, lines
