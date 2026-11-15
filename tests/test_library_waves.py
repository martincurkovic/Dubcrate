"""The library remembers a track's CDJ waveform until its file changes, and analyses files on a worker."""

import numpy as np
import pytest

from dubcrate import wave_model, wavetest
from dubcrate.library import Library, TrackRecord, waves


def rec(ext_id, path=None, **kw):
    return TrackRecord(ext_id=ext_id, path=path or f"/nonexistent/{ext_id}.mp3", title=ext_id, duration=100, **kw)  # fmt: skip


def test_a_waveform_is_kept_until_the_file_changes_size():
    with Library.open(":memory:") as lib:
        lib.replace_source("folder", "/m", "M", [rec("a", file_size=1000), rec("b", file_size=2000)])  # fmt: skip
        assert all(t.waveform is None for t in lib.tracks())
        values = list(range(1, 16)) * 6 + [3] * 10
        for t in lib.tracks():
            lib.set_waveform(t.id, values)
        assert list(lib.tracks()[0].waveform) == values
        lib.replace_source("folder", "/m", "M", [rec("a", file_size=1000), rec("b", file_size=2500)])  # fmt: skip
        assert {t.ext_id: t.waveform is not None for t in lib.tracks()} == {"a": True, "b": False}  # fmt: skip


def test_a_waveform_must_be_a_hundred_values_of_a_nibble():
    with Library.open(":memory:") as lib:
        lib.replace_source("folder", "/m", "M", [rec("a")])
        track_id = lib.tracks()[0].id
        with pytest.raises(ValueError):
            lib.set_waveform(track_id, [1] * 99)
        with pytest.raises(ValueError):
            lib.set_waveform(track_id, [16] + [1] * 99)


def test_only_a_readable_track_without_a_waveform_needs_one():
    with Library.open(":memory:") as lib:
        lib.replace_source("folder", "/m", "M", [rec("a")])
        (track,) = lib.tracks()
        assert track.missing and not waves.needs_waveform(
            track
        )  # (the file isn't there)


def write_tone(path, seconds=12, level=-24.0):
    audio = (wave_model_tone(seconds, level) * 32767).astype(np.int16)
    wavetest.write_wav(path, audio)


def wave_model_tone(seconds, level):
    t = np.arange(round(seconds * wave_model.RATE)) / wave_model.RATE
    s = 10 ** (level / 20) * np.sin(2 * np.pi * 125 * t)
    return np.stack([s, s], axis=1)


def test_files_are_analysed_and_stored_and_a_bad_file_is_a_message(tmp_path):
    good, bad = tmp_path / "good.wav", tmp_path / "bad.wav"
    write_tone(good)
    bad.write_bytes(b"this is not audio")
    with Library.open(":memory:") as lib:
        lib.replace_source("folder", str(tmp_path), "M", [rec("good", str(good), file_size=1), rec("bad", str(bad), file_size=2)])  # fmt: skip
        done = []
        found, problems = waves.measure_waveforms(
            lib.tracks(), lambda n, total: done.append((n, total))
        )
        assert [t.title for t in lib.tracks() if t.id in found] == ["good"]
        assert len(problems) == 1 and "bad" in problems[0]
        assert done[-1] == (2, 2)
        waves.store_waveforms(lib, found)
        track = next(t for t in lib.tracks() if t.title == "good")
        values = list(track.waveform)
        assert (
            len(values) == 100 and max(values) == 15
        )  # a quiet tone, scaled up to the top
        assert not waves.needs_waveform(track)
        assert (
            waves.ensure_waveforms(lib, [track.id]) == []
        )  # nothing left to do for it
