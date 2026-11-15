"""Waveforms in a card sync: merged into a file's records, planned, written and read back (no drive, no ffmpeg)."""

from pathlib import Path

import pytest

from dubcrate import card_discs, card_report, card_sync, wave_model
from dubcrate import cdj_card as cc
from dubcrate.library import Library, TrackRecord

DATA = Path(__file__).parent / "data" / "cdj_card"


def saw(shift=0):
    return [1 + (k + shift) % 15 for k in range(100)]


def record(track, values):
    return wave_model.records(track, values)


def toc(n=4):
    return cc.toc_from_lengths([75 * 60] * n)


def disc_id(n=4):
    return cc.DiscId.from_toc(*toc(n))


# -- merge_waveforms ------------------------------------------------------------------------------


def test_a_track_with_no_record_gets_ours_in_its_slot_and_the_gap_is_the_players_empty_slot():
    merge = card_sync.merge_waveforms([], [], {3: saw()})
    assert merge.added == [3] and merge.kept == [] and merge.pending == []
    assert [r.track for r in merge.fine] == [
        0,
        0,
        3,
    ]  # slots for tracks 1 and 2, left empty
    assert [r.track for r in merge.coarse] == [0, 0, 3]
    assert merge.fine[2].time_values() == saw()
    assert not any(merge.fine[0].data)


def test_a_track_that_already_has_a_record_keeps_it_untouched():
    theirs = record(1, saw(5))
    merge = card_sync.merge_waveforms([theirs[0]], [theirs[1]], {1: saw(), 2: saw(2)})
    assert merge.kept == [1] and merge.added == [2]
    assert (
        merge.fine[0] is theirs[1] and merge.coarse[0] is theirs[0]
    )  # the very same record
    assert merge.fine[1].time_values() == saw(2)


def test_a_part_filled_record_counts_as_a_record_and_an_empty_one_does_not():
    half = cc.WaveRecord.fine_in_time_order(1, saw()[:50] + [0] * 50)
    empty = cc.WaveRecord.unplayed()
    merge = card_sync.merge_waveforms(
        [empty, empty], [half, empty], {1: saw(), 2: saw()}
    )
    assert merge.kept == [1] and merge.added == [2]
    assert merge.fine[0] is half


def test_a_track_not_analysed_yet_is_pending_and_writes_nothing():
    merge = card_sync.merge_waveforms([], [], {1: None, 2: saw()})
    assert merge.pending == [1] and merge.added == [2]
    assert [r.track for r in merge.fine] == [0, 2]  # slot 1 stays the empty one


def test_the_two_lists_stay_the_same_length():
    one = record(1, saw())
    merge = card_sync.merge_waveforms(
        [one[0]], [one[1], cc.WaveRecord.unplayed()], {4: saw()}
    )
    assert len(merge.coarse) == len(merge.fine) == 4


# -- plan_card --------------------------------------------------------------------------------------


def request(waves, wanted=()):
    return card_sync.DiscRequest(disc_id(), list(wanted), waves=waves)


def test_a_disc_not_on_the_card_with_only_waveforms_gets_an_entry_and_a_file():
    plan = card_sync.plan_card(cc.IndexFile(), {}, [request({1: saw(), 2: saw(1)})])
    disc = plan.discs[0]
    assert disc.status == card_sync.Status.NEW_DISC and disc.waves_added == [1, 2]
    assert plan.changes and [r.track for r in disc.file.fine] == [1, 2]


def test_a_disc_whose_waveforms_are_all_on_the_card_is_in_sync_and_writes_nothing():
    first = record(1, saw())
    plan = card_sync.plan_card(cc.IndexFile(), {}, [request({1: saw()})])
    index = plan.index
    name = plan.discs[0].name
    again = card_sync.plan_card(
        index, {name: plan.discs[0].file}, [request({1: saw(3)})]
    )
    assert again.discs[0].status == card_sync.Status.IN_SYNC
    assert again.discs[0].waves_kept == [1] and again.discs[0].waves_added == []
    assert not again.changes
    assert first[1].time_values() == plan.discs[0].file.fine[0].time_values()


def test_waveforms_are_added_to_a_file_that_has_cues_and_the_cues_and_hot_cues_are_untouched():
    base = cc.TrackFile.new(disc_id())
    base.cues = [cc.CueEntry.memory(0, 500, 1)]
    base.hot_cues[0] = cc.HotCue(1, 700, 1)
    index = cc.IndexFile()
    entry = index.add(disc_id())
    plan = card_sync.plan_card(index, {entry.name: base}, [request({1: saw()})])
    disc = plan.discs[0]
    assert disc.status == card_sync.Status.UPDATE and disc.waves_added == [1]
    assert disc.file.cues == base.cues and disc.file.hot_cues == base.hot_cues
    assert base.fine == []  # the file given is never changed


def test_a_disc_with_only_unanalysed_tracks_plans_nothing_but_says_how_many_are_pending():
    plan = card_sync.plan_card(cc.IndexFile(), {}, [request({1: None, 2: None})])
    disc = plan.discs[0]
    assert disc.status == card_sync.Status.NOTHING and disc.waves_pending == [1, 2]
    assert not plan.changes


def test_a_planned_file_is_written_and_read_back_with_its_records(tmp_path):
    plan = card_sync.plan_card(cc.IndexFile(), {}, [request({1: saw(), 3: saw(7)})])
    cc.write_card(tmp_path, plan.index, plan.writes(), create=True)
    index, files = cc.read_card(tmp_path)
    file = files[index.entries[0].name]
    assert [r.track for r in file.fine] == [1, 0, 3]
    assert file.fine[0].time_values() == saw() and file.fine[2].time_values() == saw(7)
    assert list(file.coarse[0].data) == wave_model.coarse_values(saw())


def test_the_plan_in_words_says_what_is_added_kept_and_pending():
    first = record(1, saw())
    plan = card_sync.plan_card(
        cc.IndexFile(), {}, [request({1: saw(), 2: saw(), 3: saw(), 5: None})]
    )
    lines = card_report.describe_plan(plan.discs[0])
    assert "  + waveforms for tracks 1-3" in lines
    assert any(
        "1 track still to be analysed for a waveform (5)" in line for line in lines
    )
    kept = card_sync.plan_card(
        plan.index,
        {plan.discs[0].name: plan.discs[0].file},
        [request({1: saw(), 4: saw()})],
    )
    lines = card_report.describe_plan(kept.discs[0])
    assert "  + waveform for track 4" in lines
    assert any(
        "1 track already has a waveform on the card (1): left alone" in line
        for line in lines
    )
    assert first


@pytest.mark.parametrize(
    ("tracks", "text"),
    [([1], "1"), ([1, 2, 3], "1-3"), ([1, 2, 4, 6, 7], "1-2, 4, 6-7"), ([], "")],
)
def test_track_numbers_are_written_as_runs(tracks, text):
    assert card_report.track_list(tracks) == text


# -- from the library -------------------------------------------------------------------------------


@pytest.fixture
def lib():
    with Library.open(":memory:") as library:
        yield library


def disc_of_files(lib, tmp_path, n=3):
    paths = []
    for k in range(n):
        p = tmp_path / f"t{k}.flac"
        p.write_bytes(b"x")
        paths.append(p)
    lib.replace_source(
        "folder", str(tmp_path), "S",
        [TrackRecord(ext_id=f"t{k}", path=str(p), title=f"t{k}", duration=100.0, file_size=1) for k, p in enumerate(paths)],
    )  # fmt: skip
    ids = [t.id for t in lib.tracks(sort="path")]
    for track_id in ids:
        lib.set_cd_sectors(track_id, 75 * 60)
    disc = lib.create_disc("D")
    lib.add_to_disc(disc, ids)
    lib.mark_burned(disc)
    return disc, ids


def test_waveforms_are_only_wanted_when_asked_for(lib, tmp_path):
    disc, _ = disc_of_files(lib, tmp_path)
    assert card_discs.prepare_disc(lib, disc).request.waves == {}
    wanted = card_discs.prepare_disc(lib, disc, waves=True)
    assert wanted.request.waves == {1: None, 2: None, 3: None}
    assert [t.title for t in wanted.to_analyse] == ["t0", "t1", "t2"]


def test_an_analysed_track_brings_its_values_and_a_missing_file_is_said(lib, tmp_path):
    disc, ids = disc_of_files(lib, tmp_path)
    lib.set_waveform(ids[0], saw())
    (tmp_path / "t2.flac").unlink()
    lib.replace_source(
        "folder", str(tmp_path), "S",
        [TrackRecord(ext_id=f"t{k}", path=str(tmp_path / f"t{k}.flac"), title=f"t{k}", duration=100.0, file_size=1) for k in range(3)],
    )  # fmt: skip
    found = card_discs.prepare_disc(lib, disc, waves=True)
    assert found.request.waves[1] == saw() and found.request.waves[2] is None
    assert 3 not in found.request.waves
    assert any(
        "Track 3 has no waveform: its file is missing" in n for n in found.request.notes
    )
    assert [t.title for t in found.to_analyse] == ["t1"]


def test_the_survey_says_cues_and_waveforms_and_old_wording_is_unchanged_without_them(
    lib, tmp_path
):
    disc, ids = disc_of_files(lib, tmp_path)
    for track_id in ids:
        lib.set_waveform(track_id, saw())
    card = (cc.IndexFile(), {})
    cues = card_discs.RekordboxCues(lib)
    plain = card_discs.survey_discs(lib, card, cues, {disc: "D"})[0]
    assert plain.status == "No rekordbox cues" and not plain.writable
    with_waves = card_discs.survey_discs(lib, card, cues, {disc: "D"}, waves=True)[0]
    assert with_waves.status == "Not on the card: 3 waveforms to add"
    assert with_waves.writable and with_waves.waves_to_add == 3


def test_a_survey_with_tracks_still_to_analyse_is_writable_and_says_so(lib, tmp_path):
    disc, _ = disc_of_files(lib, tmp_path)
    survey = card_discs.survey_discs(lib, (cc.IndexFile(), {}), card_discs.RekordboxCues(lib), {disc: "D"}, waves=True)[0]  # fmt: skip
    assert survey.status == "3 waveforms to add" and survey.writable
