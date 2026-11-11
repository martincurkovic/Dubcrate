"""A 3-band waveform (low / mid / high), from rekordbox's analysis or computed from audio.

Rekordbox draws a track as three overlaid shapes: the lows in blue, the mids in
amber, the highs in white. Its analysis files store exactly that, three bytes per
column; tracks without analysis get the same structure computed from their audio,
so a single renderer serves both.

Rekordbox's other two looks, the colour (RGB) and the blue waveform, are derived
from the same three bands, so they work for every track. How each is derived was
fitted to rekordbox's own colour and blue waveforms on 60 real tracks (see
dev_docs/rekordbox-analysis.md): they come out similar, not identical.
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

STYLE_BANDS, STYLE_RGB, STYLE_BLUE = "bands", "rgb", "blue"
STYLES = (STYLE_BANDS, STYLE_RGB, STYLE_BLUE)
# Red is the lows, green the mids, blue the highs. Raw shares over-weight green and
# under-weight blue next to rekordbox's own colours; these gains were fitted to them.
_RGB_GAINS = np.array([1.0, 0.5, 1.4], dtype=np.float32)
# The blue waveform's whiteness is the share of a column that is not bass, stretched
# so that its spread matches rekordbox's (which reaches both ends of 0..1).
_WHITE_FLOOR, _WHITE_SPAN = 0.25, 0.53
_SILENT = 1e-3  # a column with less energy than this has no colour


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


def overall_height(bands: np.ndarray) -> np.ndarray:
    """How tall the single-shape styles draw each column: the RMS of the three bands
    (rekordbox's own height follows it, r = 0.86), at most 1."""
    return np.minimum(1.0, np.sqrt((bands.astype(np.float32) ** 2).sum(axis=1)))


def _shares(bands: np.ndarray) -> np.ndarray:
    total = bands.sum(axis=1, keepdims=True)
    return np.where(total > _SILENT, bands / np.maximum(total, _SILENT), 0.0)


def rgb_colors(bands: np.ndarray) -> np.ndarray:
    """Each column's colour in the RGB style, shape (columns, 3), each 0..1.

    Which bands are in the column picks the hue (lows red, mids green, highs blue);
    how loud it is shows in the height, not the colour. The strongest channel is full.
    """
    weighted = _shares(bands.astype(np.float32)) * _RGB_GAINS
    top = weighted.max(axis=1, keepdims=True)
    return np.where(top > 0, weighted / np.maximum(top, 1e-9), 0.0).astype(np.float32)


def whiteness(bands: np.ndarray) -> np.ndarray:
    """How white each column's core is in the blue style, 0..1: bassy columns stay
    blue, brighter ones whiten."""
    bands = bands.astype(np.float32)
    bass_free = 1.0 - _shares(bands)[:, BAND_LOW]
    white = np.clip((bass_free - _WHITE_FLOOR) / _WHITE_SPAN, 0.0, 1.0)
    return np.where(bands.sum(axis=1) > _SILENT, white, 0.0).astype(np.float32)


def _to_bytes(colors: np.ndarray) -> np.ndarray:
    return np.round(np.clip(colors, 0.0, 1.0) * 255).astype(np.uint8)


def raster_columns(
    heights: np.ndarray,
    body: np.ndarray,
    core: np.ndarray | None,
    *,
    rows: int,
    extent: float,
    mirrored: bool,
    core_fraction: float = 0.55,
) -> np.ndarray:
    """Draw one coloured bar per column as RGBA pixels, shape (rows, columns, 4).

    `heights` (0..1) scale to `extent` rows from the baseline: the floor, or the
    middle row when `mirrored` (bars grow both ways). `body` (columns, 3) is each
    bar's colour; with `core` (columns, 3), the inner `core_fraction` of each bar is
    drawn in that colour instead. Pixels outside every bar are transparent.
    """
    columns = len(heights)
    y = np.arange(rows, dtype=np.float32)[:, None] + 0.5
    distance = np.abs(y - rows / 2) if mirrored else rows - y
    reach = (np.asarray(heights, np.float32) * extent)[None, :]
    image = np.empty((rows, columns, 4), dtype=np.uint8)
    image[..., :3] = _to_bytes(body)[None, :, :]
    if (
        core is not None
    ):  # in uint8 throughout: this runs once per frame in the detail view
        inner = (distance <= reach * core_fraction)[..., None]
        np.copyto(image[..., :3], _to_bytes(core)[None, :, :], where=inner)
    image[..., 3] = (distance <= reach).astype(np.uint8) * 255
    return image
