import pytest

from dubcrate.library import Library, TrackRecord
from dubcrate.library.store import DiscError


def rec(ext_id: str, seconds: float = 100) -> TrackRecord:
    return TrackRecord(
        ext_id=ext_id,
        path=f"/nonexistent/{ext_id}.mp3",
        title=ext_id,
        artist="A",
        duration=seconds,
    )


@pytest.fixture
def lib():
    with Library.open(":memory:") as library:
        yield library


@pytest.fixture
def disc(lib):
    lib.replace_source("folder", "/m", "M", [rec(f"t{i}") for i in range(3)])
    ids = [t.id for t in lib.tracks(sort="path")]
    disc_id = lib.create_disc("Night")
    lib.add_to_disc(disc_id, ids)
    return disc_id


def test_a_burn_saves_what_was_on_the_disc(lib, disc):
    burn_id = lib.record_burn(disc, drive="LiteOn", speed=10)
    b = lib.burn(burn_id)
    assert (b.disc_id, b.name, b.drive, b.speed, b.simulated) == (
        disc, "Night", "LiteOn", 10, False,
    )  # fmt: skip
    assert [t.title for t in b.tracks] == ["t0", "t1", "t2"]
    assert b.used_sectors == lib.disc(disc).used_sectors
    assert b.track_count == 3


def test_editing_the_disc_afterwards_leaves_the_record_alone(lib, disc):
    burn_id = lib.record_burn(disc)
    lib.remove_from_disc(disc, [0])
    lib.update_disc(disc, name="Renamed")
    b = lib.burn(burn_id)
    assert b.name == "Night"
    assert [t.title for t in b.tracks] == ["t0", "t1", "t2"]


def test_a_record_survives_deleting_the_disc(lib, disc):
    burn_id = lib.record_burn(disc)
    lib.delete_disc(disc)
    b = lib.burn(burn_id)
    assert b.disc_id is None
    assert b.track_count == 3


def test_a_record_survives_removing_the_source(lib, disc):
    burn_id = lib.record_burn(disc)
    lib.delete_disc(disc)
    (source,) = lib.sources()
    lib.remove_source(source.id)
    b = lib.burn(burn_id)
    assert [t.track_id for t in b.tracks] == [None, None, None]
    assert [t.title for t in b.tracks] == ["t0", "t1", "t2"]


def test_burns_list_newest_first_and_filter_by_disc(lib, disc):
    other = lib.create_disc("Other")
    first = lib.record_burn(disc)
    second = lib.record_burn(disc, simulated=True)
    third = lib.record_burn(other)
    assert [b.id for b in lib.burns()] == [third, second, first]
    assert [b.id for b in lib.burns(disc)] == [second, first]
    assert lib.burn(second).simulated is True


def test_duplicating_makes_an_editable_copy(lib, disc):
    burn_id = lib.record_burn(disc)
    result = lib.duplicate_burn(burn_id)
    assert result.skipped == ()
    assert lib.disc(result.disc_id).name == "Night (copy)"
    assert [e.track.title for e in lib.disc_entries(result.disc_id)] == [
        "t0", "t1", "t2",
    ]  # fmt: skip
    lib.remove_from_disc(result.disc_id, [0])
    assert lib.burn(burn_id).track_count == 3


def test_duplicating_keeps_capacity_pregap_and_accepts_a_name(lib):
    lib.replace_source("folder", "/m", "M", [rec("a")])
    disc_id = lib.create_disc("D", capacity_sectors=333_000, pregap_sectors=150)
    lib.add_to_disc(disc_id, [t.id for t in lib.tracks()])
    result = lib.duplicate_burn(lib.record_burn(disc_id), name="Again")
    s = lib.disc(result.disc_id)
    assert (s.name, s.capacity_sectors, s.pregap_sectors) == ("Again", 333_000, 150)


def test_duplicating_skips_tracks_that_left_the_library(lib, disc):
    burn_id = lib.record_burn(disc)
    lib.delete_disc(disc)
    # t1 vanishes from the source on re-import; nothing uses it, so it is removed
    lib.replace_source("folder", "/m", "M", [rec("t0"), rec("t2")])
    result = lib.duplicate_burn(burn_id)
    assert [t.title for t in result.skipped] == ["t1"]
    assert [e.track.title for e in lib.disc_entries(result.disc_id)] == ["t0", "t2"]


def test_deleting_a_burn_record_keeps_the_disc(lib, disc):
    burn_id = lib.record_burn(disc)
    lib.delete_burn(burn_id)
    assert lib.burns() == []
    assert lib.disc(disc).track_count == 3
    with pytest.raises(DiscError, match="no burn with id"):
        lib.burn(burn_id)


def test_track_usage_reports_planned_discs_and_burns(lib, disc):
    ids = [t.id for t in lib.tracks(sort="path")]
    lib.record_burn(disc)
    other = lib.create_disc("Other")
    lib.add_to_disc(other, ids[:1])
    usage = lib.track_usage(ids)
    assert [d.name for d in usage[ids[0]].discs] == ["Night", "Other"]
    assert [b.name for b in usage[ids[0]].burns] == ["Night"]
    assert [d.name for d in usage[ids[1]].discs] == ["Night"]


def test_track_usage_leaves_out_unused_tracks_and_simulated_burns(lib, disc):
    lib.replace_source("folder", "/n", "N", [rec("fresh")])
    fresh = next(t.id for t in lib.tracks() if t.title == "fresh")
    used = [t.id for t in lib.tracks(sort="path") if t.title != "fresh"]
    lib.delete_disc(disc)
    burn_disc = lib.create_disc("Test run")
    lib.add_to_disc(burn_disc, used[:1])
    lib.record_burn(burn_disc, simulated=True)
    lib.delete_disc(burn_disc)
    assert lib.track_usage([fresh, *used]) == {}


def test_track_usage_keeps_burns_of_deleted_discs(lib, disc):
    ids = [t.id for t in lib.tracks(sort="path")]
    lib.record_burn(disc)
    lib.delete_disc(disc)
    usage = lib.track_usage(ids)
    assert usage[ids[0]].discs == ()
    assert [b.name for b in usage[ids[0]].burns] == ["Night"]


def test_track_usage_handles_more_ids_than_one_query_chunk(lib):
    assert lib.track_usage(range(1, 1500)) == {}
