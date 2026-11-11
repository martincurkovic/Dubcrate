from dubcrate.planning import fit_tracks, split_sequential


def test_fit_takes_everything_that_fits_in_order():
    accepted, rejected = fit_tracks([100, 100, 100], used=0, count=0, capacity=1000)
    assert (accepted, rejected) == ([0, 1, 2], [])


def test_fit_skips_a_misfit_but_keeps_going_by_default():
    accepted, rejected = fit_tracks([400, 700, 300], used=0, count=0, capacity=800)
    assert (accepted, rejected) == ([0, 2], [1])


def test_strict_fit_stops_at_the_first_misfit():
    accepted, rejected = fit_tracks(
        [400, 700, 300], used=0, count=0, capacity=800, strict=True
    )
    assert (accepted, rejected) == ([0], [1, 2])


def test_fit_counts_what_is_already_on_the_disc():
    accepted, rejected = fit_tracks([300, 300], used=500, count=2, capacity=1000)
    assert (accepted, rejected) == ([0], [1])


def test_fit_charges_a_pregap_before_every_track_but_the_first():
    # Empty disc: 100 + (100+50) + (100+50) = 400.
    accepted, _ = fit_tracks([100] * 5, used=0, count=0, capacity=400, pregap=50)
    assert accepted == [0, 1, 2]
    # Non-empty disc: even the first new track needs a gap.
    accepted, _ = fit_tracks([100], used=0, count=1, capacity=120, pregap=50)
    assert accepted == []


def test_fit_respects_the_track_limit():
    accepted, rejected = fit_tracks(
        [1] * 5, used=0, count=0, capacity=1000, max_tracks=3
    )
    assert (accepted, rejected) == ([0, 1, 2], [3, 4])


def test_fit_with_nothing_to_add():
    assert fit_tracks([], used=0, count=0, capacity=10) == ([], [])


def test_split_starts_a_new_disc_when_the_next_track_does_not_fit():
    groups, too_long = split_sequential([400, 400, 400, 400, 400], capacity=1000)
    assert groups == [[0, 1], [2, 3], [4]] and too_long == []


def test_split_keeps_order_and_never_reorders_to_pack_tighter():
    groups, _ = split_sequential([600, 600, 300], capacity=1000)
    assert groups == [[0], [1, 2]]  # the 300 stays after the second 600


def test_split_reports_tracks_longer_than_an_empty_disc():
    groups, too_long = split_sequential([500, 2000, 500], capacity=1000)
    assert groups == [[0, 2]] and too_long == [1]


def test_split_counts_pregaps():
    groups, _ = split_sequential([300, 300, 300], capacity=700, pregap=50)
    assert groups == [[0, 1], [2]]  # 300 + 350 = 650 fits, a third would be 1000


def test_split_respects_the_track_limit():
    groups, _ = split_sequential([1] * 7, capacity=1000, max_tracks=3)
    assert groups == [[0, 1, 2], [3, 4, 5], [6]]


def test_split_of_nothing_is_no_discs():
    assert split_sequential([], capacity=1000) == ([], [])
