"""The same file under two rows (rekordbox's and the Explorer's), matched on its path: the
"already used" check, the marks on the track list, rekordbox's tempo, key and analysis for an
Explorer file."""

import unicodedata
from pathlib import Path

import pytest

from dubcrate.audio.player import AudioPlayer
from dubcrate.library import Library, TrackRecord
from dubcrate.library.folder import DirectoryScan
from dubcrate.library.store import path_key
from dubcrate.ui import preview
from dubcrate.ui.main_window import MainWindow
from dubcrate.ui.player_strip import PlayerStrip
from dubcrate.ui.preview import PreviewController


def rb(path: str, **kw) -> TrackRecord:
    return TrackRecord(
        ext_id=path, path=path, title=Path(path).stem, duration=200, **kw
    )


@pytest.fixture
def lib():
    with Library.open(":memory:") as library:
        yield library


def rekordbox(lib, *records):
    lib.replace_source("rekordbox", "/rb/master.db", "rekordbox", records)
    return {t.path: t for t in lib.tracks()}


def explorer(lib, directory: str, *records):
    lib.sync_directory(directory, records)
    return {t.path: t for t in lib.tracks(node_id=lib.explorer_node(directory))}


# -- what makes two paths one file -----------------------------------------------------------


def test_paths_differing_only_in_case_or_unicode_form_are_the_same_file():
    nfc = unicodedata.normalize("NFC", "/m/Café.mp3")
    nfd = unicodedata.normalize("NFD", "/m/Café.mp3")
    assert nfc != nfd
    assert path_key(nfc) == path_key(nfd) == path_key("/M/CAFÉ.MP3")
    assert path_key("/m/a.mp3") != path_key("/m/b.mp3")


# -- the twin ----------------------------------------------------------------------------------


def test_an_explorer_file_that_is_in_rekordbox_has_it_as_its_twin(lib):
    rekordbox(lib, rb("/m/a.mp3", bpm=128.0, key="8A", analysis_path="/PIONEER/a.DAT"))
    found = explorer(lib, "/m", rb("/m/a.mp3"), rb("/m/other.mp3"))
    twin = lib.rekordbox_twin(found["/m/a.mp3"])
    assert twin is not None and twin.analysis_path == "/PIONEER/a.DAT"
    assert lib.rekordbox_twin(found["/m/other.mp3"]) is None
    assert lib.rekordbox_twin(twin) is None  # (a rekordbox track has none)


def test_a_rekordbox_track_that_has_left_rekordbox_is_not_a_twin(lib):
    rekordbox(lib, rb("/m/a.mp3", bpm=128.0))
    found = explorer(lib, "/m", rb("/m/a.mp3"))
    rekordbox(lib)  # (re-imported without it: kept only if a disc uses it)
    assert lib.rekordbox_twin(found["/m/a.mp3"]) is None


# -- rekordbox's tempo and key ---------------------------------------------------------------------


def test_an_explorer_file_takes_rekordboxs_tempo_and_key(lib):
    rekordbox(lib, rb("/m/a.mp3", bpm=128.0, key="8A"))
    found = explorer(lib, "/m", rb("/m/a.mp3", bpm=100.0, key="1B"))
    assert (found["/m/a.mp3"].bpm, found["/m/a.mp3"].key) == (128.0, "8A")


def test_the_tags_fill_in_what_rekordbox_has_not_got(lib):
    rekordbox(lib, rb("/m/a.mp3", bpm=128.0))  # (no key)
    found = explorer(lib, "/m", rb("/m/a.mp3", bpm=100.0, key="1B"))
    assert (found["/m/a.mp3"].bpm, found["/m/a.mp3"].key) == (128.0, "1B")


def test_a_file_not_in_rekordbox_keeps_its_tags(lib):
    rekordbox(lib, rb("/m/a.mp3", bpm=128.0))
    found = explorer(lib, "/m", rb("/m/b.mp3", bpm=90.0, key="2A"))
    assert (found["/m/b.mp3"].bpm, found["/m/b.mp3"].key) == (90.0, "2A")


def test_the_match_ignores_case_and_unicode_form(lib):
    nfd = unicodedata.normalize("NFD", "/m/Café.mp3")
    rekordbox(lib, rb("/m/Café.mp3", bpm=128.0))
    found = explorer(lib, "/m", rb(nfd))
    assert found[nfd].bpm == 128.0


# -- already used --------------------------------------------------------------------------------


def test_a_track_is_used_when_its_rekordbox_twin_is_on_a_disc(lib):
    rb_tracks = rekordbox(lib, rb("/m/a.mp3"), rb("/m/b.mp3"))
    found = explorer(lib, "/m", rb("/m/a.mp3"), rb("/m/b.mp3"), rb("/m/c.mp3"))
    disc = lib.create_disc("Night")
    lib.add_to_disc(disc, [rb_tracks["/m/a.mp3"].id])
    lib.mark_burned(disc, when="2026-11-01T20:00:00+00:00")
    usage = lib.track_usage([t.id for t in found.values()])
    assert list(usage) == [
        found["/m/a.mp3"].id
    ]  # b is on no disc, c is not in rekordbox
    assert [(d.name, d.burned_at) for d in usage[found["/m/a.mp3"].id].discs] == [
        ("Night", "2026-11-01T20:00:00+00:00")
    ]


def test_it_works_the_other_way_round_too(lib):
    found = explorer(lib, "/m", rb("/m/a.mp3"))
    rb_tracks = rekordbox(lib, rb("/m/a.mp3"))
    disc = lib.create_disc("Night")
    lib.add_to_disc(disc, [found["/m/a.mp3"].id])
    assert list(lib.track_usage([rb_tracks["/m/a.mp3"].id])) == [
        rb_tracks["/m/a.mp3"].id
    ]


def test_usage_lists_a_disc_once_even_if_both_rows_are_on_it(lib):
    rb_tracks = rekordbox(lib, rb("/m/a.mp3"))
    found = explorer(lib, "/m", rb("/m/a.mp3"))
    disc = lib.create_disc("Night")
    lib.add_to_disc(disc, [rb_tracks["/m/a.mp3"].id, found["/m/a.mp3"].id])
    usage = lib.track_usage([found["/m/a.mp3"].id])
    assert [d.name for d in usage[found["/m/a.mp3"].id].discs] == ["Night"]


def test_unknown_ids_and_nothing_have_no_usage(lib):
    assert lib.track_usage([]) == {} and lib.track_usage([999]) == {}


def test_same_file_ids_adds_the_twins(lib):
    rb_tracks = rekordbox(lib, rb("/m/a.mp3"), rb("/m/b.mp3"))
    found = explorer(lib, "/m", rb("/m/a.mp3"), rb("/m/c.mp3"))
    a_rb, a_ex = rb_tracks["/m/a.mp3"].id, found["/m/a.mp3"].id
    assert lib.same_file_ids([a_rb]) == {a_rb, a_ex}
    assert lib.same_file_ids([a_ex]) == {a_rb, a_ex}
    assert lib.same_file_ids([found["/m/c.mp3"].id]) == {found["/m/c.mp3"].id}
    assert lib.same_file_ids([]) == set()


# -- the window's marks --------------------------------------------------------------------------


def test_a_track_whose_twin_is_on_the_open_disc_or_another_is_marked(qapp, tmp_path):
    lib = Library.open(":memory:")
    rb_tracks = rekordbox(lib, rb("/m/a.mp3"), rb("/m/b.mp3"))
    win = MainWindow(lib)
    try:
        open_disc = lib.create_disc("Open")
        other = lib.create_disc("Other")
        lib.add_to_disc(open_disc, [rb_tracks["/m/a.mp3"].id])
        lib.add_to_disc(other, [rb_tracks["/m/b.mp3"].id])
        win.refresh_discs()
        win.open_disc(open_disc)
        scan = DirectoryScan(Path("/m"))
        scan.tracks += [rb("/m/a.mp3"), rb("/m/b.mp3"), rb("/m/c.mp3")]
        win._explorer_scanned("/m", scan)  # (syncs, and marks again)
        found = {t.path: t.id for t in lib.tracks(node_id=lib.explorer_node("/m"))}
        model = win.track_model
        assert found["/m/a.mp3"] in model._on_disc
        assert found["/m/b.mp3"] in model._on_other
        assert found["/m/c.mp3"] not in model._on_disc | model._on_other
    finally:
        win.close()
        win.deleteLater()
        lib.close()


# -- previewing ------------------------------------------------------------------------------------


@pytest.fixture
def loads(monkeypatch):
    """What `PreviewController.load` asks `load_media` for, run at once instead of on a worker."""
    asked = []
    monkeypatch.setattr(preview, "run_async", lambda work, done, failed: work())
    monkeypatch.setattr(preview, "load_media", lambda *args: asked.append(args) or None)
    return asked


def test_an_explorer_file_in_rekordbox_previews_with_rekordboxs_analysis(
    qapp, tmp_path, loads
):
    song = tmp_path / "a.mp3"
    song.write_bytes(b"x")
    lib = Library.open(":memory:")
    rekordbox(lib, rb(str(song), analysis_path="/PIONEER/a.DAT"))
    found = explorer(lib, str(tmp_path), rb(str(song)))
    controller = PreviewController(lib, PlayerStrip(), AudioPlayer())
    try:
        controller.load(found[str(song)])
        assert loads == [(str(song), Path("/rb/share"), "/PIONEER/a.DAT")]
    finally:
        controller.shutdown()
        lib.close()


def test_an_explorer_file_not_in_rekordbox_previews_without_analysis(
    qapp, tmp_path, loads
):
    song = tmp_path / "b.mp3"
    song.write_bytes(b"x")
    lib = Library.open(":memory:")
    found = explorer(lib, str(tmp_path), rb(str(song)))
    controller = PreviewController(lib, PlayerStrip(), AudioPlayer())
    try:
        controller.load(found[str(song)])
        assert loads == [(str(song), None, None)]
    finally:
        controller.shutdown()
        lib.close()
