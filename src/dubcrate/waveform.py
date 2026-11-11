"""A 3-band waveform (low / mid / high), from rekordbox's analysis or computed from audio.

Rekordbox draws a track as three overlaid shapes: the lows in blue, the mids in
amber, the highs in white. Its analysis files store exactly that, three bytes per
column; tracks without analysis get the same structure computed from their audio,
so a single renderer serves both.
"""

from dataclasses import dataclass

import numpy as np

from dubcrate.audio.devices import SAMPLE_RATE

COLUMNS_PER_SECOND = 150  # rekordbox's detail waveform rate; ours matches
PREVIEW_COLUMNS = 1200  # rekordbox's whole-track preview
BAND_LOW, BAND_MID, BAND_HIGH = 0, 1, 2
_BANDS_HZ = ((20, 200), (200, 2_000), (2_000, 20_000))  # low, mid, high
_WINDOW = (
    2048  # 21.5 Hz per bin at 44.1 kHz: enough resolution to separate the bass band
)
_CHUNK = 2048  # columns per FFT batch, to bound memory
_GAMMA = 0.7  # lifts quiet passages so they stay visible, as the rekordbox display does
_SCALE_PERCENTILE = (
    99.5  # louder than this clips; one stray peak shouldn't flatten the rest
)
_RAW_MAX = 127.0  # rekordbox stores each band as 0..127


@dataclass(frozen=True)
class Waveform:
    bands: np.ndarray  # float32, shape (columns, 3), each 0..1
    columns_per_second: float

    @property
    def columns(self) -> int:
        return len(self.bands)

    @property
    def duration(self) -> float:
        return (
            self.columns / self.columns_per_second if self.columns_per_second else 0.0
        )

    def resampled(self, columns: int, how: str = "max") -> "Waveform":
        """This waveform at `columns` columns.

        Shrinking takes the loudest of each group (`how="max"`, which keeps every
        peak) or their average (`"mean"`, which reads better for a whole track).
        Growing repeats columns.
        """
        if columns <= 0 or self.columns == 0:
            return Waveform(np.zeros((0, 3), np.float32), self.columns_per_second)
        rate = self.columns_per_second * columns / self.columns
        if columns >= self.columns:
            index = np.arange(columns) * self.columns // columns
            return Waveform(self.bands[index], rate)
        edges = np.linspace(0, self.columns, columns + 1).astype(int)
        edges[1:] = np.maximum(edges[1:], edges[:-1] + 1)  # never an empty group
        if how == "mean":
            counts = np.diff(edges)[
                :, None
            ]  # edges ends at self.columns, so the last group is counted too
            return Waveform(
                (np.add.reduceat(self.bands, edges[:-1], axis=0) / counts).astype(
                    np.float32
                ),
                rate,
            )
        return Waveform(np.maximum.reduceat(self.bands, edges[:-1], axis=0), rate)


def from_bytes(raw: bytes, columns_per_second: float) -> Waveform:
    """A waveform from rekordbox's 3-band bytes (low, mid, high per column)."""
    usable = len(raw) - len(raw) % 3
    data = np.frombuffer(raw[:usable], dtype=np.uint8).reshape(-1, 3).astype(np.float32)
    return Waveform(np.clip(data / _RAW_MAX, 0.0, 1.0), columns_per_second)


def compute_waveform(
    mono: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    columns_per_second: float = COLUMNS_PER_SECOND,
) -> Waveform:
    """The 3-band waveform of float32 mono audio, one column per 1/`columns_per_second` s.

    Each column is the energy in the low, mid and high band around that moment.
    All bands share one scale, so their relative levels (big bass, small highs)
    survive, as in rekordbox's own.
    """
    hop = max(1, round(sample_rate / columns_per_second))
    columns = max(1, len(mono) // hop)
    pad = (_WINDOW - hop) // 2
    padded = np.pad(mono.astype(np.float32), (pad, _WINDOW + hop))
    windowed = np.lib.stride_tricks.sliding_window_view(padded, _WINDOW)
    taper = np.hanning(_WINDOW).astype(np.float32)
    bins = np.fft.rfftfreq(_WINDOW, 1.0 / sample_rate)
    masks = [(bins >= lo) & (bins < hi) for lo, hi in _BANDS_HZ]
    out = np.zeros((columns, 3), dtype=np.float32)
    for start in range(0, columns, _CHUNK):
        stop = min(columns, start + _CHUNK)
        spectrum = np.fft.rfft(windowed[start * hop : stop * hop : hop] * taper, axis=1)
        power = spectrum.real**2 + spectrum.imag**2
        for band, mask in enumerate(masks):
            out[start:stop, band] = np.sqrt(power[:, mask].sum(axis=1))
    peak = float(np.percentile(out.max(axis=1), _SCALE_PERCENTILE))
    if peak <= 0:
        return Waveform(np.zeros_like(out), columns_per_second)
    return Waveform(np.clip(out / peak, 0.0, 1.0) ** _GAMMA, columns_per_second)
