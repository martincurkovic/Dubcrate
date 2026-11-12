"""The Explorer's side of the library: a hidden source whose folders are synced one at a time."""

import sqlite3
from pathlib import Path

import pytest

from dubcrate.library import Library, TrackRecord
from dubcrate.library import store as store_module
from dubcrate.library.store import EXPLORER_PATH


def rec(directory: str, name: str, **kw) -> TrackRecord:
    path = f"{directory}/{name}"
    return TrackRecord(
        ext_id=path, path=path, title=kw.pop("title", name), duration=100, **kw
    )


@pytest.fixture
def lib():
    with Library.open(":memory:") as library:
        yield library


def names(lib, node_id):
    return [t.title for t in lib.tracks(node_id=node_id)]


def test_a_synced_folder_is_a_list_of_tracks_the_explorer_can_show(lib):
    assert lib.explorer_node("/m/a") is None
    node = lib.sync_directory("/m/a", [rec("/m/a", "one.mp3"), rec("/m/a", "two.mp3")])
    assert lib.explorer_node("/m/a") == node
    assert names(lib, node) == ["one.mp3", "two.mp3"]  # in the order they were given


def test_the_explorers_tracks_are_not_part_of_the_library(lib):
    lib.sync_directory("/m/a", [rec("/m/a", "one.mp3")])
    assert lib.sources() == []
    assert lib.tracks() == [] and lib.count_tracks() == 0
    assert lib.tracks(search="one") == []  # nor do they turn up in a search of it


def test_a_real_source_next_to_the_explorer_is_unaffected(lib):
    lib.replace_source("folder", "/real", "Real", [rec("/real", "kept.mp3")])
    lib.sync_directory("/m/a", [rec("/m/a", "one.mp3")])
    assert [t.title for t in lib.tracks()] == ["kept.mp3"]
    assert [s.name for s in lib.sources()] == ["Real"]
    assert lib.sources()[0].track_count == 1


def test_reading_a_folder_again_keeps_the_ids_and_follows_changes(lib):
    node = lib.sync_directory("/m/a", [rec("/m/a", "one.mp3"), rec("/m/a", "two.mp3")])
    before = {t.title: t.id for t in lib.tracks(node_id=node)}
    again = lib.sync_directory(
        "/m/a",
        [rec("/m/a", "one.mp3", title="Renamed"), rec("/m/a", "three.mp3")],
    )
    assert again == node  # the same list
    after = {t.title: t.id for t in lib.tracks(node_id=node)}
    assert set(after) == {"Renamed", "three.mp3"}
    assert after["Renamed"] == before["one.mp3"]  # matched on its path: same id


def test_a_track_that_left_the_folder_goes_unless_a_disc_uses_it(lib):
    node = lib.sync_directory(
        "/m/a",
        [rec("/m/a", "gone.mp3"), rec("/m/a", "used.mp3"), rec("/m/a", "stay.mp3")],
    )
    ids = {t.title: t.id for t in lib.tracks(node_id=node)}
    disc = lib.create_disc("D")
    lib.add_to_disc(disc, [ids["used.mp3"]])
    lib.sync_directory("/m/a", [rec("/m/a", "stay.mp3")])
    assert names(lib, node) == ["stay.mp3"]
    assert lib.track(ids["gone.mp3"]) is None  # deleted
    kept = lib.track(ids["used.mp3"])
    assert kept is not None and kept.stale  # the disc keeps it, marked
    assert [e.track.title for e in lib.disc_entries(disc)] == ["used.mp3"]


def test_a_stale_track_is_revived_when_it_comes_back(lib):
    node = lib.sync_directory("/m/a", [rec("/m/a", "x.mp3")])
    [track] = lib.tracks(node_id=node)
    disc = lib.create_disc("D")
    lib.add_to_disc(disc, [track.id])
    lib.sync_directory("/m/a", [])
    assert lib.track(track.id).stale
    lib.sync_directory("/m/a", [rec("/m/a", "x.mp3")])
    revived = lib.track(track.id)
    assert not revived.stale and names(lib, node) == ["x.mp3"]


def test_folders_are_synced_independently(lib):
    a = lib.sync_directory("/m/a", [rec("/m/a", "a1.mp3")])
    b = lib.sync_directory("/m/b", [rec("/m/b", "b1.mp3")])
    lib.sync_directory("/m/a", [])
    assert names(lib, a) == [] and names(lib, b) == ["b1.mp3"]


def test_an_explorer_track_can_go_on_a_disc_like_any_other(lib):
    node = lib.sync_directory("/m/a", [rec("/m/a", "one.mp3"), rec("/m/a", "two.mp3")])
    ids = [t.id for t in lib.tracks(node_id=node)]
    disc = lib.create_disc("D")
    result = lib.add_to_disc(disc, ids)
    assert list(result.added) == ids
    assert [e.track.title for e in lib.disc_entries(disc)] == ["one.mp3", "two.mp3"]
    assert lib.tracks_on_discs() == set(ids)


def test_pruning_forgets_what_no_disc_uses(lib):
    node = lib.sync_directory(
        "/m/a", [rec("/m/a", "used.mp3"), rec("/m/a", "free.mp3")]
    )
    lib.sync_directory("/m/b", [rec("/m/b", "other.mp3")])
    used = next(t for t in lib.tracks(node_id=node) if t.title == "used.mp3")
    disc = lib.create_disc("D")
    lib.add_to_disc(disc, [used.id])
    assert lib.prune_explorer() == 2  # free.mp3 and other.mp3
    assert names(lib, node) == ["used.mp3"]
    assert lib.explorer_node("/m/b") is None  # a folder with nothing left is forgotten
    assert lib.explorer_node("/m/a") == node
    assert [e.track.title for e in lib.disc_entries(disc)] == ["used.mp3"]


def test_pruning_with_nothing_read_does_nothing(lib):
    assert lib.prune_explorer() == 0


def test_reading_after_a_prune_starts_clean(lib):
    lib.sync_directory("/m/a", [rec("/m/a", "one.mp3")])
    lib.prune_explorer()
    node = lib.sync_directory("/m/a", [rec("/m/a", "one.mp3")])
    assert names(lib, node) == ["one.mp3"]


def test_the_measured_cd_length_is_dropped_when_the_file_size_changes(lib):
    node = lib.sync_directory("/m/a", [rec("/m/a", "x.mp3", file_size=100)])
    [track] = lib.tracks(node_id=node)
    lib.set_cd_sectors(track.id, 5000)
    lib.sync_directory("/m/a", [rec("/m/a", "x.mp3", file_size=100)])
    assert lib.track(track.id).cd_sectors == 5000  # the same file: kept
    lib.sync_directory("/m/a", [rec("/m/a", "x.mp3", file_size=999)])
    assert lib.track(track.id).cd_sectors is None  # a different file: measure it again


def test_the_explorers_source_is_hidden_and_has_its_own_path(lib):
    lib.sync_directory("/m/a", [rec("/m/a", "one.mp3")])
    row = lib._conn.execute("SELECT hidden, kind FROM source").fetchone()
    assert (row["hidden"], row["kind"]) == (1, "folder")
    assert lib._conn.execute("SELECT path FROM source").fetchone()[0] == EXPLORER_PATH


def test_a_library_from_before_the_explorer_is_upgraded(tmp_path):
    """Schema 9 adds source.hidden: a library at 8 keeps its sources, all of them shown."""
    conn = sqlite3.connect(str(tmp_path / "old.db"))
    conn.execute("PRAGMA foreign_keys = ON")
    for target, script in enumerate(store_module._MIGRATIONS[:8], start=1):
        with conn:
            if callable(script):
                script(conn)
            else:
                for statement in script.split(";"):
                    if statement.strip():
                        conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {target}")
    conn.execute(
        "INSERT INTO source (kind, path, name, imported_at) VALUES"
        " ('rekordbox', '/x', 'rekordbox', 'then')"
    )
    conn.commit()
    conn.close()
    with Library.open(Path(tmp_path / "old.db")) as library:
        assert [s.name for s in library.sources()] == ["rekordbox"]
        library.sync_directory("/m/a", [rec("/m/a", "one.mp3")])
        assert [s.name for s in library.sources()] == ["rekordbox"]
