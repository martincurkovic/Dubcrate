"""Discs are playlists: they live in folders, remember when they were burned, and can be copied."""

import sqlite3

import pytest

from dubcrate.library import Library, TrackRecord
from dubcrate.library import store as store_module
from dubcrate.library.store import DiscError


def rec(ext_id, seconds=100) -> TrackRecord:
    return TrackRecord(
        ext_id=ext_id,
        path=f"/nonexistent/{ext_id}.mp3",
        title=f"Song {ext_id}",
        artist="Artist",
        duration=seconds,
    )


@pytest.fixture
def lib():
    with Library.open(":memory:") as library:
        library.replace_source("rekordbox", "/db", "rb", [rec(c) for c in "abcd"])
        yield library


def track_ids(lib):
    return [t.id for t in lib.tracks(sort="title")]


# -- folders --------------------------------------------------------------------


def test_a_new_disc_is_at_the_top_level_and_has_not_been_burned(lib):
    s = lib.disc(lib.create_disc("Night"))
    assert (s.folder_id, s.last_burned_at) == (None, None)


def test_folders_can_be_created_nested_renamed_and_listed(lib):
    gigs = lib.create_disc_folder("Gigs")
    sets = lib.create_disc_folder("Sets", parent_id=gigs)
    lib.rename_disc_folder(sets, "Club Sets")
    folders = {f.id: f for f in lib.disc_folders()}
    assert (folders[gigs].name, folders[gigs].parent_id) == ("Gigs", None)
    assert (folders[sets].name, folders[sets].parent_id) == ("Club Sets", gigs)


def test_a_disc_can_be_made_in_a_folder_and_moved_between_them(lib):
    a, b = lib.create_disc_folder("A"), lib.create_disc_folder("B")
    disc = lib.create_disc("Night", folder_id=a)
    assert lib.disc(disc).folder_id == a
    lib.move_disc(disc, b)
    assert lib.disc(disc).folder_id == b
    lib.move_disc(disc, None)  # out to the top level
    assert lib.disc(disc).folder_id is None


def test_moving_a_disc_does_not_change_what_is_on_it(lib):
    disc = lib.create_disc("Night")
    lib.add_to_disc(disc, track_ids(lib))
    lib.set_entry_text(disc, 0, "title", "Edited")
    lib.move_disc(disc, lib.create_disc_folder("F"))
    assert next(e.title for e in lib.disc_entries(disc)) == "Edited"
    assert lib.disc(disc).track_count == 4


def test_a_folder_can_move_into_another_but_never_into_itself_or_its_own_child(lib):
    a = lib.create_disc_folder("A")
    b = lib.create_disc_folder("B", parent_id=a)
    c = lib.create_disc_folder("C")
    lib.move_disc_folder(c, a)
    assert {f.id: f.parent_id for f in lib.disc_folders()}[c] == a
    with pytest.raises(DiscError, match="inside itself"):
        lib.move_disc_folder(a, a)
    with pytest.raises(DiscError, match="inside itself"):
        lib.move_disc_folder(a, b)  # b is inside a
    lib.move_disc_folder(b, None)
    assert {f.id: f.parent_id for f in lib.disc_folders()}[b] is None


def test_deleting_a_folder_moves_what_is_in_it_up_a_level_and_deletes_no_disc(lib):
    outer = lib.create_disc_folder("Outer")
    inner = lib.create_disc_folder("Inner", parent_id=outer)
    deep = lib.create_disc("Deep", folder_id=inner)
    mid = lib.create_disc_folder("Mid", parent_id=inner)
    lib.delete_disc_folder(inner)
    assert inner not in {f.id for f in lib.disc_folders()}
    assert lib.disc(deep).folder_id == outer  # up to the parent, not lost
    assert {f.id: f.parent_id for f in lib.disc_folders()}[mid] == outer
    assert lib.disc(deep).track_count == 0 and deep in {d.id for d in lib.discs()}


def test_deleting_a_top_level_folder_sends_its_discs_to_the_top_level(lib):
    folder = lib.create_disc_folder("F")
    disc = lib.create_disc("D", folder_id=folder)
    lib.delete_disc_folder(folder)
    assert lib.disc(disc).folder_id is None


def test_unknown_folders_and_discs_are_clear_errors(lib):
    with pytest.raises(DiscError, match="no disc folder with id 9"):
        lib.rename_disc_folder(9, "x")
    with pytest.raises(DiscError, match="no disc folder with id 9"):
        lib.create_disc("D", folder_id=9)
    disc = lib.create_disc("D")
    with pytest.raises(DiscError, match="no disc folder with id 9"):
        lib.move_disc(disc, 9)
    with pytest.raises(DiscError, match="no disc with id 99"):
        lib.move_disc(99, None)


def test_a_blank_folder_name_is_refused(lib):
    with pytest.raises(DiscError, match="needs a name"):
        lib.create_disc_folder("   ")


# -- burned: a disc just remembers when --------------------------------------------


def test_burning_a_disc_marks_when_and_the_disc_stays_fully_editable(lib):
    disc = lib.create_disc("Night")
    lib.add_to_disc(disc, track_ids(lib))
    lib.mark_burned(disc, when="2026-11-01T20:00:00+00:00")
    assert lib.disc(disc).last_burned_at == "2026-11-01T20:00:00+00:00"
    lib.remove_from_disc(disc, [0])  # still an ordinary playlist
    lib.set_entry_text(disc, 0, "title", "Changed After")
    lib.update_disc(disc, name="Renamed")
    assert lib.disc(disc).track_count == 3
    assert (
        lib.disc(disc).last_burned_at == "2026-11-01T20:00:00+00:00"
    )  # (a note, not a snapshot)


def test_marking_burned_now_uses_the_current_time_and_the_latest_wins(lib):
    disc = lib.create_disc("Night")
    lib.mark_burned(disc, when="2026-01-01T00:00:00+00:00")
    lib.mark_burned(disc)
    assert lib.disc(disc).last_burned_at > "2026-01-01T00:00:00+00:00"


# -- copying a disc --------------------------------------------------------------


def test_duplicating_a_disc_copies_tracks_edits_size_and_folder_but_not_the_burn(lib):
    folder = lib.create_disc_folder("F")
    disc = lib.create_disc(
        "Night", capacity_sectors=333_000, pregap_sectors=150, folder_id=folder
    )
    lib.add_to_disc(disc, track_ids(lib))
    lib.set_entry_text(disc, 1, "title", "Edited")
    lib.mark_burned(disc)
    copy = lib.duplicate_disc(disc)
    s = lib.disc(copy)
    assert (s.name, s.capacity_sectors, s.pregap_sectors, s.folder_id) == (
        "Night (copy)",
        333_000,
        150,
        folder,
    )
    assert s.last_burned_at is None  # the copy hasn't been burned
    assert [e.title for e in lib.disc_entries(copy)][1] == "Edited"
    lib.remove_from_disc(copy, [0])
    assert lib.disc(disc).track_count == 4  # independent


def test_a_duplicate_can_be_named(lib):
    disc = lib.create_disc("Night")
    assert lib.disc(lib.duplicate_disc(disc, "Night 2")).name == "Night 2"


# -- where a track is already used ------------------------------------------------


def test_track_usage_lists_every_disc_and_says_when_one_was_burned(lib):
    ids = track_ids(lib)
    one, two = lib.create_disc("One"), lib.create_disc("Two")
    lib.add_to_disc(one, ids[:2])
    lib.add_to_disc(two, ids[:1])
    lib.mark_burned(one, when="2026-11-01T20:00:00+00:00")
    usage = lib.track_usage(ids)
    assert [(d.name, d.burned_at) for d in usage[ids[0]].discs] == [
        ("One", "2026-11-01T20:00:00+00:00"), ("Two", None),
    ]  # fmt: skip
    assert [d.name for d in usage[ids[1]].discs] == ["One"]
    assert ids[2] not in usage


# -- the schema ---------------------------------------------------------------


def test_a_v5_library_with_burn_records_upgrades_into_playlists(tmp_path):
    path = tmp_path / "v5.db"
    conn = sqlite3.connect(path)
    for script in store_module._MIGRATIONS[:5]:
        for statement in script.split(";"):
            if statement.strip():
                conn.execute(statement)
    conn.execute("PRAGMA user_version = 5")
    conn.execute(
        "INSERT INTO source (kind, path, name, imported_at) VALUES ('folder', '/m', 'M', 'now')"
    )
    for i, name in ((1, "a"), (2, "b")):
        conn.execute(
            "INSERT INTO track (id, source_id, ext_id, path, title, artist, duration)"
            f" VALUES ({i}, 1, '{name}', '/m/{name}.mp3', 'Song {name}', 'Artist', 100)"
        )  # fmt: skip
    conn.execute(
        "INSERT INTO disc (id, name, capacity_sectors, created_at, updated_at) VALUES (1, 'Live', 360000, 'n', 'n')"
    )
    conn.execute(
        "INSERT INTO disc_track (disc_id, track_id, position) VALUES (1, 1, 0)"
    )
    # a burn of a disc that still exists, a simulated one, and a burn of a disc deleted since
    conn.execute(
        "INSERT INTO burn (id, disc_id, name, capacity_sectors, pregap_sectors, used_sectors, simulated, burned_at) VALUES (1, 1, 'Live', 360000, 0, 7500, 0, '2026-11-01T20:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO burn (id, disc_id, name, capacity_sectors, pregap_sectors, used_sectors, simulated, burned_at) VALUES (2, 1, 'Live', 360000, 0, 7500, 1, '2026-12-25T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO burn (id, disc_id, name, capacity_sectors, pregap_sectors, used_sectors, simulated, burned_at) VALUES (3, NULL, 'Gone Disc', 333000, 150, 15000, 0, '2026-10-01T10:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO burn_track (burn_id, position, track_id, title, artist, sectors, path) VALUES (3, 0, 1, 'Song a', 'Artist', 7500, '/m/a.mp3')"
    )
    conn.execute(
        "INSERT INTO burn_track (burn_id, position, track_id, title, artist, sectors, path) VALUES (3, 1, 2, 'Burned As Edited', 'Edited Artist', 7500, '/m/b.mp3')"
    )
    conn.execute(
        "INSERT INTO burn_track (burn_id, position, track_id, title, artist, sectors, path) VALUES (3, 2, NULL, 'Vanished', 'X', 7500, '/m/c.mp3')"
    )
    conn.commit()
    conn.close()
    with Library.open(path) as lib:
        assert (
            lib._conn.execute("PRAGMA user_version").fetchone()[0]
            == store_module.SCHEMA_VERSION
        )
        discs = {d.name: d for d in lib.discs()}
        # the disc that still existed remembers its (real, not simulated) burn
        assert discs["Live"].last_burned_at == "2026-11-01T20:00:00+00:00"
        # the burn of a deleted disc became a disc of its own, with what had been burned
        gone = discs["Gone Disc"]
        assert (gone.capacity_sectors, gone.pregap_sectors) == (333_000, 150)
        assert gone.last_burned_at == "2026-10-01T10:00:00+00:00"
        entries = lib.disc_entries(gone.id)
        assert [e.title for e in entries] == [
            "Song a",
            "Burned As Edited",
        ]  # the vanished track is gone
        assert (entries[1].title_override, entries[1].artist_override) == (
            "Burned As Edited",
            "Edited Artist",
        )
        assert not entries[0].edited  # unchanged text isn't an "edit"
        # and the old tables are gone
        tables = {
            r[0]
            for r in lib._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "burn" not in tables and "burn_track" not in tables
        assert "disc_folder" in tables
