import sqlite3

import pytest

from dubcrate.library import Library, NodeRecord, TrackRecord
from dubcrate.library.store import SCHEMA_VERSION


def rec(ext_id: str, path: str | None = None, **kw) -> TrackRecord:
    return TrackRecord(ext_id=ext_id, path=path or f"/nonexistent/{ext_id}.mp3", **kw)


@pytest.fixture
def lib():
    with Library.open(":memory:") as library:
        yield library


def test_first_import_adds_everything(lib):
    summary = lib.replace_source(
        "folder", "/music", "Music", [rec("a", title="A"), rec("b", title="B")]
    )
    assert (summary.added, summary.updated, summary.removed) == (2, 0, 0)
    [source] = lib.sources()
    assert (source.kind, source.name, source.track_count) == ("folder", "Music", 2)


def test_reimport_keeps_track_ids_and_syncs_changes(lib):
    lib.replace_source("folder", "/m", "M", [rec("a", title="Old"), rec("b"), rec("c")])
    ids = {t.ext_id: t.id for t in lib.tracks()}
    summary = lib.replace_source(
        "folder", "/m", "M renamed", [rec("a", title="New"), rec("c"), rec("d")]
    )
    assert (summary.added, summary.updated, summary.removed) == (1, 2, 1)
    after = {t.ext_id: t for t in lib.tracks()}
    assert set(after) == {"a", "c", "d"}
    assert after["a"].id == ids["a"] and after["c"].id == ids["c"]
    assert after["a"].title == "New"
    assert lib.sources()[0].name == "M renamed"


def test_same_ext_id_in_different_sources_is_independent(lib):
    lib.replace_source("folder", "/one", "one", [rec("x", title="one")])
    lib.replace_source("folder", "/two", "two", [rec("x", title="two")])
    assert sorted(t.title for t in lib.tracks()) == ["one", "two"]


def test_duplicate_ext_id_in_one_import_is_one_track(lib):
    summary = lib.replace_source(
        "folder", "/m", "M", [rec("a", title="1"), rec("a", title="2")]
    )
    assert summary.added == 1
    assert [t.title for t in lib.tracks()] == ["2"]


def test_missing_flag_reflects_disk(lib, tmp_path):
    real = tmp_path / "real.mp3"
    real.write_bytes(b"x")
    lib.replace_source("folder", str(tmp_path), "t", [rec("r", str(real)), rec("gone")])
    flags = {t.ext_id: t.missing for t in lib.tracks()}
    assert flags == {"r": False, "gone": True}


def test_nodes_come_back_parents_first_in_sibling_order(lib):
    nodes = [
        NodeRecord("f", None, "Folder", True),
        NodeRecord("p2", "f", "Second", False, ("b",)),
        NodeRecord("p1", "f", "First", False, ("a",)),
        NodeRecord("top", None, "Top", False),
    ]
    lib.replace_source("rekordbox", "/db", "rb", [rec("a"), rec("b")], nodes)
    [source] = lib.sources()
    got = lib.nodes(source.id)
    assert [n.name for n in got] == ["Folder", "Second", "First", "Top"]
    assert [n.is_folder for n in got] == [True, False, False, False]
    assert got[1].parent_id == got[0].id and got[3].parent_id is None


def test_node_tracks_keep_playlist_order_and_skip_unknown(lib):
    nodes = [NodeRecord("p", None, "P", False, ("c", "a", "ghost", "b"))]
    lib.replace_source("rekordbox", "/db", "rb", [rec("a"), rec("b"), rec("c")], nodes)
    [node] = lib.nodes(lib.sources()[0].id)
    assert node.track_count == 3
    assert [t.ext_id for t in lib.tracks(node_id=node.id)] == ["c", "a", "b"]
    desc = [t.ext_id for t in lib.tracks(node_id=node.id, descending=True)]
    assert desc == ["b", "a", "c"]


def test_removing_a_track_removes_it_from_nodes(lib):
    nodes = [NodeRecord("p", None, "P", False, ("a", "b"))]
    lib.replace_source("folder", "/m", "M", [rec("a"), rec("b")], nodes)
    lib.replace_source("folder", "/m", "M", [rec("a")], nodes)
    [node] = lib.nodes(lib.sources()[0].id)
    assert [t.ext_id for t in lib.tracks(node_id=node.id)] == ["a"]


def test_reimport_rebuilds_nodes_without_duplicating(lib):
    nodes = [NodeRecord("p", None, "P", False, ("a",))]
    lib.replace_source("folder", "/m", "M", [rec("a")], nodes)
    lib.replace_source("folder", "/m", "M", [rec("a")], nodes)
    assert len(lib.nodes(lib.sources()[0].id)) == 1


def test_search_requires_every_term_across_fields(lib):
    lib.replace_source(
        "folder", "/m", "M",
        [
            rec("1", title="It Gets Better", artist="Kettama"),
            rec("2", title="Comes and Goes", artist="Kettama"),
            rec("3", title="Better Off", artist="Someone"),
        ],
    )  # fmt: skip
    assert lib.count_tracks(search="kettama better") == 1
    assert lib.count_tracks(search="BETTER") == 2
    assert lib.count_tracks(search="") == 3


def test_search_treats_wildcards_literally(lib):
    lib.replace_source(
        "folder", "/m", "M", [rec("1", title="100% Pure"), rec("2", title="1000 Pure")]
    )
    assert [t.ext_id for t in lib.tracks(search="100%")] == ["1"]
    lib.replace_source(
        "folder", "/n", "N", [rec("3", title="a_b"), rec("4", title="axb")]
    )
    assert [
        t.ext_id for t in lib.tracks(source_id=lib.sources()[-1].id, search="a_b")
    ] == ["3"]


def test_sorting_limit_and_offset(lib):
    lib.replace_source(
        "folder", "/m", "M",
        [rec("1", title="b", bpm=128), rec("2", title="A", bpm=140), rec("3", title="c", bpm=120)],
    )  # fmt: skip
    assert [t.title for t in lib.tracks(sort="title")] == ["A", "b", "c"]
    assert [t.bpm for t in lib.tracks(sort="bpm", descending=True)] == [140, 128, 120]
    assert [t.title for t in lib.tracks(sort="title", limit=1, offset=1)] == ["b"]


def test_default_sort_is_artist_then_title(lib):
    lib.replace_source(
        "folder", "/m", "M",
        [rec("1", artist="B", title="x"), rec("2", artist="a", title="z"), rec("3", artist="a", title="y")],
    )  # fmt: skip
    assert [t.ext_id for t in lib.tracks()] == ["3", "2", "1"]


def test_unknown_sort_is_rejected(lib):
    with pytest.raises(ValueError, match="unknown sort"):
        lib.tracks(sort="id; DROP TABLE track")


def test_remove_source_cascades(lib):
    nodes = [NodeRecord("p", None, "P", False, ("a",))]
    lib.replace_source("folder", "/m", "M", [rec("a")], nodes)
    lib.remove_source(lib.sources()[0].id)
    assert lib.sources() == [] and lib.tracks() == []
    assert lib._conn.execute("SELECT COUNT(*) FROM node_track").fetchone()[0] == 0


def test_single_track_lookup(lib):
    lib.replace_source("folder", "/m", "M", [rec("a", title="T", bpm=127.5, key="8A")])
    [t] = lib.tracks()
    got = lib.track(t.id)
    assert (got.title, got.bpm, got.key) == ("T", 127.5, "8A")
    assert lib.track(999_999) is None


def test_persists_and_reopens(tmp_path):
    path = tmp_path / "sub" / "lib.db"
    with Library.open(path) as lib:
        lib.replace_source("folder", "/m", "M", [rec("a")])
    with Library.open(path) as lib:
        assert lib.count_tracks() == 1


def test_refuses_a_newer_schema(tmp_path):
    path = tmp_path / "lib.db"
    Library.open(path).close()
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="newer"):
        Library.open(path)
