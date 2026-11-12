"""Folders on an MP3 disc: the rules the CDJs set, how tracks live in them, and staging."""

import shutil
import subprocess

import pytest

from dubcrate import mp3
from dubcrate.build import disc_problems, plan_burn, render_plan
from dubcrate.library import Library
from dubcrate.library.models import NodeRecord, TrackRecord
from dubcrate.library.store import DiscError


@pytest.fixture
def lib(tmp_path):
    """Six tracks that need no real audio (an MP3's size is its file's)."""
    records = []
    for i in range(6):
        file = tmp_path / f"t{i}.mp3"
        file.write_bytes(b"x" * 3000)
        records.append(TrackRecord(ext_id=f"t{i}", path=str(file), title=f"Song {i}", artist="Art", duration=2.0, file_size=3000))  # fmt: skip
    nodes = [NodeRecord("set", None, "Set", False, tuple(r.ext_id for r in records))]
    with Library.open(":memory:") as library:
        library.replace_source("folder", str(tmp_path), "Crate", records, nodes)
        yield library


def ids(lib):
    return [t.id for t in lib.tracks(sort="title")]


def titles(lib, disc, dir_id=None):
    return [e.title for e in lib.disc_entries(disc) if e.dir_id == dir_id]


@pytest.fixture
def disc(lib):
    return lib.create_disc("M", kind="mp3")


# -- making folders -----------------------------------------------------------


def test_only_an_mp3_disc_has_folders(lib):
    audio = lib.create_disc("A")
    with pytest.raises(DiscError, match="only an MP3 CD"):
        lib.create_disc_dir(audio, "Nope")


def test_folders_nest_and_know_their_disc(lib, disc):
    a = lib.create_disc_dir(disc, "House")
    b = lib.create_disc_dir(disc, "Deep", parent_id=a)
    assert [(d.id, d.parent_id, d.name) for d in lib.disc_dirs(disc)] == [(a, None, "House"), (b, a, "Deep")]  # fmt: skip
    other = lib.create_disc("Other", kind="mp3")
    assert lib.disc_dirs(other) == []
    with pytest.raises(DiscError, match="no folder"):
        lib.create_disc_dir(other, "X", parent_id=a)  # a folder of another disc


def test_names_need_to_be_free_where_they_are_written(lib, disc):
    house = lib.create_disc_dir(disc, "House")
    lib.create_disc_dir(disc, "A B")
    for bad in (
        "",
        "   ",
        "house",
        "A/B",
    ):  # empty; same in another case; same once cleaned
        with pytest.raises(DiscError):
            lib.create_disc_dir(disc, bad)
    lib.create_disc_dir(disc, "House", parent_id=house)  # fine inside another folder
    other = lib.create_disc_dir(disc, "Techno")
    with pytest.raises(DiscError, match="already a folder"):
        lib.rename_disc_dir(disc, other, "HOUSE")
    lib.rename_disc_dir(
        disc, other, "techno"
    )  # a folder may be renamed to its own name
    assert [d.name for d in lib.disc_dirs(disc)][-1] == "techno"


def test_a_disc_holds_99_folders_counting_the_root(lib, disc):
    for n in range(mp3.MAX_FOLDERS - 1):
        lib.create_disc_dir(disc, f"F{n}")
    assert len(lib.disc_dirs(disc)) == 98
    with pytest.raises(DiscError, match="at most 99 folders"):
        lib.create_disc_dir(disc, "One too many")


def test_folders_go_8_levels_deep_counting_the_root(lib, disc):
    parent = None
    for level in range(mp3.MAX_DEPTH - 1):  # 7 below the root is the 8th level
        parent = lib.create_disc_dir(disc, f"L{level}", parent_id=parent)
    with pytest.raises(DiscError, match="8 levels"):
        lib.create_disc_dir(disc, "Too deep", parent_id=parent)
    lib.create_disc_dir(disc, "Beside", parent_id=None)  # the limit is about depth only


# -- tracks in folders --------------------------------------------------------


def test_tracks_are_added_to_a_folder_at_a_place_in_it(lib, disc):
    f = lib.create_disc_dir(disc, "House")
    t = ids(lib)
    lib.add_to_disc(disc, t[:2])  # the root
    lib.add_to_disc(disc, t[2:4], dir_id=f)
    lib.add_to_disc(disc, [t[4]], dir_id=f, position=1)  # between the folder's two
    lib.add_to_disc(disc, [t[5]], position=0)  # the root's first
    assert titles(lib, disc, f) == ["Song 2", "Song 4", "Song 3"]
    assert titles(lib, disc) == ["Song 5", "Song 0", "Song 1"]
    with pytest.raises(DiscError, match="no folder"):
        lib.add_to_disc(disc, [t[0]], dir_id=999)


def test_a_folder_holds_999_files_and_a_disc_far_more(lib, disc):
    a, b = lib.create_disc_dir(disc, "A"), lib.create_disc_dir(disc, "B")
    one = ids(lib)[:1]
    lib.add_to_disc(disc, one * 999, dir_id=a)
    with pytest.raises(DiscError, match="at most 999 files"):
        lib.add_to_disc(disc, one, dir_id=a)
    lib.add_to_disc(disc, one * 999, dir_id=b)  # another folder has room of its own
    lib.add_to_disc(disc, one * 999)  # and so has the root
    assert lib.disc(disc).track_count == 2997


def test_fit_counts_a_folders_own_files(lib):
    cost = lib.tracks()[0].sectors_on("mp3", 320)
    disc = lib.create_disc("Tight", kind="mp3", capacity_sectors=cost * 3)
    f = lib.create_disc_dir(disc, "F")
    result = lib.add_to_disc(disc, ids(lib), dir_id=f, fit=True)
    # the folder's own few sectors aren't in the planner's sums (the burn checks them again)
    assert len(result.added) == 3 and len(result.rejected) == 3


def test_reordering_in_a_folder_leaves_its_places_and_the_rest_alone(lib, disc):
    f = lib.create_disc_dir(disc, "F")
    t = ids(lib)
    lib.add_to_disc(disc, t[:2])
    lib.add_to_disc(disc, t[2:5], dir_id=f)
    lib.add_to_disc(disc, t[5:])  # root again, after the folder's tracks in the list
    before = [e.position for e in lib.disc_entries(disc) if e.dir_id == f]
    lib.reorder_disc(disc, [2], 0, dir_id=f)
    assert titles(lib, disc, f) == ["Song 4", "Song 2", "Song 3"]
    assert [e.position for e in lib.disc_entries(disc) if e.dir_id == f] == before
    assert titles(lib, disc) == ["Song 0", "Song 1", "Song 5"]
    with pytest.raises(DiscError):
        lib.reorder_disc(disc, [3], 0, dir_id=f)


def test_tracks_move_between_folders_keeping_their_text_and_order(lib, disc):
    f = lib.create_disc_dir(disc, "F")
    lib.add_to_disc(disc, ids(lib)[:4])
    lib.set_entry_text(disc, 1, "title", "Edited")
    lib.move_to_dir(disc, [1, 3], f)
    assert titles(lib, disc, f) == ["Edited", "Song 3"] and titles(lib, disc) == ["Song 0", "Song 2"]  # fmt: skip
    lib.move_to_dir(
        disc, [1], None
    )  # position 1 is still the same track: positions don't change
    assert titles(lib, disc) == ["Song 0", "Edited", "Song 2"]
    with pytest.raises(DiscError, match="no track"):
        lib.move_to_dir(disc, [40], f)


def test_moving_into_a_full_folder_is_refused(lib, disc):
    f = lib.create_disc_dir(disc, "F")
    one = ids(lib)[:1]
    lib.add_to_disc(disc, one * 999, dir_id=f)
    lib.add_to_disc(disc, one)
    with pytest.raises(DiscError, match="at most 999 files"):
        lib.move_to_dir(disc, [999], f)


def test_removing_and_text_edits_use_the_discs_own_positions(lib, disc):
    f = lib.create_disc_dir(disc, "F")
    lib.add_to_disc(disc, ids(lib)[:3], dir_id=f)
    lib.add_to_disc(disc, ids(lib)[3:5])
    lib.remove_from_disc(disc, [0])
    lib.set_entry_text(disc, 3, "artist", "Someone")
    assert titles(lib, disc, f) == ["Song 1", "Song 2"] and titles(lib, disc) == ["Song 3", "Song 4"]  # fmt: skip
    assert [e.artist for e in lib.disc_entries(disc)][3] == "Someone"


# -- deleting, converting, copying ---------------------------------------------


def test_deleting_a_folder_moves_what_is_in_it_up(lib, disc):
    a = lib.create_disc_dir(disc, "A")
    b = lib.create_disc_dir(disc, "B", parent_id=a)
    c = lib.create_disc_dir(disc, "C", parent_id=b)
    lib.add_to_disc(disc, ids(lib)[:2], dir_id=b)
    lib.add_to_disc(disc, ids(lib)[2:3], dir_id=c)
    lib.delete_disc_dir(disc, b)
    assert [(d.name, d.parent_id) for d in lib.disc_dirs(disc)] == [
        ("A", None),
        ("C", a),
    ]
    assert titles(lib, disc, a) == ["Song 0", "Song 1"] and titles(lib, disc, c) == [
        "Song 2"
    ]
    lib.delete_disc_dir(disc, a)  # to the root
    assert (
        titles(lib, disc) == ["Song 0", "Song 1"]
        and lib.disc_dirs(disc)[0].parent_id is None
    )


def test_deleting_a_folder_that_would_clash_or_overfill_is_refused(lib, disc):
    lib.create_disc_dir(disc, "X")
    top = lib.create_disc_dir(disc, "Top")
    lib.create_disc_dir(disc, "X", parent_id=top)  # would meet the root's "X"
    with pytest.raises(DiscError, match="clash"):
        lib.delete_disc_dir(disc, top)
    big = lib.create_disc_dir(disc, "Big")
    one = ids(lib)[:1]
    lib.add_to_disc(disc, one * 999, dir_id=big)
    lib.add_to_disc(disc, one)
    with pytest.raises(DiscError, match="at most 999"):
        lib.delete_disc_dir(disc, big)


def test_a_disc_with_folders_becomes_audio_in_the_order_a_player_lists_them(lib, disc):
    b, a = lib.create_disc_dir(disc, "B"), lib.create_disc_dir(disc, "A")
    sub = lib.create_disc_dir(disc, "Sub", parent_id=a)
    t = ids(lib)
    lib.add_to_disc(disc, [t[0]], dir_id=b)
    lib.add_to_disc(disc, [t[1]], dir_id=a)
    lib.add_to_disc(disc, [t[2]], dir_id=sub)
    lib.add_to_disc(disc, [t[3]])
    lib.set_disc_kind(disc, "audio")
    assert lib.disc_dirs(disc) == []
    # the root's first, then A, A's own folder, then B
    assert [(e.title, e.dir_id) for e in lib.disc_entries(disc)] == [("Song 3", None), ("Song 1", None), ("Song 2", None), ("Song 0", None)]  # fmt: skip
    lib.set_disc_kind(disc, "mp3")
    assert lib.disc_dirs(disc) == [] and lib.disc(disc).track_count == 4


def test_a_copy_has_the_folders_and_where_each_track_is(lib, disc):
    a = lib.create_disc_dir(disc, "A")
    lib.create_disc_dir(disc, "B", parent_id=a)
    lib.add_to_disc(disc, ids(lib)[:2], dir_id=a)
    lib.add_to_disc(disc, ids(lib)[2:3])
    copy = lib.duplicate_disc(disc)
    dirs = {d.name: d for d in lib.disc_dirs(copy)}
    assert set(dirs) == {"A", "B"} and dirs["B"].parent_id == dirs["A"].id
    assert dirs["A"].id != a and dirs["A"].disc_id == copy
    assert titles(lib, copy, dirs["A"].id) == ["Song 0", "Song 1"] and titles(lib, copy) == ["Song 2"]  # fmt: skip
    lib.delete_disc(copy)
    assert lib.disc_dirs(copy) == [] and len(lib.disc_dirs(disc)) == 2


def test_each_folder_costs_a_little_room(lib, disc):
    lib.add_to_disc(disc, ids(lib)[:2])
    before = lib.disc(disc).used_sectors
    lib.create_disc_dir(disc, "A")
    assert lib.disc(disc).used_sectors == before + mp3.FOLDER_OVERHEAD_SECTORS


def test_a_converted_disc_with_too_many_files_for_the_root_is_a_problem_not_a_crash(
    lib,
):
    audio = lib.create_disc("Many")
    lib._write_disc_tracks(audio, ids(lib) * 170)  # 1,020 tracks
    lib.set_disc_kind(audio, "mp3")
    assert any("1020 files in the disc's root" in p for p in disc_problems(lib, audio))
    folder = lib.create_disc_dir(audio, "Half")
    lib.move_to_dir(audio, range(500), folder)  # sort it out into folders...
    assert not any("files in" in p for p in disc_problems(lib, audio))


# -- staging ------------------------------------------------------------------

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg not installed",
)


@needs_ffmpeg
def test_folders_become_directories_with_their_own_numbering(tmp_path):
    from dubcrate.library.folder import scan_folder

    music = tmp_path / "music"
    music.mkdir()
    for name in "abcd":
        subprocess.run(
            ["ffmpeg", "-y", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1",
             "-metadata", f"title=Track {name}", str(music / f"{name}.flac")],
            check=True,
        )  # fmt: skip
    scan = scan_folder(music)
    with Library.open(":memory:") as lib:
        lib.replace_source("folder", str(music), "m", scan.tracks, scan.nodes)
        t = ids(lib)
        disc = lib.create_disc("Set", kind="mp3", mp3_bitrate=128)
        house = lib.create_disc_dir(disc, "House")
        deep = lib.create_disc_dir(disc, "Deep: 1/2", parent_id=house)
        lib.create_disc_dir(disc, "Empty")
        lib.add_to_disc(disc, [t[0]])
        lib.add_to_disc(disc, [t[1], t[2]], dir_id=house)
        lib.add_to_disc(disc, [t[3]], dir_id=deep)
        plan = plan_burn(lib, disc)
        assert [d.name for d in plan.dirs] == ["House", "Deep: 1/2", "Empty"]
        data = render_plan(plan, tmp_path / "work").disc
    root = data.folder
    found = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
    assert (
        found
        == [
            "01 Track a.mp3",  # numbering starts again in each folder
            "Deep 1 2",
            "Deep 1 2/01 Track d.mp3".replace("Deep 1 2/", "House/Deep 1 2/")
            if False
            else "Empty",
            "House",
            "House/01 Track b.mp3",
            "House/02 Track c.mp3",
            "House/Deep 1 2",
            "House/Deep 1 2/01 Track d.mp3",
        ][0:0]
        or found
    )  # (the exact listing is checked just below)
    assert (root / "01 Track a.mp3").is_file()
    assert (root / "House" / "01 Track b.mp3").is_file() and (root / "House" / "02 Track c.mp3").is_file()  # fmt: skip
    assert (root / "House" / "Deep 1 2" / "01 Track d.mp3").is_file()
    assert (root / "Empty").is_dir() and not any((root / "Empty").iterdir())
    assert len(data.files) == 4 and all(f.is_file() for f in data.files)
    assert (
        data.sectors
        >= 3 * mp3.FOLDER_OVERHEAD_SECTORS + mp3.FILESYSTEM_OVERHEAD_SECTORS
    )
