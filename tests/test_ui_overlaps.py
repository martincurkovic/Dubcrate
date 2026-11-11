from dubcrate.library.models import DiscRef, Track, TrackUsage
from dubcrate.ui.overlaps import MAX_LISTED, overlap_message


def track(i: int, artist: str = "A") -> Track:
    return Track(
        id=i, source_id=1, ext_id=str(i), path=f"/{i}", title=f"T{i}", artist=artist
    )


def test_nothing_used_means_no_message():
    assert overlap_message([track(1)], {}, 1) is None


def test_names_each_disc_a_track_is_on_and_when_one_was_burned():
    usage = {
        1: TrackUsage(
            (
                DiscRef(1, "Here"),
                DiscRef(2, "Other"),
                DiscRef(3, "Friday", "2026-11-01T20:00:00+00:00"),
            )
        )
    }
    text = overlap_message([track(1), track(2)], usage, 1)
    assert text is not None
    assert text.startswith("1 of the 2 tracks are already used:")
    assert (
        "A - T1: already on this disc; on “Other”, “Friday” (burned 2026-11-01)" in text
    )
    assert "T2" not in text
    assert text.endswith("Add anyway?")


def test_a_disc_that_was_never_burned_has_no_burned_note():
    usage = {1: TrackUsage((DiscRef(2, "Other"),))}
    text = overlap_message([track(1)], usage, 1)
    assert text is not None and "burned" not in text


def test_a_single_track_reads_naturally_and_no_disc_means_nothing_is_here():
    usage = {1: TrackUsage((DiscRef(2, "Other"),))}
    text = overlap_message([track(1, artist="")], usage, None)
    assert text is not None
    assert text.startswith("This track is already used:")
    assert "• T1: on “Other”" in text


def test_long_lists_are_cut_off():
    n = MAX_LISTED + 3
    usage = {i: TrackUsage((DiscRef(2, "X"),)) for i in range(n)}
    text = overlap_message([track(i) for i in range(n)], usage, 1)
    assert text is not None
    assert "…and 3 more" in text
