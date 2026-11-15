"""The waveform model: known tones, the player's own records for the second test disc, and the packing order."""

from pathlib import Path

import numpy as np
import pytest

from dubcrate import cdj_card as cc
from dubcrate import wave_model, wavetest

DATA = Path(__file__).parent / "data" / "cdj_card"
PROBES = {p.number: p for p in wavetest.build_probe_tracks(1)}
PLAYER = cc.TrackFile.parse((DATA / "disc4_probes_and_music.PDJ").read_bytes())


def tone(
    freq: float, level_db: float, seconds: float = 20.0, gains=(1.0, 1.0)
) -> np.ndarray:
    t = np.arange(round(seconds * wave_model.RATE)) / wave_model.RATE
    sig = 10 ** (level_db / 20) * np.sin(2 * np.pi * freq * t)
    return np.stack([sig * gains[0], sig * gains[1]], axis=1)


def player_columns(track: int) -> list[int]:
    return next(r for r in PLAYER.fine if r.track == track).time_values()


@pytest.mark.parametrize(
    ("level", "expected"), [(-6, 15), (-12, 12), (-18, 9), (-30, 3)]
)
def test_a_125_hz_tone_reads_two_db_a_value_as_the_player_does(level, expected):
    values = wave_model.column_values(tone(125, level))
    mid = values[10:90]
    assert all(abs(v - expected) <= 2 for v in mid), (level, set(mid))


def test_silence_reads_one_and_a_quiet_tone_reads_the_floor_of_two():
    assert set(wave_model.column_values(np.zeros((wave_model.RATE * 10, 2)))) == {1}
    assert set(wave_model.column_values(tone(1000, -20, 10))[5:95]) == {
        2
    }  # outside the band: the floor


def test_the_two_channels_are_averaged():
    both = wave_model.column_values(tone(125, -12))[50]
    left = wave_model.column_values(tone(125, -12, gains=(1.0, 0.0)))[50]
    cancelled = wave_model.column_values(tone(125, -12, gains=(1.0, -1.0)))[50]
    assert 2 <= both - left <= 4  # 6 dB, three values
    assert cancelled == 1


def test_the_model_follows_the_players_own_record_of_the_level_ladder():
    audio = wavetest.render(PROBES[3])
    ours = wave_model.column_values(audio)
    theirs = player_columns(3)
    pairs = [(a, b) for a, b in zip(ours, theirs) if b >= 3]
    assert len(pairs) > 40
    assert (
        np.mean([abs(a - b) <= 2 for a, b in pairs]) >= 0.95
    )  # (it reads pure tones about one value high)
    assert np.mean([abs(a - b) <= 1 for a, b in pairs]) >= 0.6


def test_the_coarse_values_follow_the_players_own_pairs():
    close = total = 0
    for fine, coarse in zip(PLAYER.fine, PLAYER.coarse):
        if fine.track == 0:
            continue
        ours = wave_model.coarse_values(fine.time_values())
        theirs = list(coarse.data)
        for a, b, v in zip(ours, theirs, fine.time_values()[::2]):
            if v and b:
                total += 1
                close += abs(a - b) <= 1
    assert close / total >= 0.97


def test_a_fine_record_packs_the_earlier_column_into_the_low_nibble():
    record = cc.WaveRecord.fine_in_time_order(1, [1, 2] + [0] * 98)
    assert record.data[0] == 0x21  # the later column (2) high, the earlier (1) low
    assert record.time_values()[:2] == [1, 2]
    assert record.nibbles()[:2] == [2, 1]  # the byte's own order, high first
    with pytest.raises(cc.CardFormatError):
        cc.WaveRecord.fine_in_time_order(1, [16] + [0] * 99)
    assert cc.WaveRecord.unplayed().track == 0 and not any(
        cc.WaveRecord.unplayed().data
    )


def test_every_record_the_player_wrote_round_trips_through_time_order():
    for record in PLAYER.fine:
        again = cc.WaveRecord.fine_in_time_order(record.track, record.time_values())
        assert again.data == record.data


def test_the_test_pattern_is_a_sawtooth_that_cannot_be_music():
    values = wave_model.pattern_values()
    assert len(values) == 100 and min(values) == 1 and max(values) == 15
    assert values[:16] == list(range(1, 16)) + [1]


def test_a_too_short_track_or_a_wrong_length_is_refused():
    with pytest.raises(ValueError):
        wave_model.column_values(np.zeros((10, 2)))
    with pytest.raises(ValueError):
        wave_model.coarse_values([1] * 99)


def test_records_pack_the_columns_and_the_coarse_pairs_for_one_track():
    values = wave_model.pattern_values()
    coarse, fine = wave_model.records(3, values)
    assert coarse.track == fine.track == 3
    assert fine.time_values() == values
    assert list(coarse.data) == wave_model.coarse_values(values)
    half = values[:50] + [0] * 50
    _, fine = wave_model.records(1, half)
    assert fine.time_values()[50:] == [0] * 50  # unplayed columns stay zero
