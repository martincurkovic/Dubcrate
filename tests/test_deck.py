"""The deck's CUE, loops and quantise: pure decisions, no audio."""

import numpy as np
import pytest

from dubcrate.deck import AUTO_SIZES, Deck, size_label

BEAT = 0.5  # 120 bpm


@pytest.fixture
def deck():
    d = Deck()
    d.load(
        120.0, np.arange(1.0, 119.0, BEAT), 120.0
    )  # a grid from 1.0 s, every half second
    return d


def test_the_cue_point_starts_at_the_beginning_and_a_new_track_forgets_everything(deck):
    assert deck.cue_point == 0.0 and not deck.cue_set and deck.loop is None
    deck.cue_press(10.0, playing=False)
    deck.mark_in(5.0)
    deck.mark_out(7.0)
    deck.load(60.0, None, None)
    assert deck.cue_point == 0.0 and not deck.cue_set
    assert deck.loop is None and not deck.loop_active and deck.markers() == ()


def test_cue_while_playing_goes_back_to_the_cue_point(deck):
    assert deck.cue_press(40.0, playing=True) == "return"
    assert deck.cue_point == 0.0  # (the cue point itself does not move)


def test_cue_while_stopped_on_the_cue_point_previews_and_elsewhere_sets_it(deck):
    assert deck.cue_press(0.0, playing=False) == "preview"
    assert deck.cue_press(0.01, playing=False) == "preview"  # (within a hair of it)
    assert not deck.cue_set
    assert deck.cue_press(12.3, playing=False) == "set"
    assert deck.cue_point == 12.3 and deck.cue_set
    assert deck.cue_press(12.3, playing=False) == "preview"  # (now on the new one)


def test_quantise_snaps_to_the_nearest_beat_of_the_grid_and_off_it_does_not(deck):
    assert deck.snap(10.26) == 10.26
    deck.quantise = True
    assert deck.snap(10.26) == pytest.approx(10.5)
    assert deck.snap(10.24) == pytest.approx(10.0)
    # before the first beat (1.0 s) the grid goes on at its own spacing: beats at 0.5 and 0.0
    assert deck.snap(0.3) == pytest.approx(0.5)
    assert deck.snap(0.1) == pytest.approx(0.0)
    assert deck.snap(500.0) == 120.0  # (held to the track; the grid's reach is clipped)


def test_with_quantise_on_the_playhead_need_only_be_near_the_cue_points_beat_to_preview(
    deck,
):
    deck.quantise = True
    deck.cue_press(10.3, playing=False)  # the cue point is the beat at 10.5
    assert deck.cue_point == pytest.approx(10.5)
    assert (
        deck.cue_press(10.3, playing=False) == "preview"
    )  # (a fifth of a second off: still "on" it)
    assert deck.cue_press(10.74, playing=False) == "preview"
    assert (
        deck.cue_press(10.1, playing=False) == "set"
    )  # (the nearest beat is another one: a new cue point)
    assert deck.cue_point == pytest.approx(10.0)


def test_without_quantise_only_a_hair_off_the_cue_point_counts_as_on_it(deck):
    deck.cue_press(10.3, playing=False)
    assert deck.cue_press(10.3, playing=False) == "preview"
    assert deck.cue_press(10.5, playing=False) == "set"  # (a fifth of a second away)


def test_a_quantised_cue_is_set_on_the_beat(deck):
    deck.quantise = True
    deck.cue_press(20.4, playing=False)
    assert deck.cue_point == pytest.approx(20.5)


def test_quantise_uses_the_tempo_when_there_is_no_grid():
    deck = Deck()
    deck.load(60.0, None, 120.0)
    deck.quantise = True
    assert deck.snap(1.3) == pytest.approx(1.5)  # (counted from the start of the track)
    none = Deck()
    none.load(60.0, None, None)
    none.quantise = True
    assert none.snap(1.3) == 1.3  # (no tempo at all: nothing to snap to)


def test_an_auto_loop_runs_the_chosen_beats_from_the_playhead(deck):
    deck.auto_beats = 4
    assert deck.auto_loop(10.1) == (
        10.1,
        pytest.approx(12.1),
    )  # 4 beats = 2 s at 120 bpm
    assert deck.loop_active and deck.loop == (10.1, pytest.approx(12.1))


def test_an_auto_loop_starts_on_the_beat_when_quantise_is_on(deck):
    deck.quantise = True
    deck.auto_beats = 2
    start, end = deck.auto_loop(10.1)
    assert (start, end) == (pytest.approx(10.0), pytest.approx(11.0))


def test_an_auto_loop_follows_a_changing_tempo():
    deck = Deck()
    beats = np.concatenate(
        [np.arange(0.0, 4.0, 0.5), np.arange(4.0, 12.0, 0.25)]
    )  # slows... then doubles
    deck.load(20.0, beats, 120.0)
    deck.auto_beats = 4
    start, end = deck.auto_loop(
        2.0
    )  # beats 4 to 8 of the grid: 2.0, 2.5, 3.0, 3.5, then 4.0
    assert (start, end) == (pytest.approx(2.0), pytest.approx(4.0))
    start, end = deck.auto_loop(5.0)  # in the faster part: four beats are one second
    assert end - start == pytest.approx(1.0)


def test_an_auto_loop_that_would_run_past_the_end_or_has_no_tempo_is_refused(deck):
    deck.auto_beats = 32  # 16 s
    assert deck.auto_loop(110.0) is None and not deck.loop_active
    bare = Deck()
    bare.load(60.0, None, None)
    assert bare.auto_loop(5.0) is None


def test_resizing_a_loop_that_is_on_keeps_its_start_and_later_loops_use_the_size(deck):
    deck.auto_beats = 4
    deck.auto_loop(10.0)
    assert deck.resize_auto(8) == (10.0, pytest.approx(14.0))
    assert deck.auto_beats == 8 and deck.loop_active
    assert deck.resize_auto(0.5) == (10.0, pytest.approx(10.25))
    deck.exit_loop()
    assert deck.resize_auto(2) is None  # (no loop on: only the size changes)
    assert deck.auto_beats == 2 and deck.auto_loop(20.0) == (20.0, pytest.approx(21.0))
    deck.auto_loop(110.0)
    assert deck.resize_auto(32) is None and deck.loop_out == pytest.approx(
        111.0
    )  # (does not fit)


def test_manual_in_then_out_starts_a_loop_and_out_needs_an_in(deck):
    assert deck.mark_out(5.0) is None
    assert deck.mark_in(5.0) == 5.0
    assert deck.mark_out(5.0) is None  # (not after it)
    assert deck.mark_out(7.5) == (5.0, 7.5)
    assert deck.loop_active


def test_a_new_in_leaves_the_loop_and_drops_an_out_that_is_now_before_it(deck):
    deck.mark_in(5.0)
    deck.mark_out(7.0)
    deck.mark_in(6.0)  # still before the out
    assert not deck.loop_active and deck.loop == (6.0, 7.0)
    deck.mark_in(9.0)  # past it
    assert deck.loop is None and deck.loop_out is None


def test_reloop_leaves_a_loop_that_is_on_and_goes_back_into_the_last_one(deck):
    assert deck.reloop() is None  # (no loop yet: nothing)
    deck.mark_in(5.0)
    deck.mark_out(7.0)
    assert deck.reloop() is None and not deck.loop_active  # (was on: now off)
    assert deck.reloop() == (5.0, 7.0) and deck.loop_active  # (back in)


def test_markers_show_the_cue_point_the_loop_and_a_loop_that_is_off(deck):
    assert deck.markers() == ()
    deck.cue_press(12.0, playing=False)
    assert [(c.kind, c.time) for c in deck.markers()] == [("cue", 12.0)]
    deck.mark_in(20.0)
    assert [(c.kind, c.time) for c in deck.markers()] == [("cue", 12.0), ("cue", 20.0)]
    deck.mark_out(24.0)
    kinds = [(c.kind, c.time, c.end) for c in deck.markers()]
    assert kinds == [("cue", 12.0, None), ("loop", 20.0, 24.0)]
    deck.reloop()  # off: its two ends show
    assert [(c.kind, c.time) for c in deck.markers()] == [("cue", 12.0), ("cue", 20.0), ("cue", 24.0)]  # fmt: skip
    assert all(c.slot is None for c in deck.markers())  # (never taken for a hot cue)


def test_the_sizes_run_from_a_64th_of_a_beat_to_32_beats_and_have_labels():
    assert AUTO_SIZES == (1 / 64, 1 / 32, 1 / 16, 1 / 8, 1 / 4, 0.5, 1, 2, 4, 8, 16, 32)
    assert [size_label(b) for b in AUTO_SIZES] == [
        "1/64",
        "1/32",
        "1/16",
        "1/8",
        "1/4",
        "1/2",
        "1",
        "2",
        "4",
        "8",
        "16",
        "32",
    ]


def test_a_64th_of_a_beat_is_a_loop_even_at_a_fast_tempo(deck):
    deck.auto_beats = 1 / 64
    found = deck.auto_loop(10.0)  # the grid here is 120 bpm: 1/64 beat is 7.8 ms
    assert found is not None and found[1] - found[0] == pytest.approx(BEAT / 64)
    fast = Deck()
    fast.load(120.0, np.arange(1.0, 119.0, 0.25), 240.0)  # 240 bpm: 1/64 beat is 3.9 ms
    fast.auto_beats = 1 / 64
    assert fast.auto_loop(10.0) is not None


def test_a_saved_loop_is_turned_on_from_its_two_edges_without_snapping(deck):
    deck.quantise = True  # (the edges are where they were saved: not moved to a beat)
    assert deck.recall_loop(10.13, 12.77) == (10.13, 12.77)
    assert deck.loop == (10.13, 12.77) and deck.loop_active
    assert [(c.kind, c.time, c.end) for c in deck.markers()] == [("loop", 10.13, 12.77)]


def test_a_loop_that_is_too_short_or_past_the_end_or_before_the_start_is_not_turned_on(
    deck,
):
    assert deck.recall_loop(5.0, 5.0) is None
    assert deck.recall_loop(5.0, 130.0) is None  # (the track is 120 s)
    assert deck.recall_loop(-1.0, 4.0) is None
    assert deck.loop is None and not deck.loop_active


def test_recalling_a_loop_replaces_the_one_that_was_on(deck):
    deck.mark_in(5.0)
    deck.mark_out(7.0)
    assert deck.recall_loop(20.0, 24.0) == (20.0, 24.0)
    assert deck.loop == (20.0, 24.0) and deck.loop_active
