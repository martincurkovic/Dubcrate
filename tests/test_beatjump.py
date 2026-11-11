import numpy as np
import pytest

from dubcrate.beatjump import jump_time

# 120 BPM for 8 beats (0.5 s apiece), then the tempo drops to 100 BPM (0.6 s apiece)
STEADY = np.arange(0, 20) * 0.5
CHANGING = np.concatenate([np.arange(0, 8) * 0.5, 3.5 + np.arange(1, 8) * 0.6])


def test_jumping_forward_moves_by_that_many_beats():
    assert jump_time(STEADY, 2.0, 4) == pytest.approx(4.0)
    assert jump_time(STEADY, 2.0, 1) == pytest.approx(2.5)


def test_jumping_back_moves_the_other_way():
    assert jump_time(STEADY, 5.0, -4) == pytest.approx(3.0)


def test_the_position_within_a_beat_is_kept_so_the_groove_stays_in_phase():
    assert jump_time(STEADY, 2.2, 4) == pytest.approx(4.2)  # not snapped to the beat
    assert jump_time(STEADY, 2.2, -4) == pytest.approx(0.2)


def test_a_tempo_change_in_the_grid_is_followed():
    # beat 4 is at 2.0 s; four beats on is beat 8, the first at the slower tempo: 3.5 + 0.6
    assert jump_time(CHANGING, 2.0, 4) == pytest.approx(4.1)
    # from beat 7 (3.5 s), four beats on is beat 11: four 0.6 s steps later
    assert jump_time(CHANGING, 3.5, 4) == pytest.approx(3.5 + 4 * 0.6)
    # and back across the change again
    assert jump_time(CHANGING, 4.7, -4) == pytest.approx(2.5)  # beat 9 back to beat 5


def test_before_the_first_beat_and_after_the_last_it_carries_on_at_the_end_tempo():
    assert jump_time(STEADY, 0.0, -4) == pytest.approx(-2.0)  # the caller clamps
    last = STEADY[-1]
    assert jump_time(STEADY, last, 4) == pytest.approx(last + 2.0)
    assert jump_time(STEADY, -1.0, 4) == pytest.approx(1.0)  # starting before the grid


def test_a_grid_too_small_to_have_a_tempo_gives_nothing():
    assert jump_time(np.zeros(0), 1.0, 4) is None
    assert jump_time(np.array([1.0]), 1.0, 4) is None
    assert jump_time(np.array([1.0, 1.0, 1.0]), 1.0, 4) is None  # no spacing
