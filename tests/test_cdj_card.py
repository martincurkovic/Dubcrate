"""The CDJ-1000MK3 card files: every fixture here was written by the player itself (a real card,
see dev_docs/cdj1000mk3-card.md), so reading them and writing them back byte for byte is the test."""

from pathlib import Path

import pytest

from dubcrate import cdj_card as cc

DATA = Path(__file__).parent / "data" / "cdj_card"
FILES = sorted(p.name for p in DATA.glob("*.PDJ") if not p.name.startswith("index"))
INDEXES = sorted(p.name for p in DATA.glob("index*.PDJ"))


def toc(name: str) -> tuple[list[int], int]:
    """Track starts and lead-out, in absolute frames, from a saved `drutil toc`."""
    return cc.toc_from_drutil((DATA / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", FILES)
def test_track_file_round_trips_byte_for_byte(name):
    raw = (DATA / name).read_bytes()
    assert cc.TrackFile.parse(raw).to_bytes() == raw


@pytest.mark.parametrize("name", INDEXES)
def test_index_round_trips_byte_for_byte(name):
    raw = (DATA / name).read_bytes()
    assert cc.IndexFile.parse(raw).to_bytes() == raw


def test_checksum_is_the_sum_of_big_endian_words_before_csum():
    raw = (DATA / "one_cue.PDJ").read_bytes()
    assert cc.checksum(raw[: raw.index(b"CSUM")]) == 0x09D3


def test_a_full_disc_file_decodes_to_what_was_set_on_the_player():
    f = cc.TrackFile.parse((DATA / "disc1_full.PDJ").read_bytes())
    # memory cues at 5 s and 10 s into track 1, 4 s and 1:04 into track 2, 2 s into track 1,
    # then a loop of about 40 to 42 s in track 1 (set by ear)
    assert [(c.index, c.kind, c.position, c.end, c.track, c.flag) for c in f.cues] == [
        (0, 0x10, 525, 0, 1, 0),
        (1, 0x10, 900, 0, 1, 0),
        (2, 0x10, 29205, 0, 2, 0),
        (3, 0x10, 33705, 0, 2, 0),
        (4, 0x10, 300, 0, 1, 0),
        (5, 0x20, 3165, 3365, 1, 0x00010000),
    ]
    assert [c.is_loop for c in f.cues] == [False] * 5 + [True]
    a, b, c = f.hot_cues
    assert (a.slot, a.position, a.track) == (1, 1275, 1)  # hot cue A, 15 s into track 1
    assert (b.slot, b.position, b.track) == (2, 61454, 3)  # hot cue B, 2 s into track 3
    assert c is None
    assert [r.track for r in f.coarse] == [r.track for r in f.fine] == [1, 2, 3]


def test_positions_are_the_track_start_plus_the_time_in_the_track():
    starts, _ = toc("CD1_TOC.txt")
    f = cc.TrackFile.parse((DATA / "disc1_full.PDJ").read_bytes())
    # 4 seconds and 1:04 into track 2
    assert f.cues[2].position == starts[1] + 4 * 75
    assert f.cues[3].position == starts[1] + 64 * 75
    # and 2 seconds into track 3 for hot cue B
    assert f.hot_cues[1].position == starts[2] + 2 * 75


def test_a_file_with_nothing_set_has_empty_lists():
    f = cc.TrackFile.parse((DATA / "disc2_waveform_only.PDJ").read_bytes())
    assert f.cues == []
    assert f.hot_cues == [None, None, None]
    assert [r.track for r in f.coarse] == [1]
    assert f.disc_id.flags == cc.FILE_FLAGS


def test_the_coarse_waveform_is_about_the_mean_of_the_fine_one():
    f = cc.TrackFile.parse((DATA / "disc1_full.PDJ").read_bytes())
    for coarse, fine in zip(f.coarse, f.fine, strict=True):
        nibbles = fine.nibbles()
        assert len(nibbles) == 100
        close = sum(
            abs(coarse.data[i] - (nibbles[2 * i] + nibbles[2 * i + 1] + 1) // 2) <= 1
            for i in range(cc.WAVE_COLUMNS)
            if coarse.data[i]
        )
        # measured: nearly every column, not every one
        assert close >= 45


def test_a_bad_checksum_is_refused_unless_asked_not_to():
    raw = bytearray((DATA / "one_cue.PDJ").read_bytes())
    raw[0x3B] ^= 1  # the low byte of the cue's position: 525 becomes 524
    with pytest.raises(cc.CardFormatError, match="checksum"):
        cc.TrackFile.parse(bytes(raw))
    assert cc.TrackFile.parse(bytes(raw), verify=False).cues[0].position == 524


@pytest.mark.parametrize("cut", [0, 5, 40, 100, 165])
def test_a_truncated_file_is_an_error_not_a_crash(cut):
    raw = (DATA / "one_cue.PDJ").read_bytes()[:cut]
    with pytest.raises(cc.CardFormatError):
        cc.TrackFile.parse(raw)


def test_something_that_is_not_a_card_file_is_refused():
    with pytest.raises(cc.CardFormatError, match="player wrote"):
        cc.TrackFile.parse(b"RIFF" + bytes(200))
    with pytest.raises(cc.CardFormatError):
        cc.IndexFile.parse(b"PIONEER INDX\0\0\0\1MP3 \0\0\0\0")


# --- the disc ID -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("toc_name", "words"),
    [
        ("CD1_TOC.txt", "000c0000 000559db 000070e9 0004f0db"),
        ("CD2_TOC.txt", "000c0000 00058084 00007f26 00051d66"),
        ("CD3_TOC.txt", "00140000 00055df5 00003a50 000521a2"),  # 20 tracks
    ],
)
def test_the_disc_id_is_made_from_the_toc(toc_name, words):
    starts, lead_out = toc(toc_name)
    assert cc.DiscId.from_toc(starts, lead_out).to_bytes() == bytes.fromhex(words)


def test_the_ids_in_the_index_are_the_tocs_of_the_discs():
    index = cc.IndexFile.parse((DATA / "index_three_discs.PDJ").read_bytes())
    for entry, name in zip(
        index.entries, ("CD1_TOC.txt", "CD2_TOC.txt", "CD3_TOC.txt"), strict=True
    ):
        assert entry.disc_id == cc.DiscId.from_toc(*toc(name))
    assert [e.name for e in index.entries] == ["CDJ00001", "CDJ00002", "CDJ00003"]


def test_the_ids_flags_differ_between_the_index_and_the_disc_file_but_the_key_doesnt():
    index = cc.IndexFile.parse((DATA / "index_three_discs.PDJ").read_bytes())
    f = cc.TrackFile.parse((DATA / "disc1_full.PDJ").read_bytes())
    assert index.entries[0].disc_id.flags == cc.INDEX_FLAGS
    assert f.disc_id.flags == cc.FILE_FLAGS
    assert index.entries[0].disc_id.key == f.disc_id.key
    assert index.find(f.disc_id) is index.entries[0]


def test_a_one_track_disc_has_no_id_we_can_make():
    with pytest.raises(cc.CardFormatError, match="one track"):
        cc.DiscId.from_toc([150], 5000)


def test_toc_from_lengths_gives_the_tocs_starts():
    for name in ("CD1_TOC.txt", "CD2_TOC.txt", "CD3_TOC.txt"):
        starts, lead_out = toc(name)
        lengths = [b - a for a, b in zip(starts, [*starts[1:], lead_out], strict=True)]
        assert cc.toc_from_lengths(lengths) == (starts, lead_out)


def test_toc_from_lengths_with_a_gap():
    starts, lead_out = cc.toc_from_lengths([1000, 2000, 3000], gap=150)
    assert starts == [150, 1300, 3450]
    assert lead_out == 6450


# --- writing ---------------------------------------------------------------------------------


def test_a_file_built_from_nothing_matches_the_players_file():
    # what disc 1 looked like after one cue: built here, equal to the file the player wrote
    starts, lead_out = toc("CD1_TOC.txt")
    f = cc.TrackFile.new(cc.DiscId.from_toc(starts, lead_out))
    f.cues.append(cc.CueEntry.memory(0, cc.LEAD_IN_FRAMES + 5 * 75, track=1))
    assert f.to_bytes() == (DATA / "one_cue.PDJ").read_bytes()


def test_a_waveform_only_file_built_from_nothing_matches_too():
    raw = (DATA / "disc2_waveform_only.PDJ").read_bytes()
    parsed = cc.TrackFile.parse(raw)
    f = cc.TrackFile.new(cc.DiscId.from_toc(*toc("CD2_TOC.txt")))
    f.coarse = [cc.WaveRecord.coarse(1, list(parsed.coarse[0].data))]
    f.fine = [cc.WaveRecord.fine(1, parsed.fine[0].nibbles())]
    f.pad = (
        parsed.pad
    )  # the two bytes after the checksum are junk in the player's files
    assert f.to_bytes() == raw


def test_cues_hot_cues_and_a_loop_built_here_read_back_the_same():
    starts, lead_out = toc("CD3_TOC.txt")
    f = cc.TrackFile.new(cc.DiscId.from_toc(starts, lead_out))
    f.cues = [
        cc.CueEntry.memory(0, starts[3] + 10 * 75, track=4),
        cc.CueEntry.loop(1, starts[0] + 8 * 75, starts[0] + 16 * 75, track=1),
    ]
    f.hot_cues[2] = cc.HotCue(3, starts[19] + 75, track=20)
    again = cc.TrackFile.parse(f.to_bytes())
    assert again.cues == f.cues
    assert again.hot_cues == f.hot_cues
    assert again.cues[1].is_loop


def test_the_hot_cue_chunk_says_60_once_a_slot_is_used_and_0_before():
    f = cc.TrackFile.new(cc.DiscId.from_toc(*toc("CD1_TOC.txt")))
    empty = f.to_bytes()
    f.hot_cues[0] = cc.HotCue(1, 1275, track=1)
    used = f.to_bytes()
    marker = lambda raw: int.from_bytes(raw[raw.index(b"HC11") + 4 :][:4], "big")
    assert (marker(empty), marker(used)) == (0, 60)


def test_too_many_cues_are_refused():
    f = cc.TrackFile.new(cc.DiscId.from_toc(*toc("CD1_TOC.txt")))
    f.cues = [cc.CueEntry.memory(n, 150 + n, track=1) for n in range(cc.MAX_CUES + 1)]
    with pytest.raises(cc.CardFormatError, match="at most"):
        f.to_bytes()


def test_the_index_adds_a_disc_once_and_names_its_file():
    index = cc.IndexFile.parse((DATA / "index_one_disc.PDJ").read_bytes())
    new = cc.DiscId.from_toc(*toc("CD2_TOC.txt"), flags=cc.FILE_FLAGS)
    entry = index.add(new)
    assert entry.name == "CDJ00002"
    assert entry.file_name == "CDJ00002.PDJ"
    assert (
        entry.disc_id.flags == cc.INDEX_FLAGS
    )  # the index's own flags, whatever came in
    assert index.add(new) is entry  # a second time finds it
    assert len(index.entries) == 2
    # the player's own index of those two discs: the first 72 bytes of the three-disc one, count 2
    two = bytearray((DATA / "index_three_discs.PDJ").read_bytes()[:72])
    two[23] = 2
    assert index.to_bytes() == bytes(two)


# --- a whole card ------------------------------------------------------------------------------


def test_read_card_reads_from_a_copy_of_the_folder_or_the_card_root(tmp_path):
    folder = tmp_path / "PIONEER" / "CDJ"
    folder.mkdir(parents=True)
    (folder / cc.INDEX_NAME).write_bytes((DATA / "index_three_discs.PDJ").read_bytes())
    (folder / "CDJ00001.PDJ").write_bytes((DATA / "disc1_full.PDJ").read_bytes())
    (folder / "CDJ00003.PDJ").write_bytes(
        (DATA / "disc3_waveform_only.PDJ").read_bytes()
    )
    for where in (tmp_path, tmp_path / "PIONEER", folder):
        index, files = cc.read_card(where)
        assert [e.name for e in index.entries] == ["CDJ00001", "CDJ00002", "CDJ00003"]
        assert (
            files["CDJ00002"] is None
        )  # in the index, no file yet (the player makes it later)
        assert len(files["CDJ00001"].cues) == 6


def test_a_folder_that_is_not_a_card_is_an_expected_error(tmp_path):
    with pytest.raises(cc.CardFormatError, match="card"):
        cc.read_card(tmp_path)


def test_toc_from_drutil_reads_the_tracks_and_the_lead_out():
    starts, lead_out = toc("CD1_TOC.txt")
    assert len(starts) == 12
    assert (starts[0], starts[1], starts[-1], lead_out) == (150, 28905, 323803, 350683)
    with pytest.raises(cc.CardFormatError, match="drutil"):
        cc.toc_from_drutil("nothing useful here")


# --- writing a card ------------------------------------------------------------------------------


def make_card(tmp_path):
    folder = tmp_path / "PIONEER" / "CDJ"
    folder.mkdir(parents=True)
    (folder / cc.INDEX_NAME).write_bytes((DATA / "index_three_discs.PDJ").read_bytes())
    (folder / "CDJ00001.PDJ").write_bytes((DATA / "disc1_full.PDJ").read_bytes())
    return folder


def test_write_card_writes_the_files_then_the_index_and_reads_back(tmp_path):
    folder = make_card(tmp_path)
    index, _ = cc.read_card(tmp_path)
    file = cc.TrackFile.new(index.entries[1].disc_id)
    file.cues.append(cc.CueEntry.memory(0, 525, track=1))
    cc.write_card(tmp_path, index, {"CDJ00002": file})
    again_index, again = cc.read_card(tmp_path)
    assert again["CDJ00002"].cues == file.cues
    assert again["CDJ00001"] is not None  # untouched
    assert again_index.to_bytes() == index.to_bytes()
    assert sorted(p.name for p in folder.iterdir()) == [
        "CDJ00001.PDJ",
        "CDJ00002.PDJ",
        "CDJINDX0.PDJ",
    ]  # no temporary file left behind


def test_write_card_leaves_the_other_files_exactly_as_they_were(tmp_path):
    folder = make_card(tmp_path)
    before = (folder / "CDJ00001.PDJ").read_bytes()
    index, _ = cc.read_card(tmp_path)
    file = cc.TrackFile.new(index.entries[2].disc_id)
    cc.write_card(tmp_path, index, {"CDJ00003": file})
    assert (folder / "CDJ00001.PDJ").read_bytes() == before


def test_write_card_refuses_a_file_the_index_does_not_list(tmp_path):
    make_card(tmp_path)
    index, _ = cc.read_card(tmp_path)
    file = cc.TrackFile.new(index.entries[0].disc_id)
    with pytest.raises(cc.CardFormatError, match="not in the index"):
        cc.write_card(tmp_path, index, {"CDJ00099": file})


def test_write_card_makes_the_folder_only_when_asked(tmp_path):
    index = cc.IndexFile()
    disc = cc.DiscId.from_toc(*toc("CD2_TOC.txt"))
    entry = index.add(disc)
    file = cc.TrackFile.new(disc)
    with pytest.raises(cc.CardFormatError, match="card"):
        cc.write_card(tmp_path, index, {entry.name: file})
    cc.write_card(tmp_path, index, {entry.name: file}, create=True)
    again_index, files = cc.read_card(tmp_path)
    assert [e.name for e in again_index.entries] == ["CDJ00001"]
    assert files["CDJ00001"].disc_id.key == disc.key


def test_find_cards_keeps_the_volumes_that_hold_a_card_in_order(tmp_path):
    card = tmp_path / "CDJ"
    (card / "PIONEER" / "CDJ").mkdir(parents=True)
    (card / "PIONEER" / "CDJ" / cc.INDEX_NAME).write_bytes(b"x")
    plain = tmp_path / "Backup"
    plain.mkdir()
    other = tmp_path / "Other"
    (other / "PIONEER" / "CDJ").mkdir(parents=True)  # (no index: not a card)
    gone = tmp_path / "Unplugged"
    assert cc.find_cards([plain, card, other, gone]) == [card]


def test_a_write_asks_the_system_to_flush_the_card_before_it_returns(
    tmp_path, monkeypatch
):
    folder = tmp_path / "PIONEER" / "CDJ"
    folder.mkdir(parents=True)
    (folder / cc.INDEX_NAME).write_bytes(cc.IndexFile().to_bytes())
    flushed = []
    monkeypatch.setattr(cc.os, "sync", lambda: flushed.append(1), raising=False)
    cc.write_card(tmp_path, cc.IndexFile(), {})
    assert flushed == [1]
