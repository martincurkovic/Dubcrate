"""The waveform test disc's audio: exact lengths, known levels, deterministic, and unchanged by a burn."""

import itertools
import json
import wave

import numpy as np
import pytest

from dubcrate import render as render_module
from dubcrate import tools, wavetest
from dubcrate.wavetest import Segment, TrackSpec, build_tracks, db, render

TRACKS = build_tracks()
BY_NUMBER = {t.number: t for t in TRACKS}


def seg_audio(number: int, label_part: str) -> np.ndarray:
    track = BY_NUMBER[number]
    audio = render(track).astype(np.float64) / 32768
    seg = next((s for s in track.segments if s.label == label_part), None) or next(
        s for s in track.segments if label_part in s.label
    )
    a, b = seg.samples
    return audio[a:b]


def rms_db(x: np.ndarray) -> float:
    return 20 * np.log10(np.sqrt(np.mean(x**2)))


def test_the_disc_is_twenty_tracks_of_whole_sectors_that_fit_a_cd():
    assert [t.number for t in TRACKS] == list(range(1, 21))
    assert all(t.samples % wavetest.SECTOR == 0 for t in TRACKS)
    assert sum(t.seconds for t in TRACKS) == 2040  # 34 minutes
    assert sum(t.sectors for t in TRACKS) + 150 <= 74 * 60 * 75
    assert len({t.title for t in TRACKS}) == 20


def test_most_tracks_are_100_seconds_so_a_column_is_two_seconds_and_a_fine_one_is_one():
    assert [t.seconds for t in TRACKS if t.number not in (19, 20)] == [100.0] * 18
    assert BY_NUMBER[19].seconds == 200 and BY_NUMBER[20].seconds == 40


def test_segments_lie_inside_their_track_and_cover_it_without_gaps_except_the_alignment_track():
    for track in TRACKS:
        assert all(0 <= s.start < s.end <= track.seconds for s in track.segments), (
            track.title
        )
        if track.number == 3:
            continue  # (silence with bursts laid over it)
        edges = sorted((s.start, s.end) for s in track.segments)
        assert edges[0][0] == 0 and edges[-1][1] == track.seconds, track.title
        assert all(abs(a[1] - b[0]) < 1e-9 for a, b in itertools.pairwise(edges)), (
            track.title
        )


def test_the_audio_is_the_same_every_time_and_follows_the_manifest():
    again = wavetest.tracks_from_manifest(
        json.loads(json.dumps(wavetest.manifest(TRACKS)))
    )
    assert wavetest.manifest(again) == wavetest.manifest(TRACKS)
    for number in (3, 13):  # (a track with clicks, a track with dither and silence)
        assert np.array_equal(render(BY_NUMBER[number]), render(again[number - 1]))


def test_a_staircase_step_has_the_peak_it_says():
    top = seg_audio(1, "0 dBFS peak")
    assert np.abs(top).max() == pytest.approx(1.0, abs=2e-4)
    mid = seg_audio(1, "-30 dBFS peak")
    assert 20 * np.log10(np.abs(mid).max()) == pytest.approx(-30.0, abs=0.1)
    linear = seg_audio(2, "40% of full scale")
    assert np.abs(linear).max() == pytest.approx(0.40, abs=0.001)


def test_digital_silence_is_exactly_zero_and_a_click_is_one_full_scale_sample():
    assert not render(BY_NUMBER[18]).any()
    audio = render(BY_NUMBER[3])
    click = audio[round(70.5 * wavetest.RATE) : round(70.52 * wavetest.RATE)]
    assert (
        click != 0
    ).sum() == 2 and click.max() == 32767  # (one sample on each channel)


def test_the_equal_rms_shapes_really_are_equal_in_rms_and_differ_in_peak():
    for number, level in ((6, -20.0), (10, -32.0)):
        peaks = {}
        for label in (
            "sine",
            "square",
            "triangle",
            "white noise",
            "pink noise",
            "1% pulses",
            "2% pulses",
            "5% pulses",
        ):
            x = seg_audio(number, label)
            assert rms_db(x) == pytest.approx(level, abs=0.4), (number, label)
            peaks[label] = 20 * np.log10(np.abs(x).max())
        assert (
            peaks["square"]
            < peaks["sine"]
            < peaks["triangle"]
            < peaks["1% pulses"] + 0.1
        )
        assert peaks["1% pulses"] == pytest.approx(
            level + 20, abs=0.1
        )  # (0 dBFS at -20)


def test_no_shape_asks_for_more_than_full_scale():
    with pytest.raises(ValueError, match="would need a peak"):
        wavetest._peak_for_rms("pulse", -20.0, 0.0005)
    assert all(np.abs(render(BY_NUMBER[n])).max() <= 32767 for n in (6, 10))


def test_the_stereo_track_puts_each_signal_where_it_says():
    left = seg_audio(7, "left only")
    assert (
        rms_db(left[:, 0]) == pytest.approx(-12 - 3.01, abs=0.1)
        and rms_db(left[:, 1]) < -85
    )
    right = seg_audio(7, "right only")
    assert (
        rms_db(right[:, 1]) == pytest.approx(-12 - 3.01, abs=0.1)
        and rms_db(right[:, 0]) < -85
    )
    anti = seg_audio(7, "right inverted")
    assert (
        np.abs(anti[:, 0] + anti[:, 1]).max() < 3 / 32768
    )  # (cancels in the sum, to the dither)
    unequal = seg_audio(7, "left 0, right -12")
    assert rms_db(unequal[:, 0]) - rms_db(unequal[:, 1]) == pytest.approx(12.0, abs=0.2)
    independent = seg_audio(7, "independent")
    assert abs(np.corrcoef(independent[:, 0], independent[:, 1])[0, 1]) < 0.01


def test_a_ramp_rises_in_dB_and_a_linear_ramp_rises_in_amplitude():
    ramp = render(BY_NUMBER[9]).astype(np.float64)[:, 0] / 32768
    tenths = [
        20 * np.log10(np.abs(ramp[i * 441000 : (i + 1) * 441000]).max() + 1e-9)
        for i in range(10)
    ]
    assert all(b > a for a, b in itertools.pairwise(tenths)) and tenths[
        -1
    ] == pytest.approx(0, abs=0.1)
    linear = np.abs(render(BY_NUMBER[14]).astype(np.float64)[:, 0] / 32768)
    assert linear[:441000].max() == pytest.approx(0.1, abs=0.002)
    assert linear[-441000:].max() == pytest.approx(1.0, abs=0.002)


def test_dither_keeps_a_very_quiet_tone_a_tone_and_never_touches_silence():
    quiet = seg_audio(13, "sine -90 dBFS")
    assert quiet.std() > 0 and len(np.unique(quiet)) >= 3
    floor = seg_audio(13, "dither only")
    assert np.abs(floor).max() * 32768 <= 2 and floor.any()
    assert not seg_audio(13, "digital silence").any()


def test_a_length_that_is_not_a_whole_number_of_sectors_is_noticed():
    assert (
        TrackSpec(1, "t", 1.0, "", (Segment(0, 1.0, "silence"),)).samples
        % wavetest.SECTOR
        == 0
    )
    odd = TrackSpec(1, "odd", 1.001, "", (Segment(0, 1.001, "silence"),))
    assert odd.samples % wavetest.SECTOR != 0


# -- through the burn pipeline ----------------------------------------------------------------------------


@pytest.fixture
def ffmpeg():
    tool = tools.find_tool("ffmpeg")
    if tool is None:
        pytest.skip("no ffmpeg")
    return tool.path


def read_wav(path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (2, 2, 44100)
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2)


def test_the_burn_pipeline_passes_the_test_audio_through_bit_for_bit(tmp_path, ffmpeg):
    """If it dithered or changed a sample, the disc would measure the pipeline, not the player."""
    spec = TrackSpec(
        98, "t", 4.0, "", (
            Segment(0, 1.0, "silence"),
            Segment(1.0, 2.0, "sine", {"freq": 1000.0, "peak_db": -90.0}),
            Segment(2.0, 3.0, "pink", {"rms_db": -20.0}),
            Segment(3.0, 4.0, "sine", {"freq": 1000.0, "peak_db": 0.0}),
        ), seed=5,
    )  # fmt: skip
    audio = render(spec)
    wav, flac = tmp_path / "in.wav", tmp_path / "in.flac"
    wavetest.write_wav(wav, audio)
    import subprocess

    subprocess.run(
        [
            str(ffmpeg),
            "-y",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(wav),
            "-c:a",
            "flac",
            str(flac),
        ],
        check=True,
    )
    for source in (wav, flac):
        out = tmp_path / f"out-{source.suffix[1:]}.wav"
        sectors = render_module.render_track(source, out)
        assert sectors == spec.sectors
        assert np.array_equal(read_wav(out), audio), source.name


def test_writing_the_disc_makes_checked_files_a_manifest_and_a_readme_and_refuses_a_used_folder(
    tmp_path, ffmpeg
):
    tracks = [
        TrackSpec(
            1,
            "First: a tone",
            2.0,
            "n",
            (Segment(0, 2.0, "sine", {"freq": 1000.0, "peak_db": -10.0}),),
        ),
        TrackSpec(2, "Second / silence", 2.0, "n", (Segment(0, 2.0, "silence"),)),
    ]
    written = wavetest.write_disc(
        tmp_path / "disc", tracks, ffmpeg, say=lambda _t: None
    )
    assert [p.name for p in written] == [
        "01 First a tone.flac",
        "02 Second silence.flac",
    ]
    assert wavetest._file_name(BY_NUMBER[1]).startswith(
        "01 dB staircase 1 kHz sine minus 72 to 0 dBFS"
    )
    assert "31_5 Hz" in wavetest._file_name(BY_NUMBER[8])
    assert (tmp_path / "disc" / "manifest.json").is_file() and "PLAY EVERY TRACK" in (
        tmp_path / "disc" / "README.txt"
    ).read_text(encoding="utf-8")
    assert not any(
        p.name.startswith(".") for p in (tmp_path / "disc").iterdir()
    )  # (no stray wav)
    assert np.array_equal(wavetest.decode(written[0], ffmpeg), render(tracks[0]))
    with pytest.raises(FileExistsError):
        wavetest.write_disc(tmp_path / "disc", tracks, ffmpeg, say=lambda _t: None)


def test_db_helper():
    assert db(0) == 1.0 and db(-20) == pytest.approx(0.1)
