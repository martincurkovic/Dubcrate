"""Memory cues set on the player, found on the card and brought back (no ffmpeg, no rekordbox, no real card)."""

from pathlib import Path

import pytest

from dubcrate import card_discs, card_import
from dubcrate import cdj_card as cc
from dubcrate.analysis import Cue
from dubcrate.library import Library, TrackRecord

DATA = Path(__file__).parent / "data" / "cdj_card"


def toc():
    return cc.toc_from_drutil((DATA / "CD2_TOC.txt").read_text(encoding="utf-8"))


STARTS, LEAD_OUT = toc()


def at(track, seconds):
    return STARTS[track - 1] + round(seconds * 75)


def file_with(*cues, hot=()):
    f = cc.TrackFile.new(cc.DiscId.from_toc(STARTS, LEAD_OUT))
    f.cues = [cc.CueEntry.memory(i, pos, track) for i, (track, pos) in enumerate(cues)]
    for slot, (track, pos) in enumerate(hot, start=1):
        f.hot_cues[slot - 1] = cc.HotCue(slot, pos, track)
    return f


def test_a_cue_nothing_knows_is_new_with_its_time_in_the_track():
    found = card_import.new_cues(file_with((2, at(2, 12.0))), STARTS, LEAD_OUT, {})
    assert found.by_track == {2: [12000]}
    assert found.total == 1


def test_cues_dubcrate_has_are_not_new_whatever_their_kind():
    known = {
        1: [Cue("memory", 5.0), Cue("hot", 20.0, slot=1), Cue("loop", 30.0, end=34.0)],
    }
    card = file_with(
        (1, at(1, 5.0)), (1, at(1, 20.0) + 2), (1, at(1, 30.0) - 1), (1, at(1, 60.0))
    )
    found = card_import.new_cues(card, STARTS, LEAD_OUT, known)
    assert found.by_track == {
        1: [60000]
    }  # a hot cue or loop written as a memory cue is not a new one


def test_the_same_new_cue_twice_is_one_and_times_come_out_sorted():
    card = file_with((1, at(1, 40.0)), (1, at(1, 10.0)), (1, at(1, 40.0) + 1))
    assert card_import.new_cues(card, STARTS, LEAD_OUT, {}).by_track == {
        1: [10000, 40000]
    }


def test_loops_and_player_hot_cues_are_counted_and_not_imported():
    card = file_with((1, at(1, 3.0)), hot=[(1, at(1, 9.0)), (2, at(2, 9.0))])
    card.cues.append(cc.CueEntry.loop(1, at(1, 50.0), at(1, 52.0), 1))
    found = card_import.new_cues(card, STARTS, LEAD_OUT, {})
    assert found.by_track == {1: [3000]}
    assert (found.loops, found.hot_cues) == (1, 2)


def test_a_cue_is_placed_by_its_position_when_the_track_it_names_is_wrong():
    card = file_with((1, at(3, 7.0)))  # names track 1, but lies in track 3
    assert card_import.new_cues(card, STARTS, LEAD_OUT, {}).by_track == {3: [7000]}


def test_a_cue_outside_the_disc_is_counted():
    card = file_with((1, LEAD_OUT + 500))
    found = card_import.new_cues(card, STARTS, LEAD_OUT, {})
    assert found.outside == 1 and found.by_track == {}


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


def test_the_library_half_names_the_tracks_and_skips_what_dubcrate_has(lib):
    card_file = file_with((2, at(2, 8.0)), (2, at(2, 15.0)), (3, at(3, 1.0)))
    card, surveys = disc_on_card(lib, card_file)
    track2 = lib.disc_entries(surveys[0].disc.id)[1].track
    lib.set_memory_cues(track2.path, [(8000, "mine")])  # already edited in Dubcrate
    findings = card_import.survey_new_cues(
        lib, card, card_discs.RekordboxCues(lib, load=lambda _p: {}), surveys
    )
    assert [d.path for d in findings] == ["F / D"]
    tracks = {t.number: t for t in findings[0].tracks}
    assert (
        tracks[2].cues == [15000]
        and tracks[2].title == "T1"
        and tracks[2].artist == "A"
    )
    assert tracks[3].cues == [1000]
    assert findings[0].total == 2


def test_a_track_keeps_room_for_ten_memory_cues(lib):
    card_file = file_with(*[(1, at(1, 10.0 * n)) for n in range(1, 7)])
    card, surveys = disc_on_card(lib, card_file)
    track1 = lib.disc_entries(surveys[0].disc.id)[0].track
    lib.set_memory_cues(
        track1.path, [(n * 1000 + 500, "") for n in range(1, 6)]
    )  # five, none on the card
    findings = card_import.survey_new_cues(
        lib, card, card_discs.RekordboxCues(lib, load=lambda _p: {}), surveys
    )
    found = findings[0].tracks[0]
    assert found.cues == [10000, 20000, 30000, 40000, 50000]
    assert found.over_limit == 1


def test_a_disc_not_on_the_card_or_with_nothing_new_gives_nothing(lib):
    card, surveys = disc_on_card(lib, file_with())
    cues = card_discs.RekordboxCues(lib, load=lambda _p: {})
    assert card_import.survey_new_cues(lib, card, cues, surveys) == []
    empty = (cc.IndexFile(), {})
    assert card_import.survey_new_cues(lib, empty, cues, surveys) == []
