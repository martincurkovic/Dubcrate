"""What changed on the player, found on the card and brought back (no ffmpeg, no rekordbox, no real card)."""

from pathlib import Path

import pytest

from dubcrate import card_discs, card_import
from dubcrate import cdj_card as cc
from dubcrate.analysis import Cue
from dubcrate.card_import import (
    ADD,
    HOT,
    LOOP,
    MOVE,
    MOVE_LOOP,
    REMOVE,
    REMOVE_LOOP,
    Row,
)
from dubcrate.card_sync import WantedCue
from dubcrate.library import Library, TrackRecord

DATA = Path(__file__).parent / "data" / "cdj_card"


def toc():
    return cc.toc_from_drutil((DATA / "CD2_TOC.txt").read_text(encoding="utf-8"))


STARTS, LEAD_OUT = toc()


def at(track, seconds):
    return STARTS[track - 1] + round(seconds * 75)


def file_with(*cues, hot=(), loops=()):
    f = cc.TrackFile.new(cc.DiscId.from_toc(STARTS, LEAD_OUT))
    f.cues = [cc.CueEntry.memory(i, pos, track) for i, (track, pos) in enumerate(cues)]
    for track, start, end in loops:
        f.cues.append(cc.CueEntry.loop(len(f.cues), start, end, track))
    for slot, (track, pos) in enumerate(hot, start=1):
        f.hot_cues[slot - 1] = cc.HotCue(slot, pos, track)
    return f


def found(card, known=None, baseline=None):
    return card_import.changes_on_card(card, STARTS, LEAD_OUT, known or {}, baseline)


def kinds(result, track):
    return [(r.kind, r.ms) for r in result.by_track.get(track, [])]


# -- new cues -------------------------------------------------------------------------------


def test_a_cue_nothing_knows_is_new_with_its_time_in_the_track():
    result = found(file_with((2, at(2, 12.0))))
    assert kinds(result, 2) == [(ADD, 12000)]
    assert result.total == 1


def test_cues_dubcrate_has_are_not_new_whatever_their_kind():
    known = {
        1: [Cue("memory", 5.0), Cue("hot", 20.0, slot=1), Cue("loop", 30.0, end=34.0)],
    }
    card = file_with(
        (1, at(1, 5.0)), (1, at(1, 20.0) + 2), (1, at(1, 30.0) - 1), (1, at(1, 60.0))
    )
    # a hot cue or a loop written as a memory cue is not a new one
    assert kinds(found(card, known), 1) == [(ADD, 60000)]


def test_the_same_new_cue_twice_is_one_and_times_come_out_sorted():
    card = file_with((1, at(1, 40.0)), (1, at(1, 10.0)), (1, at(1, 40.0) + 1))
    assert kinds(found(card), 1) == [(ADD, 10000), (ADD, 40000)]


def test_a_cue_is_placed_by_its_position_when_the_track_it_names_is_wrong():
    card = file_with((1, at(3, 7.0)))  # names track 1, but lies in track 3
    assert kinds(found(card), 3) == [(ADD, 7000)]


def test_a_cue_outside_the_disc_is_counted():
    result = found(file_with((1, LEAD_OUT + 500)))
    assert result.outside == 1 and result.by_track == {}


# -- loops set on the player -----------------------------------------------------------------


def test_a_loop_set_on_the_player_is_new_with_its_start_and_end():
    card = file_with(loops=[(1, at(1, 50.0), at(1, 52.0))])
    [row] = found(card).by_track[1]
    assert (row.kind, row.ms, row.end_ms, row.ticked) == (LOOP, 50000, 52000, True)


def test_a_loop_dubcrate_has_is_not_new_even_a_hot_cues_loop_or_a_few_frames_off():
    known = {1: [Cue("loop", 50.0, end=52.0), Cue("loop", 60.0, end=62.0, slot=4)]}
    card = file_with(
        loops=[
            (1, at(1, 50.0) + 1, at(1, 52.0) - 2),  # the same loop, a frame or two off
            (
                1,
                at(1, 60.0),
                at(1, 62.0),
            ),  # a hot cue saved as a loop: synced as a memory loop
            (1, at(1, 50.0), at(1, 55.0)),  # the same start, another end: a new loop
        ]
    )
    assert [(r.kind, r.ms, r.end_ms) for r in found(card, known).by_track[1]] == [
        (LOOP, 50000, 55000)
    ]


def test_a_loop_is_not_the_cue_at_its_start_and_a_loop_without_an_end_is_skipped():
    known = {1: [Cue("memory", 50.0)]}
    card = file_with(loops=[(1, at(1, 50.0), at(1, 52.0)), (1, at(1, 70.0), at(1, 70.0))])  # fmt: skip
    assert [r.kind for r in found(card, known).by_track[1]] == [LOOP]


def test_the_same_loop_twice_is_one():
    card = file_with(loops=[(1, at(1, 50.0), at(1, 52.0)), (1, at(1, 50.0) + 1, at(1, 52.0))])  # fmt: skip
    assert len(found(card).by_track[1]) == 1


# -- the player's hot cues -----------------------------------------------------------------


def test_the_players_hot_cues_are_listed_as_memory_cues_but_not_ticked():
    card = file_with(hot=[(1, at(1, 9.0)), (2, at(2, 9.0))])
    [one] = found(card).by_track[1]
    [two] = found(card).by_track[2]
    assert (one.kind, one.ms, one.slot, one.ticked) == (HOT, 9000, 1, False)
    assert (two.slot, two.ms) == (2, 9000)


def test_a_hot_cue_where_dubcrate_or_the_player_already_has_a_cue_is_not_listed():
    known = {1: [Cue("memory", 9.0)]}
    card = file_with((1, at(1, 20.0)), hot=[(1, at(1, 9.0) + 1), (1, at(1, 20.0)), (1, at(1, 40.0))])  # fmt: skip
    assert [(r.kind, r.ms) for r in found(card, known).by_track[1]] == [
        (ADD, 20000),
        (HOT, 40000),
    ]


# -- the baseline: what a sync left on the card ------------------------------------------------


def test_a_cue_in_the_baseline_that_dubcrate_no_longer_has_is_not_offered_back():
    card = file_with((1, at(1, 5.0)), (1, at(1, 30.0)))
    baseline = [
        (1, at(1, 5.0), 0)
    ]  # the sync wrote 5 s; Dubcrate has since dropped or moved it
    assert kinds(found(card), 1) == [
        (ADD, 5000),
        (ADD, 30000),
    ]  # (no baseline: both are new)
    assert kinds(found(card, {}, baseline), 1) == [(ADD, 30000)]


def test_a_cue_deleted_on_the_player_is_offered_for_deletion_when_dubcrate_can_delete_it():
    known = {1: [Cue("memory", 5.0), Cue("memory", 40.0)]}
    card = file_with((1, at(1, 40.0)))
    baseline = [(1, at(1, 5.0), 0), (1, at(1, 40.0), 0)]
    result = found(card, known, baseline)
    assert kinds(result, 1) == [(REMOVE, 5000)] and result.kept == 0


def test_a_hot_cue_or_a_hot_cues_loop_deleted_on_the_player_is_kept_and_counted():
    known = {1: [Cue("hot", 5.0, slot=1), Cue("loop", 40.0, end=44.0, slot=2)]}
    card = file_with()
    baseline = [(1, at(1, 5.0), 0), (1, at(1, 40.0), at(1, 44.0))]
    result = found(card, known, baseline)
    assert result.by_track == {} and result.kept == 2


def test_something_deleted_on_the_player_that_dubcrate_does_not_have_is_nothing():
    result = found(file_with(), {}, [(1, at(1, 5.0), 0)])
    assert result.by_track == {} and result.kept == 0


def test_a_cue_still_on_the_card_a_frame_off_is_not_deleted():
    known = {1: [Cue("memory", 5.0)]}
    card = file_with((1, at(1, 5.0) + 2))
    assert found(card, known, [(1, at(1, 5.0), 0)]).by_track == {}


def test_a_loop_saved_in_dubcrate_and_deleted_on_the_player_can_be_deleted():
    known = {1: [Cue("loop", 40.0, end=44.0, local=True)]}
    baseline = [(1, at(1, 40.0), at(1, 44.0))]
    [row] = found(file_with(), known, baseline).by_track[1]
    assert (row.kind, row.ms, row.end_ms) == (REMOVE_LOOP, 40000, 44000)


def test_a_memory_loop_in_rekordbox_deleted_on_the_player_can_be_deleted():
    known = {1: [Cue("loop", 40.0, end=44.0)]}  # not `local`: rekordbox has it
    [row] = found(file_with(), known, [(1, at(1, 40.0), at(1, 44.0))]).by_track[1]
    assert (row.kind, row.ms, row.end_ms) == (REMOVE_LOOP, 40000, 44000)


def test_a_hot_cues_loop_deleted_on_the_player_is_kept_and_counted():
    known = {1: [Cue("loop", 40.0, end=44.0, slot=4)]}  # a hot cue saved as a loop
    result = found(file_with(), known, [(1, at(1, 40.0), at(1, 44.0))])
    assert result.by_track == {} and result.kept == 1


def test_one_deletion_and_one_new_cue_in_a_track_are_shown_as_a_move():
    known = {1: [Cue("memory", 5.0)]}
    card = file_with((1, at(1, 8.0)))
    result = found(card, known, [(1, at(1, 5.0), 0)])
    [row] = result.by_track[1]
    assert (row.kind, row.ms, row.old_ms) == (MOVE, 8000, 5000)


def test_deletions_and_new_cues_are_paired_in_time_order_only_when_there_are_as_many():
    known = {1: [Cue("memory", 5.0), Cue("memory", 50.0)]}
    baseline = [(1, at(1, 5.0), 0), (1, at(1, 50.0), 0)]
    both = found(file_with((1, at(1, 55.0)), (1, at(1, 9.0))), known, baseline)
    assert [(r.kind, r.old_ms, r.ms) for r in both.by_track[1]] == [
        (MOVE, 5000, 9000),
        (MOVE, 50000, 55000),
    ]
    odd = found(file_with((1, at(1, 9.0))), known, baseline)  # two gone, one new
    assert sorted(kinds(odd, 1)) == [(ADD, 9000), (REMOVE, 5000), (REMOVE, 50000)]


def test_a_moved_loop_is_a_loop_move():
    known = {1: [Cue("loop", 40.0, end=44.0, local=True)]}
    baseline = [(1, at(1, 40.0), at(1, 44.0))]
    card = file_with(loops=[(1, at(1, 60.0), at(1, 64.0))])
    [row] = found(card, known, baseline).by_track[1]
    assert (row.kind, row.ms, row.end_ms, row.old_ms, row.old_end_ms) == (
        MOVE_LOOP,
        60000,
        64000,
        40000,
        44000,
    )


def test_the_baseline_may_be_wanted_cues_as_a_sync_has_them():
    known = {1: [Cue("memory", 5.0)]}
    result = found(file_with(), known, [WantedCue(1, at(1, 5.0))])
    assert kinds(result, 1) == [(REMOVE, 5000)]


# -- what the review hands on --------------------------------------------------------------


def test_ticked_rows_become_additions_and_deletions_and_a_move_is_both():
    track = object()
    rows = [
        Row(ADD, 1000),
        Row(HOT, 2000, slot=2, ticked=False),
        Row(LOOP, 3000, 4000),
        Row(REMOVE, 5000),
        Row(REMOVE_LOOP, 6000, 7000),
        Row(MOVE, 9000, old_ms=8000),
        Row(MOVE_LOOP, 12000, 13000, 10000, 11000),
    ]
    choice = card_import.choice_from(track, rows)
    assert choice.add == [1000, 2000, 9000]
    assert choice.loops == [(3000, 4000), (12000, 13000)]
    assert choice.remove == [5000, 8000]
    assert choice.remove_loops == [(6000, 7000), (10000, 11000)]
    assert not choice.empty and card_import.choice_from(track, []).empty


def test_the_summary_says_what_was_done():
    R = card_import.ImportResult
    assert (
        R(added=2, loops=1).summary()
        == "Imported 2 memory cues and 1 loop from the card"
    )
    assert R(added=1, removed=2).summary() == "Imported 1 memory cue from the card; deleted 2 as on the card"  # fmt: skip
    assert R(removed=1).summary() == "Deleted 1 as on the card"
    assert R().summary() == "Nothing imported"
    assert "left out" in R(added=1, left_out=2).summary()


# -- against a library -------------------------------------------------------------------


@pytest.fixture
def lib():
    with Library.open(":memory:") as library:
        yield library


def disc_on_card(lib, card_file):
    lengths = [b - a for a, b in zip(STARTS, [*STARTS[1:], LEAD_OUT], strict=True)]
    lib.replace_source(
        "folder", "/f", "S",
        [TrackRecord(ext_id=f"t{n}", path=f"/nonexistent/t{n}.flac", title=f"T{n}", artist="A", duration=n + 1.0) for n in range(len(lengths))],
    )  # fmt: skip
    ids = [t.id for t in lib.tracks(sort="path")]
    for track_id, sectors in zip(ids, lengths, strict=True):
        lib.set_cd_sectors(track_id, sectors)
    disc_id = lib.create_disc("D")
    lib.add_to_disc(disc_id, ids)
    index = cc.IndexFile()
    entry = index.add(cc.DiscId.from_toc(STARTS, LEAD_OUT))
    card = (index, {entry.name: card_file})
    surveys = card_discs.survey_discs(
        lib, card, card_discs.RekordboxCues(lib, load=lambda _p: {}), {disc_id: "F / D"}
    )
    return card, surveys


def survey(lib, card, surveys):
    return card_import.survey_new_cues(
        lib, card, card_discs.RekordboxCues(lib, load=lambda _p: {}), surveys
    )


def test_the_library_half_names_the_tracks_and_skips_what_dubcrate_has(lib):
    card_file = file_with((2, at(2, 8.0)), (2, at(2, 15.0)), (3, at(3, 1.0)))
    card, surveys = disc_on_card(lib, card_file)
    track2 = lib.disc_entries(surveys[0].disc.id)[1].track
    lib.set_memory_cues(track2.path, [(8000, "mine")])  # already edited in Dubcrate
    findings = survey(lib, card, surveys)
    assert [d.path for d in findings] == ["F / D"]
    tracks = {t.number: t for t in findings[0].tracks}
    assert [(r.kind, r.ms) for r in tracks[2].rows] == [(ADD, 15000)]
    assert tracks[2].title == "T1" and tracks[2].artist == "A"
    assert [(r.kind, r.ms) for r in tracks[3].rows] == [(ADD, 1000)]
    assert findings[0].total == 2 and findings[0].has_baseline is False


def test_loops_saved_in_dubcrate_are_known_to_the_survey(lib):
    card_file = file_with(loops=[(1, at(1, 20.0), at(1, 24.0)), (1, at(1, 30.0), at(1, 34.0))])  # fmt: skip
    card, surveys = disc_on_card(lib, card_file)
    track1 = lib.disc_entries(surveys[0].disc.id)[0].track
    lib.set_memory_loops(track1.path, [(20000, 24000, "")])
    [finding] = survey(lib, card, surveys)
    assert [(r.kind, r.ms, r.end_ms) for r in finding.tracks[0].rows] == [(LOOP, 30000, 34000)]  # fmt: skip


def test_a_track_keeps_room_for_ten_memory_cues_and_loops(lib):
    card_file = file_with(*[(1, at(1, 10.0 * n)) for n in range(1, 7)])
    card, surveys = disc_on_card(lib, card_file)
    track1 = lib.disc_entries(surveys[0].disc.id)[0].track
    lib.set_memory_cues(
        track1.path, [(n * 1000 + 500, "") for n in range(1, 5)]
    )  # four
    lib.set_memory_loops(
        track1.path, [(70000, 72000, "")]
    )  # and a loop: five places used
    [finding] = survey(lib, card, surveys)
    found_track = finding.tracks[0]
    assert [r.ms for r in found_track.rows] == [10000, 20000, 30000, 40000, 50000]
    assert found_track.over_limit == 1


def test_a_deletion_frees_a_place_and_a_move_needs_none(lib):
    card_file = file_with(*[(1, at(1, 10.0 * n)) for n in range(1, 7)])
    card, surveys = disc_on_card(lib, card_file)
    track1 = lib.disc_entries(surveys[0].disc.id)[0].track
    # ten memory cues in Dubcrate: the card has six of them, so four were deleted on the player
    lib.set_memory_cues(track1.path, [(n * 10_000, "") for n in range(1, 11)])
    key = surveys[0].input.request.disc_id.key
    lib.set_card_baseline(key, [(1, at(1, 10.0 * n), 0) for n in range(1, 11)])
    [finding] = survey(lib, card, surveys)
    assert [r.kind for r in finding.tracks[0].rows] == [REMOVE] * 4
    assert finding.tracks[0].over_limit == 0 and finding.has_baseline


def test_the_baseline_in_the_library_finds_what_was_deleted_on_the_player(lib):
    card_file = file_with((2, at(2, 15.0)))
    card, surveys = disc_on_card(lib, card_file)
    track2 = lib.disc_entries(surveys[0].disc.id)[1].track
    lib.set_memory_cues(track2.path, [(8000, ""), (15000, "")])
    assert survey(lib, card, surveys) == []  # no baseline: a deletion can't be noticed
    key = surveys[0].input.request.disc_id.key
    lib.set_card_baseline(key, [(2, at(2, 8.0), 0), (2, at(2, 15.0), 0)])
    [finding] = survey(lib, card, surveys)
    assert [(r.kind, r.ms) for r in finding.tracks[0].rows] == [(REMOVE, 8000)]


def test_the_library_keeps_a_baseline_per_disc_and_an_empty_one_is_not_none(lib):
    key = (3, 100_000, 5_000, 90_000)
    assert lib.card_baseline(key) is None
    lib.set_card_baseline(key, [(1, 200, 0), (2, 6_000, 6_400)])
    assert lib.card_baseline(key) == [(1, 200, 0), (2, 6_000, 6_400)]
    assert lib.card_baseline((9, 1, 2, 3)) is None  # (another disc)
    lib.set_card_baseline(key, [])  # the last sync left no cues
    assert lib.card_baseline(key) == []
    lib.set_card_baseline(key, [(1, 300, 0)])  # replaced, not added to
    assert lib.card_baseline(key) == [(1, 300, 0)]


def test_a_disc_not_on_the_card_or_with_nothing_new_gives_nothing(lib):
    card, surveys = disc_on_card(lib, file_with())
    assert survey(lib, card, surveys) == []
    empty = (cc.IndexFile(), {})
    assert survey(lib, empty, surveys) == []
