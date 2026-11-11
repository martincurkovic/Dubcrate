from pathlib import Path
from types import SimpleNamespace

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
