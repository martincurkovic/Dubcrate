"""The extra rekordbox fields: label, remixer, composer, My Tags, plays, date added."""

import sqlite3
from types import SimpleNamespace

import pytest

from dubcrate.library import Library, TrackRecord
from dubcrate.library import rekordbox as rb
from dubcrate.library import store as store_module


def content(**kw):
    base = {
        "ID": "10", "FolderPath": "/music/a.mp3", "Title": "Song", "ArtistName": "Artist", "AlbumName": "Album",
        "GenreName": "House", "ReleaseYear": 2024, "Commnt": "note", "BPM": 12800, "KeyName": "8A", "Length": 300,
        "BitRate": 320, "SampleRate": 44100, "BitDepth": 16, "Rating": 4, "FileSize": 12345,
        "AnalysisDataPath": "/PIONEER/USBANLZ/x/ANLZ0000.DAT", "rb_local_deleted": 0,
    }  # fmt: skip
    return SimpleNamespace(**{**base, **kw})


def rec(ext_id="a", **kw) -> TrackRecord:
    return TrackRecord(
        ext_id=ext_id, path=f"/nonexistent/{ext_id}.mp3", title=ext_id, **kw
    )


@pytest.fixture
def lib():
    with Library.open(":memory:") as library:
        yield library


# -- the importer ---------------------------------------------------------------


def test_the_importer_reads_label_remixer_composer_tags_plays_and_date_added():
    t = rb.track_from_content(
        content(
            LabelName="PMEDIA", RemixerName="Some Remixer", ComposerName="KETTAMA",
            MyTagNames=["Pop Remix", "Extended Pop"], DJPlayCount=7, StockDate="2025-10-04",
        )
    )  # fmt: skip
    assert (t.label, t.remixer, t.composer) == ("PMEDIA", "Some Remixer", "KETTAMA")
    assert t.my_tags == "Pop Remix, Extended Pop"
    assert (t.play_count, t.date_added) == (7, "2025-10-04")


def test_missing_or_empty_extra_fields_become_empty_not_errors():
    # An older pyrekordbox or database may not have these attributes at all.
    t = rb.track_from_content(content())
    assert (t.label, t.remixer, t.composer, t.my_tags, t.date_added) == (
        "",
        "",
        "",
        "",
        "",
    )
    assert t.play_count is None
    t = rb.track_from_content(
        content(LabelName=None, RemixerName=None, ComposerName=None, MyTagNames=None,
                DJPlayCount=0, StockDate=None)
    )  # fmt: skip
    assert (t.label, t.my_tags, t.date_added) == ("", "", "")
    assert t.play_count is None  # never played is "nothing", not 0


def test_a_date_object_is_stored_as_text():
    import datetime

    t = rb.track_from_content(content(StockDate=datetime.date(2025, 10, 4)))
    assert t.date_added == "2025-10-04"


# -- the store ------------------------------------------------------------------


def test_the_extra_fields_are_stored_and_read_back(lib):
    lib.replace_source(
        "rekordbox", "/db", "rb",
        [rec(label="L", remixer="R", composer="C", my_tags="One, Two", play_count=3, date_added="2025-01-02")],
    )  # fmt: skip
    [t] = lib.tracks()
    assert (t.label, t.remixer, t.composer, t.my_tags) == ("L", "R", "C", "One, Two")
    assert (t.play_count, t.date_added) == (3, "2025-01-02")


def test_a_reimport_updates_them_and_keeps_the_track_id(lib):
    lib.replace_source("rekordbox", "/db", "rb", [rec(my_tags="Old", play_count=1)])
    [before] = lib.tracks()
    lib.replace_source(
        "rekordbox", "/db", "rb", [rec(my_tags="New, Tag", play_count=5)]
    )
    [after] = lib.tracks()
    assert after.id == before.id
    assert (after.my_tags, after.play_count) == ("New, Tag", 5)


def test_folder_tracks_simply_have_none_of_them(lib):
    lib.replace_source("folder", "/m", "M", [rec()])
    [t] = lib.tracks()
    assert (t.label, t.my_tags, t.date_added, t.play_count) == ("", "", "", None)


def test_a_v3_library_upgrades_and_its_tracks_get_empty_extras(tmp_path):
    path = tmp_path / "v3.db"
    conn = sqlite3.connect(path)
    for script in store_module._MIGRATIONS[:3]:
        for statement in script.split(";"):
            if statement.strip():
                conn.execute(statement)
    conn.execute("PRAGMA user_version = 3")
    conn.execute(
        "INSERT INTO source (kind, path, name, imported_at) VALUES ('folder', '/m', 'M', 'now')"
    )
    conn.execute(
        "INSERT INTO track (id, source_id, ext_id, path, title, duration, year)"
        " VALUES (7, 1, 'x', '/m/x.mp3', 'Kept', 200, 2020)"
    )  # fmt: skip
    conn.commit()
    conn.close()
    with Library.open(path) as lib:
        assert (
            lib._conn.execute("PRAGMA user_version").fetchone()[0]
            == store_module.SCHEMA_VERSION
        )
        [t] = lib.tracks()
        assert (t.id, t.title, t.year) == (7, "Kept", 2020)
        assert (t.label, t.remixer, t.composer, t.my_tags, t.date_added) == (
            "",
            "",
            "",
            "",
            "",
        )
        assert t.play_count is None


@pytest.mark.parametrize(
    "key", ["album", "year", "rating", "comment", "bitrate", "sample_rate", "label", "remixer",
            "composer", "my_tags", "date_added", "play_count", "path"],
)  # fmt: skip
def test_every_extra_column_can_be_sorted_by(lib, key):
    lib.replace_source("rekordbox", "/db", "rb", [rec("a"), rec("b")])
    assert len(lib.tracks(sort=key)) == 2
    assert len(lib.tracks(sort=key, descending=True)) == 2


def test_sorting_by_the_new_fields_orders_them_properly(lib):
    lib.replace_source(
        "rekordbox", "/db", "rb",
        [rec("a", label="Zed", play_count=2, date_added="2025-03-01"),
         rec("b", label="alpha", play_count=9, date_added="2024-01-01"),
         rec("c", label="Mid", play_count=None, date_added="2026-06-30")],
    )  # fmt: skip
    assert [t.ext_id for t in lib.tracks(sort="label")] == [
        "b",
        "c",
        "a",
    ]  # case-insensitive
    assert [t.ext_id for t in lib.tracks(sort="date_added")] == ["b", "a", "c"]
    assert [t.ext_id for t in lib.tracks(sort="play_count", descending=True)][:2] == [
        "b",
        "a",
    ]
