from dubcrate.library.models import BurnRef, DiscRef, Track, TrackUsage
from dubcrate.ui.overlaps import MAX_LISTED, overlap_message


def track(i: int, artist: str = "A") -> Track:
    return Track(
        id=i, source_id=1, ext_id=str(i), path=f"/{i}", title=f"T{i}", artist=artist
    )


def test_nothing_used_means_no_message():
    assert overlap_message([track(1)], {}, 1) is None


def test_names_each_place_a_track_is_used():
    usage = {
        1: TrackUsage(
            (DiscRef(1, "Here"), DiscRef(2, "Other")),
            (BurnRef(5, "Friday", "2026-11-01T20:00:00+00:00"),),
        )
    }
    text = overlap_message([track(1), track(2)], usage, 1)
    assert text is not None
    assert text.startswith("1 of the 2 tracks are already used:")
    assert "A - T1: already on this disc; planned on “Other”;" in text
    assert "burned on “Friday” (2026-11-01)" in text
    assert "T2" not in text
    assert text.endswith("Add anyway?")


def test_a_single_track_reads_naturally_and_no_disc_means_nothing_is_here():
    usage = {1: TrackUsage((DiscRef(2, "Other"),))}
    text = overlap_message([track(1, artist="")], usage, None)
    assert text is not None
    assert text.startswith("This track is already used:")
    assert "• T1: planned on “Other”" in text


def test_long_lists_are_cut_off():
    n = MAX_LISTED + 3
    usage = {i: TrackUsage((DiscRef(2, "X"),)) for i in range(n)}
    text = overlap_message([track(i) for i in range(n)], usage, 1)
    assert text is not None
    assert "…and 3 more" in text
