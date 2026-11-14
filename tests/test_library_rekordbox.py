from pathlib import Path
from types import SimpleNamespace

import pytest

from dubcrate.library import rekordbox as rb


def content(**kw):
    base = {
        "ID": "10", "FolderPath": "/music/a.mp3", "Title": "Song", "ArtistName": "Artist", "AlbumName": "Album",
        "GenreName": "House", "ReleaseYear": 2024, "Commnt": "note", "BPM": 12800, "KeyName": "8A", "Length": 300,
        "BitRate": 320, "SampleRate": 44100, "BitDepth": 16, "Rating": 4, "FileSize": 12345,
        "AnalysisDataPath": "/PIONEER/USBANLZ/x/ANLZ0000.DAT", "rb_local_deleted": 0,
    }  # fmt: skip
    return SimpleNamespace(**{**base, **kw})


def playlist(id, parent="root", name="P", attr=0, seq=0, smart=None, deleted=0):
    return SimpleNamespace(
        ID=id, ParentID=parent, Name=name, Attribute=attr, Seq=seq, SmartList=smart,
        rb_local_deleted=deleted,
    )  # fmt: skip


def song(playlist_id, content_id, no, deleted=0):
    return SimpleNamespace(
        PlaylistID=playlist_id,
        ContentID=content_id,
        TrackNo=no,
        rb_local_deleted=deleted,
    )


def test_track_mapping_converts_units():
    t = rb.track_from_content(content())
    assert t.ext_id == "10" and t.path == "/music/a.mp3"
    assert t.bpm == 128.0  # stored x100
    assert (t.title, t.artist, t.album, t.genre) == ("Song", "Artist", "Album", "House")
    assert (t.key, t.duration, t.year, t.rating) == ("8A", 300.0, 2024, 4)
    assert (t.bitrate, t.sample_rate, t.bit_depth, t.file_size) == (
        320,
        44100,
        16,
        12345,
    )
    assert t.analysis_path == "/PIONEER/USBANLZ/x/ANLZ0000.DAT"


def test_track_mapping_handles_empty_fields():
    t = rb.track_from_content(
        content(Title=None, ArtistName=None, BPM=0, KeyName=None, Rating=0, ReleaseYear=0,
                AnalysisDataPath=None, BitDepth=0)
    )  # fmt: skip
    assert t.title == "a"  # falls back to the file stem
    assert (t.artist, t.key) == ("", "")
    assert t.bpm is None and t.rating is None and t.year is None and t.bit_depth is None
    assert t.analysis_path is None


def test_track_without_a_path_is_skipped():
    assert rb.track_from_content(content(FolderPath=None)) is None
    assert rb.track_from_content(content(FolderPath="")) is None


def test_nodes_nest_and_order_by_seq_and_track_number():
    playlists = [
        playlist("f", name="Folder", attr=1, seq=2),
        playlist("p2", parent="f", name="Second", seq=2),
        playlist("p1", parent="f", name="First", seq=1),
        playlist("top", name="Top", seq=1),
    ]
    songs = [song("p1", "b", 2), song("p1", "a", 1), song("p1", "c", 3)]
    nodes, notes = rb.build_nodes(playlists, songs, {"a", "b", "c"})
    assert [n.name for n in nodes] == ["Top", "Folder", "First", "Second"]
    by_name = {n.name: n for n in nodes}
    assert (
        by_name["First"].parent_ext_id == "f" and by_name["Top"].parent_ext_id is None
    )
    assert by_name["Folder"].is_folder and not by_name["First"].is_folder
    assert by_name["First"].track_ext_ids == ("a", "b", "c")
    assert notes == []


def test_nodes_skip_deleted_system_and_smart_playlists():
    playlists = [
        playlist("keep", name="Keep"),
        playlist("gone", name="Deleted", deleted=1),
        playlist("100000", name="Cloud trial", attr=-128),
        playlist("200000", name="CUE Analysis Playlist"),
        playlist("smart", name="Smart", attr=4),
    ]
    nodes, notes = rb.build_nodes(playlists, [], set())
    assert [n.name for n in nodes] == ["Keep"]
    assert len(notes) == 1 and "smart" in notes[0]


def test_nodes_ignore_deleted_and_unknown_songs():
    playlists = [playlist("p", name="P")]
    songs = [song("p", "a", 1), song("p", "dead", 2, deleted=1), song("p", "ghost", 3)]
    [node], _ = rb.build_nodes(playlists, songs, {"a", "dead"})
    assert node.track_ext_ids == ("a",)


def test_orphaned_node_is_attached_to_the_top_level():
    playlists = [playlist("p", parent="missing-parent", name="Orphan")]
    [node], _ = rb.build_nodes(playlists, [], set())
    assert node.parent_ext_id is None


def test_duplicate_tracks_in_a_playlist_are_kept():
    playlists = [playlist("p")]
    songs = [song("p", "a", 1), song("p", "a", 2)]
    [node], _ = rb.build_nodes(playlists, songs, {"a"})
    assert node.track_ext_ids == ("a", "a")


def test_snapshot_copies_db_and_sidecars_without_touching_the_original(tmp_path):
    src = tmp_path / "rekordbox"
    src.mkdir()
    for name, data in [("master.db", b"db"), ("master.db-wal", b"wal"),
                       ("master.db-shm", b"shm"), ("masterPlaylists6.xml", b"<x/>"),
                       ("unrelated.txt", b"nope")]:  # fmt: skip
        (src / name).write_bytes(data)
    dest = tmp_path / "copy"
    dest.mkdir()
    copied = rb._snapshot(src / "master.db", dest)
    assert copied == dest / "master.db"
    assert sorted(p.name for p in dest.iterdir()) == [
        "master.db", "master.db-shm", "master.db-wal", "masterPlaylists6.xml",
    ]  # fmt: skip
    assert (dest / "master.db-wal").read_bytes() == b"wal"
    assert (src / "master.db").read_bytes() == b"db"


def test_snapshot_without_sidecars(tmp_path):
    src = tmp_path / "master.db"
    src.write_bytes(b"db")
    dest = tmp_path / "copy"
    dest.mkdir()
    rb._snapshot(src, dest)
    assert [p.name for p in dest.iterdir()] == ["master.db"]


def test_missing_database_is_a_clear_error(tmp_path):
    import pytest

    with pytest.raises(rb.RekordboxError, match="no rekordbox master.db"):
        rb.read_master_db(tmp_path / "nope.db")


def test_default_master_db_is_none_when_not_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert rb.default_master_db() is None


def cue_row(**kw):
    base = {
        "ContentID": "10",
        "InMsec": 15000,
        "OutMsec": -1,
        "Kind": 0,
        "Comment": None,
        "rb_local_deleted": 0,
    }
    return SimpleNamespace(**{**base, **kw})  # fmt: skip


def test_a_cue_row_is_a_memory_cue_a_hot_cue_or_a_loop():
    memory = rb.cue_from_row(cue_row(Kind=0, InMsec=51))
    assert (memory.kind, memory.time, memory.slot, memory.end) == (
        "memory",
        0.051,
        None,
        None,
    )
    hot = rb.cue_from_row(cue_row(Kind=2, InMsec=85832, Comment="drop"))
    assert (hot.kind, hot.time, hot.slot, hot.label) == ("hot", 85.832, 2, "drop")
    loop = rb.cue_from_row(cue_row(Kind=5, InMsec=243801, OutMsec=303801))
    assert (loop.kind, loop.time, loop.end, loop.slot) == ("loop", 243.801, 303.801, 4)


def test_hot_cue_kinds_run_a_b_c_then_skip_four_to_d_e_f():
    slots = {k: rb.cue_from_row(cue_row(Kind=k)).slot for k in range(8)}
    assert slots == {0: None, 1: 1, 2: 2, 3: 3, 4: None, 5: 4, 6: 5, 7: 6}
    assert rb.cue_from_row(cue_row(Kind=5)).kind == "hot"  # hot cue D, on hot cue C
    assert (
        rb.cue_from_row(cue_row(Kind=4)).kind == "memory"
    )  # never seen: kept, as a memory cue


def test_a_cue_with_no_usable_time_or_a_deleted_one_is_skipped():
    assert rb.cue_from_row(cue_row(InMsec=None)) is None
    assert rb.cue_from_row(cue_row(InMsec=-1)) is None
    assert rb.cue_from_row(cue_row(rb_local_deleted=1)) is None
    assert (
        rb.cue_from_row(cue_row(OutMsec=0)).kind == "memory"
    )  # -1 and 0 both mean no end


def test_cues_are_grouped_by_track_in_time_order():
    rows = [
        cue_row(ContentID="1", Kind=3, InMsec=90000),
        cue_row(ContentID="2", Kind=1, InMsec=5000),
        cue_row(ContentID="1", Kind=1, InMsec=30000),
        cue_row(ContentID="1", Kind=0, InMsec=40, rb_local_deleted=1),
    ]
    grouped = rb.cues_by_content(rows)
    assert [c.time for c in grouped["1"]] == [30.0, 90.0]
    assert [c.time for c in grouped["2"]] == [5.0]


def test_reading_cues_from_a_database_with_no_cue_table_is_a_plain_error(
    tmp_path, monkeypatch
):
    import sys
    from types import ModuleType

    class NoCueTable:
        def __init__(self, path):
            pass

        def get_cue(self):
            raise RuntimeError("no such table: djmdCue")

        def close(self):
            pass

    fake = ModuleType("pyrekordbox.masterdb")
    fake.MasterDatabase = NoCueTable
    monkeypatch.setitem(sys.modules, "pyrekordbox.masterdb", fake)
    database = tmp_path / "master.db"
    database.write_bytes(b"x")
    with pytest.raises(rb.RekordboxError, match="could not read the cues.*djmdCue"):
        rb.read_cues(database)
