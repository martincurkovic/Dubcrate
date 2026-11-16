"""The bar and beat count beside the zoomed waveform's playhead."""

import numpy as np
import pytest

from dubcrate.barcount import bar_and_beat, bars_text
from dubcrate.ui.waveform_view import DetailView
from dubcrate.waveform import Waveform

TIMES = np.arange(0.0, 20.0, 0.5)  # 120 bpm
NUMBERS = np.tile(np.array([1, 2, 3, 4], dtype=np.int8), 10)


def test_the_bar_and_beat_are_counted_from_one_along_the_grid():
    assert bar_and_beat(TIMES, NUMBERS, 0.0) == (1, 1)
    assert bar_and_beat(TIMES, NUMBERS, 0.49) == (1, 1)  # (still in the first beat)
    assert bar_and_beat(TIMES, NUMBERS, 0.5) == (1, 2)
    assert bar_and_beat(TIMES, NUMBERS, 1.5) == (1, 4)
    assert bar_and_beat(TIMES, NUMBERS, 2.0) == (2, 1)
    assert bar_and_beat(TIMES, NUMBERS, 5.3) == (3, 3)


def test_it_matches_rbxports_example_bar_13_beat_3_at_132_bpm_and_23_seconds():
    beat = 60 / 132
    times = np.arange(0.0, 60.0, beat)
    numbers = np.resize(np.array([1, 2, 3, 4], dtype=np.int8), len(times))
    assert bars_text(times, numbers, 23.1) == "13.3 Bars"


def test_before_the_first_beat_or_with_no_grid_there_is_nothing():
    late = TIMES + 1.0
    assert bar_and_beat(late, NUMBERS, 0.4) is None
    assert bar_and_beat(np.zeros(0), np.zeros(0, dtype=np.int8), 3.0) is None
    assert bars_text(np.zeros(0), np.zeros(0, dtype=np.int8), 3.0) is None
    assert bar_and_beat(TIMES, NUMBERS[:5], 3.0) is None  # (a grid that doesn't add up)


def test_a_grid_that_starts_mid_bar_counts_that_part_bar_as_bar_one():
    numbers = np.roll(NUMBERS, -2)  # starts on beat 3
    assert bar_and_beat(TIMES, numbers, 0.0) == (1, 3)
    assert bar_and_beat(TIMES, numbers, 1.0) == (2, 1)


def test_past_the_last_beat_it_stays_on_the_last_one():
    assert bar_and_beat(TIMES, NUMBERS, 500.0) == (10, 4)


def test_a_beat_number_that_is_not_1_to_4_gives_nothing():
    numbers = NUMBERS.copy()
    numbers[3] = 0
    assert bar_and_beat(TIMES, numbers, 1.5) is None


def make_detail(beats=True):
    detail = DetailView()
    detail.resize(400, detail.HEIGHT)
    wave = Waveform(np.full((600, 3), 0.5, dtype=np.float32), 150.0)
    detail.set_track(wave, TIMES if beats else None, NUMBERS if beats else None)
    return detail


def test_the_zoomed_waveform_says_the_bar_and_beat_and_follows_the_playhead(qapp):
    detail = make_detail()
    detail.set_position(2.5)
    assert detail.bar_text() == "2.2 Bars"
    detail.set_position(5.3)
    assert detail.bar_text() == "3.3 Bars"
    detail.close()


def test_a_track_with_no_beat_grid_shows_no_counter(qapp):
    detail = make_detail(beats=False)
    detail.set_position(2.5)
    assert detail.bar_text() is None
    detail.close()


@pytest.mark.parametrize("scheme", ["dark", "light", "system6"])
def test_the_counter_is_drawn_left_of_the_playhead_in_every_look(qapp, scheme):
    from dubcrate.ui import theme

    theme.apply_to_app(qapp, scheme)
    detail = make_detail()
    detail.set_position(2.5)
    detail.show()
    with_counter = detail.grab().toImage()
    detail.set_track(detail._waveform)  # (the same waveform, no grid: no counter)
    detail.set_position(2.5)
    without = detail.grab().toImage()
    centre = detail.width() // 2
    differs = [
        with_counter.pixel(x, y) != without.pixel(x, y)
        for x in range(centre - 90, centre - 4)
        for y in range(2, 18)
    ]
    assert any(differs)
    detail.close()
