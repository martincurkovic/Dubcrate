"""A rekordbox USB stick read into the library: a synthetic stick (a real encrypted `exportLibrary.db`, made here),
the stick's own `.PIONEER` folder, paths from its root, playlists, cues, and the read-only promise."""

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from dubcrate.analysis import Cue
from dubcrate.cancel import Cancelled, CancelToken
from dubcrate.library import Library, TrackRecord
from dubcrate.library import rekordbox_usb as ru
from dubcrate.library.rekordbox import RekordboxError

sqlcipher = pytest.importorskip("sqlcipher3.dbapi2")
models = pytest.importorskip("pyrekordbox.onelibrary.models")
from pyrekordbox.onelibrary.database import BLOB, deobfuscate


def row(model, **given):
    """A model row with every NOT NULL column that wasn't given filled with a dull value, and every date column
    given a date (pyrekordbox's date type can't store None)."""
    for column in model.__table__.columns:
        if column.name in given:
            continue
        kind = column.type.python_type
        if type(column.type).__name__ == "DateTime":  # (its python_type is `object`)
            given[column.name] = datetime(2026, 1, 1)
        elif (
            not column.nullable and column.default is None and kind in (int, str, float)
        ):
            given[column.name] = kind()
    return model(**given)


def make_stick(tmp_path: Path, pioneer: str = "PIONEER", tracks=2, extra=()) -> Path:
    """A stick under `tmp_path`: `exportLibrary.db` with `tracks` tracks, a folder with a playlist, an empty system
    playlist and cues (a memory cue, hot cue A, a loop, a cue with no time)."""
    root = tmp_path / "stick"
    folder = root / pioneer / "rekordbox"
    folder.mkdir(parents=True)
    key = deobfuscate(BLOB)
    engine = create_engine(
        f"sqlite+pysqlcipher://:{key}@/{folder / ru.DB_NAME}?",
        module=sqlcipher,
    )
    models.Content.__mro__[1].metadata.create_all(engine)
    with Session(engine) as s:
        s.add_all([
            row(models.Artist, artist_id=1, name="Art"),
            row(models.Artist, artist_id=2, name="Mixer"),
            row(models.Genre, genre_id=1, name="House"),
            row(models.Key, key_id=1, name="8A"),
            row(models.Label, label_id=1, name="Lbl"),
            row(models.Album, album_id=1, name="Alb"),
        ])  # fmt: skip
        for n in range(1, tracks + 1):
            s.add(row(
                models.Content, content_id=n, title=f"Track {n}", path=f"/Contents/Art/Track {n}.mp3",
                artist_id_artist=1, artist_id_remixer=2, genre_id=1, key_id=1, label_id=1, album_id=1,
                bpmx100=12850, length=200 + n, releaseYear=2020, rating=4, bitrate=320, samplingRate=44100,
                bitDepth=16, fileSize=1000 + n, djComment="note", djPlayCount=3,
                dateAdded=datetime(2026, 2, 8),
                analysisDataFilePath=f"/{pioneer}/USBANLZ/P000/0000000{n}/ANLZ0000.DAT",
            ))  # fmt: skip
        s.add(row(models.Content, content_id=99, title="No path", path=""))
        s.add_all([
            row(models.Playlist, playlist_id=1, name="Sets", attribute=1, playlist_id_parent=0, sequenceNo=1),
            row(models.Playlist, playlist_id=2, name="Warm", attribute=0, playlist_id_parent=1, sequenceNo=1),
            row(models.Playlist, playlist_id=3, name="CUE Analysis Playlist", attribute=0, playlist_id_parent=0, sequenceNo=2),
            row(models.PlaylistContent, playlist_id=2, content_id=2, sequenceNo=1),
            row(models.PlaylistContent, playlist_id=2, content_id=1, sequenceNo=2),
            row(models.PlaylistContent, playlist_id=2, content_id=99, sequenceNo=3),
        ])  # fmt: skip
        s.add_all([
            row(models.Cue, cue_id=1, content_id=1, kind=0, inUsec=27_000, outUsec=-1, cueComment="1.1Bars"),
            row(models.Cue, cue_id=2, content_id=1, kind=1, inUsec=43_936_000, outUsec=-1),
            row(models.Cue, cue_id=3, content_id=1, kind=0, inUsec=60_000_000, outUsec=68_000_000),
            row(models.Cue, cue_id=4, content_id=1, kind=5, inUsec=90_000_000, outUsec=-1),
            row(models.Cue, cue_id=5, content_id=1, kind=0, inUsec=-1, outUsec=-1),
            *extra,
        ])  # fmt: skip
        s.commit()
    engine.dispose()
    return root


@pytest.fixture
def stick(tmp_path):
    return make_stick(tmp_path)


# -- finding a stick -----------------------------------------------------------------------------


def test_a_stick_is_found_from_its_root_its_pioneer_folder_or_its_database(stick):
    db = stick / "PIONEER" / "rekordbox" / ru.DB_NAME
    for given in (stick, stick / "PIONEER", stick / "PIONEER" / "rekordbox", db):
        assert ru.stick_root(given) == stick
    assert ru.library_file(stick) == db


def test_a_hidden_pioneer_folder_works_too(tmp_path):
    root = make_stick(tmp_path, pioneer=".PIONEER")
    assert ru.stick_root(root) == root and ru.read_usb(root).tracks


def test_a_folder_that_is_no_stick_is_not_one(tmp_path):
    assert ru.stick_root(tmp_path) is None
    with pytest.raises(RekordboxError, match="isn't a rekordbox USB stick"):
        ru.read_usb(tmp_path)
    with pytest.raises(RekordboxError):
        ru.read_usb_cues(tmp_path)


def test_a_stick_with_only_the_old_export_pdb_says_so(tmp_path):
    folder = tmp_path / "old" / "PIONEER" / "rekordbox"
    folder.mkdir(parents=True)
    (folder / "export.pdb").write_bytes(b"\0" * 16)
    with pytest.raises(RekordboxError, match="older rekordbox"):
        ru.read_usb(tmp_path / "old")


def test_sticks_are_picked_out_of_the_volumes(stick, tmp_path):
    assert ru.find_sticks([tmp_path / "gone", tmp_path, stick]) == [stick]


# -- the tracks ----------------------------------------------------------------------------------


def test_tracks_get_the_sticks_root_in_front_and_all_their_fields(stick):
    read = ru.read_usb(stick)
    assert read.source == stick
    by_id = {t.ext_id: t for t in read.tracks}
    assert set(by_id) == {"1", "2"}  # (the one with no path is skipped, and said)
    assert read.notes == ["skipped 1 track(s) with no file path"]
    t = by_id["1"]
    assert t.path == str(stick / "Contents" / "Art" / "Track 1.mp3")
    assert (t.title, t.artist, t.album, t.genre, t.key, t.label, t.remixer) == (
        "Track 1", "Art", "Alb", "House", "8A", "Lbl", "Mixer",
    )  # fmt: skip
    assert (t.bpm, t.year, t.rating, t.duration, t.bitrate, t.sample_rate) == (128.5, 2020, 4, 201.0, 320, 44100)  # fmt: skip
    assert (t.comment, t.play_count, t.date_added, t.file_size) == ("note", 3, "2026-02-08", 1001)  # fmt: skip
    assert t.analysis_path == "/PIONEER/USBANLZ/P000/00000001/ANLZ0000.DAT"


def test_playlists_come_with_their_order_and_the_empty_system_one_is_left_out(stick):
    nodes = {n.name: n for n in ru.read_usb(stick).nodes}
    assert set(nodes) == {"Sets", "Warm"}
    assert nodes["Sets"].is_folder and nodes["Sets"].parent_ext_id is None
    assert nodes["Warm"].parent_ext_id == "1"
    assert nodes["Warm"].track_ext_ids == (
        "2",
        "1",
    )  # (by position; the skipped track is not in it)


def test_a_cancel_stops_the_read(stick):
    token = CancelToken()
    token.cancel()
    with pytest.raises(Cancelled):
        ru.read_usb(stick, cancel=token)


# -- the cues ------------------------------------------------------------------------------------


def test_cues_have_the_same_kinds_as_masterdbs(stick):
    cues = ru.read_usb_cues(stick)
    assert set(cues) == {"1"}  # (a cue with no time is dropped)
    assert cues["1"] == [
        Cue("memory", 0.027, label="1.1Bars"),
        Cue("hot", 43.936, slot=1),
        Cue("loop", 60.0, 68.0),
        Cue("hot", 90.0, slot=4),
    ]


# -- read-only -----------------------------------------------------------------------------------


def test_reading_leaves_the_stick_exactly_as_it_was(stick):
    files = {p: p.read_bytes() for p in stick.rglob("*") if p.is_file()}
    before = {p: p.stat().st_mtime_ns for p in files}
    ru.read_usb(stick)
    ru.read_usb_cues(stick)
    assert {p: p.read_bytes() for p in stick.rglob("*") if p.is_file()} == files
    assert {p: p.stat().st_mtime_ns for p in files} == before


def test_the_database_is_read_from_a_copy(stick, monkeypatch):
    opened = []
    real = ru._open
    monkeypatch.setattr(
        ru, "_open", lambda copy, source: opened.append(copy) or real(copy, source)
    )
    ru.read_usb(stick)
    assert opened and opened[0].parent != stick / "PIONEER" / "rekordbox"


def test_the_module_never_writes_to_a_database():
    source = Path(ru.__file__).read_text(encoding="utf-8")
    assert "commit(" not in source and ".add(" not in source and "delete(" not in source


# -- in the library ------------------------------------------------------------------------------


def test_a_stick_is_its_own_kind_of_source_and_a_reimport_keeps_ids(stick):
    with Library.open(":memory:") as lib:
        read = ru.read_usb(stick)
        lib.replace_source(
            "rekordbox_usb", str(read.source), "Stick", read.tracks, read.nodes
        )
        [source] = lib.sources()
        assert (source.kind, source.track_count) == ("rekordbox_usb", 2)
        ids = {t.ext_id: t.id for t in lib.tracks()}
        again = ru.read_usb(stick)
        lib.replace_source(
            "rekordbox_usb", str(again.source), "Stick", again.tracks, again.nodes
        )
        assert {t.ext_id: t.id for t in lib.tracks()} == ids


def test_the_schema_upgrade_keeps_a_library_and_allows_sticks(tmp_path):
    from dubcrate.library import store

    path = tmp_path / "old.db"
    full = store._MIGRATIONS
    store._MIGRATIONS = full[:19]
    try:
        old = Library.open(path)
        old.replace_source(
            "folder", "/f", "F", [TrackRecord(ext_id="a", path="/x/a.mp3", title="A")]
        )
        old.close()
    finally:
        store._MIGRATIONS = full
    with Library.open(path) as lib:
        assert [t.title for t in lib.tracks()] == ["A"]
        lib.replace_source(
            "rekordbox_usb",
            "/Volumes/X",
            "X",
            [TrackRecord(ext_id="1", path="/Volumes/X/b.mp3", title="B")],
        )
        assert sorted(s.kind for s in lib.sources()) == ["folder", "rekordbox_usb"]
        assert lib._conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert lib._conn.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError):
            lib._conn.execute("INSERT INTO source (kind, path, name, imported_at) VALUES ('nope', 'p', 'n', 't')")  # fmt: skip
    with Library.open(path) as again:  # (the new definition is what is stored)
        assert len(again.sources()) == 2


def test_the_command_line_imports_a_stick(stick, tmp_path, capsys):
    from dubcrate import cli

    db = tmp_path / "lib.db"
    assert cli.main(["--library", str(db), "import-usb", str(stick)]) == 0
    assert "added 2" in capsys.readouterr().out
    assert cli.main(["--library", str(db), "sources"]) == 0
    assert "rekordbox_usb" in capsys.readouterr().out
    assert cli.main(["--library", str(db), "import-usb", str(tmp_path)]) == 1
    assert "isn't a rekordbox USB stick" in capsys.readouterr().err


def test_shutil_is_only_used_to_copy():
    # (a guard on the copy helper: it must be a copy, never a move)
    source = Path(ru.__file__).read_text(encoding="utf-8")
    assert (
        "shutil.copy2" in source
        and "shutil.move" not in source
        and "rmtree" not in source
    )
    assert shutil


# -- a stick's tracks in the places that read rekordbox ---------------------------------------------


@pytest.fixture
def lib_with_stick(stick):
    with Library.open(":memory:") as lib:
        read = ru.read_usb(stick)
        lib.replace_source(
            "rekordbox_usb", str(read.source), "Stick", read.tracks, read.nodes
        )
        yield lib, stick


def test_the_sources_module_says_where_each_kind_keeps_things(stick, tmp_path):
    from dubcrate.library import rekordbox_sources as rs

    assert rs.carries_rekordbox_data("rekordbox") and rs.carries_rekordbox_data(
        "rekordbox_usb"
    )
    assert not rs.carries_rekordbox_data("folder")
    assert rs.analysis_root("rekordbox_usb", stick) == stick
    assert (
        rs.analysis_root("rekordbox", tmp_path / "rb" / "master.db")
        == tmp_path / "rb" / "share"
    )
    assert (
        rs.cue_database("rekordbox_usb", stick)
        == stick / "PIONEER" / "rekordbox" / ru.DB_NAME
    )
    gone = rs.cue_database(
        "rekordbox_usb", tmp_path / "unplugged"
    )  # (where it would be)
    assert gone.name == ru.DB_NAME and not gone.exists()
    assert (
        rs.cue_database("rekordbox", tmp_path / "master.db") == tmp_path / "master.db"
    )
    # any of the three things a cue source can be pointed at
    for given in (stick, stick / "PIONEER" / "rekordbox" / ru.DB_NAME):
        assert set(rs.read_cues_at(given)) == {"1"}
    seen = []
    rs.read_cues_at(tmp_path / "master.db", lambda p: seen.append(p) or {})
    assert seen == [tmp_path / "master.db"]


def test_cues_for_the_card_come_from_the_stick(lib_with_stick):
    from dubcrate.card_discs import RekordboxCues

    lib, _stick = lib_with_stick
    track = next(t for t in lib.tracks() if t.ext_id == "1")
    cues = RekordboxCues(lib).for_track(track)
    # (the synthetic stick has no audio files, so no lead is taken off)
    assert [(c.kind, c.time, c.slot) for c in cues] == [
        ("memory", 0.027, None),
        ("hot", 43.936, 1),
        ("loop", 60.0, None),
        ("hot", 90.0, 4),
    ]
    other = next(t for t in lib.tracks() if t.ext_id == "2")
    assert list(RekordboxCues(lib).for_track(other)) == []


def test_a_folder_track_at_a_sticks_path_has_the_stick_track_as_its_twin(
    lib_with_stick,
):
    lib, stick = lib_with_stick
    on_stick = next(t for t in lib.tracks() if t.ext_id == "1")
    lib.replace_source(
        "folder",
        str(stick / "Contents"),
        "Contents",
        [TrackRecord(ext_id="f", path=on_stick.path, title="Same file")],
    )
    folder_track = next(t for t in lib.tracks() if t.title == "Same file")
    assert lib.rekordbox_twin(folder_track).id == on_stick.id
    assert lib.rekordbox_twin(on_stick) is None


def test_the_cue_cache_reads_a_stick_but_the_writer_never_sees_it(
    lib_with_stick, qapp, wait_for
):
    from dubcrate.ui import cue_cache

    lib, stick = lib_with_stick
    cache = cue_cache.RekordboxCueCache(lib)
    track = next(t for t in lib.tracks() if t.ext_id == "1")
    changes = []
    cache.changed.connect(lambda: changes.append(1))
    assert cache.cues_for(track) is None  # (being read)
    assert wait_for(lambda: changes)
    assert [c.kind for c in cache.cues_for(track)] == ["memory", "hot", "loop", "hot"]
    assert cache.cue_file_for(track) == stick / "PIONEER" / "rekordbox" / ru.DB_NAME
    assert (
        cache.database_for(track) is None
    )  # (what the editor writes to: only a computer's master.db)


def test_nothing_that_writes_to_rekordbox_knows_about_sticks():
    root = Path(ru.__file__).parents[1]
    for name in ("rekordbox_write.py", "ui/rekordbox_editor.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "rekordbox_usb" not in text and "rekordbox_sources" not in text and "cue_file_for" not in text, name  # fmt: skip
    assert 'kind == "rekordbox"' in (root / "ui" / "rekordbox_editor.py").read_text(encoding="utf-8")  # fmt: skip


def test_the_finder_finds_a_sticks_beat_grids_and_cue_tables(lib_with_stick):
    from dubcrate import track_finder

    lib, stick = lib_with_stick
    disc = lib.create_disc("D")
    ids = [t.id for t in lib.tracks()]
    lib.add_to_disc(disc, ids)
    found = track_finder.beat_sources(lib, disc_id=disc)
    assert {root for root, _ in found.values()} == {stick}
    assert {path for _, path in found.values()} == {
        "/PIONEER/USBANLZ/P000/00000001/ANLZ0000.DAT",
        "/PIONEER/USBANLZ/P000/00000002/ANLZ0000.DAT",
    }
    assert track_finder.rekordbox_databases(lib) == [str(stick)]
    tables, problem = track_finder.read_cue_tables([str(stick)])
    assert problem == "" and set(tables[str(stick)]) == {"1"}
