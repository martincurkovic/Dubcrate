"""Dubcrate discs as card sync requests (no ffmpeg, no rekordbox: lengths are set, analysis is stood in)."""

from pathlib import Path

import pytest

from dubcrate import card_discs, card_sync
from dubcrate import cdj_card as cc
from dubcrate.analysis import Cue
from dubcrate.library import Library, TrackRecord

DATA = Path(__file__).parent / "data" / "cdj_card"


def cd2():
    return cc.toc_from_drutil((DATA / "CD2_TOC.txt").read_text(encoding="utf-8"))


def lengths_of(starts, lead_out):
    return [b - a for a, b in zip(starts, [*starts[1:], lead_out], strict=True)]


def rec(ext_id, **kw):
    return TrackRecord(
        ext_id=ext_id, path=f"/nonexistent/{ext_id}.flac", title=ext_id, **kw
    )


@pytest.fixture
def lib():
    with Library.open(":memory:") as library:
        yield library


def make_disc(lib, lengths, *, kind="audio", pregap=0, source="folder", **rec_kw):
    """A disc whose tracks have these measured lengths (sectors)."""
    lib.replace_source(
        source, f"/{source}{len(lib.sources())}", "S",
        [rec(f"{source}{len(lib.sources())}-{n}", duration=n + 1.0, **rec_kw) for n in range(len(lengths))],
    )  # fmt: skip
    ids = [t.id for t in lib.tracks(sort="path")][-len(lengths) :]
    for track_id, sectors in zip(ids, lengths, strict=True):
        lib.set_cd_sectors(track_id, sectors)
    disc_id = lib.create_disc("D", pregap_sectors=pregap, kind=kind)
    lib.add_to_disc(disc_id, ids)
    return disc_id


def cd2_disc(lib, **kw):
    return make_disc(lib, lengths_of(*cd2()), **kw)


def test_a_disc_of_measured_tracks_gives_the_id_of_its_toc(lib):
    result = card_discs.prepare_disc(lib, cd2_disc(lib))
    assert result.problems == []
    # the lengths are CD 2's, so the ID is the one the player made for CD 2
    assert result.request.disc_id == cc.DiscId.from_toc(*cd2())


def test_a_disc_not_marked_as_burned_says_so(lib):
    disc_id = cd2_disc(lib)
    assert any(
        "isn't marked as burned" in w
        for w in card_discs.prepare_disc(lib, disc_id).warnings
    )
    lib.mark_burned(disc_id)
    assert card_discs.prepare_disc(lib, disc_id).warnings == []


def test_a_disc_with_gaps_is_warned_about_not_refused(lib):
    disc_id = cd2_disc(lib, pregap=150)
    lib.mark_burned(disc_id)
    result = card_discs.prepare_disc(lib, disc_id)
    assert result.request is not None
    assert any("gaps between tracks" in w for w in result.warnings)
    # and the gaps are in the TOC it computed: each later track starts 150 frames further on
    starts, _ = card_discs.disc_toc(lib.disc_entries(disc_id), 150)
    assert starts[1] - starts[0] == lengths_of(*cd2())[0] + 150


def test_only_audio_discs_with_two_tracks_and_measured_lengths_can_be_synced(lib):
    mp3_disc = make_disc(lib, [1000, 1000, 1000], kind="mp3")
    assert "MP3 discs" in card_discs.prepare_disc(lib, mp3_disc).problems[0]

    one = make_disc(lib, [5000])
    assert "one track" in card_discs.prepare_disc(lib, one).problems[0]

    unmeasured = cd2_disc(lib)
    first = lib.disc_entries(unmeasured)[0].track.id
    lib._conn.execute("UPDATE track SET cd_sectors = NULL WHERE id = ?", (first,))
    result = card_discs.prepare_disc(lib, unmeasured)
    assert "1 of 12 tracks have no measured length" in result.problems[0]
    assert result.request is None


def test_toc_differences_name_each_track_that_moved_and_the_end():
    planned = ([150, 1000, 2000], 3000)
    assert card_discs.toc_differences(planned, planned) == []
    assert card_discs.toc_differences(planned, ([150, 1010, 2000], 3075)) == [
        "track 2 starts at 0:13.35, planned 0:13.25",
        "it ends at 0:41.00, planned 0:40.00",
    ]
    assert (
        "has 2 tracks, this disc 3"
        in (card_discs.toc_differences(planned, ([150, 1000], 2000))[0])
    )


def test_a_disc_in_the_drive_gives_the_id_even_with_no_measured_lengths(lib):
    disc_id = cd2_disc(lib)
    first = lib.disc_entries(disc_id)[0].track.id
    lib._conn.execute("UPDATE track SET cd_sectors = NULL WHERE id = ?", (first,))
    result = card_discs.prepare_disc(lib, disc_id, toc=cd2())
    assert result.problems == [] and result.from_drive
    assert result.request.disc_id == cc.DiscId.from_toc(*cd2())
    assert (
        result.warnings == []
    )  # (nothing to compare with, and not "not burned" either)


def test_a_disc_in_the_drive_that_matches_the_plan_has_no_warnings(lib):
    result = card_discs.prepare_disc(lib, cd2_disc(lib), toc=cd2())
    assert (
        result.warnings == []
    )  # not marked as burned doesn't matter: the disc is here


def test_a_disc_in_the_drive_that_differs_from_the_plan_uses_the_drive_and_says_so(lib):
    starts, lead_out = cd2()
    edited = ([*starts[:-1], starts[-1] + 75], lead_out + 75)
    result = card_discs.prepare_disc(lib, cd2_disc(lib), toc=edited)
    assert result.request.disc_id == cc.DiscId.from_toc(*edited)
    assert "isn't the disc as planned" in result.warnings[0]
    assert "follows the disc in the drive" in result.warnings[0]


def test_cues_land_on_the_drives_positions_not_the_planned_ones(lib):
    disc_id = cd2_disc(lib, source="rekordbox")
    entries = lib.disc_entries(disc_id)
    cues = keyed(entries, {1: [Cue("memory", 10.0)]})
    starts, lead_out = cd2()
    moved = ([starts[0], starts[1] + 300, *starts[2:]], lead_out)
    result = card_discs.prepare_disc(
        lib, disc_id, card_discs.RekordboxCues(lib, load=lambda _p: cues), toc=moved
    )
    assert result.request.wanted == [card_sync.WantedCue(2, starts[1] + 300 + 750)]


def test_a_drive_disc_with_another_track_count_is_refused(lib):
    starts, lead_out = cd2()
    result = card_discs.prepare_disc(lib, cd2_disc(lib), toc=(starts[:-1], lead_out))
    assert result.request is None
    assert "11 tracks and this one 12" in result.problems[0]


def keyed(entries, cues_by_index):
    """What rekordbox's database would hold: cues by the track's content ID (its ext_id)."""
    return {entries[i].track.ext_id: cues for i, cues in cues_by_index.items()}


def test_rekordbox_cues_of_each_track_become_cues_at_the_discs_positions(lib):
    disc_id = cd2_disc(lib, source="rekordbox")
    entries = lib.disc_entries(disc_id)
    cues = keyed(
        entries,
        {
            0: [Cue("memory", 15.0), Cue("hot", 40.0, slot=2)],
            11: [Cue("loop", 30.0, end=34.0)],
        },
    )
    result = card_discs.prepare_disc(
        lib, disc_id, card_discs.RekordboxCues(lib, load=lambda _path: cues)
    )
    starts, _ = cd2()
    assert result.cue_tracks == 2
    assert result.request.wanted == [
        card_sync.WantedCue(1, 150 + 15 * 75),
        card_sync.WantedCue(1, 150 + 40 * 75),
        card_sync.WantedCue(12, starts[11] + 30 * 75, end=starts[11] + 34 * 75),
    ]


def test_rekordbox_is_read_once_however_many_tracks_ask(lib):
    disc_id = cd2_disc(lib, source="rekordbox")
    calls = []
    cues = card_discs.RekordboxCues(lib, load=lambda path: calls.append(path) or {})
    card_discs.prepare_disc(lib, disc_id, cues)
    assert calls == [Path("/rekordbox0")]  # the source's path (rekordbox's master.db)


def test_a_file_in_a_folder_that_is_also_in_rekordbox_uses_rekordbox_cues(lib):
    shared = "/music/same.flac"
    lib.replace_source("rekordbox", "/rb/master.db", "rb", [
        TrackRecord(ext_id="4711", path=shared, title="t", duration=60.0)
    ])  # fmt: skip
    lib.replace_source("folder", "/music", "music", [
        TrackRecord(ext_id=shared, path=shared, title="t", duration=60.0)
    ])  # fmt: skip
    folder_track = next(t for t in lib.tracks() if t.ext_id == shared)
    cues = card_discs.RekordboxCues(
        lib, load=lambda _p: {"4711": [Cue("hot", 5.0, slot=1)]}
    )
    assert cues.for_track(folder_track) == [Cue("hot", 5.0, slot=1)]


def test_a_track_with_no_rekordbox_twin_has_no_cues_and_rekordbox_is_not_read(lib):
    disc_id = cd2_disc(lib)  # a folder's tracks
    cues = card_discs.RekordboxCues(lib, load=lambda _p: pytest.fail("not asked"))
    result = card_discs.prepare_disc(lib, disc_id, cues)
    assert result.request.wanted == [] and result.cue_tracks == 0


# -- looking at every disc against a card (the Sync tab's list) -------------------------------------


def empty_card():
    return cc.IndexFile([], 1), {}


def survey(lib, disc_id=None, card="empty", cues=None):
    cues = cues or (lambda _path: {})
    paths = {d.id: d.name for d in lib.discs()}
    return card_discs.survey_discs(
        lib,
        empty_card() if card == "empty" else card,
        card_discs.RekordboxCues(lib, load=cues),
        paths,
    )


def test_no_card_means_no_plan_and_a_status_that_says_to_choose_one(lib):
    cd2_disc(lib)
    (found,) = survey(lib, card=None)
    assert found.plan is None and found.status == "Choose a card" and not found.writable


def test_a_disc_with_rekordbox_cues_is_new_on_an_empty_card_with_its_cues_counted(lib):
    disc_id = cd2_disc(lib, source="rekordbox")
    entries = lib.disc_entries(disc_id)
    cues = keyed(entries, {0: [Cue("memory", 15.0), Cue("hot", 40.0, slot=2)]})
    (found,) = survey(lib, cues=lambda _p: cues)
    assert found.status == "Not on the card: 2 cues to add"
    assert found.writable and found.to_add == 2
    text = "\n".join(found.detail())
    assert "rekordbox cues found on 1 of 12 tracks" in text
    assert "+ cue  track 1 at 0:17.00" in text  # 15 s in, after the 2 s lead-in


def test_a_disc_whose_cues_are_all_on_the_card_is_in_sync(lib):
    disc_id = cd2_disc(lib, source="rekordbox")
    cues = keyed(lib.disc_entries(disc_id), {0: [Cue("memory", 15.0)]})
    (first,) = survey(lib, cues=lambda _p: cues)
    plan = card_sync.plan_card(*empty_card(), [first.input.request])
    card = (plan.index, plan.writes())
    (again,) = survey(lib, card=card, cues=lambda _p: cues)
    assert again.status == "In sync" and not again.writable


def test_a_disc_with_no_rekordbox_cues_has_nothing_to_write(lib):
    cd2_disc(lib)
    (found,) = survey(lib)
    assert found.status == "No rekordbox cues" and not found.writable


def test_a_disc_that_cant_be_synced_says_so_in_the_list_and_why_in_the_detail(lib):
    disc_id = cd2_disc(lib)
    first = lib.disc_entries(disc_id)[0].track.id
    lib._conn.execute("UPDATE track SET cd_sectors = NULL WHERE id = ?", (first,))
    (found,) = survey(lib)
    assert found.status == "Can't sync" and found.plan is None
    assert "can't sync: 1 of 12 tracks have no measured length" in "\n".join(
        found.detail()
    )


def test_only_audio_discs_with_tracks_are_listed(lib):
    cd2_disc(lib)
    make_disc(lib, [1000, 1000, 1000], kind="mp3")
    lib.create_disc("Empty")
    assert [f.disc.name for f in survey(lib)] == ["D"]


def test_an_unreadable_rekordbox_database_is_said_for_each_disc_not_raised(lib):
    cd2_disc(lib, source="rekordbox")

    def broken(_path):
        raise card_discs.RekordboxError("it is locked")

    (found,) = survey(lib, cues=broken)
    assert found.status == "Can't sync"
    assert "Couldn't read rekordbox's cues: it is locked" in found.input.problems[0]
