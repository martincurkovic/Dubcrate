"""rekordbox's time against the audio on the disc, per MP3 (`cue_timing`)."""

import pytest

from dubcrate import cue_timing as ct
from dubcrate.analysis import Cue


def frame(
    maker=b"LAME", delay=576, tag=b"Info", crc=False, mono=False, version=3
) -> bytes:
    """An MP3's first frame as LAME writes it: a header, side info, the Info / Xing tag and its LAME extension."""
    byte1 = {3: 0xFA, 2: 0xF2, 0: 0xE2}[version] | (
        0 if crc else 1
    )  # MPEG1, MPEG2, MPEG2.5, Layer III
    head = bytes([0xFF, byte1, 0x90, 0xC0 if mono else 0x00])
    side = (17 if mono else 32) if version == 3 else (9 if mono else 17)
    pad = 0
    packed = bytes([delay >> 4, ((delay & 15) << 4) | (pad >> 8), pad & 255])
    ext = maker + b"3.100" + bytes(12) + packed
    flags = (15).to_bytes(4, "big")
    body = bytes(2 if crc else 0) + bytes(side) + tag + flags + bytes(4 + 4 + 100 + 4)
    return head + body + ext + bytes(100)


def with_id3(data: bytes, size: int = 3000) -> bytes:
    syncsafe = bytes(
        [(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F]
    )
    return b"ID3\x04\x00\x00" + syncsafe + bytes(size) + data


def test_a_lame_file_is_ahead_by_its_info_frame_and_both_delays():
    assert ct.lead_samples(frame()) == (1152 + 576 + 529, 44100)  # about 51 ms
    assert ct.lead_samples(frame(tag=b"Xing")) == (1152 + 576 + 529, 44100)


def test_a_file_ffmpeg_made_is_ahead_by_the_delay_alone():
    assert ct.lead_samples(frame(maker=b"Lavc")) == (576 + 529, 44100)  # about 25 ms
    assert ct.lead_samples(frame(maker=b"Lavf")) == (576 + 529, 44100)


def test_no_info_frame_no_lead():
    plain = bytes([0xFF, 0xFB, 0x90, 0x00]) + bytes(400)  # audio from the first frame
    assert ct.lead_samples(plain) == (0, 0)
    assert ct.lead_samples(b"") == (0, 0)
    assert ct.lead_samples(b"not an mp3 at all") == (0, 0)


def test_what_is_not_understood_is_left_alone():
    assert ct.lead_samples(frame(delay=0)) == (
        0,
        0,
    )  # (measured files with none disagreed with each other)
    assert ct.lead_samples(frame(maker=b"Lame")) == (0, 0)  # an encoder not measured
    assert ct.lead_samples(frame()[:60]) == (0, 0)  # cut short


def test_a_crc_mono_and_mpeg2_frame_put_the_tag_where_it_is():
    assert ct.lead_samples(frame(crc=True)) == (1152 + 576 + 529, 44100)
    assert ct.lead_samples(frame(mono=True)) == (1152 + 576 + 529, 44100)
    samples, rate = ct.lead_samples(
        frame(version=2)
    )  # 22.05 kHz: a frame is 576 samples
    assert (samples, rate) == (576 + 576 + 529, 22050)


def test_a_file_is_read_past_its_id3_tag(tmp_path):
    path = tmp_path / "a.mp3"
    path.write_bytes(with_id3(frame(), size=70000))  # a cover picture makes the tag big
    assert ct.lead_ms(path) == 51
    ffmpeg_made = tmp_path / "b.mp3"
    ffmpeg_made.write_bytes(frame(maker=b"Lavc"))
    assert ct.lead_ms(ffmpeg_made) == 25


def test_only_an_mp3_that_can_be_read_has_a_lead(tmp_path):
    wav = tmp_path / "a.wav"
    wav.write_bytes(frame())  # (the name says it isn't an MP3)
    assert ct.lead_ms(wav) == 0
    assert ct.lead_ms(tmp_path / "missing.mp3") == 0
    empty = tmp_path / "e.mp3"
    empty.write_bytes(b"")
    assert ct.lead_ms(empty) == 0


def test_a_file_that_changes_is_read_again(tmp_path):
    path = tmp_path / "a.mp3"
    path.write_bytes(frame())
    assert ct.lead_ms(path) == 51
    path.write_bytes(frame(maker=b"Lavc") + bytes(10))  # another size
    assert ct.lead_ms(path) == 25


def test_cues_move_earlier_by_the_lead_and_never_before_the_start():
    cues = [
        Cue("memory", 1.0, label="a"),
        Cue("memory", 0.02),
        Cue("loop", 8.0, end=12.0),
        Cue("hot", 30.0, slot=2),
    ]
    got = ct.to_audio(cues, 51)
    assert [round(c.time, 3) for c in got] == [0.949, 0.0, 7.949, 29.949]
    assert round(got[2].end, 3) == 11.949
    assert got[0].label == "a" and got[3].slot == 2 and got[0].end is None
    assert ct.to_audio(cues, 0) == cues
    assert ct.rekordbox_ms(949, 51) == 1000


@pytest.mark.parametrize("ms", [0, 1, 500, 123456])
def test_a_round_trip_through_audio_time_is_exact(ms):
    [back] = ct.to_audio([Cue("memory", ms / 1000)], 51)
    assert ct.rekordbox_ms(round(back.time * 1000), 51) == max(ms, 51)
