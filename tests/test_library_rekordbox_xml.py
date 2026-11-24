"""A rekordbox XML file read into the library: tracks, playlists and cues, the paths in its URLs, the read-only
promise, and the places that read rekordbox's cues (card sync, the cue cache) taking them from it."""

from pathlib import Path

import pytest

from dubcrate.cancel import Cancelled, CancelToken
from dubcrate.library import Library, TrackRecord
from dubcrate.library import rekordbox_xml as rx
from dubcrate.library.rekordbox import RekordboxError

XML = """<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="7.2.3" Company="AlphaTheta"/>
  <COLLECTION Entries="5">
    <TRACK TrackID="1" Name="Alpha &amp; Omega" Artist="Art" Composer="Comp" Album="Alb" Genre="House" Kind="MP3 File"
           Size="1000" TotalTime="200" Year="2020" AverageBpm="128.50" DateAdded="2026-02-08" BitRate="320"
           SampleRate="44100" Comments="note" PlayCount="3" Rating="204" Location="file://localhost/Users/me/Music/Alpha%20%26%20Omega.mp3"
           Remixer="Mixer" Tonality="8A" Label="Lbl">
      <TEMPO Inizio="0.025" Bpm="128.50" Metro="4/4" Battito="1"/>
      <POSITION_MARK Name="" Type="0" Start="0.027" Num="-1"/>
      <POSITION_MARK Name="Drop" Type="0" Start="43.936" Num="0" Red="40" Green="226" Blue="20"/>
      <POSITION_MARK Name="" Type="0" Start="90.0" Num="3"/>
      <POSITION_MARK Name="" Type="4" Start="60.0" End="68.0" Num="-1"/>
      <POSITION_MARK Name="" Type="4" Start="70.0" End="72.0" Num="2"/>
      <POSITION_MARK Name="" Type="1" Start="1.0" Num="-1"/>
      <POSITION_MARK Name="" Type="3" Start="2.0" Num="-1"/>
      <POSITION_MARK Name="" Type="4" Start="80.0" End="79.0" Num="-1"/>
      <POSITION_MARK Name="" Type="0" Num="-1"/>
    </TRACK>
    <TRACK TrackID="2" Name="Beta" Artist="Art" TotalTime="100" AverageBpm="0.00" Rating="0"
           Location="file://localhost/C:/Music/Beta.mp3"/>
    <TRACK TrackID="3" Name="No place" Artist="Art"/>
    <TRACK TrackID="2" Name="Beta again" Location="file://localhost/Users/me/Music/Again.mp3"/>
    <TRACK TrackID="5" Name="Plain" Location="/Users/me/Music/Plain Path.mp3"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="3">
      <NODE Type="0" Name="Sets" Count="1">
        <NODE Type="1" Name="Warm" KeyType="0" Entries="3">
          <TRACK Key="2"/><TRACK Key="1"/><TRACK Key="404"/>
        </NODE>
      </NODE>
      <NODE Type="1" Name="By place" KeyType="1" Entries="2">
        <TRACK Key="file://localhost/Users/me/Music/Alpha%20%26%20Omega.mp3"/><TRACK Key="file://localhost/nowhere.mp3"/>
      </NODE>
      <NODE Type="1" Name="Warm" KeyType="0" Entries="1"><TRACK Key="5"/></NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""


@pytest.fixture
def xml_file(tmp_path):
    path = tmp_path / "rekordbox.xml"
    path.write_text(XML, encoding="utf-8")
    return path


# -- the paths ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("location", "path"),
    [
        (
            "file://localhost/Users/me/Music/Alpha%20%26%20Omega.mp3",
            "/Users/me/Music/Alpha & Omega.mp3",
        ),
        ("file:///Users/me/a.mp3", "/Users/me/a.mp3"),
        ("file://localhost/C:/Music/Beta.mp3", "C:/Music/Beta.mp3"),
        ("/Users/me/Plain Path.mp3", "/Users/me/Plain Path.mp3"),
        ("file://localhost/Users/me/caf%C3%A9.mp3", "/Users/me/café.mp3"),
        ("", ""),
    ],
)
def test_a_location_becomes_a_file_path(location, path):
    assert rx.location_path(location) == path


# -- the tracks ------------------------------------------------------------------------------------


def test_tracks_get_their_fields_and_those_with_no_file_or_a_repeated_id_are_skipped(
    xml_file,
):
    read = rx.read_xml(xml_file)
    assert read.source == xml_file
    assert [t.ext_id for t in read.tracks] == ["1", "2", "5"]
    assert read.notes == ["skipped 2 track(s) with no file path (or listed twice)"]
    t = read.tracks[0]
    assert t.path == "/Users/me/Music/Alpha & Omega.mp3"
    assert (t.title, t.artist, t.album, t.genre, t.key, t.label, t.remixer, t.composer) == (
        "Alpha & Omega", "Art", "Alb", "House", "8A", "Lbl", "Mixer", "Comp",
    )  # fmt: skip
    assert (t.bpm, t.year, t.rating, t.duration, t.bitrate, t.sample_rate) == (128.5, 2020, 4, 200.0, 320, 44100)  # fmt: skip
    assert (t.comment, t.play_count, t.date_added, t.file_size) == ("note", 3, "2026-02-08", 1000)  # fmt: skip
    assert t.analysis_path is None  # (an XML file has no analysis files)
    beta = read.tracks[1]
    assert (beta.bpm, beta.rating, beta.path) == (None, None, "C:/Music/Beta.mp3")
    assert read.tracks[2].title == "Plain"


# -- the playlists -----------------------------------------------------------------------------------


def test_playlists_come_as_folders_and_playlists_with_their_tracks_in_order(xml_file):
    nodes = rx.read_xml(xml_file).nodes
    by_name = {(n.name, n.parent_ext_id is None): n for n in nodes}
    assert [n.name for n in nodes] == [
        "Sets",
        "Warm",
        "By place",
        "Warm",
    ]  # (ROOT is not a node)
    sets = by_name[("Sets", True)]
    assert sets.is_folder and sets.track_ext_ids == ()
    inside = next(n for n in nodes if n.parent_ext_id == sets.ext_id)
    assert (inside.name, inside.is_folder, inside.track_ext_ids) == (
        "Warm",
        False,
        ("2", "1"),
    )  # (404 isn't a track)
    place = by_name[("By place", True)]
    assert place.track_ext_ids == (
        "1",
    )  # (KeyType 1: by location; the other file isn't in the collection)
    assert (
        len({n.ext_id for n in nodes}) == 4
    )  # (two playlists called Warm are two nodes)


# -- the cues ------------------------------------------------------------------------------------------


def test_cues_are_memory_hot_and_loops_and_fades_and_loads_are_not_cues(xml_file):
    cues = rx.read_xml_cues(xml_file)
    assert set(cues) == {"1"}
    kinds = [(c.kind, c.time, c.end, c.slot, c.label) for c in cues["1"]]
    assert kinds == [
        ("memory", 0.027, None, None, ""),
        ("hot", 43.936, None, 1, "Drop"),
        ("loop", 60.0, 68.0, None, ""),
        ("loop", 70.0, 72.0, 3, ""),  # (a hot cue saved as a loop keeps its slot)
        ("hot", 90.0, None, 4, ""),
    ]


# -- not an XML file ---------------------------------------------------------------------------------------


def test_what_is_not_a_rekordbox_xml_file_is_explained(tmp_path, xml_file):
    assert rx.problem(xml_file) == ""
    other = tmp_path / "other.xml"
    other.write_text("<plist><dict/></plist>", encoding="utf-8")
    assert "doesn't start with DJ_PLAYLISTS" in rx.problem(other)
    text = tmp_path / "notes.xml"
    text.write_text("just some words", encoding="utf-8")
    assert "isn't a rekordbox XML file" in rx.problem(text)
    empty = tmp_path / "empty.xml"
    empty.write_text("", encoding="utf-8")
    assert rx.problem(empty)
    assert "isn't a file" in rx.problem(tmp_path / "missing.xml")
    for bad in (other, text, empty, tmp_path / "missing.xml"):
        with pytest.raises(RekordboxError):
            rx.read_xml(bad)
        with pytest.raises(RekordboxError):
            rx.read_xml_cues(bad)


def test_a_cancel_stops_the_read(xml_file):
    token = CancelToken()
    token.cancel()
    with pytest.raises(Cancelled):
        rx.read_xml(xml_file, cancel=token)


# -- read-only ---------------------------------------------------------------------------------------------


def test_reading_leaves_the_file_exactly_as_it_was(xml_file):
    before = (xml_file.read_bytes(), xml_file.stat().st_mtime_ns)
    rx.read_xml(xml_file)
    rx.read_xml_cues(xml_file)
    assert (xml_file.read_bytes(), xml_file.stat().st_mtime_ns) == before


def test_the_module_never_writes():
    source = Path(rx.__file__).read_text(encoding="utf-8")
    for word in (
        ".write(",
        "open(",
        "write_text",
        "ElementTree(",
        ".replace(",
        "unlink",
        "shutil",
    ):
        assert word not in source, word


# -- in the library and the places that read cues ------------------------------------------------------------


def import_xml(lib, path):
    read = rx.read_xml(path)
    return lib.replace_source(
        "rekordbox_xml",
        str(read.source),
        path.stem,
        read.tracks,
        read.nodes,
        read.notes,
    )


def test_an_xml_file_is_its_own_source_and_a_reimport_keeps_ids(xml_file):
    with Library.open(":memory:") as lib:
        summary = import_xml(lib, xml_file)
        [source] = lib.sources()
        assert (source.kind, source.name, source.track_count) == (
            "rekordbox_xml",
            "rekordbox",
            3,
        )
        assert summary.nodes == 4
        ids = {t.ext_id: t.id for t in lib.tracks()}
        import_xml(lib, xml_file)
        assert {t.ext_id: t.id for t in lib.tracks()} == ids


def test_cues_for_the_card_come_from_the_xml_file(xml_file):
    from dubcrate.card_discs import RekordboxCues

    with Library.open(":memory:") as lib:
        import_xml(lib, xml_file)
        track = next(t for t in lib.tracks() if t.ext_id == "1")
        cues = RekordboxCues(lib).for_track(
            track
        )  # (the file isn't there, so no lead is taken off)
        assert [(c.kind, c.time, c.slot) for c in cues] == [
            ("memory", 0.027, None),
            ("hot", 43.936, 1),
            ("loop", 60.0, None),
            ("loop", 70.0, 3),
            ("hot", 90.0, 4),
        ]
        assert (
            list(
                RekordboxCues(lib).for_track(
                    next(t for t in lib.tracks() if t.ext_id == "5")
                )
            )
            == []
        )


def test_the_cue_cache_reads_an_xml_file_but_the_writer_never_sees_it(
    xml_file, qapp, wait_for
):
    from dubcrate.ui import cue_cache

    with Library.open(":memory:") as lib:
        import_xml(lib, xml_file)
        cache = cue_cache.RekordboxCueCache(lib)
        track = next(t for t in lib.tracks() if t.ext_id == "1")
        changes = []
        cache.changed.connect(lambda: changes.append(1))
        assert cache.cues_for(track) is None
        assert wait_for(lambda: changes)
        assert len(cache.cues_for(track)) == 5
        assert cache.cue_file_for(track) == xml_file
        assert (
            cache.database_for(track) is None
        )  # (what the editor writes to: only a computer's master.db)


def test_a_folder_track_at_the_xml_tracks_path_has_it_as_its_twin(tmp_path):
    music = tmp_path / "Music" / "Alpha.mp3"
    music.parent.mkdir()
    music.write_bytes(b"x")
    xml = tmp_path / "r.xml"
    xml.write_text(
        XML.replace(
            "/Users/me/Music/Alpha%20%26%20Omega.mp3", str(music).replace(" ", "%20")
        ),
        encoding="utf-8",
    )
    with Library.open(":memory:") as lib:
        import_xml(lib, xml)
        on_xml = next(t for t in lib.tracks() if t.ext_id == "1")
        lib.replace_source(
            "folder",
            str(music.parent),
            "Music",
            [TrackRecord(ext_id="f", path=str(music), title="Same file")],
        )
        folder_track = next(t for t in lib.tracks() if t.title == "Same file")
        assert (
            lib.rekordbox_twin(folder_track).id == on_xml.id
            and lib.rekordbox_twin(on_xml) is None
        )


def test_nothing_that_writes_to_rekordbox_knows_about_xml():
    root = Path(rx.__file__).parents[1]
    for name in ("rekordbox_write.py", "ui/rekordbox_editor.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "rekordbox_xml" not in text and "read_xml" not in text, name


def test_our_own_xml_export_reads_back_with_the_same_files_and_playlists(tmp_path):
    """`playlist_export.write_xml` writes the file; reading it gives the same tracks at the same paths."""
    from dubcrate import playlist_export

    music = tmp_path / "Mu sic"
    music.mkdir()
    records = []
    for n in (1, 2):
        file = music / f"Tr&ck {n}.mp3"
        file.write_bytes(b"x")
        records.append(TrackRecord(ext_id=f"t{n}", path=str(file), title=f"Track {n}", artist="Art", bpm=120.0 + n, key="8A", duration=100.0))  # fmt: skip
    with Library.open(":memory:") as lib:
        lib.replace_source("folder", str(music), "Mu sic", records)
        disc = lib.create_disc("Gig")
        lib.add_to_disc(disc, [t.id for t in lib.tracks(sort="path")])
        out = tmp_path / "out.xml"
        playlist_export.write_xml(playlist_export.collect(lib, disc_id=disc), out)
    read = rx.read_xml(out)
    assert sorted(t.path for t in read.tracks) == sorted(r.path for r in records)
    assert sorted((t.title, t.bpm, t.key) for t in read.tracks) == [
        ("Track 1", 121.0, "8A"),
        ("Track 2", 122.0, "8A"),
    ]
    [playlist] = [n for n in read.nodes if not n.is_folder]
    assert playlist.name == "Gig" and len(playlist.track_ext_ids) == 2


def test_the_schema_upgrade_allows_xml_sources_on_a_v19_library(tmp_path):
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
        assert lib._conn.execute("PRAGMA user_version").fetchone()[0] == len(full)
        lib.replace_source(
            "rekordbox_xml",
            "/x/r.xml",
            "R",
            [TrackRecord(ext_id="1", path="/x/b.mp3", title="B")],
        )
        lib.replace_source(
            "rekordbox_usb",
            "/Volumes/X",
            "X",
            [TrackRecord(ext_id="1", path="/Volumes/X/b.mp3", title="B")],
        )
        assert sorted(s.kind for s in lib.sources()) == [
            "folder",
            "rekordbox_usb",
            "rekordbox_xml",
        ]
        assert [t.title for t in lib.tracks(search="A")] == ["A"]
        assert lib._conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


# -- the command line -----------------------------------------------------------------------------------------


def test_the_command_line_imports_an_xml_file(xml_file, tmp_path, capsys):
    from dubcrate import cli

    db = tmp_path / "lib.db"
    assert cli.main(["--library", str(db), "import-xml", str(xml_file)]) == 0
    assert "added 3" in capsys.readouterr().out
    assert cli.main(["--library", str(db), "sources"]) == 0
    assert "rekordbox_xml" in capsys.readouterr().out
    assert (
        cli.main(["--library", str(db), "import-xml", str(tmp_path / "nope.xml")]) == 1
    )
    assert "isn't a file" in capsys.readouterr().err


# -- review fixes: which source is a track's twin ---------------------------------------------------------------


def test_the_computers_library_is_a_files_twin_before_an_xml_export_or_a_stick():
    """An XML export (or a stick) imported FIRST must not become the twin of the same file in master.db: it has no
    analysis, and the cue cache would read the XML and the editor would stop writing to rekordbox."""
    with Library.open(":memory:") as lib:
        lib.replace_source(
            "rekordbox_xml",
            "/x/r.xml",
            "X",
            [TrackRecord(ext_id="1", path="/m/a.mp3", title="a")],
        )
        lib.replace_source(
            "rekordbox_usb",
            "/Volumes/S",
            "S",
            [TrackRecord(ext_id="2", path="/m/a.mp3", title="a")],
        )
        lib.replace_source("rekordbox", "/rb/master.db", "RB", [TrackRecord(ext_id="9", path="/m/a.mp3", title="a", analysis_path="/PIONEER/x/ANLZ0000.DAT")])  # fmt: skip
        by_ext = {t.ext_id: t for t in lib.tracks()}
        assert (
            lib.rekordbox_twin(by_ext["9"]) is None
        )  # (the master.db track has no twin of its own)
        assert (
            lib.rekordbox_twin(by_ext["1"]).ext_id == "9"
        )  # (the XML's takes the computer's analysis)
        assert lib.rekordbox_twin(by_ext["2"]).ext_id == "9"
        lib.replace_source(
            "folder", "/m", "M", [TrackRecord(ext_id="f", path="/m/a.mp3", title="a")]
        )
        folder = next(t for t in lib.tracks() if t.ext_id == "f")
        assert lib.rekordbox_twin(folder).analysis_path == "/PIONEER/x/ANLZ0000.DAT"


def test_without_a_computers_library_a_stick_comes_before_an_xml_file():
    with Library.open(":memory:") as lib:
        lib.replace_source(
            "rekordbox_xml",
            "/x/r.xml",
            "X",
            [TrackRecord(ext_id="1", path="/m/a.mp3", title="a")],
        )
        lib.replace_source(
            "rekordbox_usb",
            "/Volumes/S",
            "S",
            [TrackRecord(ext_id="2", path="/m/a.mp3", title="a")],
        )
        lib.replace_source(
            "folder", "/m", "M", [TrackRecord(ext_id="f", path="/m/a.mp3", title="a")]
        )
        folder = next(t for t in lib.tracks() if t.ext_id == "f")
        assert lib.rekordbox_twin(folder).ext_id == "2"


# -- review fixes: which reader a cue source gets ----------------------------------------------------------------


def test_a_master_db_copy_with_any_name_is_still_read_as_a_master_db(tmp_path):
    from dubcrate.library import rekordbox_sources as rs

    copy = tmp_path / "master.db.bak"
    copy.write_bytes(b"x")
    seen = []
    rs.read_cues_at(copy, lambda p: seen.append(p) or {})
    odd = tmp_path / "master_backup.sqlite"
    odd.write_bytes(b"x")
    rs.read_cues_at(odd, lambda p: seen.append(p) or {})
    assert seen == [copy, odd]
    # a folder, a stick's own database and a drive that isn't there now are sticks
    for stick in (tmp_path, tmp_path / "Volumes" / "Gone"):
        with pytest.raises(RekordboxError, match="isn't a rekordbox USB stick"):
            rs.read_cues_at(stick, lambda p: seen.append(p) or {})
    assert seen == [copy, odd]
