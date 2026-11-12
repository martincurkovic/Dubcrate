import sqlite3

import pytest

from dubcrate.cd import DISC_74_SECTORS, DISC_80_SECTORS, MAX_TRACKS
from dubcrate.library import Library, NodeRecord, TrackRecord
from dubcrate.library import store as store_module
from dubcrate.library.store import DiscError, LibraryError


def rec(ext_id: str, seconds: float = 100, **kw) -> TrackRecord:
    return TrackRecord(
        ext_id=ext_id,
        path=f"/nonexistent/{ext_id}.mp3",
        title=ext_id,
        duration=seconds,
        **kw,
    )


@pytest.fixture
def lib():
    with Library.open(":memory:") as library:
        yield library


def seed(lib, seconds_each=100, count=5, path="/m"):
    lib.replace_source(
        "folder", path, "M", [rec(f"t{i}", seconds_each) for i in range(count)]
    )
    return [t.id for t in lib.tracks(sort="path")]


def titles(lib, disc_id):
    return [e.track.title for e in lib.disc_entries(disc_id)]


def test_a_new_disc_is_empty_80_minutes_and_gapless(lib):
    disc_id = lib.create_disc("Night")
    s = lib.disc(disc_id)
    assert (s.name, s.capacity_sectors, s.pregap_sectors) == (
        "Night",
        DISC_80_SECTORS,
        0,
    )
    assert (s.track_count, s.used_sectors, s.free_sectors, s.over) == (
        0,
        0,
        DISC_80_SECTORS,
        False,
    )


def test_unknown_disc_is_a_clear_error(lib):
    with pytest.raises(DiscError, match="no disc with id 9"):
        lib.disc(9)


def test_adding_tracks_keeps_order_and_totals_their_lengths(lib):
    ids = seed(lib)
    disc_id = lib.create_disc("D")
    result = lib.add_to_disc(disc_id, ids[:3])
    assert result.added == tuple(ids[:3]) and result.rejected == ()
    assert titles(lib, disc_id) == ["t0", "t1", "t2"]
    s = lib.disc(disc_id)
    # Unmeasured tracks use the conservative estimate: 100 s + 1 s margin each.
    assert s.track_count == 3 and s.used_sectors == 3 * 101 * 75


def test_measured_length_replaces_the_estimate(lib):
    ids = seed(lib)
    disc_id = lib.create_disc("D")
    lib.add_to_disc(disc_id, ids[:1])
    assert lib.disc(disc_id).used_sectors == 101 * 75
    lib.set_cd_sectors(ids[0], 7500)
    assert lib.disc(disc_id).used_sectors == 7500
    assert lib.disc_entries(disc_id)[0].track.length_exact


def test_pregap_counts_between_tracks_only(lib):
    ids = seed(lib)
    for i in ids:
        lib.set_cd_sectors(i, 1000)
    disc_id = lib.create_disc("D", pregap_sectors=150)
    lib.add_to_disc(disc_id, ids[:3])
    assert lib.disc(disc_id).used_sectors == 3000 + 2 * 150
    lib.update_disc(disc_id, pregap_sectors=0)
    assert lib.disc(disc_id).used_sectors == 3000


def test_adding_past_capacity_is_allowed_and_flagged_over(lib):
    ids = seed(lib, count=3)
    for i in ids:
        lib.set_cd_sectors(i, 150_000)
    disc_id = lib.create_disc("D")
    lib.add_to_disc(disc_id, ids)
    s = lib.disc(disc_id)
    assert s.over and s.free_sectors == DISC_80_SECTORS - 450_000


def test_fit_adds_only_what_still_fits(lib):
    ids = seed(lib, count=4)
    for i, sectors in zip(ids, [200_000, 200_000, 100_000, 100_000], strict=True):
        lib.set_cd_sectors(i, sectors)
    disc_id = lib.create_disc("D")
    result = lib.add_to_disc(disc_id, ids, fit=True)
    assert result.added == (ids[0], ids[2])
    assert result.rejected == (ids[1], ids[3])
    assert not lib.disc(disc_id).over


def test_strict_fit_stops_at_the_first_misfit(lib):
    ids = seed(lib, count=3)
    for i, sectors in zip(ids, [300_000, 200_000, 10_000], strict=True):
        lib.set_cd_sectors(i, sectors)
    disc_id = lib.create_disc("D")
    result = lib.add_to_disc(disc_id, ids, fit=True, strict=True)
    assert result.added == (ids[0],) and result.rejected == (ids[1], ids[2])


def test_insert_at_a_position(lib):
    ids = seed(lib)
    disc_id = lib.create_disc("D")
    lib.add_to_disc(disc_id, [ids[0], ids[1]])
    lib.add_to_disc(disc_id, [ids[4]], position=1)
    assert titles(lib, disc_id) == ["t0", "t4", "t1"]
    lib.add_to_disc(disc_id, [ids[3]], position=99)  # clamps to the end
    assert titles(lib, disc_id)[-1] == "t3"


def test_the_same_track_can_appear_twice(lib):
    [a, *_] = seed(lib)
    disc_id = lib.create_disc("D")
    lib.add_to_disc(disc_id, [a, a])
    assert titles(lib, disc_id) == ["t0", "t0"]


def test_unknown_track_ids_are_rejected_without_changing_the_disc(lib):
    ids = seed(lib)
    disc_id = lib.create_disc("D")
    with pytest.raises(LibraryError, match="unknown track"):
        lib.add_to_disc(disc_id, [ids[0], 999_999])
    assert lib.disc(disc_id).track_count == 0


def test_a_disc_holds_at_most_99_tracks(lib):
    ids = seed(lib, seconds_each=1, count=MAX_TRACKS + 1)
    disc_id = lib.create_disc("D")
    with pytest.raises(DiscError, match="at most 99"):
        lib.add_to_disc(disc_id, ids)
    assert lib.disc(disc_id).track_count == 0
    lib.add_to_disc(disc_id, ids[:MAX_TRACKS])
    with pytest.raises(DiscError):
        lib.add_to_disc(disc_id, ids[-1:])
    # With --fit the limit just stops the filling.
    other = lib.create_disc("E")
    result = lib.add_to_disc(other, ids, fit=True)
    assert len(result.added) == MAX_TRACKS and len(result.rejected) == 1


def test_remove_by_position_closes_the_gap(lib):
    ids = seed(lib)
    disc_id = lib.create_disc("D")
    lib.add_to_disc(disc_id, ids)
    lib.remove_from_disc(disc_id, [0, 2])
    assert titles(lib, disc_id) == ["t1", "t3", "t4"]
    assert [e.position for e in lib.disc_entries(disc_id)] == [0, 1, 2]
    with pytest.raises(DiscError, match="position"):
        lib.remove_from_disc(disc_id, [7])


def test_move_reorders(lib):
    ids = seed(lib, count=4)
    disc_id = lib.create_disc("D")
    lib.add_to_disc(disc_id, ids)
    lib.move_in_disc(disc_id, 0, 2)
    assert titles(lib, disc_id) == ["t1", "t2", "t0", "t3"]
    lib.move_in_disc(disc_id, 3, 0)
    assert titles(lib, disc_id) == ["t3", "t1", "t2", "t0"]
    with pytest.raises(DiscError):
        lib.move_in_disc(disc_id, 0, 9)


def test_update_and_delete_a_disc(lib):
    ids = seed(lib)
    disc_id = lib.create_disc("Old")
    lib.add_to_disc(disc_id, ids[:2])
    lib.update_disc(disc_id, name="New", capacity_sectors=DISC_74_SECTORS)
    s = lib.disc(disc_id)
    assert (s.name, s.capacity_sectors, s.pregap_sectors) == ("New", DISC_74_SECTORS, 0)
    lib.delete_disc(disc_id)
    assert lib.discs() == []
    assert lib.count_tracks() == 5  # deleting a disc never touches the library


def test_split_makes_ordered_discs_and_names_them(lib):
    ids = seed(lib, count=5)
    for i in ids:
        lib.set_cd_sectors(i, 150_000)
    result = lib.split_into_discs(ids, "Set", capacity_sectors=DISC_80_SECTORS)
    assert len(result.disc_ids) == 3 and result.too_long == ()
    assert [lib.disc(d).name for d in result.disc_ids] == ["Set 1", "Set 2", "Set 3"]
    assert [lib.disc(d).track_count for d in result.disc_ids] == [2, 2, 1]
    assert titles(lib, result.disc_ids[0]) == ["t0", "t1"]
    assert not any(lib.disc(d).over for d in result.disc_ids)


def test_split_into_one_disc_keeps_the_plain_name(lib):
    ids = seed(lib, count=2)
    result = lib.split_into_discs(ids, "Set")
    assert [lib.disc(d).name for d in result.disc_ids] == ["Set"]


def test_split_skips_tracks_longer_than_a_disc(lib):
    ids = seed(lib, count=3)
    lib.set_cd_sectors(ids[1], DISC_80_SECTORS + 1)
    result = lib.split_into_discs(ids, "Set")
    assert result.too_long == (ids[1],)
    assert titles(lib, result.disc_ids[0]) == ["t0", "t2"]


# -- what happens to discs when sources change ------------------------------


def test_reimport_keeps_a_vanished_track_that_a_disc_uses(lib):
    lib.replace_source("folder", "/m", "M", [rec("a"), rec("b"), rec("c")])
    ids = {t.ext_id: t.id for t in lib.tracks()}
    disc_id = lib.create_disc("D")
    lib.add_to_disc(disc_id, [ids["a"]])
    summary = lib.replace_source("folder", "/m", "M", [rec("c")])
    assert summary.removed == 1  # "b" is gone; "a" stays because the disc needs it
    assert any("kept 1 track" in n for n in summary.notes)
    assert [t.ext_id for t in lib.tracks()] == ["c"]  # hidden from browsing
    entries = lib.disc_entries(disc_id)
    assert [e.track.ext_id for e in entries] == ["a"] and entries[0].track.stale
    # It comes back to life if it reappears.
    lib.replace_source("folder", "/m", "M", [rec("a"), rec("c")])
    assert not lib.disc_entries(disc_id)[0].track.stale
    assert lib.count_tracks() == 2


def test_stale_tracks_are_hidden_unless_asked_for(lib):
    lib.replace_source("folder", "/m", "M", [rec("a"), rec("b")])
    [a, _b] = lib.tracks(sort="path")
    lib.add_to_disc(lib.create_disc("D"), [a.id])
    lib.replace_source("folder", "/m", "M", [rec("b")])
    assert lib.count_tracks() == 1 and lib.count_tracks(include_stale=True) == 2
    assert lib.sources()[0].track_count == 1


def test_measured_length_is_dropped_when_the_file_size_changes(lib):
    lib.replace_source(
        "folder", "/m", "M", [rec("a", file_size=1000), rec("b", file_size=2000)]
    )
    for t in lib.tracks():
        lib.set_cd_sectors(t.id, 5000)
    lib.replace_source(
        "folder", "/m", "M", [rec("a", file_size=1000), rec("b", file_size=2500)]
    )
    got = {t.ext_id: t.cd_sectors for t in lib.tracks()}
    assert got == {"a": 5000, "b": None}


def test_removing_a_source_with_tracks_on_discs_needs_force(lib):
    ids = seed(lib)
    disc_id = lib.create_disc("D")
    lib.add_to_disc(disc_id, ids[:2])
    [source] = lib.sources()
    with pytest.raises(LibraryError, match="on 1 disc"):
        lib.remove_source(source.id)
    assert lib.count_tracks() == 5
    lib.remove_source(source.id, force=True)
    assert lib.sources() == [] and lib.disc(disc_id).track_count == 0


def test_deleting_a_node_keeps_disc_contents(lib):
    lib.replace_source(
        "rekordbox",
        "/db",
        "rb",
        [rec("a")],
        [NodeRecord("p", None, "P", False, ("a",))],
    )
    [t] = lib.tracks()
    disc_id = lib.create_disc("D")
    lib.add_to_disc(disc_id, [t.id])
    lib.replace_source("rekordbox", "/db", "rb", [rec("a")], [])
    assert titles(lib, disc_id) == ["a"]


# -- schema migration ---------------------------------------------------------


def test_a_v1_database_upgrades_in_place_and_keeps_its_data(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    for statement in store_module._SCHEMA_V1.split(";"):
        if statement.strip():
            conn.execute(statement)
    conn.execute("PRAGMA user_version = 1")
    conn.execute(
        "INSERT INTO source (kind, path, name, imported_at) VALUES ('folder', '/m', 'M', 'now')"
    )
    conn.execute(
        "INSERT INTO track (id, source_id, ext_id, path, title, duration)"
        " VALUES (42, 1, 'x', '/m/x.mp3', 'Old', 200)"
    )
    conn.commit()
    conn.close()
    with Library.open(path) as lib:
        assert (
            lib._conn.execute("PRAGMA user_version").fetchone()[0]
            == store_module.SCHEMA_VERSION
        )
        [t] = lib.tracks()
        assert (t.id, t.title, t.stale, t.cd_sectors) == (42, "Old", False, None)
        disc_id = lib.create_disc("D")
        lib.add_to_disc(disc_id, [42])
        assert lib.disc(disc_id).track_count == 1


def test_reorder_moves_a_block_to_an_insertion_point(lib):
    ids = seed(lib, count=6)
    disc_id = lib.create_disc("D")
    lib.add_to_disc(disc_id, ids)
    lib.reorder_disc(disc_id, [3, 4], 1)  # drop between t0 and t1
    assert titles(lib, disc_id) == ["t0", "t3", "t4", "t1", "t2", "t5"]
    lib.reorder_disc(disc_id, [1], 6)  # drop after the last row
    assert titles(lib, disc_id) == ["t0", "t4", "t1", "t2", "t5", "t3"]


def test_reorder_to_just_after_a_blocks_own_position_keeps_order(lib):
    ids = seed(lib, count=4)
    disc_id = lib.create_disc("D")
    lib.add_to_disc(disc_id, ids)
    lib.reorder_disc(disc_id, [1, 2], 2)  # inside the block being moved
    assert titles(lib, disc_id) == ["t0", "t1", "t2", "t3"]
    lib.reorder_disc(disc_id, [2, 0], 4)  # positions are taken in disc order
    assert titles(lib, disc_id) == ["t1", "t3", "t0", "t2"]


def test_reorder_rejects_bad_positions(lib):
    ids = seed(lib, count=2)
    disc_id = lib.create_disc("D")
    lib.add_to_disc(disc_id, ids)
    with pytest.raises(DiscError):
        lib.reorder_disc(disc_id, [5], 0)
    with pytest.raises(DiscError):
        lib.reorder_disc(disc_id, [], 0)


def test_tracks_on_discs_lists_every_track_that_is_on_a_disc_but_the_excluded_one(lib):
    ids = [t.id for t in lib.tracks(sort="title")]
    a, b = lib.create_disc("A"), lib.create_disc("B")
    lib.add_to_disc(a, ids[:2])
    lib.add_to_disc(b, ids[1:3])
    assert lib.tracks_on_discs() == set(ids[:3])
    assert lib.tracks_on_discs(exclude=a) == set(ids[1:3])  # on B
    assert lib.tracks_on_discs(exclude=b) == set(ids[:2])  # on A
    lib.delete_disc(b)
    assert lib.tracks_on_discs() == set(ids[:2])
    assert lib.tracks_on_discs(exclude=99) == set(
        ids[:2]
    )  # (a disc that isn't there excludes nothing)
