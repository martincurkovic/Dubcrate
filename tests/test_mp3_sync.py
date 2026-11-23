"""Syncing an MP3 disc's waveforms and cues to the card: the plan (on the player's own file), the write, the image's
layout, where a cue lands, and the whole flow on real rendered MP3s."""

import shutil
import subprocess
from pathlib import Path

import pytest

from dubcrate import card_mp3, iso
from dubcrate import cdj_card as cc
from dubcrate import cdj_card_mp3 as m3
from dubcrate import mp3_sync as ms
from dubcrate.analysis import Cue
from dubcrate.library import Library
from dubcrate.library.folder import scan_folder

DATA = Path(__file__).parent / "data" / "cdj_card" / "mp3"
LAST = DATA / "MPJ00001_hot_c_and_delete.PDJ"
NEEDS_FFMPEG = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="no ffmpeg"
)

# the disc on the player's card: 15 files, root 3 and folders A, B, C of 4; the image is 84,029 sectors and its
# last file starts at 80,245 (the three fields of the ID that are understood)
KEYS = [(0, n) for n in (1, 2, 3)] + [(f, n) for f in (1, 2, 3) for n in (1, 2, 3, 4)]
TABLE = m3.FrameTable(
    tuple(135 + 1045 * i for i in range(8000)), 44100
)  # 8000 frames: 15,674 in 1/75 s
WAVE = [1 + (i % 15) for i in range(100)]


def index():
    return m3.Mp3Index.parse((DATA / "MPJINDX0.PDJ").read_bytes())


def card_file():
    return m3.Mp3TrackFile.parse(LAST.read_bytes())


def disc(waves=True, cues=None, sectors=84029, last=80245, count=15):
    cues = cues or {}
    tracks = [
        ms.Mp3TrackInput(
            f,
            n,
            f"T{f}.{n}",
            TABLE,
            list(WAVE) if waves else None,
            cues.get((f, n), []),
        )
        for f, n in KEYS
    ]
    return ms.Mp3DiscInput("The Disc", count, last, sectors, tracks)


def plan(d=None, file=None):
    return ms.plan_mp3_disc(d or disc(), index(), {"MPJ00001": file or card_file()})


# -- finding the disc ------------------------------------------------------------------------------


def test_a_disc_the_player_has_not_seen_has_no_entry_and_is_not_written():
    for wrong in ({"sectors": 84030}, {"last": 80246}, {"count": 14}):
        p = plan(disc(**wrong))
        assert p.status is ms.Mp3Status.NO_ENTRY and not p.changed and p.file is None
        assert "Play the disc in the player once" in p.message


def test_two_entries_that_fit_are_ambiguous():
    twin = index()
    twin.entries.append(m3.Mp3IndexEntry("MPJ00002", twin.entries[0].disc_id))
    p = ms.plan_mp3_disc(disc(), twin, {})
    assert p.status is ms.Mp3Status.AMBIGUOUS and "2 discs" in p.message


# -- waveforms -------------------------------------------------------------------------------------


def test_waveforms_are_added_only_for_tracks_the_card_has_none_for_and_nothing_there_changes():
    before = card_file()
    p = plan()
    assert p.status is ms.Mp3Status.READY and p.changed
    assert (p.waves_added, p.waves_kept, p.waves_pending) == (4, 11, 0)
    new = p.file
    assert [w.pack() for w in new.coarse[: len(before.coarse)]] == [
        w.pack() for w in before.coarse
    ]
    assert [w.pack() for w in new.fine[: len(before.fine)]] == [
        w.pack() for w in before.fine
    ]
    added = [w.key for w in new.coarse[len(before.coarse) :]]
    assert added == [(1, 3), (1, 4), (2, 3), (3, 1)]  # in the disc's order
    assert added == [w.key for w in new.fine[len(before.fine) :]]
    assert new.coarse[-1].columns_played == 50 and new.disc_id == before.disc_id
    assert [c.raw for c in new.cues] == [
        c.raw for c in before.cues
    ] and new.hot == before.hot


def test_a_record_the_player_half_filled_is_kept_not_replaced():
    p = plan()  # (3,3) has 1 of 50 columns on the card
    partial = next(w for w in p.file.coarse if w.key == (3, 3))
    assert partial.columns_played == 1


def test_a_track_not_analysed_yet_is_counted_and_gets_nothing():
    p = plan(disc(waves=False))
    assert (p.waves_added, p.waves_pending, p.waves_kept) == (0, 4, 11)
    assert p.status is ms.Mp3Status.IN_SYNC and not p.changed


def test_planning_again_on_the_merged_file_adds_nothing():
    first = plan()
    again = plan(file=m3.Mp3TrackFile.parse(first.file.to_bytes()))
    assert (
        again.status is ms.Mp3Status.IN_SYNC
        and again.waves_added == 0
        and again.waves_kept == 15
    )


def test_a_disc_with_an_entry_but_no_file_gets_a_new_file_with_the_cards_id():
    p = ms.plan_mp3_disc(disc(), index(), {"MPJ00001": None})
    assert p.status is ms.Mp3Status.READY and not p.had_file and p.waves_added == 15
    assert p.file.disc_id == index().entries[0].disc_id and p.file.cues == []
    m3.Mp3TrackFile.parse(
        p.file.to_bytes()
    )  # (a file the player can read, checksum good)


# -- cues ------------------------------------------------------------------------------------------


def test_cues_are_added_after_the_cards_with_the_next_index_and_the_numbers_of_their_frames():
    wanted = {(0, 2): [(5000, 0)], (1, 3): [(300, 0), (8000, 8200)]}
    p = plan(disc(cues=wanted))
    assert p.cues_added == 3 and p.cues_already == 0
    new = p.file.cues[-3:]
    assert [(c.folder, c.file, c.is_loop) for c in new] == [
        (0, 2, False),
        (1, 3, False),
        (1, 3, True),
    ]
    assert p.file.cue_indexes[-3:] == [
        8,
        9,
        10,
    ]  # one past the highest the player used (7); index 6 was deleted
    frame, byte = TABLE.locate(5000)
    assert (new[0].position, new[0].frame, new[0].byte, new[0].length) == (
        5000,
        frame,
        byte,
        TABLE.length_frames,
    )
    loop = new[2]
    assert (loop.position, loop.end, loop.end_frame, loop.end_byte) == (8000, 8200, TABLE.locate(8200)[0], TABLE.locate(8200)[1])  # fmt: skip
    assert m3.Mp3TrackFile.parse(p.file.to_bytes()).cue_indexes[-3:] == [
        8,
        9,
        10,
    ]  # (and it reads back)


def test_a_cue_the_card_has_is_not_added_again_nor_one_within_two_frames_of_a_cue_or_a_hot_cue():
    wanted = {
        (0, 2): [
            (1474, 0),
            (1476, 0),
            (917, 0),
            (1478, 0),
        ],  # a memory cue at 1474; hot A at 916; 1478 is 4 away
        (0, 3): [
            (3359, 3493),
            (3358, 3496),
            (3358, 3494),
        ],  # the loop at 3358-3493: the first and last are the same
    }
    p = plan(disc(cues=wanted))
    assert p.cues_already == 5  # 1474, 1476, 917 | 3359-3493, 3358-3494
    assert (
        p.cues_added == 2
    )  # 1478 (4 frames from the cue) and the loop 3358-3496 (3 frames different at its end)
    assert [(c.position, c.end) for c in p.file.cues[-2:]] == [(1478, 0), (3358, 3496)]


def test_hot_cues_and_everything_on_the_card_are_left_exactly_as_they_were():
    before = card_file()
    p = plan(disc(cues={(0, 2): [(5000, 0)]}))
    assert p.file.hot == before.hot
    assert [c.raw for c in p.file.cues[: len(before.cues)]] == [
        c.raw for c in before.cues
    ]
    assert p.file.cue_indexes[: len(before.cues)] == before.cue_indexes


def test_a_cue_past_the_end_of_the_track_is_left_out_and_said():
    p = plan(
        disc(
            cues={
                (0, 2): [(TABLE.length_frames + 5, 0), (100, TABLE.length_frames + 9)]
            }
        )
    )
    assert p.cues_added == 0 and len(p.notes) == 2 and "past the end" in p.notes[0]


def test_a_disc_holds_a_hundred_cues_and_loops_and_the_rest_are_counted():
    full = m3.Mp3TrackFile.new(index().entries[0].disc_id)
    full.cues = [m3.Mp3Entry.cue(TABLE, 0, 1, 10 * i + 1000) for i in range(99)]
    full.cue_indexes = list(range(99))
    p = ms.plan_mp3_disc(
        disc(cues={(0, 2): [(5, 0), (50, 0), (500, 0)]}), index(), {"MPJ00001": full}
    )
    assert (p.cues_added, p.cues_overflow) == (1, 2) and len(p.file.cues) == 100


def test_the_plan_in_words():
    text = "\n".join(
        ms.describe_plan(plan(disc(cues={(0, 2): [(5000, 0), (1474, 0)]})), "The Disc")
    )
    assert (
        "to add to MPJ00001.PDJ: 4 waveforms, 1 cue" in text
        and "11 waveforms already there stay" in text
    )
    assert "1 cue already there" in text
    assert "play the disc" in "\n".join(ms.describe_plan(plan(disc(sectors=1)))).lower()


# -- writing ---------------------------------------------------------------------------------------


def make_card(tmp_path):
    mpj = tmp_path / "card" / "PIONEER" / "MPJ"
    mpj.mkdir(parents=True)
    shutil.copy(DATA / "MPJINDX0.PDJ", mpj / "MPJINDX0.PDJ")
    shutil.copy(LAST, mpj / "MPJ00001.PDJ")
    return tmp_path / "card", mpj


def test_the_write_backs_the_files_up_writes_one_file_and_reads_it_back(tmp_path):
    card, mpj = make_card(tmp_path)
    p = ms.plan_mp3_disc(*[disc(cues={(0, 2): [(5000, 0)]}), *m3.read_mp3_card(card)])
    out = ms.apply_mp3_plan(card, p, tmp_path / "backups")
    assert out.wrote and (out.waveforms_written, out.cues_written, out.name) == (
        4,
        1,
        "MPJ00001",
    )
    assert (
        out.backup / "MPJ00001.PDJ"
    ).read_bytes() == LAST.read_bytes()  # the old file is kept
    assert (out.backup / "MPJINDX0.PDJ").read_bytes() == (
        DATA / "MPJINDX0.PDJ"
    ).read_bytes()
    assert (mpj / "MPJINDX0.PDJ").read_bytes() == (
        DATA / "MPJINDX0.PDJ"
    ).read_bytes()  # the index is not touched
    written = m3.Mp3TrackFile.parse((mpj / "MPJ00001.PDJ").read_bytes())
    assert len(written.coarse) == 15 and len(written.cues) == 8
    assert not list(mpj.glob("*.TMP"))
    # and a second sync of the same disc finds nothing to do
    again = ms.plan_mp3_disc(disc(cues={(0, 2): [(5000, 0)]}), *m3.read_mp3_card(card))
    assert again.status is ms.Mp3Status.IN_SYNC
    assert not ms.apply_mp3_plan(card, again, tmp_path / "backups").wrote


def test_nothing_is_written_when_there_is_no_entry_or_nothing_to_add(tmp_path):
    card, mpj = make_card(tmp_path)
    before = (mpj / "MPJ00001.PDJ").read_bytes()
    none = ms.plan_mp3_disc(disc(sectors=5), *m3.read_mp3_card(card))
    assert not ms.apply_mp3_plan(card, none, tmp_path / "b").wrote
    assert (mpj / "MPJ00001.PDJ").read_bytes() == before and not (
        tmp_path / "b"
    ).exists()


def test_an_entry_with_no_file_yet_is_given_one(tmp_path):
    card, mpj = make_card(tmp_path)
    (mpj / "MPJ00001.PDJ").unlink()  # the player made the entry, not the file
    p = ms.plan_mp3_disc(disc(), *m3.read_mp3_card(card))
    out = ms.apply_mp3_plan(card, p, tmp_path / "b")
    assert out.wrote and m3.Mp3TrackFile.parse(
        (mpj / "MPJ00001.PDJ").read_bytes()
    ).coarse[0].key == (0, 1)


# -- the image's layout ----------------------------------------------------------------------------


def stage(tmp_path, nested=False):
    root = tmp_path / "Disc"
    for folder, names in {
        "": ["b.mp3", "a.mp3"],
        "Folder B": ["x.mp3"],
        "Folder A": ["y.mp3", "z.mp3"],
    }.items():
        (root / folder).mkdir(parents=True, exist_ok=True)
        for n, name in enumerate(names):
            (root / folder / name).write_bytes(bytes([n + 1]) * (5000 + 3000 * n))
    if nested:
        (root / "Folder A" / "Deeper").mkdir()
        (root / "Folder A" / "Deeper" / "q.mp3").write_bytes(b"q" * 100)
    return root


def test_the_layout_numbers_files_and_folders_as_the_player_does_and_reads_the_extents(
    tmp_path,
):
    root = stage(tmp_path)
    image = iso.build_iso(root, "Disc", tmp_path / "disc.iso")
    layout = iso.image_layout(image, root)
    assert layout.file_count == 5 and layout.sectors == image.stat().st_size // 2048
    assert [(f.folder, f.file, f.path.name) for f in layout.files] == [
        (0, 1, "a.mp3"), (0, 2, "b.mp3"),  # (the root's files by name)
        (1, 1, "y.mp3"), (1, 2, "z.mp3"),  # (Folder A is folder 1: folders are numbered in the order of their names)
        (2, 1, "x.mp3"),
    ]  # fmt: skip
    assert layout.last_file_start == max(f.extent for f in layout.files)
    assert [f.size for f in layout.files] == [
        8000,
        5000,
        5000,
        8000,
        5000,
    ]  # (a.mp3 was staged the bigger)
    # the extents are real: the bytes at them are the files' (the image is read the way the CDJ would)
    raw = image.read_bytes()
    for f in layout.files:
        assert raw[f.extent * 2048 : f.extent * 2048 + 3] == f.path.read_bytes()[:3]


def test_a_folder_inside_a_folder_is_refused_because_the_numbering_is_not_known(
    tmp_path,
):
    root = stage(tmp_path, nested=True)
    image = iso.build_iso(root, "Disc", tmp_path / "disc.iso")
    with pytest.raises(iso.IsoError, match="Folders inside folders"):
        iso.image_layout(image, root)


# -- where a cue lands -----------------------------------------------------------------------------


def test_cues_become_positions_in_the_players_time_with_the_info_frame_added():
    cues = [
        Cue("memory", 10.0),
        Cue("hot", 20.0, slot=1),  # a hot cue becomes a memory cue
        Cue("loop", 30.0, 32.0),
        Cue("memory", 10.01),  # within two frames of the first: the same cue
        Cue("memory", 9999.0),  # past the end
    ]
    got, notes = card_mp3.positions(cues, 0.026, length_frames=15674)
    # (10.026 s x 75 = 751.95 -> 751: rounded DOWN to its frame, as the audio sync does)
    assert got == [(751, 0), (1501, 0), (2251, 2401)]
    assert len(notes) == 1 and "past the end" in notes[0]


# -- the whole flow, on real rendered MP3s ---------------------------------------------------------


def tone(path: Path, seconds: float, **tags):
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = [a for k, v in tags.items() for a in ("-metadata", f"{k}={v}")]
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-v", "error", "-f", "lavfi", "-i", f"sine=duration={seconds}",
         "-c:a", "libmp3lame", "-b:a", "128k", "-ar", "44100", *meta, str(path)],
        check=True,
    )  # fmt: skip
    return path


class FakeCues:
    """Stands in for `RekordboxCues`: a fixed list of cues in audio time per track title."""

    def __init__(self, by_title):
        self.by_title = by_title

    def for_track(self, track):
        return self.by_title.get(track.title, [])


@NEEDS_FFMPEG
def test_a_real_disc_is_rendered_laid_out_matched_to_the_card_and_written(tmp_path):
    music = tmp_path / "music"
    for i, (title, seconds) in enumerate(
        [("One", 6), ("Two", 7), ("Three", 8), ("Four", 9)]
    ):
        tone(music / f"{title}.mp3", seconds, title=title, artist="Me")
    scan = scan_folder(music)
    lib = Library.open(":memory:")
    lib.replace_source("folder", str(music), "music", scan.tracks, scan.nodes)
    ids = {t.title: t.id for t in lib.tracks()}
    disc_id = lib.create_disc("Test Disc", kind="mp3")
    folder = lib.create_disc_dir(disc_id, "Folder A")
    lib.add_to_disc(disc_id, [ids["One"], ids["Two"]])
    lib.add_to_disc(disc_id, [ids["Three"], ids["Four"]], dir_id=folder)

    request = card_mp3.collect_request(
        lib,
        disc_id,
        FakeCues(
            {
                "Two": [Cue("memory", 2.0), Cue("loop", 3.0, 4.0)],
                "Four": [Cue("hot", 5.0, slot=2)],
            }
        ),
    )
    work = tmp_path / "work"
    work.mkdir()
    prepared = card_mp3.prepare_disc(request, work, analyse=lambda path: list(WAVE))
    d = prepared.disc
    assert (d.file_count, [(t.folder, t.file) for t in d.tracks]) == (
        4,
        [(0, 1), (0, 2), (1, 1), (1, 2)],
    )
    staged = next(
        pt for pt in work.iterdir() if pt.is_dir()
    )  # (the folder the disc was rendered into)
    again = iso.image_layout(work / "disc.iso", staged)
    assert (d.file_count, d.last_file_start, d.image_sectors) == (again.file_count, again.last_file_start, again.sectors)  # fmt: skip
    assert set(prepared.analysed) == set(
        ids.values()
    )  # nothing was cached: all four were analysed from the rendered files
    two = d.tracks[1]
    assert (
        two.cues[0][0] > 150 and two.cues[1][1] > two.cues[1][0]
    )  # a cue and a loop, in 1/75 s frames
    assert d.tracks[3].cues[0][0] > 375  # the hot cue at 5 s became a memory cue

    # a card whose entry has exactly this image's three numbers (the rest of the ID is the player's own)
    raw_id = bytes([5, 0x2C, 6, d.file_count, 0x33, 0x87, 0x45, 0xE7]) + d.last_file_start.to_bytes(3, "big") + d.image_sectors.to_bytes(3, "big") + b"\0\0"  # fmt: skip
    card = tmp_path / "card"
    (card / "PIONEER" / "MPJ").mkdir(parents=True)
    (card / "PIONEER" / "MPJ" / "MPJINDX0.PDJ").write_bytes(
        m3.Mp3Index([m3.Mp3IndexEntry("MPJ00001", m3.Mp3DiscId(raw_id))]).to_bytes()
    )
    index_, files = m3.read_mp3_card(card)
    p = ms.plan_mp3_disc(d, index_, files)
    assert p.status is ms.Mp3Status.READY and (p.waves_added, p.cues_added) == (4, 3)
    out = ms.apply_mp3_plan(card, p, tmp_path / "backups")
    written = m3.read_mp3_card(card)[1]["MPJ00001"]
    assert out.wrote and [w.key for w in written.coarse] == [
        (0, 1),
        (0, 2),
        (1, 1),
        (1, 2),
    ]
    assert [(c.folder, c.file, c.is_loop) for c in written.cues] == [
        (0, 2, False),
        (0, 2, True),
        (1, 2, False),
    ]
    # the byte offsets written are the rendered files' real frame offsets
    rendered = {pt.name: pt for pt in work.rglob("*.mp3")}
    table = m3.frame_table(next(pt for name, pt in rendered.items() if "Two" in name))
    cue = written.cues[0]
    assert (cue.frame, cue.byte) == table.locate(cue.position)
    lib.close()


@NEEDS_FFMPEG
def test_a_disc_that_is_not_an_mp3_disc_or_has_a_folder_in_a_folder_is_refused_in_words(
    tmp_path,
):
    music = tmp_path / "music"
    tone(music / "A.mp3", 3, title="A")
    scan = scan_folder(music)
    lib = Library.open(":memory:")
    lib.replace_source("folder", str(music), "music", scan.tracks, scan.nodes)
    track = lib.tracks()[0].id
    audio = lib.create_disc("Audio")
    lib.add_to_disc(audio, [track])
    with pytest.raises(card_mp3.Mp3SyncError, match="audio disc"):
        card_mp3.collect_request(lib, audio, FakeCues({}))
    deep = lib.create_disc("Deep", kind="mp3")
    outer = lib.create_disc_dir(deep, "Outer")
    lib.create_disc_dir(deep, "Inner", parent_id=outer)
    lib.add_to_disc(deep, [track], dir_id=outer)
    with pytest.raises(card_mp3.Mp3SyncError, match="Folders inside folders"):
        card_mp3.collect_request(lib, deep, FakeCues({}))
    lib.close()


def test_the_constants_agree_with_the_card_module():
    assert ms.DUPLICATE_FRAMES == 2 and cc.MAX_CUES == 100
