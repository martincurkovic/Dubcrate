"""Beat jump: move a number of beats forward or back, along the track's beat grid."""

import numpy as np


def jump_time(beat_times: np.ndarray, position: float, beats: float) -> float | None:
    """Where `beats` beats on from `position` (negative: back) is, in seconds.

    The distance is measured in beats of the grid, so a tempo change in the track is
    followed, and the place within the beat is kept, so the jump lands in phase.
    Outside the grid it carries on at the first or last beat's spacing. The result may
    be negative or past the end: the caller clamps it. None if the grid is too small
    (or too broken) to have a tempo.
    """
    n = len(beat_times)
    if n < 2:
        return None
    first = float(beat_times[1] - beat_times[0])
    last = float(beat_times[-1] - beat_times[-2])
    if first <= 0 or last <= 0:
        return None
    index = np.arange(n, dtype=np.float64)
    if position <= beat_times[0]:
        at = (position - beat_times[0]) / first
    elif position >= beat_times[-1]:
        at = (n - 1) + (position - beat_times[-1]) / last
    else:
        at = float(np.interp(position, beat_times, index))
    target = at + beats
    if target <= 0:
        return float(beat_times[0] + target * first)
    if target >= n - 1:
        return float(beat_times[-1] + (target - (n - 1)) * last)
    return float(np.interp(target, index, beat_times))
