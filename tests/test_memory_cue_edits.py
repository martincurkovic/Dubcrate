"""Memory cues edited in Dubcrate: kept in its own library, laid over rekordbox's, and only written to rekordbox by `rekordbox_write` (when the user allows it)."""

import pytest

from dubcrate import card_discs
from dubcrate.analysis import Cue
from dubcrate.library import Library, TrackRecord
from dubcrate.library.cue_edits import (
    MAX_MEMORY_CUES,
    is_memory,
    is_memory_loop,
    memory_count,
    memory_list,
    with_edits,
)


@pytest.fixture
def lib():
    with Library.open(":memory:") as library:
        yield library


def one_track(lib, path="/m/Song.mp3"):
    lib.replace_source("folder", "/m", "M", [TrackRecord(ext_id=path, path=path, title="Song", duration=200.0, file_size=1)])  # fmt: skip
    return lib.tracks()[0]


RK = (
    Cue("memory", 0.1),
    Cue("memory", 31.5, label="drop"),
    Cue("loop", 60.0, end=64.0),
    Cue("hot", 29.25, slot=2),
    Cue("hot", 77.9, slot=1),
)


def test_a_track_not_edited_has_no_edits_and_rekordbox_cues_pass_through(lib):
    track = one_track(lib)
    assert lib.memory_cues(track.path) is None
    assert with_edits(lib, track, RK) == list(RK)


def test_edited_memory_cues_are_kept_in_time_order_and_a_time_is_kept_once(lib):
    track = one_track(lib)
    lib.set_memory_cues(
        track.path, [(5000, "b"), (1000, "a"), (5000, "dup"), (-20, "early")]
    )
    assert lib.memory_cues(track.path) == [(0, "early"), (1000, "a"), (5000, "b")]
    lib.set_memory_cues(track.path, [(2000, "x")])  # (replaces, not adds)
    assert lib.memory_cues(track.path) == [(2000, "x")]


def test_an_edit_to_no_cues_at_all_is_still_an_edit(lib):
    track = one_track(lib)
    lib.set_memory_cues(track.path, [])
    assert (
        lib.memory_cues(track.path) == []
    )  # (not None: every memory cue was deleted on purpose)
    assert [c.kind for c in with_edits(lib, track, RK)] == [
        "hot",
        "loop",
        "hot",
    ]  # (by time)


def test_clearing_the_edits_goes_back_to_rekordboxs(lib):
    track = one_track(lib)
    lib.set_memory_cues(track.path, [(2000, "x")])
    lib.clear_memory_cues(track.path)
    assert lib.memory_cues(track.path) is None and with_edits(lib, track, RK) == list(
        RK
    )
    lib.clear_memory_cues(track.path)  # (nothing to clear: fine)


def test_the_edited_cues_replace_only_the_plain_memory_cues(lib):
    track = one_track(lib)
    lib.set_memory_cues(track.path, [(12_000, "mine"), (90_000, "")])
    layered = with_edits(lib, track, RK)
    assert (
        [(c.kind, c.slot, round(c.time, 2)) for c in layered]
        == [
            ("memory", None, 12.0),
            ("hot", 2, 29.25),
            ("loop", None, 60.0),
            ("hot", 1, 77.9),
            ("memory", None, 90.0),
        ]
    )  # (hot cues and the memory loop are rekordbox's, untouched; the two memory cues are the edited ones)
    assert layered[0].label == "mine"


def test_which_cues_count_as_plain_memory_cues():
    assert is_memory(Cue("memory", 1.0))
    assert not is_memory(Cue("hot", 1.0, slot=1)) and not is_memory(
        Cue("loop", 1.0, end=2.0)
    )
    assert memory_list(RK) == [(100, ""), (31500, "drop")]


def test_edits_belong_to_the_file_so_another_rows_copy_and_a_reimport_keep_them(lib):
    track = one_track(lib, "/m/Café.mp3")
    lib.set_memory_cues(track.path, [(7000, "x")])
    assert lib.memory_cues("/M/CAFÉ.MP3") == [
        (7000, "x")
    ]  # (the same file by another spelling)
    lib.replace_source("folder", "/m", "M", [TrackRecord(ext_id="other-id", path="/m/Café.mp3", title="Song", duration=200.0, file_size=2)])  # fmt: skip
    assert lib.memory_cues(lib.tracks()[0].path) == [(7000, "x")]


def test_the_limit_is_the_players_ten():
    assert MAX_MEMORY_CUES == 10


def test_sync_writes_the_edited_memory_cues_to_the_card(lib):
    """A folder track has no rekordbox cues; the ones edited in Dubcrate are what the card gets for it."""
    for n in range(2):
        lib.replace_source("folder", f"/m{n}", f"M{n}", [TrackRecord(ext_id=f"a{n}", path=f"/m{n}/{n}.flac", title=f"T{n}", duration=100.0, file_size=1)])  # fmt: skip
    ids = [t.id for t in lib.tracks(sort="path")]
    for track_id in ids:
        lib.set_cd_sectors(track_id, 75 * 60)
    disc = lib.create_disc("D")
    lib.add_to_disc(disc, ids)
    lib.mark_burned(disc)
    first = lib.tracks_by_ids(ids)[0]
    lib.set_memory_cues(first.path, [(15_000, "")])
    found = card_discs.prepare_disc(lib, disc, card_discs.RekordboxCues(lib))
    assert [(c.track, c.position) for c in found.request.wanted] == [(1, 150 + 15 * 75)]


# -- memory loops saved in Dubcrate ------------------------------------------------------------


def test_saved_loops_are_kept_in_time_order_once_each_and_only_if_they_end_after_they_start(
    lib,
):
    track = one_track(lib)
    assert lib.memory_loops(track.path) == []
    lib.set_memory_loops(track.path, [(9000, 12000, "b"), (1000, 3000, "a"), (9000, 12000, "dup"), (5000, 5000, "empty"), (7000, 6000, "back"), (-5, 500, "early")])  # fmt: skip
    assert lib.memory_loops(track.path) == [(0, 500, "early"), (1000, 3000, "a"), (9000, 12000, "b")]  # fmt: skip
    lib.clear_memory_loops(track.path)
    assert lib.memory_loops(track.path) == []


def test_saved_loops_are_added_and_leave_rekordboxs_loops_and_memory_cues_alone(lib):
    track = one_track(lib)
    lib.set_memory_loops(track.path, [(10_000, 14_000, "mine")])
    layered = with_edits(lib, track, RK)
    assert layered != list(RK) and len(layered) == len(RK) + 1
    assert [c for c in layered if not c.local] == sorted(RK, key=lambda c: c.time)  # (rekordbox's, all of them)  # fmt: skip
    [mine] = [c for c in layered if c.local]
    assert (mine.kind, mine.time, mine.end, mine.slot, mine.label) == ("loop", 10.0, 14.0, None, "mine")  # fmt: skip
    assert (
        lib.memory_cues(track.path) is None
    )  # (saving a loop is not a memory cue edit)


def test_saved_loops_and_edited_memory_cues_work_together(lib):
    track = one_track(lib)
    lib.set_memory_cues(track.path, [(2000, "")])
    lib.set_memory_loops(track.path, [(10_000, 14_000, "")])
    kinds = [(c.kind, c.local) for c in with_edits(lib, track, RK)]
    assert kinds.count(("memory", False)) == 1 and kinds.count(("loop", True)) == 1
    assert kinds.count(("loop", False)) == 1  # (rekordbox's own loop stays)


def test_a_saved_loop_rekordbox_has_by_now_is_not_shown_twice(lib):
    track = one_track(lib)
    lib.set_memory_loops(track.path, [(60_010, 64_000, "")])
    assert with_edits(lib, track, RK) == sorted(RK, key=lambda c: c.time)


def test_loops_belong_to_the_file_like_memory_cues(lib):
    track = one_track(lib, "/m/Café.mp3")
    lib.set_memory_loops(track.path, [(1000, 2000, "")])
    assert lib.memory_loops("/M/CAFÉ.MP3") == [(1000, 2000, "")]


def test_memory_count_counts_cues_and_memory_loops_but_not_hot_cues_or_hot_loops():
    cues = [Cue("memory", 1.0), Cue("loop", 2.0, end=3.0), Cue("hot", 4.0, slot=1), Cue("loop", 5.0, end=6.0, slot=2)]  # fmt: skip
    assert memory_count(cues) == 2
    assert is_memory_loop(cues[1]) and not is_memory_loop(cues[3])


def test_the_rekordbox_writer_is_never_handed_a_saved_loop(lib):
    """The writer's input is `memory_cue_sets()`: loops are kept in another table, so one cannot be written as a cue."""
    track = one_track(lib)
    lib.set_memory_loops(track.path, [(1000, 2000, "")])
    assert lib.memory_cue_sets() == {}
    lib.set_memory_cues(track.path, [(5000, "")])
    assert list(lib.memory_cue_sets().values()) == [[(5000, "")]]
