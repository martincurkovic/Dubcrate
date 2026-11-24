"""What the player added to an MP3 disc, found on the card and brought back (no ffmpeg, no rekordbox, no real card).

The card file is one the player wrote (`tests/data/cdj_card/mp3/`): a disc with three folders and cues, loops and hot
cues set on it. The slots (which (folder, file) is which track) are made by hand for that file."""

from pathlib import Path

import pytest

from dubcrate import card_import, card_mp3, iso, mp3
from dubcrate import cdj_card_mp3 as m3
from dubcrate.analysis import Cue
from dubcrate.card_import import ADD, HOT, LOOP
from dubcrate.card_mp3 import Mp3Slot
from dubcrate.library import Library, TrackRecord
from dubcrate.library.models import DiscImage

DATA = Path(__file__).parent / "data" / "cdj_card" / "mp3"


def player_file() -> m3.Mp3TrackFile:
    return m3.Mp3TrackFile.parse((DATA / "MPJ00001_all_folders.PDJ").read_bytes())


# (folder, file) -> the disc's track, its lead and the track's length, as the player's own entries say
LENGTHS = {(0, 2): 15304, (0, 3): 24721, (2, 1): 16932, (2, 4): 19349, (1, 1): 12181, (3, 1): 30127}  # fmt: skip


def slots(lead=51):
    return {
        key: Mp3Slot(number, lead, LENGTHS[key])
        for number, key in enumerate(LENGTHS, 2)
    }


def ms(frames, lead=51):
    return round(frames * 1000 / 75) - lead


def rows(result, track):
    return [(r.kind, r.ms, r.end_ms) for r in result.by_track.get(track, [])]


# -- the card's file ----------------------------------------------------------------------------


def test_every_cue_loop_and_hot_cue_the_player_has_is_new_to_an_empty_library_in_audio_time():
    result = card_import.changes_on_mp3_card(player_file(), slots(), {})
    # track 2 is (0, 2): a cue at 1474 frames and the first hot cue (916), shown last
    assert rows(result, 2) == [(ADD, ms(1474), 0), (HOT, ms(916), 0)]
    assert rows(result, 3) == [(LOOP, ms(3358), ms(3493))]
    assert result.outside == 0 and result.kept == 0


def test_hot_cues_are_not_ticked_and_keep_their_slot_number():
    result = card_import.changes_on_mp3_card(player_file(), slots(), {})
    hots = [r for rs in result.by_track.values() for r in rs if r.kind == HOT]
    assert [(r.slot, r.ticked) for r in hots] == [(1, False), (2, False)]
    assert all(r.ticked for rs in result.by_track.values() for r in rs if r.kind != HOT)  # fmt: skip


def test_a_card_time_has_the_files_lead_taken_off_and_never_goes_below_zero():
    with_lead = card_import.changes_on_mp3_card(player_file(), slots(lead=51), {})
    without = card_import.changes_on_mp3_card(player_file(), slots(lead=0), {})
    assert rows(with_lead, 2)[0][1] == rows(without, 2)[0][1] - 51
    huge = card_import.changes_on_mp3_card(player_file(), slots(lead=60000), {})
    assert rows(huge, 2)[0][1] == 0


def test_a_cue_dubcrate_has_is_not_new_even_a_frame_or_two_off():
    # Dubcrate's cue is in audio time; the player's position is that plus the lead
    known = {2: [Cue("memory", (ms(1474) + 20) / 1000)]}  # 20 ms is under two frames
    result = card_import.changes_on_mp3_card(player_file(), slots(), known)
    assert (ADD, ms(1474), 0) not in rows(result, 2)
    far = {2: [Cue("memory", (ms(1474) + 400) / 1000)]}
    result = card_import.changes_on_mp3_card(player_file(), slots(), far)
    assert (ADD, ms(1474), 0) in rows(result, 2)


def test_a_cue_is_compared_in_the_players_time_not_in_the_cards_raw_time():
    # a cue at the same raw time, but with no lead taken off, is 51 ms (about 4 frames) away: new
    raw = {2: [Cue("memory", ms(1474, lead=0) / 1000)]}
    result = card_import.changes_on_mp3_card(player_file(), slots(), raw)
    assert (ADD, ms(1474), 0) in rows(result, 2)


def test_a_loop_dubcrate_has_is_not_new_and_a_cue_at_its_start_is_not_the_loop():
    known = {3: [Cue("loop", ms(3358) / 1000, ms(3493) / 1000)]}
    result = card_import.changes_on_mp3_card(player_file(), slots(), known)
    assert rows(result, 3) == []
    only_a_cue = {3: [Cue("memory", ms(3358) / 1000)]}
    result = card_import.changes_on_mp3_card(player_file(), slots(), only_a_cue)
    assert rows(result, 3) == [(LOOP, ms(3358), ms(3493))]


def test_a_hot_cue_where_dubcrate_or_the_player_has_a_cue_is_not_listed():
    known = {2: [Cue("hot", ms(916) / 1000, slot=1)]}
    result = card_import.changes_on_mp3_card(player_file(), slots(), known)
    assert rows(result, 2) == [(ADD, ms(1474), 0)]
    f = player_file()
    f.cues.append(m3.Mp3Entry(m3.KIND_CUE, 2, 1, 919, 0, 16932, 0, 0, 0, 0))
    result = card_import.changes_on_mp3_card(f, slots(), {})
    assert HOT not in [r.kind for r in result.by_track[4]]


def test_entries_for_a_file_the_disc_does_not_have_are_counted_not_offered():
    gone = {k: v for k, v in slots().items() if k != (2, 4)}
    result = card_import.changes_on_mp3_card(player_file(), gone, {})
    assert 5 not in result.by_track and result.outside == 1


def test_a_track_whose_length_is_far_from_the_entrys_is_another_track():
    # the disc was changed since: what the player calls (folder 1, file 1) is a different, shorter track now
    changed = {**slots(), (1, 1): Mp3Slot(6, 51, LENGTHS[(1, 1)] - 600)}
    result = card_import.changes_on_mp3_card(player_file(), changed, {})
    assert 6 not in result.by_track and result.outside == 2  # its cue and its loop
    near = {**changed, (1, 1): Mp3Slot(6, 51, LENGTHS[(1, 1)] - 100)}
    result = card_import.changes_on_mp3_card(player_file(), near, {})
    assert 6 in result.by_track


def test_a_length_that_is_not_known_is_not_checked():
    unknown = {k: Mp3Slot(v.track, v.lead_ms, 0) for k, v in slots().items()}
    result = card_import.changes_on_mp3_card(player_file(), unknown, {})
    assert result.outside == 0 and result.total == 10


def test_a_deleted_cue_is_not_noticed_without_a_baseline():
    empty = m3.Mp3TrackFile.new(player_file().disc_id)
    result = card_import.changes_on_mp3_card(empty, slots(), {2: [Cue("memory", 5.0)]})
    assert result.total == 0 and result.kept == 0


# -- the numbers: which (folder, file) is which track ---------------------------------------------


@pytest.fixture
def lib():
    with Library.open(":memory:") as library:
        yield library


def make_disc(lib, n=6, folders=("b folder", "A folder"), per_folder=2):
    """An MP3 disc with `n` tracks in its root and `per_folder` in each folder; track paths do not exist."""
    count = n + per_folder * len(folders)
    lib.replace_source(
        "folder", "/f", "S",
        [TrackRecord(ext_id=f"t{i}", path=f"/nonexistent/t{i}.mp3", title=f"Title {i}", artist="Art", duration=100.0 + i) for i in range(count)],
    )  # fmt: skip
    ids = [t.id for t in lib.tracks(sort="path")]
    disc = lib.create_disc("Disc", kind="mp3")
    lib.add_to_disc(disc, ids[:n])
    at = n
    for name in folders:
        folder = lib.create_disc_dir(disc, name)
        lib.add_to_disc(disc, ids[at : at + per_folder], dir_id=folder)
        at += per_folder
    return disc


@pytest.fixture
def lead51(monkeypatch):
    monkeypatch.setattr(card_mp3, "rendered_lead_ms", lambda _p, _b: 51)


def test_folders_are_numbered_in_the_order_of_their_names_and_files_from_one(
    lib, lead51
):
    disc = make_disc(lib)
    found = card_mp3.mp3_slots(lib.disc_entries(disc), lib.disc_dirs(disc))
    # the root's six files, then "A folder" (1) and "b folder" (2): created in the other order
    assert [found[(0, n)].track for n in range(1, 7)] == [1, 2, 3, 4, 5, 6]
    assert [found[(1, n)].track for n in (1, 2)] == [9, 10]
    assert [found[(2, n)].track for n in (1, 2)] == [7, 8]
    assert all(s.lead_ms == 51 for s in found.values())


def test_a_slot_has_the_tracks_length_measured_if_known_else_its_duration(lib, lead51):
    disc = make_disc(lib, n=2, folders=())
    first = lib.disc_entries(disc)[0].track
    lib.set_cd_sectors(first.id, 9000)
    found = card_mp3.mp3_slots(lib.disc_entries(disc), lib.disc_dirs(disc))
    assert found[(0, 1)].length_frames == 9000
    assert found[(0, 2)].length_frames == round(101.0 * 75)


def test_a_folder_inside_a_folder_is_refused(lib, lead51):
    disc = make_disc(lib, n=1, folders=("one",))
    inner = lib.create_disc_dir(disc, "inner", parent_id=lib.disc_dirs(disc)[0].id)
    assert inner
    with pytest.raises(card_mp3.Mp3SyncError):
        card_mp3.mp3_slots(lib.disc_entries(disc), lib.disc_dirs(disc))


def test_a_file_that_cannot_be_read_has_no_slot(lib):
    disc = make_disc(
        lib, n=2, folders=()
    )  # (the paths do not exist: no lead can be found)
    assert card_mp3.mp3_slots(lib.disc_entries(disc), lib.disc_dirs(disc)) == {}


def test_the_numbers_are_the_ones_the_burned_image_has(lib, lead51, tmp_path):
    """The numbers must be what `iso.image_layout` finds, which is what the player names a file by. More than 99
    files in a folder is where the written names (`10`, `100`, `11`) sort differently from the disc's order."""
    disc = make_disc(lib, n=105, folders=("b folder", "A folder"), per_folder=3)
    entries = lib.disc_entries(disc)
    dirs = lib.disc_dirs(disc)
    folder = tmp_path / "stage"
    where = {None: folder, **{d.id: folder / mp3.dir_name(d.name) for d in dirs}}
    count: dict = {}
    who: dict[Path, int] = {}
    for e in entries:
        count[e.dir_id] = number = count.get(e.dir_id, 0) + 1
        path = where[e.dir_id] / mp3.file_name(number, e.artist, e.title)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\0" * 10)
        who[path] = e.position + 1
    image = iso.build_iso(folder, "Disc", tmp_path / "disc.iso")
    layout = iso.image_layout(image, folder)
    found = card_mp3.mp3_slots(entries, dirs)
    assert len(layout.files) == len(found) == len(entries)
    for f in layout.files:
        assert found[(f.folder, f.file)].track == who[f.path]
    # and the order did differ from the disc's, or this proves nothing
    assert any(f.file != found[(f.folder, f.file)].track for f in layout.files if f.folder == 0)  # fmt: skip


# -- the library half ------------------------------------------------------------------------------


def entry_id(count, last_start, sectors):
    raw = bytes([5, 0x2C, 6, count, 0x33, 0x87, 0x45, 0xE7]) + last_start.to_bytes(3, "big") + sectors.to_bytes(3, "big") + b"\0\0"  # fmt: skip
    return m3.Mp3DiscId(raw)


def card_for(image: DiscImage, file: m3.Mp3TrackFile):
    index = m3.Mp3Index(
        [
            m3.Mp3IndexEntry(
                "MPJ00001",
                entry_id(image.file_count, image.last_file_start, image.sectors),
            )
        ]
    )
    return index, {"MPJ00001": file}


def cue(folder, file, position, length):
    return m3.Mp3Entry(m3.KIND_CUE, folder, file, position, 0, length, 0, 0, 0, 0)


def library_survey(lib, disc, card, cues=None, discs=None):
    rows = card_mp3.survey_mp3_discs(lib, {disc: "F / Disc"}, (True, True))
    return card_import.survey_new_mp3_cues(
        lib,
        card,
        card_import.RekordboxCues(lib, load=lambda _p: {}) if cues is None else cues,
        rows if discs is None else discs,
    )


@pytest.fixture
def burned(lib, lead51):
    """A disc of four tracks (two in the root, two in "A folder"), its image recorded, durations measured."""
    disc = make_disc(lib, n=2, folders=("A folder",), per_folder=2)
    entries = lib.disc_entries(disc)
    for e in entries:
        lib.set_cd_sectors(e.track.id, 7500 + 75 * e.position)  # 100 s and a bit
    image = DiscImage("2026-11-24T10:00:00+00:00", "Disc", 4, 1, 1234, 5678)
    lib.set_disc_image(disc, image)
    return disc, image


def length_of(lib, disc, position):
    return lib.disc_entries(disc)[position].track.cd_sectors


def test_the_survey_names_the_tracks_and_skips_what_dubcrate_has(lib, burned):
    disc, image = burned
    file = m3.Mp3TrackFile.new(entry_id(4, 1234, 5678))
    file.cues = [
        cue(0, 2, 2000, length_of(lib, disc, 1)),  # track 2
        cue(1, 1, 3000, length_of(lib, disc, 2)),  # track 3: first file of folder 1
    ]
    second = lib.disc_entries(disc)[1].track
    lib.set_memory_cues(second.path, [(round((2000 * 1000 / 75) - 51), "mine")])
    found = library_survey(lib, disc, card_for(image, file))
    assert found.unchecked == []
    [finding] = found.findings
    assert (finding.name, finding.path, finding.has_baseline) == ("Disc", "F / Disc", False)  # fmt: skip
    [only] = finding.tracks
    assert (only.number, only.title, only.artist) == (3, "Title 2", "Art")
    assert [(r.kind, r.ms) for r in only.rows] == [(ADD, round(3000 * 1000 / 75) - 51)]


def test_a_disc_the_card_has_no_file_for_is_left_out_quietly(lib, burned):
    disc, _image = burned
    other = m3.Mp3Index([m3.Mp3IndexEntry("MPJ00001", entry_id(9, 1, 2))])
    found = library_survey(lib, disc, (other, {"MPJ00001": m3.Mp3TrackFile.new(entry_id(9, 1, 2))}))  # fmt: skip
    assert found.findings == [] and found.unchecked == []


def test_a_disc_with_no_record_of_its_image_cannot_be_looked_at(lib):
    disc = make_disc(lib, n=2, folders=())
    index = m3.Mp3Index([])
    found = library_survey(lib, disc, (index, {}))
    assert found.findings == [] and found.unchecked == ["Disc"]


def test_a_disc_that_no_longer_matches_its_record_is_not_looked_at(lib, burned):
    disc, image = burned
    file = m3.Mp3TrackFile.new(entry_id(4, 1234, 5678))
    file.cues = [cue(0, 1, 2000, length_of(lib, disc, 0))]
    extra = lib.tracks(sort="path")[0].id
    lib.add_to_disc(disc, [extra])  # the disc has five tracks now, the record says four
    found = library_survey(lib, disc, card_for(image, file))
    assert found.findings == [] and found.unchecked == ["Disc"]


def test_a_track_keeps_room_for_ten_memory_cues(lib, burned):
    disc, image = burned
    length = length_of(lib, disc, 0)
    file = m3.Mp3TrackFile.new(entry_id(4, 1234, 5678))
    file.cues = [cue(0, 1, 1000 + 500 * n, length) for n in range(4)]
    first = lib.disc_entries(disc)[0].track
    lib.set_memory_cues(first.path, [(100000 + 10000 * n, "") for n in range(8)])
    [finding] = library_survey(lib, disc, card_for(image, file)).findings
    [only] = finding.tracks
    assert len(only.rows) == 2 and only.over_limit == 2
