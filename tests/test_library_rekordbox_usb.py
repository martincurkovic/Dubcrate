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


# -- a stick that comes back under another name, and one that is unplugged ----------------------------


def moved_copy(stick: Path, tmp_path: Path, name: str = "stick 1") -> Path:
    """The same stick mounted somewhere else (its drive is called something else now)."""
    new = tmp_path / name
    shutil.copytree(stick, new)
    return new


def import_stick(lib, stick, name=None):
    return ru.store_stick(lib, ru.read_usb(stick), name)


def test_a_stick_that_comes_back_under_another_name_is_the_same_source(tmp_path):
    stick = make_stick(tmp_path)
    with Library.open(":memory:") as lib:
        first = import_stick(lib, stick)
        ids = {t.ext_id: t.id for t in lib.tracks()}
        disc = lib.create_disc("D")
        lib.add_to_disc(disc, [ids["1"]])
        lib.set_memory_cues(
            lib.tracks()[0].path, [(5000, "mine")]
        )  # (an edit, kept by the file's path)
        again = moved_copy(stick, tmp_path)
        shutil.rmtree(stick)  # (the old mount is gone)
        second = import_stick(lib, again)
        [source] = lib.sources()
        assert (second.source_id, source.path, source.name) == (
            first.source_id,
            str(again),
            "stick",
        )
        assert {
            t.ext_id: t.id for t in lib.tracks()
        } == ids  # (no new tracks, same ids)
        assert [e.track.id for e in lib.disc_entries(disc)] == [ids["1"]]
        moved = next(t for t in lib.tracks() if t.ext_id == "1")
        assert moved.path == str(again / "Contents" / "Art" / "Track 1.mp3")
        assert lib.memory_cues(moved.path) == [
            (5000, "mine")
        ]  # the edit followed the file
        assert (
            any("was moved here" in n for n in second.notes)
            and "“stick”" in second.notes[0]
        )
        assert (second.added, second.removed) == (0, 0)


def test_a_stick_that_is_still_plugged_in_is_not_taken_for_a_moved_one(tmp_path):
    stick = make_stick(tmp_path)
    with Library.open(":memory:") as lib:
        import_stick(lib, stick)
        import_stick(
            lib, moved_copy(stick, tmp_path)
        )  # (a copy, while the first is still there)
        assert sorted(s.name for s in lib.sources()) == ["stick", "stick 1"]


def test_a_different_stick_is_not_taken_for_a_moved_one(tmp_path):
    first = make_stick(tmp_path / "a", tracks=2)
    other = make_stick(tmp_path / "b", tracks=2)
    with Library.open(":memory:") as lib:
        import_stick(lib, first)
        lib._conn.execute(
            "UPDATE track SET ext_id = ext_id || 'x'"
        )  # (it holds other tracks than `other` does)
        shutil.rmtree(first)
        import_stick(lib, other)
        assert len(lib.sources()) == 2


def test_a_given_name_wins_over_the_old_one(tmp_path):
    stick = make_stick(tmp_path)
    with Library.open(":memory:") as lib:
        import_stick(lib, stick)
        again = moved_copy(stick, tmp_path)
        shutil.rmtree(stick)
        import_stick(lib, again, "Gig stick")
        assert [s.name for s in lib.sources()] == ["Gig stick"]


def test_moving_a_source_onto_another_one_is_refused(tmp_path):
    from dubcrate.library.store import LibraryError

    with Library.open(":memory:") as lib:
        a = lib.replace_source(
            "rekordbox_usb",
            "/Volumes/A",
            "A",
            [TrackRecord(ext_id="1", path="/Volumes/A/x.mp3")],
        )
        lib.replace_source(
            "rekordbox_usb",
            "/Volumes/B",
            "B",
            [TrackRecord(ext_id="1", path="/Volumes/B/x.mp3")],
        )
        with pytest.raises(LibraryError):
            lib.move_source(a.source_id, "/Volumes/B")
        lib.move_source(a.source_id, "/Volumes/A")  # (to where it is: nothing)


def test_missing_tracks_are_all_of_them_when_the_stick_is_gone_else_the_ones_not_found(
    tmp_path,
):
    real = tmp_path / "here.mp3"
    real.write_bytes(b"x")
    paths = [(1, str(real)), (2, str(tmp_path / "gone.mp3"))]
    assert ru.missing_tracks(paths, present=True) == {2}
    assert ru.missing_tracks(paths, present=False) == {1, 2}
    assert ru.stick_present(tmp_path) is False


def test_the_library_can_mark_tracks_not_found_and_found_again():
    with Library.open(":memory:") as lib:
        s = lib.replace_source("rekordbox_usb", "/Volumes/A", "A", [TrackRecord(ext_id=str(n), path=f"/Volumes/A/{n}.mp3") for n in (1, 2, 3)])  # fmt: skip
        ids = [i for i, _ in lib.track_paths(s.source_id)]
        assert all(
            t.missing for t in lib.tracks()
        )  # (the files aren't there: an import says so)
        assert lib.set_missing(s.source_id, []) == 3 and not any(
            t.missing for t in lib.tracks()
        )
        assert lib.set_missing(s.source_id, [ids[0]]) == 1
        assert [t.missing for t in lib.tracks(sort="path")] == [True, False, False]
        assert lib.set_missing(s.source_id, [ids[0]]) == 0  # (nothing changed)


def test_the_command_line_recognises_a_moved_stick(tmp_path, capsys):
    from dubcrate import cli

    stick = make_stick(tmp_path)
    db = tmp_path / "lib.db"
    assert cli.main(["--library", str(db), "import-usb", str(stick)]) == 0
    again = moved_copy(stick, tmp_path)
    shutil.rmtree(stick)
    capsys.readouterr()
    assert cli.main(["--library", str(db), "import-usb", str(again)]) == 0
    out = capsys.readouterr().out
    assert "added 0" in out and "was moved here" in out


# -- review fixes: the schema fallback, and moving a source in one transaction -----------------------------------------


def test_the_schema_upgrade_rebuilds_the_table_when_its_definition_cannot_be_edited(
    tmp_path, monkeypatch
):
    from dubcrate.library import store

    path = tmp_path / "old.db"
    full = store._MIGRATIONS
    store._MIGRATIONS = full[:19]
    try:
        old = Library.open(path)
        a = old.replace_source("folder", "/f", "F", [TrackRecord(ext_id=str(n), path=f"/f/{n}.mp3", title=f"T{n}") for n in (1, 2)])  # fmt: skip
        disc = old.create_disc("D")
        old.add_to_disc(disc, [t.id for t in old.tracks()])
        old.close()
    finally:
        store._MIGRATIONS = full
    monkeypatch.setattr(
        store, "_edit_source_check", lambda *args: False
    )  # (as on a build that locks the schema)
    with Library.open(path) as lib:
        assert [t.title for t in lib.tracks(sort="path")] == [
            "T1",
            "T2",
        ]  # (no track lost to the cascade)
        assert len(lib.disc_entries(disc)) == 2
        assert (
            lib._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        )  # (and they are back on)
        assert lib._conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert lib._conn.execute("PRAGMA foreign_key_check").fetchall() == []
        lib.replace_source("rekordbox_usb", "/Volumes/X", "X", [TrackRecord(ext_id="1", path="/Volumes/X/b.mp3", title="B")])  # fmt: skip
        lib.replace_source(
            "rekordbox_xml",
            "/x/r.xml",
            "R",
            [TrackRecord(ext_id="1", path="/x/c.mp3", title="C")],
        )
        assert sorted(s.kind for s in lib.sources()) == [
            "folder",
            "rekordbox_usb",
            "rekordbox_xml",
        ]
        with pytest.raises(sqlite3.IntegrityError):
            lib._conn.execute("INSERT INTO source (kind, path, name, imported_at) VALUES ('nope', 'p', 'n', 't')")  # fmt: skip
        lib.remove_source(
            a.source_id, force=True
        )  # (the cascade still works on the new table)
        assert [t.title for t in lib.tracks(sort="path")] == ["B", "C"]
    with Library.open(path) as again:
        assert again._conn.execute("PRAGMA user_version").fetchone()[0] == len(full)


def test_moving_a_source_and_reading_it_again_is_one_transaction(tmp_path):
    stick = make_stick(tmp_path)
    with Library.open(":memory:") as lib:
        import_stick(lib, stick)
        before = [(t.ext_id, t.path) for t in lib.tracks(sort="path")]
        source = lib.sources()[0]

        def breaks():
            yield TrackRecord(ext_id="1", path="/Volumes/New/Contents/a.mp3", title="x")
            raise RuntimeError("the read failed half way")

        with pytest.raises(RuntimeError):
            lib.replace_source(
                "rekordbox_usb", "/Volumes/New", "N", breaks(), move_from=source.id
            )
        # (nothing moved: the source and its tracks are exactly where they were)
        assert lib.sources()[0].path == source.path
        assert [(t.ext_id, t.path) for t in lib.tracks(sort="path")] == before


def test_a_stick_with_no_tracks_is_not_offline_and_one_with_all_its_tracks_missing_is():
    lib = Library.open(":memory:")
    lib.replace_source("rekordbox_usb", "/Volumes/EMPTY", "EMPTY", [])
    lib.replace_source("rekordbox_usb", "/Volumes/GONE", "GONE", [TrackRecord(ext_id="1", path="/Volumes/GONE/a.mp3", title="A")])  # fmt: skip
    gone = next(s for s in lib.sources() if s.name == "GONE")
    lib.set_missing(gone.id, [t.id for t in lib.tracks(source_id=gone.id)])
    assert lib.offline_source_ids() == {
        gone.id
    }  # (the empty stick has nothing to hide)
    lib.close()
