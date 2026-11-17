"""Which TOC may be recorded for a playlist: same track count, track lengths near the planned ones."""

import pytest

from dubcrate import cdj_card as cc
from dubcrate import toc_manager as tm
from dubcrate.library import Library, TrackRecord

LENGTHS = [20000, 25000, 30000, 15000]


def planned(gap=0):
    return cc.toc_from_lengths(LENGTHS, gap)


def test_a_toc_the_drive_laid_out_a_frame_late_is_accepted():
    starts, lead_out = planned()
    late = ([starts[0], *(s + 1 for s in starts[1:])], lead_out + 1)
    assert tm.candidate_problems(planned(), late) == []


def test_a_disc_burned_with_two_second_gaps_is_accepted_although_it_ends_far_later():
    assert tm.candidate_problems(planned(), planned(150)) == []


def test_a_disc_with_gaps_longer_than_the_limit_is_refused_and_says_where():
    problems = tm.candidate_problems(planned(), planned(150 * 3))
    assert "too different" in problems[0] and any("track 1 is" in p for p in problems)


def test_a_different_track_count_is_refused():
    problems = tm.candidate_problems(planned(), cc.toc_from_lengths(LENGTHS[:3]))
    assert "3 tracks" in problems[0] and "4" in problems[0]


def test_a_different_disc_with_the_same_number_of_tracks_is_refused():
    other = cc.toc_from_lengths([20000 + 900, 25000, 30000 - 900, 15000])  # 12 s off
    assert tm.candidate_problems(planned(), other)


def test_a_track_a_little_shorter_is_accepted_but_not_much():
    near = cc.toc_from_lengths([LENGTHS[0] - 30, *LENGTHS[1:]])
    assert tm.candidate_problems(planned(), near) == []
    far = cc.toc_from_lengths([LENGTHS[0] - 100, *LENGTHS[1:]])
    assert tm.candidate_problems(planned(), far)


@pytest.fixture
def lib():
    with Library.open(":memory:") as library:
        yield library


def disc_of(lib, lengths=LENGTHS, measured=True):
    lib.replace_source(
        "folder", "/f", "S",
        [TrackRecord(ext_id=str(n), path=f"/x/{n}.flac", title=str(n), duration=n + 1.0) for n in range(len(lengths))],
    )  # fmt: skip
    ids = [t.id for t in lib.tracks(sort="path")]
    if measured:
        for track_id, sectors in zip(ids, lengths, strict=True):
            lib.set_cd_sectors(track_id, sectors)
    disc_id = lib.create_disc("D")
    lib.add_to_disc(disc_id, ids)
    return disc_id


def test_record_keeps_a_plausible_toc_as_manual(lib):
    disc_id = disc_of(lib)
    starts, lead_out = planned()
    late = ([starts[0], *(s + 1 for s in starts[1:])], lead_out + 1)
    tm.record(lib, disc_id, late)
    [known] = lib.disc_tocs(disc_id)
    assert (
        known.source == "manual" and known.actual == late and known.planned == planned()
    )


def test_record_refuses_another_disc_and_keeps_nothing(lib):
    disc_id = disc_of(lib)
    with pytest.raises(tm.TocError, match="too different"):
        tm.record(lib, disc_id, cc.toc_from_lengths([5000, 5000, 5000, 5000]))
    assert lib.disc_tocs(disc_id) == []


def test_a_disc_with_unmeasured_tracks_cant_be_checked(lib):
    disc_id = disc_of(lib, measured=False)
    with pytest.raises(tm.TocError, match="no measured length"):
        tm.record(lib, disc_id, planned())


def test_a_new_toc_drops_the_records_of_a_playlist_that_has_since_changed(lib):
    disc_id = disc_of(lib)
    old = ([150, 20150, 45150, 75150], 90150)
    lib.add_disc_toc(disc_id, ([150, 1, 2, 3], 4), old)  # made for another plan
    tm.record(lib, disc_id, planned())
    assert [k.actual for k in lib.disc_tocs(disc_id)] == [planned()]
