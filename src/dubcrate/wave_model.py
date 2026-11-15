"""The CDJ-1000MK3's waveform from audio, as the player draws it (approximate; numpy only).

The player writes, for every track it plays, 100 columns of one value each (0 to 15) into the card
(`cdj_card.WaveRecord`). `dev_docs/cdj1000mk3-card.md` ("The waveform test disc, played", "Disc 2") has
what was measured; in short, a column is 1/100 of the track, its value is **2 dB a step of the level of a
bass band** (about 115 to 150 Hz, 12 dB an octave, of the mean of the two channels), taken as a power mean
(exponent 4) over the first 0.7 of the column, with a floor of 2 for anything audible and 1 for near silence, and (by default) the whole track raised so that its loudest
column reaches the top of the view.

**This is a fit, not the player's own rule** (the figures are for `scale_to_top=False`, the player's absolute scale): on nine real-music tracks it is within one value (2 dB) for
76% of columns, within two for 91%, exact for 34%, with a mean error of 1.2 values and a bias of +0.2; on pure tones it
reads about one value high. The corners, the exponent, the window and the offset were fitted on those tracks
(`~/cdj-card/wave-analysis2/`, outside the repository), not checked on another.
What it does not model: the dip (a drop of 6 dB or more makes the next 2 s block read 1), and the sampling
instants, which follow CD frames. **Records we wrote were accepted by a CDJ-1000MK3, drawn on its screen (a sawtooth showed), loaded without the player analysing
the track, and kept when the track was played** (`dev_docs/card-sync.md`, "Writing a waveform", 2026-11-15).
"""

import numpy as np

from dubcrate import cdj_card as cc

RATE = 44_100
COLUMNS = (
    100  # a track's columns (the player's W002 holds one value each; W001 one per two)
)
HIGH_PASS_HZ = 115.0  # two 2nd-order Butterworth sections, as fitted
LOW_PASS_HZ = 150.0
POWER = 4.0  # the power mean's exponent
WINDOW = 0.7  # the part of a column, from its start, that counts
OFFSET_DB = 45.0  # value = floor((level in dB + OFFSET_DB) / 2); 46 gives more exact matches on music but a bias of +0.6
MAX_VALUE = 15
SILENCE_DB = (
    -41.0
)  # a column whose full-range peak is under this reads 1, else at least 2
MAX_BOOST_DB = 24.0  # the most a quiet track is scaled up (12 values): a hiss must not fill the view


def _butterworth2(f: np.ndarray, corner: float, high: bool) -> np.ndarray:
    """The frequency response of a bilinear-transformed 2nd-order Butterworth filter on the grid `f` (Hz)."""
    wc = 2 * RATE * np.tan(np.pi * corner / RATE)
    s = 1j * 2 * RATE * np.tan(np.pi * np.maximum(f, 1e-6) / RATE)
    x = s / wc
    return (
        (x * x) / (1 + np.sqrt(2) * x + x * x)
        if high
        else 1 / (1 + np.sqrt(2) * x + x * x)
    )


def bass_band(audio: np.ndarray) -> np.ndarray:
    """The band-passed mean of the two channels (`audio`: stereo floats in units of full scale)."""
    x = np.asarray(audio, dtype=np.float64)
    mono = (x[:, 0] + x[:, 1]) / 2
    spectrum = np.fft.rfft(mono)
    f = np.fft.rfftfreq(len(mono), 1 / RATE)
    response = _butterworth2(f, HIGH_PASS_HZ, True) * _butterworth2(
        f, LOW_PASS_HZ, False
    )
    return np.fft.irfft(spectrum * response, len(mono))


def column_values(audio: np.ndarray, scale_to_top: bool = True) -> list[int]:
    """The 100 values (1 to 15) the player would show for a track, in time order (the first column first).

    `audio` is the track as the player decodes it: stereo at 44.1 kHz, shape (samples, 2).

    `scale_to_top` raises a track that would not reach the top of the view so that its **loudest column reads
    15** (every value moves up by the same number of 2 dB steps, never more than `MAX_BOOST_DB`, and a track
    that is silent throughout is left alone). A track already at the top is not changed (it is never scaled
    down). That makes the waveform the same whatever gain the burn applied: a track turned down by level
    matching (`loudness.gain_for`, up to 12 dB) is not a thin line. `False` gives the player's absolute scale.
    """
    samples = len(audio)
    if samples < COLUMNS:
        raise ValueError("The track is too short for a waveform.")
    scale = 32768.0 if np.issubdtype(np.asarray(audio).dtype, np.integer) else 1.0
    stereo = np.asarray(audio, dtype=np.float64) / scale
    y = np.abs(bass_band(stereo))
    power = np.concatenate([[0.0], np.cumsum(y**POWER)])
    peak = np.abs(
        (stereo[:, 0] + stereo[:, 1]) / 2
    )  # the mono mix: opposite channels cancel to silence
    levels, floors = [], []
    for k in range(COLUMNS):
        start = round(k * samples / COLUMNS)
        end = max(start + 1, round((k + WINDOW) * samples / COLUMNS))
        mean = (power[min(end, samples)] - power[start]) / max(
            1, min(end, samples) - start
        )
        levels.append(20 * np.log10(max(mean ** (1 / POWER), 1e-9)) + OFFSET_DB)
        column_peak = peak[start : round((k + 1) * samples / COLUMNS)].max(initial=0)
        floors.append(2 if 20 * np.log10(max(column_peak, 1e-9)) > SILENCE_DB else 1)
    boost = 0.0
    if scale_to_top and max(floors) > 1:
        # the step is 2 dB, so the loudest column lands on 15 when it sits exactly at 2 * MAX_VALUE
        boost = float(np.clip(2 * MAX_VALUE - max(levels), 0.0, MAX_BOOST_DB))
    return [
        max(floor, min(MAX_VALUE, int(np.floor((level + boost) / 2))))
        for level, floor in zip(levels, floors)
    ]


def coarse_values(values: list[int]) -> list[int]:
    """The 50 `W001` values for 100 columns: per pair, the level of the mean amplitude in the same 2 dB steps.

    Measured on the player's own records, `round(10 log10((10^(a/10) + 10^(b/10)) / 2))`, equal in 93% of
    pairs and within one in the rest (zero, an unplayed column, counts as zero amplitude)."""
    if len(values) != COLUMNS:
        raise ValueError(f"A waveform is {COLUMNS} values.")
    out = []
    for a, b in zip(values[0::2], values[1::2]):
        amps = [10 ** (v / 10) if v else 0.0 for v in (a, b)]
        mean = sum(amps) / 2
        out.append(int(np.rint(10 * np.log10(mean))) if mean > 0 else 0)
    return out


def pattern_values() -> list[int]:
    """A test pattern nobody could take for music: a sawtooth 1, 2, ... 15, 1, 2, ... (period 15 columns)."""
    return [1 + k % MAX_VALUE for k in range(COLUMNS)]


def records(track: int, values: list[int]) -> tuple[cc.WaveRecord, cc.WaveRecord]:
    """The track's `W001` and `W002` records for 100 columns in time order (0 is a column not played)."""
    coarse = cc.WaveRecord.coarse(track, coarse_values(values))
    fine = cc.WaveRecord.fine_in_time_order(track, values)
    return coarse, fine
