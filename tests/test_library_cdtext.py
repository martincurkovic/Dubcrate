"""Editing what goes on the CD (CD-TEXT artist and track name) for one disc only."""

import sqlite3

import pytest

from dubcrate.library import Library, TrackRecord
from dubcrate.library import store as store_module
from dubcrate.library.store import DiscError


def rec(ext_id, artist="Artist", seconds=100) -> TrackRecord:
    return TrackRecord(
        ext_id=ext_id,
        path=f"/nonexistent/{ext_id}.mp3",
        title=f"Song {ext_id}",
        artist=artist,
        duration=seconds,
    )


@pytest.fixture
def lib():
    with Library.open(":memory:") as library:
        yield library


@pytest.fixture
def disc(lib):
    lib.replace_source("rekordbox", "/db", "rb", [rec(c) for c in "abcd"])
    ids = [t.id for t in lib.tracks(sort="title")]
    disc_id = lib.create_disc("Night")
    lib.add_to_disc(disc_id, ids)
    return disc_id


def texts(lib, disc_id):
    return [(e.title, e.artist) for e in lib.disc_entries(disc_id)]


# -- editing --------------------------------------------------------------------


def test_an_entry_shows_the_librarys_text_until_it_is_edited(lib, disc):
    assert texts(lib, disc)[0] == ("Song a", "Artist")
    [first, *_] = lib.disc_entries(disc)
    assert not first.edited
    assert (first.title_override, first.artist_override) == (None, None)


def test_editing_the_title_and_artist_changes_only_this_disc(lib, disc):
    lib.set_entry_text(disc, 0, "title", "Edited Name")
    lib.set_entry_text(disc, 0, "artist", "Edited Artist")
    [first, *_] = lib.disc_entries(disc)
    assert (first.title, first.artist) == ("Edited Name", "Edited Artist")
    assert first.edited
    # the library's own track, which is what rekordbox and the file say, is untouched
    assert (first.track.title, first.track.artist) == ("Song a", "Artist")
    assert lib.track(first.track.id).title == "Song a"
    assert next(t.title for t in lib.tracks(sort="title")) == "Song a"


def test_the_same_track_on_another_disc_is_not_affected(lib, disc):
    other = lib.create_disc("Other")
    lib.add_to_disc(other, [lib.disc_entries(disc)[0].track.id])
    lib.set_entry_text(disc, 0, "title", "Only On Night")
    assert texts(lib, other) == [("Song a", "Artist")]


def test_only_the_edited_field_is_overridden(lib, disc):
    lib.set_entry_text(disc, 1, "artist", "Someone Else")
    [_, second, *_] = lib.disc_entries(disc)
    assert (second.title, second.artist) == ("Song b", "Someone Else")
    assert (second.title_override, second.artist_override) == (None, "Someone Else")


def test_clearing_the_text_goes_back_to_the_original(lib, disc):
    lib.set_entry_text(disc, 0, "title", "Edited")
    lib.set_entry_text(disc, 0, "title", "")
    lib.set_entry_text(disc, 1, "artist", "Edited")
    lib.set_entry_text(disc, 1, "artist", "   ")
    assert texts(lib, disc)[:2] == [("Song a", "Artist"), ("Song b", "Artist")]
    assert not any(e.edited for e in lib.disc_entries(disc))


def test_typing_the_original_text_back_is_not_an_edit(lib, disc):
    lib.set_entry_text(disc, 0, "title", "Different")
    lib.set_entry_text(disc, 0, "title", "Song a")
    assert not lib.disc_entries(disc)[0].edited


def test_surrounding_spaces_are_dropped(lib, disc):
    lib.set_entry_text(disc, 0, "title", "  Padded  ")
    assert lib.disc_entries(disc)[0].title == "Padded"


def test_a_bad_position_or_field_is_a_clear_error(lib, disc):
    with pytest.raises(DiscError, match="no track at position 9"):
        lib.set_entry_text(disc, 9, "title", "x")
    with pytest.raises(ValueError, match="title or artist"):
        lib.set_entry_text(disc, 0, "album", "x")
    with pytest.raises(DiscError, match="no disc"):
        lib.set_entry_text(999, 0, "title", "x")


# -- edits stay with their track as the disc changes ----------------------------


def test_edits_follow_their_track_when_others_are_added_before_it(lib, disc):
    lib.set_entry_text(disc, 1, "title", "Kept")
    new = lib.replace_source("rekordbox", "/db2", "rb2", [rec("z")])
    z = [t.id for t in lib.tracks(source_id=new.source_id)]
    lib.add_to_disc(disc, z, position=0)  # everything shifts down one
    assert texts(lib, disc)[2][0] == "Kept"
    assert texts(lib, disc)[0][0] == "Song z"


def test_edits_survive_removing_other_tracks(lib, disc):
    lib.set_entry_text(disc, 2, "artist", "Third")
    lib.remove_from_disc(disc, [0, 1])
    assert texts(lib, disc)[0] == ("Song c", "Third")


def test_edits_survive_moving_and_reordering(lib, disc):
    lib.set_entry_text(disc, 0, "title", "First Edited")
    lib.set_entry_text(disc, 3, "title", "Last Edited")
    lib.move_in_disc(disc, 0, 3)  # the first goes to the end
    assert [t for t, _ in texts(lib, disc)] == [
        "Song b",
        "Song c",
        "Last Edited",
        "First Edited",
    ]
    lib.reorder_disc(disc, [2, 3], 0)  # the two edited ones to the front
    assert [t for t, _ in texts(lib, disc)] == [
        "Last Edited",
        "First Edited",
        "Song b",
        "Song c",
    ]


def test_the_same_track_twice_on_a_disc_has_separate_edits(lib, disc):
    first = lib.disc_entries(disc)[0].track.id
    lib.add_to_disc(disc, [first])  # on the disc again, at the end
    lib.set_entry_text(disc, 4, "title", "Second Copy")
    assert (
        texts(lib, disc)[0][0] == "Song a" and texts(lib, disc)[4][0] == "Second Copy"
    )


def test_splitting_into_discs_starts_with_the_librarys_text(lib):
    lib.replace_source("rekordbox", "/db", "rb", [rec(c, seconds=2000) for c in "abc"])
    result = lib.split_into_discs([t.id for t in lib.tracks(sort="title")], "Split")
    for disc_id in result.disc_ids:
        assert not any(e.edited for e in lib.disc_entries(disc_id))


# -- copies, and burning ---------------------------------------------------------


def test_duplicating_a_disc_carries_its_edits_into_the_copy_independently(lib, disc):
    lib.set_entry_text(disc, 0, "title", "Edited Name")
    lib.set_entry_text(disc, 2, "artist", "Edited Artist")
    copy = lib.duplicate_disc(disc)
    assert texts(lib, copy)[0] == ("Edited Name", "Artist")
    assert texts(lib, copy)[2] == ("Song c", "Edited Artist")
    assert not lib.disc_entries(copy)[1].edited  # unedited rows stay unedited
    lib.set_entry_text(copy, 0, "title", "Only On The Copy")  # and they are separate
    assert texts(lib, disc)[0][0] == "Edited Name"


def test_burning_a_disc_does_not_freeze_its_text(lib, disc):
    lib.set_entry_text(disc, 0, "title", "Burned Name")
    lib.mark_burned(disc)
    lib.set_entry_text(disc, 0, "title", "Changed After")  # still an ordinary playlist
    assert texts(lib, disc)[0][0] == "Changed After"


def test_the_library_tracks_are_never_changed_by_any_of_it(lib, disc):
    before = [(t.title, t.artist) for t in lib.tracks(sort="title")]
    lib.set_entry_text(disc, 0, "title", "X")
    lib.set_entry_text(disc, 1, "artist", "Y")
    lib.duplicate_disc(disc)
    lib.mark_burned(disc)
    assert [(t.title, t.artist) for t in lib.tracks(sort="title")] == before


# -- the schema -----------------------------------------------------------------


def test_a_v4_library_upgrades_and_its_discs_have_no_edits(tmp_path):
    path = tmp_path / "v4.db"
    conn = sqlite3.connect(path)
    for script in store_module._MIGRATIONS[:4]:
        for statement in script.split(";"):
            if statement.strip():
                conn.execute(statement)
    conn.execute("PRAGMA user_version = 4")
    conn.execute(
        "INSERT INTO source (kind, path, name, imported_at) VALUES ('folder', '/m', 'M', 'now')"
    )
    conn.execute(
        "INSERT INTO track (id, source_id, ext_id, path, title, artist, duration)"
        " VALUES (5, 1, 'x', '/m/x.mp3', 'Old', 'Old Artist', 200)"
    )  # fmt: skip
    conn.execute(
        "INSERT INTO disc (id, name, capacity_sectors, created_at, updated_at) VALUES (1, 'D', 360000, 'n', 'n')"
    )
    conn.execute(
        "INSERT INTO disc_track (disc_id, track_id, position) VALUES (1, 5, 0)"
    )
    conn.commit()
    conn.close()
    with Library.open(path) as lib:
        assert (
            lib._conn.execute("PRAGMA user_version").fetchone()[0]
            == store_module.SCHEMA_VERSION
        )
        [entry] = lib.disc_entries(1)
        assert (entry.title, entry.artist, entry.edited) == ("Old", "Old Artist", False)
        lib.set_entry_text(1, 0, "title", "Now Edited")
        assert lib.disc_entries(1)[0].title == "Now Edited"
