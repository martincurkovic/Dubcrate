"""Discs as playlists: a rekordbox XML file and M3U files (read-only on every library)."""

from pathlib import Path

import pytest
from pyrekordbox.rbxml import RekordboxXml

from dubcrate import playlist_export as pe
from dubcrate.library import Library, TrackRecord
from dubcrate.playlist_export import PlaylistError


@pytest.fixture
def lib():
    with Library.open(":memory:") as library:
        yield library


@pytest.fixture
def music(tmp_path):
    folder = tmp_path / "Música & more"
    folder.mkdir()
    for name in ("a", "b", "c", "d"):
        (folder / f"{name}.mp3").write_bytes(b"x")
    return folder


def seed(lib, music, names=("a", "b", "c", "d")):
    lib.replace_source(
        "folder",
        str(music),
        "M",
        [
            TrackRecord(
                ext_id=n,
                path=str(music / f"{n}.mp3"),
                title=f"Title {n.upper()}",
                artist=f"Artist {n.upper()}",
                duration=200.4,
                bpm=128.0,
                key="8A",
                year=2020,
            )
            for n in names
        ],
    )
    return {t.title[-1].lower(): t.id for t in lib.tracks(sort="path")}


def test_an_audio_disc_is_a_playlist_in_its_running_order(lib, music):
    ids = seed(lib, music)
    disc = lib.create_disc("Night")
    lib.add_to_disc(disc, [ids["c"], ids["a"], ids["b"]])
    got = pe.collect(lib, disc_id=disc)
    [playlist] = got.root.all_playlists()
    assert playlist.name == "Night"
    assert [t.title for t in playlist.tracks] == ["Title C", "Title A", "Title B"]


def test_the_library_tracks_text_is_used_not_the_discs_edit(lib, music):
    ids = seed(lib, music)
    disc = lib.create_disc("Night")
    lib.add_to_disc(disc, [ids["a"]])
    lib.set_entry_text(disc, 0, "title", "Edited for the CD")
    lib.set_entry_text(disc, 0, "artist", "CD Artist")
    xml_path = Path(music.parent / "out.xml")
    pe.write_xml(pe.collect(lib, disc_id=disc), xml_path)
    text = xml_path.read_text(encoding="utf-8")
    assert 'Name="Title A"' in text
    assert "Edited for the CD" not in text
    assert "CD Artist" not in text


def test_an_mp3_disc_is_flat_root_first_then_folders_by_name(lib, music):
    ids = seed(lib, music)
    disc = lib.create_disc("MP3", kind="mp3")
    z = lib.create_disc_dir(disc, "Zed")
    a = lib.create_disc_dir(disc, "Alpha")
    lib.add_to_disc(disc, [ids["a"]], dir_id=z)
    lib.add_to_disc(disc, [ids["b"]])
    lib.add_to_disc(disc, [ids["c"]], dir_id=a)
    [playlist] = pe.collect(lib, disc_id=disc).root.all_playlists()
    assert [t.title[-1] for t in playlist.tracks] == ["B", "C", "A"]


def test_a_missing_file_is_left_out_and_reported(lib, music):
    ids = seed(lib, music)
    disc = lib.create_disc("Night")
    lib.add_to_disc(disc, [ids["a"], ids["b"]])
    (music / "b.mp3").unlink()
    seed(lib, music, names=("a", "b", "c", "d"))  # a re-import notices
    got = pe.collect(lib, disc_id=disc)
    assert [t.title for t in got.root.all_playlists()[0].tracks] == ["Title A"]
    assert got.skipped == ["Night: Artist B - Title B"]
    assert "left out" in pe.summary(got)


def test_nothing_to_export_is_an_expected_error(lib, music):
    seed(lib, music)
    lib.create_disc("Empty")
    with pytest.raises(PlaylistError, match="nothing to export"):
        pe.collect(lib)


def test_all_discs_keep_the_folders_and_drop_empty_ones(lib, music):
    ids = seed(lib, music)
    f = lib.create_disc_folder("Sets")
    inner = lib.create_disc_folder("Inner", f)
    lib.create_disc_folder("Nothing here")
    d1 = lib.create_disc("Zulu", folder_id=f)
    d2 = lib.create_disc("Alpha", folder_id=inner)
    d3 = lib.create_disc("Top")
    lib.create_disc("Blank")
    for d, t in ((d1, "a"), (d2, "b"), (d3, "c")):
        lib.add_to_disc(d, [ids[t]])
    got = pe.collect(lib)
    assert [f.name for f in got.root.folders] == ["Sets"]
    assert [p.name for p in got.root.playlists] == ["Top"]
    assert [f.name for f in got.root.folders[0].folders] == ["Inner"]
    assert got.empty_discs == ["Blank"]


def test_a_folder_export_keeps_that_folder(lib, music):
    ids = seed(lib, music)
    f = lib.create_disc_folder("Sets")
    d = lib.create_disc("One", folder_id=f)
    lib.add_to_disc(d, [ids["a"]])
    other = lib.create_disc("Elsewhere")
    lib.add_to_disc(other, [ids["b"]])
    got = pe.collect(lib, folder_id=f)
    assert [f.name for f in got.root.folders] == ["Sets"]
    assert [p.name for p in got.root.all_playlists()] == ["One"]


def test_same_named_discs_get_a_number(lib, music):
    ids = seed(lib, music)
    for _ in range(2):
        d = lib.create_disc("Mix")
        lib.add_to_disc(d, [ids["a"]])
    names = [p.name for p in pe.collect(lib).root.all_playlists()]
    assert names == ["Mix", "Mix (2)"]


def test_xml_has_each_file_once_and_the_playlists_point_at_it(lib, music, tmp_path):
    ids = seed(lib, music)
    f = lib.create_disc_folder("Sets")
    d1 = lib.create_disc("One", folder_id=f)
    d2 = lib.create_disc("Two")
    lib.add_to_disc(d1, [ids["a"], ids["b"]])
    lib.add_to_disc(d2, [ids["b"], ids["a"]])
    out = tmp_path / "all.xml"
    written = pe.write_xml(pe.collect(lib), out)
    assert (written.playlists, written.tracks) == (2, 4)
    xml = RekordboxXml(out)
    assert xml.num_tracks == 2
    tracks = {t["Name"]: t for t in xml.get_tracks()}
    assert tracks["Title A"]["Artist"] == "Artist A"
    assert tracks["Title A"]["TotalTime"] == 200
    assert tracks["Title A"]["AverageBpm"] == 128.0
    assert tracks["Title A"]["Tonality"] == "8A"
    sets = xml.get_playlist("Sets")
    one = xml.get_playlist("Sets", "One")
    two = xml.get_playlist("Two")
    assert sets.is_folder
    assert one.get_tracks() == [
        tracks["Title A"]["TrackID"],
        tracks["Title B"]["TrackID"],
    ]
    assert two.get_tracks() == [
        tracks["Title B"]["TrackID"],
        tracks["Title A"]["TrackID"],
    ]


def test_xml_location_is_a_rekordbox_file_url(lib, music, tmp_path):
    ids = seed(lib, music)
    d = lib.create_disc("One")
    lib.add_to_disc(d, [ids["a"]])
    out = tmp_path / "one.xml"
    pe.write_xml(pe.collect(lib, disc_id=d), out)
    text = out.read_text(encoding="utf-8")
    location = text.split('Location="')[1].split('"')[
        0
    ]  # the raw URL, not the decoded path
    assert location.startswith("file://localhost/")
    assert not location.startswith("file://localhost//")
    assert "M%C3%BAsica" in location  # the ú of the folder is escaped
    assert "%20%26%20" in location  # " & "


def test_xml_is_written_in_one_step_and_a_bad_place_is_a_message(lib, music, tmp_path):
    ids = seed(lib, music)
    d = lib.create_disc("One")
    lib.add_to_disc(d, [ids["a"]])
    got = pe.collect(lib, disc_id=d)
    pe.write_xml(got, tmp_path / "ok.xml")
    assert sorted(p.name for p in tmp_path.glob("*.xml*")) == ["ok.xml"]
    with pytest.raises(PlaylistError, match="Could not write"):
        pe.write_xml(got, tmp_path / "no such folder" / "x.xml")
    assert not (tmp_path / "no such folder").exists()


def test_m3u_text(lib, music):
    ids = seed(lib, music)
    d = lib.create_disc("One")
    lib.add_to_disc(d, [ids["a"], ids["b"]])
    [playlist] = pe.collect(lib, disc_id=d).root.all_playlists()
    lines = pe.m3u_text(playlist).splitlines()
    assert lines[0] == "#EXTM3U"
    assert lines[1] == "#EXTINF:200,Artist A - Title A"
    assert lines[2] == str(music / "a.mp3")
    assert len(lines) == 5


def test_m3u_single_file_is_utf8(lib, music, tmp_path):
    ids = seed(lib, music)
    d = lib.create_disc("One")
    lib.add_to_disc(d, [ids["a"]])
    out = tmp_path / "one.m3u8"
    written = pe.write_m3u(pe.collect(lib, disc_id=d), out)
    assert written.tracks == 1
    assert "Música" in out.read_text(encoding="utf-8")


def test_m3u_folder_mirrors_the_disc_folders_and_is_never_reused(lib, music, tmp_path):
    ids = seed(lib, music)
    f = lib.create_disc_folder("Sets")
    d1 = lib.create_disc("A/B: live", folder_id=f)
    d2 = lib.create_disc("Top")
    lib.add_to_disc(d1, [ids["a"]])
    lib.add_to_disc(d2, [ids["b"]])
    got = pe.collect(lib)
    first = pe.write_m3u_folder(got, tmp_path)
    second = pe.write_m3u_folder(got, tmp_path)
    assert first.path == tmp_path / "Dubcrate Playlists"
    assert second.path == tmp_path / "Dubcrate Playlists 2"
    files = sorted(
        p.relative_to(first.path).as_posix() for p in first.path.rglob("*.m3u8")
    )
    assert len(files) == 2
    assert files[1] == "Top.m3u8"
    # a slash in the disc's name did not make a folder of its own
    assert files[0].startswith("Sets/") and files[0].count("/") == 1


def test_a_failed_m3u_export_removes_its_own_folder(lib, music, tmp_path, monkeypatch):
    ids = seed(lib, music)
    d = lib.create_disc("One")
    lib.add_to_disc(d, [ids["a"]])
    got = pe.collect(lib)

    def boom(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(pe, "_write_m3u", boom)
    with pytest.raises(PlaylistError, match="Could not write"):
        pe.write_m3u_folder(got, tmp_path)
    assert not (tmp_path / "Dubcrate Playlists").exists()


def test_no_rekordbox_database_is_opened(lib, music):
    """The export never touches rekordbox's own database code."""
    source = Path(pe.__file__).read_text(encoding="utf-8")
    assert "masterdb" not in source
    assert "commit(" not in source


# -- the command line ------------------------------------------------------------------


def run_cli(tmp_path, *argv):
    from dubcrate import cli

    return cli.main(["--library", str(tmp_path / "lib.db"), *map(str, argv)])


def seeded_db(tmp_path, music):
    with Library.open(tmp_path / "lib.db") as library:
        ids = seed(library, music)
        disc = library.create_disc("Night")
        library.add_to_disc(disc, [ids["a"], ids["b"]])
    return disc


def test_cli_export_playlist_and_playlists(tmp_path, music, capsys):
    disc = seeded_db(tmp_path, music)
    out = tmp_path / "one.xml"
    assert run_cli(tmp_path, "export-playlist", disc, out) == 0
    assert RekordboxXml(out).num_tracks == 2
    assert run_cli(tmp_path, "export-playlist", disc, out) == 1  # never overwritten
    assert "already exists" in capsys.readouterr().err
    all_xml = tmp_path / "all.xml"
    assert run_cli(tmp_path, "export-playlists", all_xml) == 0
    assert run_cli(tmp_path, "export-playlists", tmp_path, "--format", "m3u") == 0
    assert (tmp_path / "Dubcrate Playlists" / "Night.m3u8").is_file()
    single = tmp_path / "one.m3u8"
    assert run_cli(tmp_path, "export-playlist", disc, single, "--format", "m3u") == 0
    assert single.read_text(encoding="utf-8").startswith("#EXTM3U")


def test_cli_m3u_all_needs_an_existing_folder(tmp_path, music, capsys):
    seeded_db(tmp_path, music)
    assert (
        run_cli(tmp_path, "export-playlists", tmp_path / "nope", "--format", "m3u") == 1
    )
    assert "not an existing folder" in capsys.readouterr().err
