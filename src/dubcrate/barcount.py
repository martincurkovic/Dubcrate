"""Where the playhead is in the beat grid, as bar and beat (pure, no Qt): the "13.3 Bars" beside the zoomed waveform's playhead."""

import numpy as np


def bar_and_beat(
    beat_times: np.ndarray, beat_numbers: np.ndarray, position: float
) -> tuple[int, int] | None:
    """The bar and the beat (1 to 4) the playhead is in, counting bars from 1 along the grid, as rekordbox does.

    The beat is the last one at or before `position`, and its number is the grid's own (1 is the first beat of a
    bar); a bar is counted at each beat 1, and a grid that starts part-way through a bar counts that start as bar 1.
    None before the first beat, with no grid, or when the grid's numbers are unusable. A stretch of the track past
    the last beat stays on its last beat.
    """
    n = len(beat_times)
    if n == 0 or len(beat_numbers) != n:
        return None
    index = int(np.searchsorted(beat_times, position + 1e-9, side="right")) - 1
    if index < 0:
        return None
    beat = int(beat_numbers[index])
    if beat < 1:
        return None
    bars = int(np.count_nonzero(beat_numbers[: index + 1] == 1))
    if beat_numbers[0] != 1:
        bars += 1  # (the part-bar the grid starts in)
    return max(bars, 1), beat


def bars_text(
    beat_times: np.ndarray, beat_numbers: np.ndarray, position: float
) -> str | None:
    """`13.3 Bars` (bar 13, beat 3), or None when there is nothing to say."""
    found = bar_and_beat(beat_times, beat_numbers, position)
    return None if found is None else f"{found[0]}.{found[1]} Bars"
