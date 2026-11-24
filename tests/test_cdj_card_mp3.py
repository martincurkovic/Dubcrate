"""The MP3 disc card files (`cdj_card_mp3`): the player's own files read and written back byte for byte, and
the player's own entries built from their numbers. The files are in `tests/data/cdj_card/mp3/`: the states of
ONE disc's `MPJ00001.PDJ` as a CDJ-1000MK3 grew it (2026-11-23), and its index."""

import shutil
import subprocess
from pathlib import Path

import pytest

from dubcrate import cdj_card as cc
from dubcrate import cdj_card_mp3 as m3

DATA = Path(__file__).parent / "data" / "cdj_card" / "mp3"
STATES = sorted(DATA.glob("MPJ00001*.PDJ"))
LAST = DATA / "MPJ00001_hot_c_and_delete.PDJ"


def test_there_are_the_states_the_notes_describe():
    assert len(STATES) >= 8 and (DATA / "MPJINDX0.PDJ").is_file()


@pytest.mark.parametrize("path", STATES, ids=lambda p: p.stem)
def test_every_file_the_player_wrote_is_read_and_written_back_byte_for_byte(path):
    raw = path.read_bytes()
    parsed = m3.Mp3TrackFile.parse(raw)  # (the checksum is verified)
    assert parsed.to_bytes() == raw


def test_the_index_is_read_and_written_back_and_finds_its_disc_by_three_fields():
    raw = (DATA / "MPJINDX0.PDJ").read_bytes()
    index = m3.Mp3Index.parse(raw)
    assert index.to_bytes() == raw
    (entry,) = index.entries
    assert entry.name == "MPJ00001" and entry.file_name == "MPJ00001.PDJ"
    # the disc: 15 files, the last starting at sector 80,245, an image of 84,029 sectors
    assert (entry.disc_id.file_count, entry.disc_id.last_file_start, entry.disc_id.image_sectors) == (15, 80245, 84029)  # fmt: skip
    assert index.find(15, 80245, 84029) == [entry]
    assert index.find(15, 80245, 84030) == [] and index.find(14, 80245, 84029) == []


def test_an_index_for_audio_discs_is_refused():
    audio = (
        Path(__file__).parent / "data" / "cdj_card" / "index_one_disc.PDJ"
    ).read_bytes()
    with pytest.raises(m3.Mp3CardError, match="another kind of disc"):
        m3.Mp3Index.parse(audio)


def test_the_last_state_reads_as_the_owner_set_it():
    f = m3.Mp3TrackFile.parse(LAST.read_bytes())
    assert f.disc_id.raw == (DATA / "MPJINDX0.PDJ").read_bytes()[0x20:0x30]
    # seven memory cues and loops, in the order set; the one with index 6 was deleted and nothing was renumbered
    assert f.cue_indexes == [0, 1, 2, 3, 4, 5, 7] and f.next_cue_index() == 8
    cue = f.cues[0]
    assert (cue.kind, cue.folder, cue.file, cue.position, cue.length) == (
        m3.KIND_CUE,
        0,
        2,
        1474,
        15304,
    )
    assert (cue.frame, cue.byte) == (752, 785897) and not cue.is_loop
    loop = f.cues[1]
    assert loop.is_loop and (loop.folder, loop.file) == (0, 3)
    assert (loop.position, loop.end) == (3358, 3493) and (
        loop.frame,
        loop.end_frame,
    ) == (1713, 1782)
    assert (loop.byte, loop.end_byte) == (
        1790088,
        1862186,
    )  # a loop's end is the start of its end frame
    # hot cues A, B and C: root file 2, folder B file 1, folder C file 3
    a, b, c = f.hot
    assert [(h.folder, h.file, h.position) for h in (a, b, c)] == [
        (0, 2, 916),
        (2, 1, 919),
        (3, 3, 443),
    ]
    assert (c.frame, c.byte, c.length) == (226, 236298, 20731)


def test_a_cue_set_after_a_loop_keeps_that_loops_leftovers_and_is_written_back_unchanged():
    f = m3.Mp3TrackFile.parse(LAST.read_bytes())
    dracula = f.cues[
        3
    ]  # a memory cue (folder B file 4), set after the loop on Rather Be
    assert not dracula.is_loop and (dracula.folder, dracula.file, dracula.position) == (
        2,
        4,
        792,
    )
    assert (
        dracula.end == 1362
    )  # the loop's, not meaningful for a cue: a reader must use is_loop
    assert (
        dracula.pack(hot=False, number=3) == dracula.raw
    )  # ... and it is written back as it was


def test_the_waveform_records_are_keyed_by_folder_and_file_in_the_order_first_played():
    f = m3.Mp3TrackFile.parse(LAST.read_bytes())
    keys = [r.key for r in f.coarse]
    assert keys == [r.key for r in f.fine]
    assert keys == [
        (0, 1),
        (0, 2),
        (1, 1),
        (1, 2),
        (2, 1),
        (2, 2),
        (0, 3),
        (3, 4),
        (2, 4),
        (3, 2),
        (3, 3),
    ]
    played = {r.key: r.columns_played for r in f.coarse}
    assert played[(1, 1)] == 50 and played[(3, 4)] == 35 and played[(2, 2)] == 0


def test_a_deleted_loop_is_just_gone():
    before = m3.Mp3TrackFile.parse(LAST.read_bytes())
    after = m3.Mp3TrackFile.parse((DATA / "MPJ00001_loop_deleted.PDJ").read_bytes())
    assert len(before.cues) == 7 and len(after.cues) == 6
    assert after.cue_indexes == [0, 1, 2, 3, 4, 5]
    assert [c.raw for c in after.cues] == [
        c.raw for c in before.cues[:6]
    ]  # the rest untouched
    assert after.hot == before.hot


# -- building the player's own entries from their numbers -----------------------------------------


def entry(kind, folder, file, position, end, length, frame, end_frame, byte, end_byte):
    return m3.Mp3Entry(
        kind, folder, file, position, end, length, frame, end_frame, byte, end_byte
    )


def differing(a: bytes, b: bytes) -> list[int]:
    return [i for i in range(len(a)) if a[i] != b[i]]


def test_a_memory_cue_hot_cue_and_loop_are_built_as_the_player_wrote_them():
    # the player's: CN11 entry 0 (a memory cue), hot slot A, and CN11 entry 1 (a loop), all byte for byte
    f = m3.Mp3TrackFile.parse((DATA / "MPJ00001_with_cues.PDJ").read_bytes())
    cue = entry(m3.KIND_CUE, 0, 2, 1474, 0, 15304, 752, 0, 785897, 0)
    assert cue.pack(hot=False, number=0) == f.cues[0].raw
    hot_a = entry(m3.KIND_CUE, 0, 2, 916, 0, 15304, 467, 0, 488101, 0)
    assert hot_a.pack(hot=True, number=1) == f.hot[0].raw
    loop = entry(m3.KIND_LOOP, 0, 3, 3358, 3493, 24721, 1713, 1782, 1790088, 1862186)
    assert loop.pack(hot=False, number=1) == f.cues[1].raw


def test_the_other_hot_cues_and_folders_differ_from_ours_only_in_the_four_bytes_nobody_understands():
    f = m3.Mp3TrackFile.parse(LAST.read_bytes())
    hot_b = entry(
        m3.KIND_CUE, 2, 1, 919, 0, 16932, 469, 0, 490216, 0
    )  # folder B file 1
    hot_c = entry(
        m3.KIND_CUE, 3, 3, 443, 0, 20731, 226, 0, 236298, 0
    )  # folder C file 3
    for slot, built, real in ((2, hot_b, f.hot[1]), (3, hot_c, f.hot[2])):
        # (the tail was 0xffffffff on hot B and 0x007effff on hot C: not understood)
        assert set(differing(built.pack(hot=True, number=slot), real.raw)) <= set(
            range(112, 116)
        )
    folder_a_loop = f.cues[5]  # folder A's loop (index 5)
    built = entry(m3.KIND_LOOP, 1, 1, 822, 995, 12181, 419, 507, 437974, 529925)
    assert built.pack(hot=False, number=5) == folder_a_loop.raw


def test_the_numbers_of_a_built_entry_read_back():
    built = entry(m3.KIND_LOOP, 3, 1, 1526, 1645, 30127, 778, 839, 813064, 876803)
    again = m3.Mp3Entry.parse(built.pack(hot=False, number=7), hot=False)
    assert (again.kind, again.folder, again.file, again.position, again.end) == (
        m3.KIND_LOOP,
        3,
        1,
        1526,
        1645,
    )
    assert (again.length, again.frame, again.end_frame, again.byte, again.end_byte) == (30127, 778, 839, 813064, 876803)  # fmt: skip


# -- a file made here -----------------------------------------------------------------------------


def test_a_file_made_here_is_read_back_with_a_good_checksum_and_continues_the_index_numbers():
    disc_id = m3.Mp3Index.parse((DATA / "MPJINDX0.PDJ").read_bytes()).entries[0].disc_id
    fresh = m3.Mp3TrackFile.new(disc_id)
    assert m3.Mp3TrackFile.parse(fresh.to_bytes()).cues == []
    fresh.cues = [entry(m3.KIND_CUE, 0, 2, 1474, 0, 15304, 752, 0, 785897, 0)]
    fresh.cue_indexes = [4]
    fresh.hot[1] = entry(m3.KIND_CUE, 2, 1, 919, 0, 16932, 469, 0, 490216, 0)
    wave = bytes(range(50))
    fresh.coarse = [m3.Mp3Wave(0, 2, wave)]
    fresh.fine = [m3.Mp3Wave(0, 2, wave[::-1])]
    raw = fresh.to_bytes()
    again = m3.Mp3TrackFile.parse(raw)  # (verified)
    assert again.cue_indexes == [4] and again.next_cue_index() == 5
    assert (
        again.hot[0] is None and again.hot[1].position == 919 and again.hot[2] is None
    )
    assert (
        again.coarse[0].data == wave
        and again.fine[0].data == wave[::-1]
        and again.fine[0].key == (0, 2)
    )
    assert again.to_bytes() == raw


def test_a_damaged_file_is_refused_in_plain_words():
    raw = LAST.read_bytes()
    with pytest.raises(m3.Mp3CardError, match="checksum"):
        m3.Mp3TrackFile.parse(raw[:100] + bytes([raw[100] ^ 1]) + raw[101:])
    with pytest.raises(m3.Mp3CardError, match="cut short"):
        m3.Mp3TrackFile.parse(raw[:300])
    with pytest.raises(m3.Mp3CardError, match="isn't a CDJ card file"):
        m3.Mp3TrackFile.parse(b"x" * 400)


def test_a_card_is_read_from_its_root_its_pioneer_folder_or_the_mpj_folder(tmp_path):
    mpj = tmp_path / "PIONEER" / "MPJ"
    mpj.mkdir(parents=True)
    shutil.copy(DATA / "MPJINDX0.PDJ", mpj / "MPJINDX0.PDJ")
    shutil.copy(LAST, mpj / "MPJ00001.PDJ")
    for start in (tmp_path, tmp_path / "PIONEER", mpj):
        index, files = m3.read_mp3_card(start)
        assert [e.name for e in index.entries] == ["MPJ00001"]
        assert files["MPJ00001"].hot[2].file == 3
    (
        mpj / "MPJ00001.PDJ"
    ).unlink()  # an entry with no file yet (the player made the entry, not the file)
    assert m3.read_mp3_card(tmp_path)[1] == {"MPJ00001": None}
    with pytest.raises(m3.Mp3CardError, match="No MP3 disc files"):
        m3.read_mp3_card(tmp_path / "nothing")


# -- the frames of an MP3 file --------------------------------------------------------------------


needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="no ffmpeg",
)


def make_mp3(path: Path, seconds=10, bitrate="128k", rate="44100", extra=()):
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=duration={seconds}",
         "-ar", rate, "-c:a", "libmp3lame", "-b:a", bitrate, *extra, str(path)],
        check=True,
    )  # fmt: skip
    return path


@needs_ffmpeg
def test_the_frame_offsets_are_the_ones_ffprobe_reports(tmp_path):
    path = make_mp3(
        tmp_path / "a.mp3", extra=("-id3v2_version", "3", "-metadata", "title=Tone")
    )
    table = m3.frame_table(path)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "packet=pos", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, encoding="utf-8",
    ).stdout.split()  # fmt: skip
    # ffprobe skips the Info frame, which is our frame 0 (and counts: the player counts it)
    assert [int(p.rstrip(",")) for p in out] == list(table.offsets[1:])
    assert table.offsets[0] > 0 and table.sample_rate == 44100
    assert table.length_frames == -(-table.frames * 1152 * 75 // 44100)


@needs_ffmpeg
def test_a_position_is_put_in_the_frame_that_holds_it(tmp_path):
    table = m3.frame_table(make_mp3(tmp_path / "a.mp3", seconds=20))
    assert table.locate(0) == (0, table.offsets[0])
    frame, byte = table.locate(916)  # 12.21 s: floor(916 x 44100 / 86400) = 467
    assert (frame, byte) == (467, table.offsets[467])
    assert table.locate(10**9) == (
        table.frames - 1,
        table.offsets[-1],
    )  # (past the end: the last frame)
    entry_ = m3.Mp3Entry.cue(table, 1, 3, 916)
    assert (entry_.frame, entry_.byte, entry_.length) == (
        467,
        table.offsets[467],
        table.length_frames,
    )


@needs_ffmpeg
def test_entries_for_a_track_are_made_from_its_frames(tmp_path):
    table = m3.frame_table(make_mp3(tmp_path / "a.mp3"))
    made = m3.entries_for(table, 2, 4, cues=[300, 600], loops=[(100, 250)])
    assert [e.is_loop for e in made] == [False, False, True]
    assert [(e.folder, e.file) for e in made] == [(2, 4)] * 3
    loop = made[2]
    assert (loop.frame, loop.end_frame) == (table.locate(100)[0], table.locate(250)[0])
    assert (loop.byte, loop.end_byte) == (table.locate(100)[1], table.locate(250)[1])


@needs_ffmpeg
def test_a_file_that_is_not_mpeg1_layer_3_or_not_clean_is_refused(tmp_path):
    junk = tmp_path / "junk.mp3"
    junk.write_bytes(b"not audio" * 100)
    with pytest.raises(m3.Mp3FrameError, match="no MPEG-1 Layer III"):
        m3.frame_table(junk)
    half_rate = make_mp3(
        tmp_path / "low.mp3", rate="22050", bitrate="64k"
    )  # MPEG-2: not this module's
    with pytest.raises(m3.Mp3FrameError, match="no MPEG-1 Layer III"):
        m3.frame_table(half_rate)
    clean = make_mp3(tmp_path / "ok.mp3")
    dirty = tmp_path / "dirty.mp3"
    dirty.write_bytes(clean.read_bytes() + b"\x00" * 5000)
    with pytest.raises(m3.Mp3FrameError, match="something other than a tag"):
        m3.frame_table(dirty)


def test_the_constants_agree_with_the_audio_disc_module():
    assert m3.HOT_SLOTS == cc.HOT_CUE_SLOTS and m3.KIND_LOOP == cc.KIND_LOOP and m3.RECORD == cc._RECORD  # fmt: skip


def _svd(label: str, sectors: int, created: bytes) -> bytes:
    """A Joliet descriptor with just the fields the disc ID reads."""
    sector = bytearray(2048)
    sector[0:6] = b"\x02CD001"
    text = label.encode("utf-16-be")
    field = text + b"\x00\x20" * ((32 - len(text)) // 2)  # padded with UCS-2 spaces
    sector[40:72] = field
    sector[80:84] = sectors.to_bytes(4, "little")
    sector[84:88] = sectors.to_bytes(4, "big")
    sector[813:830] = created
    return bytes(sector)


def test_the_disc_id_is_made_from_the_image_as_the_player_makes_it():
    """The one real disc: "MP3 Linux Test" (84,029 sectors, 3 folders and 15 files, last file at 80,245), whose
    image was made at 19:47:51 on 2026-11-23 (the burn log) and whose ID the player wrote on the card."""
    seen = bytes.fromhex(
        (DATA / "MPJINDX0.PDJ").read_bytes()[24:].hex()
    )  # the entry: its name, then the ID
    made = m3.Mp3DiscId.made_from(
        _svd("MP3 LINUX TEST", 84029, b"2026112319475100,"),
        file_count=15,
        folder_count=3,
        last_file_start=80245,
    )
    assert made.raw == seen[
        8:24
    ] and made.raw.hex() == "052c060f338745e70139750148 3d0000".replace(" ", "")


def test_a_different_label_or_second_changes_the_id():
    base = {"file_count": 15, "folder_count": 3, "last_file_start": 80245}
    one = m3.Mp3DiscId.made_from(_svd("A", 84029, b"2026112319475100,"), **base)
    other_label = m3.Mp3DiscId.made_from(_svd("B", 84029, b"2026112319475100,"), **base)
    other_second = m3.Mp3DiscId.made_from(
        _svd("A", 84029, b"2026112319475200,"), **base
    )
    assert one.raw[:2] != other_label.raw[:2] and one.raw[4:8] != other_second.raw[4:8]


def test_a_descriptor_that_is_not_joliet_is_refused():
    with pytest.raises(m3.Mp3CardError):
        m3.Mp3DiscId.made_from(
            bytes(2048), file_count=1, folder_count=0, last_file_start=0
        )
