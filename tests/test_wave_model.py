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
    values = wave_model.column_values(tone(125, level), scale_to_top=False)
    mid = values[10:90]
    assert all(abs(v - expected) <= 2 for v in mid), (level, set(mid))


def test_silence_reads_one_and_a_quiet_tone_reads_the_floor_of_two():
    assert set(
        wave_model.column_values(
            np.zeros((wave_model.RATE * 10, 2)), scale_to_top=False
        )
    ) == {1}
    assert set(
        wave_model.column_values(tone(1000, -20, 10), scale_to_top=False)[5:95]
    ) == {2}  # outside the band: the floor


def test_the_two_channels_are_averaged():
    both = wave_model.column_values(tone(125, -12), scale_to_top=False)[50]
    left = wave_model.column_values(
        tone(125, -12, gains=(1.0, 0.0)), scale_to_top=False
    )[50]
    cancelled = wave_model.column_values(
        tone(125, -12, gains=(1.0, -1.0)), scale_to_top=False
    )[50]
    assert 2 <= both - left <= 4  # 6 dB, three values
    assert cancelled == 1


def test_the_model_follows_the_players_own_record_of_the_level_ladder():
    audio = wavetest.render(PROBES[3])
    ours = wave_model.column_values(audio, scale_to_top=False)
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


def test_a_quiet_track_is_scaled_up_so_its_loudest_column_reaches_the_top():
    quiet = tone(125, -30)  # reads 3 on the player's own scale
    assert set(wave_model.column_values(quiet, scale_to_top=False)[5:95]) <= {2, 3, 4}
    scaled = wave_model.column_values(quiet)
    assert (
        max(scaled) == 15 and min(scaled[5:95]) >= 14
    )  # (a steady tone wobbles by one value)


def test_scaling_keeps_the_shape_and_moves_every_column_the_same_number_of_steps():
    loud, soft = tone(125, -12, 10), tone(125, -30, 10)
    audio = np.concatenate([loud, soft])  # a loud half, then a half 18 dB down
    raw = wave_model.column_values(audio, scale_to_top=False)
    scaled = wave_model.column_values(audio)
    assert max(scaled) == 15
    shift = 15 - max(raw)
    assert shift > 0
    for r, s in zip(raw[10:40], scaled[10:40]):  # inside the loud half
        assert s == min(15, r + shift) or abs(s - (r + shift)) <= 1
    assert scaled[20] - scaled[80] == raw[20] - raw[80]  # 18 dB, nine values, kept


def test_a_track_already_at_the_top_is_not_changed_and_never_scaled_down():
    loud = tone(125, -3)
    assert wave_model.column_values(loud) == wave_model.column_values(
        loud, scale_to_top=False
    )
    assert max(wave_model.column_values(loud)) == 15


def test_a_silent_track_is_left_alone_and_the_boost_never_passes_its_limit():
    assert set(wave_model.column_values(np.zeros((wave_model.RATE * 10, 2)))) == {1}
    faint = tone(
        125, -50
    )  # about -54 dB in the band: needs 14 values to reach the top, the limit is 12
    raw = max(wave_model.column_values(faint, scale_to_top=False))
    assert max(wave_model.column_values(faint)) <= raw + wave_model.MAX_BOOST_DB // 2


def test_a_track_with_no_bass_is_not_scaled_up_though_it_is_quiet_in_the_band():
    # a 1 kHz tone shows in the 125 Hz band only as the filter's skirt, 35 dB under its own peak
    high = tone(1000, -6)
    assert wave_model.column_values(high) == wave_model.column_values(
        high, scale_to_top=False
    )
    assert (
        max(wave_model.column_values(high)) <= 3
    )  # the player's floor, as on the first test disc


def test_the_waveform_does_not_depend_on_the_gain_a_burn_applied():
    base = tone(125, -12, 10)
    music = np.concatenate([base, tone(125, -24, 10), base])
    quieter = music * 10 ** (-10 / 20)  # turned down 10 dB by level matching
    a, b = wave_model.column_values(music), wave_model.column_values(quieter)
    assert all(abs(x - y) <= 1 for x, y in zip(a, b))


def test_filtering_a_column_at_a_time_gives_the_levels_of_filtering_the_whole_track():
    rng = np.random.default_rng(3)
    t = np.arange(wave_model.RATE * 30) / wave_model.RATE
    audio = np.stack(
        [
            0.2 * np.sin(2 * np.pi * 130 * t) * (1 + np.sin(2 * np.pi * 0.3 * t))
            + 0.05 * rng.standard_normal(len(t)),
            0.1 * np.sin(2 * np.pi * 140 * t) + 0.02 * rng.standard_normal(len(t)),
        ],
        axis=1,
    )
    levels, _, _ = wave_model._levels(audio)
    y = np.abs(wave_model.bass_band(audio))
    n = len(audio)
    for k in range(wave_model.COLUMNS):
        a = round(k * n / wave_model.COLUMNS)
        b = min(n, round((k + wave_model.WINDOW) * n / wave_model.COLUMNS))
        whole = (
            20 * np.log10(np.mean(y[a:b] ** wave_model.POWER) ** (1 / wave_model.POWER))
            + wave_model.OFFSET_DB
        )
        assert abs(levels[k] - whole) < 0.3, (k, levels[k], whole)


def test_a_long_track_is_analysed_without_holding_it_all_in_float64():
    audio = (
        np.random.default_rng(1).standard_normal((wave_model.RATE * 400, 2)) * 3000
    ).astype(np.int16)
    assert (
        len(wave_model.column_values(audio)) == 100
    )  # 400 s: the whole-track version needed ~4 GB
