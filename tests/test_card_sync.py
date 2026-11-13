"""Planning a card sync: mapping rekordbox cues, merging, and the plan (fixtures are the player's own files)."""

from pathlib import Path

from dubcrate import card_sync as cs
from dubcrate import cdj_card as cc
from dubcrate.analysis import Cue

DATA = Path(__file__).parent / "data" / "cdj_card"


def toc(name: str) -> tuple[list[int], int]:
    return cc.toc_from_drutil((DATA / name).read_text(encoding="utf-8"))


def card_state(index_file: str, **files: str):
    """An index and its files, read from fixtures: card_state("index_three_discs.PDJ", CDJ00001=...)"""
    index = cc.IndexFile.parse((DATA / index_file).read_bytes())
    loaded = {e.name: None for e in index.entries}
    for name, fixture in files.items():
        loaded[name] = cc.TrackFile.parse((DATA / fixture).read_bytes())
    return index, loaded


# --- mapping ----------------------------------------------------------------------------------


def test_every_kind_of_rekordbox_cue_becomes_a_cue_at_the_tracks_start_plus_its_time():
    starts, lead_out = toc("CD1_TOC.txt")
    mapped = cs.map_cues(
        {
            2: [
                Cue("memory", 4.0),
                Cue(
                    "hot", 64.0, slot=3, label="drop"
                ),  # a hot cue is a memory cue on the card
                Cue("loop", 100.0, end=108.0),
            ]
        },
        starts,
        lead_out,
    )
    assert mapped.notes == []
    assert mapped.cues == [
        cs.WantedCue(2, 28905 + 4 * 75),
        cs.WantedCue(2, 28905 + 64 * 75),
        cs.WantedCue(2, 28905 + 100 * 75, end=28905 + 108 * 75),
    ]


def test_the_positions_match_the_cues_the_player_stored_for_the_same_times():
    # what the player stored for 5 s and 10 s into track 1, and 4 s and 1:04 into track 2
    starts, lead_out = toc("CD1_TOC.txt")
    mapped = cs.map_cues(
        {
            1: [Cue("memory", 5.0), Cue("memory", 10.0)],
            2: [Cue("memory", 4.0), Cue("memory", 64.0)],
        },
        starts,
        lead_out,
    )
    assert [c.position for c in mapped.cues] == [525, 900, 29205, 33705]


def test_cues_are_ordered_by_track_then_time_whatever_order_they_come_in():
    starts, lead_out = toc("CD1_TOC.txt")
    mapped = cs.map_cues(
        {3: [Cue("memory", 9.0)], 1: [Cue("memory", 20.0), Cue("memory", 2.0)]},
        starts,
        lead_out,
    )
    assert [(c.track, c.position) for c in mapped.cues] == [
        (1, 300),
        (1, 1650),
        (3, 61304 + 675),
    ]


def test_a_time_in_milliseconds_is_rounded_to_the_nearest_frame():
    starts, lead_out = toc("CD1_TOC.txt")
    mapped = cs.map_cues(
        {1: [Cue("memory", 5.006), Cue("memory", 5.0067)]}, starts, lead_out
    )
    # 5.006 s is 375.45 frames (525) and 5.0067 s is 375.50 (526): within a frame of each other
    assert [c.position for c in mapped.cues] == [
        525
    ]  # and the two were one cue, within a frame


def test_a_hot_cue_and_a_memory_cue_at_the_same_place_make_one_cue():
    starts, lead_out = toc("CD1_TOC.txt")
    mapped = cs.map_cues(
        {1: [Cue("memory", 30.0), Cue("hot", 30.0, slot=1)]}, starts, lead_out
    )
    assert len(mapped.cues) == 1


def test_a_loop_and_a_cue_at_the_same_place_are_two_things():
    starts, lead_out = toc("CD1_TOC.txt")
    mapped = cs.map_cues(
        {1: [Cue("memory", 30.0), Cue("loop", 30.0, end=32.0)]}, starts, lead_out
    )
    assert [c.is_loop for c in mapped.cues] == [False, True]


def test_what_cant_be_placed_is_left_out_and_said_so():
    starts, lead_out = toc("CD1_TOC.txt")
    mapped = cs.map_cues(
        {
            1: [
                Cue("memory", 99999.0),  # far past the end of track 1
                Cue("loop", 10.0, end=10.0),  # no length
                Cue("loop", 20.0),  # no end at all
                Cue("memory", -1.0),
                Cue("memory", 3.0),  # fine
            ],
            13: [Cue("memory", 1.0)],  # CD 1 has 12 tracks
        },
        starts,
        lead_out,
    )
    assert [c.position for c in mapped.cues] == [150 + 3 * 75]
    assert len(mapped.notes) == 5
    assert any("past the end" in n for n in mapped.notes)
    assert any("not on this disc" in n for n in mapped.notes)
    assert sum("no usable end" in n for n in mapped.notes) == 2


def test_the_last_track_ends_at_the_lead_out():
    starts, lead_out = toc("CD1_TOC.txt")
    last_length = (lead_out - starts[-1]) / 75
    ok = cs.map_cues({12: [Cue("memory", last_length - 1)]}, starts, lead_out)
    past = cs.map_cues({12: [Cue("memory", last_length + 1)]}, starts, lead_out)
    assert len(ok.cues) == 1
    assert past.cues == [] and past.notes


# --- merging ----------------------------------------------------------------------------------


def disc1_cues() -> list[cc.CueEntry]:
    return cc.TrackFile.parse((DATA / "disc1_full.PDJ").read_bytes()).cues


def test_merging_keeps_what_was_there_exactly_and_adds_after_it():
    before = disc1_cues()
    merge = cs.merge_cues(
        before, [cs.WantedCue(1, 8000), cs.WantedCue(2, 40000, end=40300)]
    )
    assert merge.cues[: len(before)] == before
    assert [(c.index, c.kind, c.position, c.end, c.track) for c in merge.added] == [
        (6, cc.KIND_CUE, 8000, 0, 1),
        (7, cc.KIND_LOOP, 40000, 40300, 2),
    ]
    assert merge.cues[-2:] == merge.added
    assert merge.added[1].flag == cc.LOOP_FLAG
    assert merge.already_there == 0 and merge.overflow == []


def test_a_cue_already_on_the_card_is_not_added_again():
    # the card has cues at 525, 900, 29205 ... and a loop at 3165 to 3365
    merge = cs.merge_cues(
        disc1_cues(),
        [
            cs.WantedCue(1, 525),  # exactly there
            cs.WantedCue(1, 527),  # two frames off: the same cue
            cs.WantedCue(2, 29205),
            cs.WantedCue(1, 3166, end=3366),  # the loop, a frame off
        ],
    )
    assert merge.added == [] and merge.already_there == 4


def test_three_frames_off_is_a_different_cue():
    merge = cs.merge_cues(disc1_cues(), [cs.WantedCue(1, 525 + 3)])
    assert len(merge.added) == 1


def test_the_same_position_on_another_track_or_as_a_loop_is_different():
    merge = cs.merge_cues(
        disc1_cues(),
        [cs.WantedCue(3, 525), cs.WantedCue(1, 525, end=600)],
    )
    assert len(merge.added) == 2


def test_the_players_hundred_cue_limit_reports_the_rest_rather_than_dropping_it():
    existing = [cc.CueEntry.memory(n, 1000 + n * 10, track=1) for n in range(98)]
    merge = cs.merge_cues(existing, [cs.WantedCue(2, 5000 + n * 10) for n in range(5)])
    assert len(merge.cues) == cc.MAX_CUES == 100
    assert len(merge.added) == 2
    assert merge.overflow == [cs.WantedCue(2, 5020 + n * 10) for n in range(3)]


def test_numbering_continues_from_the_highest_index():
    existing = [cc.CueEntry.memory(0, 500, 1), cc.CueEntry.memory(7, 900, 1)]
    merge = cs.merge_cues(existing, [cs.WantedCue(1, 2000), cs.WantedCue(1, 3000)])
    assert [c.index for c in merge.added] == [8, 9]


# --- the plan ---------------------------------------------------------------------------------


def disc_id(toc_name: str) -> cc.DiscId:
    return cc.DiscId.from_toc(*toc(toc_name))


def test_a_disc_on_the_card_gets_new_cues_and_keeps_everything_else():
    index, files = card_state("index_three_discs.PDJ", CDJ00001="disc1_full.PDJ")
    before = files["CDJ00001"]
    plan = cs.plan_card(
        index, files, [cs.DiscRequest(disc_id("CD1_TOC.txt"), [cs.WantedCue(1, 8000)])]
    )
    (disc,) = plan.discs
    assert (disc.status, disc.name, disc.kept) == (cs.Status.UPDATE, "CDJ00001", 6)
    assert disc.file.cues[:6] == before.cues
    assert (
        disc.file.hot_cues == before.hot_cues
    )  # the player's hot cues are never touched
    assert disc.file.coarse == before.coarse and disc.file.fine == before.fine
    assert disc.file.disc_id == before.disc_id and disc.file.pad == before.pad
    assert plan.writes() == {"CDJ00001": disc.file}
    assert before.cues == disc1_cues()  # and what was passed in is unchanged


def test_syncing_twice_changes_nothing_the_second_time():
    index, files = card_state("index_three_discs.PDJ", CDJ00001="disc1_full.PDJ")
    request = cs.DiscRequest(disc_id("CD1_TOC.txt"), [cs.WantedCue(1, 8000)])
    first = cs.plan_card(index, files, [request])
    again = cs.plan_card(first.index, {"CDJ00001": first.discs[0].file}, [request])
    assert again.discs[0].status == cs.Status.IN_SYNC
    assert again.discs[0].already_there == 1 and again.discs[0].added == []
    assert not again.changes


def test_a_disc_in_the_index_with_no_file_gets_a_cues_only_file():
    index, files = card_state("index_three_discs.PDJ", CDJ00001="disc1_full.PDJ")
    plan = cs.plan_card(
        index, files, [cs.DiscRequest(disc_id("CD2_TOC.txt"), [cs.WantedCue(1, 1275)])]
    )
    (disc,) = plan.discs
    assert (disc.status, disc.name) == (cs.Status.NEW_FILE, "CDJ00002")
    assert disc.file.hot_cues == [None, None, None]
    assert disc.file.coarse == [] and disc.file.fine == []
    assert disc.file.disc_id.flags == cc.FILE_FLAGS
    assert [e.name for e in plan.index.entries] == ["CDJ00001", "CDJ00002", "CDJ00003"]


def test_a_new_disc_gets_an_entry_and_a_file_and_the_given_index_is_not_changed():
    index, files = card_state("index_one_disc.PDJ")
    plan = cs.plan_card(
        index, files, [cs.DiscRequest(disc_id("CD2_TOC.txt"), [cs.WantedCue(1, 1275)])]
    )
    (disc,) = plan.discs
    assert (disc.status, disc.name) == (cs.Status.NEW_DISC, "CDJ00002")
    assert [e.name for e in plan.index.entries] == ["CDJ00001", "CDJ00002"]
    assert plan.index.entries[1].disc_id.flags == cc.INDEX_FLAGS
    assert [e.name for e in index.entries] == [
        "CDJ00001"
    ]  # the caller's index, as it was


def test_a_disc_with_nothing_to_write_makes_no_entry_and_no_file():
    index, files = card_state("index_one_disc.PDJ")
    plan = cs.plan_card(
        index, files, [cs.DiscRequest(disc_id("CD2_TOC.txt"), [], ["No cues."])]
    )
    (disc,) = plan.discs
    assert (
        disc.status == cs.Status.NOTHING
        and disc.file is None
        and disc.notes == ["No cues."]
    )
    assert len(plan.index.entries) == 1
    assert not plan.changes


def test_two_discs_in_one_sync_each_get_their_own_file():
    index, files = card_state("index_one_disc.PDJ")
    plan = cs.plan_card(
        index,
        files,
        [
            cs.DiscRequest(disc_id("CD2_TOC.txt"), [cs.WantedCue(1, 1275)]),
            cs.DiscRequest(disc_id("CD3_TOC.txt"), [cs.WantedCue(2, 20000)]),
        ],
    )
    assert [(d.status, d.name) for d in plan.discs] == [
        (cs.Status.NEW_DISC, "CDJ00002"),
        (cs.Status.NEW_DISC, "CDJ00003"),
    ]
    assert set(plan.writes()) == {"CDJ00002", "CDJ00003"}


def test_the_same_disc_asked_for_twice_is_merged_not_made_twice():
    index, files = card_state("index_one_disc.PDJ")
    request = cs.DiscRequest(disc_id("CD2_TOC.txt"), [cs.WantedCue(1, 1275)])
    plan = cs.plan_card(index, files, [request, request])
    assert [d.status for d in plan.discs] == [cs.Status.NEW_DISC, cs.Status.IN_SYNC]
    assert len(plan.index.entries) == 2


def test_overflow_and_notes_come_through_the_plan():
    index, files = card_state("index_one_disc.PDJ")
    wanted = [cs.WantedCue(2, 20000 + n * 100) for n in range(cc.MAX_CUES + 3)]
    plan = cs.plan_card(
        index, files, [cs.DiscRequest(disc_id("CD2_TOC.txt"), wanted, ["a note"])]
    )
    (disc,) = plan.discs
    assert len(disc.file.cues) == cc.MAX_CUES and len(disc.overflow) == 3
    assert disc.notes == ["a note"]


def test_a_plan_written_to_a_card_reads_back_as_planned(tmp_path):
    folder = tmp_path / "PIONEER" / "CDJ"
    folder.mkdir(parents=True)
    (folder / cc.INDEX_NAME).write_bytes((DATA / "index_one_disc.PDJ").read_bytes())
    (folder / "CDJ00001.PDJ").write_bytes((DATA / "one_cue.PDJ").read_bytes())
    index, files = cc.read_card(tmp_path)
    starts, lead_out = toc("CD2_TOC.txt")
    mapped = cs.map_cues(
        {1: [Cue("memory", 15.0)], 12: [Cue("loop", 30.0, end=34.0)]}, starts, lead_out
    )
    plan = cs.plan_card(
        index,
        files,
        [
            cs.DiscRequest(disc_id("CD1_TOC.txt"), [cs.WantedCue(1, 900)]),
            cs.DiscRequest(cc.DiscId.from_toc(starts, lead_out), mapped.cues),
        ],
    )
    cc.write_card(tmp_path, plan.index, plan.writes())
    again_index, again = cc.read_card(tmp_path)
    assert [e.name for e in again_index.entries] == ["CDJ00001", "CDJ00002"]
    assert [c.position for c in again["CDJ00001"].cues] == [
        525,
        900,
    ]  # the player's, then ours
    assert [(c.track, c.is_loop) for c in again["CDJ00002"].cues] == [
        (1, False),
        (12, True),
    ]
    assert again["CDJ00002"].cues[1].end - again["CDJ00002"].cues[1].position == 4 * 75


def test_the_mapped_cues_of_the_players_own_disc_are_already_there():
    # rekordbox cues that match what the player stored for CD 1 add nothing: nothing to do
    index, files = card_state("index_three_discs.PDJ", CDJ00001="disc1_full.PDJ")
    starts, lead_out = toc("CD1_TOC.txt")
    mapped = cs.map_cues(
        {
            1: [Cue("memory", 5.0), Cue("memory", 10.0), Cue("memory", 2.0)],
            2: [Cue("memory", 4.0), Cue("memory", 64.0)],
        },
        starts,
        lead_out,
    )
    plan = cs.plan_card(
        index, files, [cs.DiscRequest(disc_id("CD1_TOC.txt"), mapped.cues)]
    )
    assert plan.discs[0].status == cs.Status.IN_SYNC
    assert plan.discs[0].already_there == 5
