"""Colours and looks for the waveform display.

The waveform reads its colours through `palette()`, not straight from the theme, so
that a light theme can have a light waveform or (a setting) keep rekordbox's dark one.
The look (3-band, RGB or blue) is a setting too; changing either emits
`notifier.changed` so every waveform on screen redraws.
"""

import numpy as np
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QImage

from dubcrate import settings
from dubcrate.waveform import (
    STYLE_BANDS,
    STYLE_BLUE,
    STYLE_RGB,
    overall_height,
    raster_columns,
    rgb_colors,
    whiteness,
)

from . import theme

# Phrase colours follow rekordbox's: intro orange-red, chorus green, down olive, up purple, outro slate.
_PHRASE_RGB = {
    "INTRO": "#d4552b",
    "CHORUS": "#4aa83a",
    "DOWN": "#a58a3b",
    "UP": "#8a3fd1",
    "OUTRO": "#6f87b8",
    "BRIDGE": "#2f8fa3",
    "VERSE": "#c0a03a",
}
_PHRASE_MONO = (
    "#8c8c8c",
    "#cfcfcf",
)  # System 6 has no colour: neighbours alternate between two greys
HOT_CUE_RGB = (
    "#e8426e",
    "#3fa0e8",
    "#3fc060",
    "#a050e0",
    "#e0a030",
    "#30c0c0",
    "#e06030",
    "#909090",
)


WAVE_STYLE_LABELS = (("3-Band", "bands"), ("RGB", "rgb"), ("Blue", "blue"))
WAVE_BACKGROUND_LABELS = (("Match the theme", "theme"), ("Always dark", "dark"))


class _Notifier(QObject):
    changed = Signal()  # the waveform look or background was changed in Settings


notifier = _Notifier()

_LIGHT_RGB_DIM = 0.8  # the RGB style's colours on a light background: bright yellow vanishes on white
_TARGET_PERCENTILE = (
    98  # the loudest column the strip scales to, as the overview always did
)


def is_mono() -> bool:
    return bool(theme.current_palette().get("usage_mono"))


def palette() -> dict:
    """The palette the waveform's colours come from: the theme's, unless the user
    wants it dark whatever the theme. System 6 is black and white: it keeps its own."""
    if is_mono():
        return theme.current_palette()
    if settings.get_wave_background() == "dark":
        return theme.DARK_PALETTE
    return theme.current_palette()


def color(key: str) -> QColor:
    return QColor(palette()[key])


def is_light() -> bool:
    return color("wave_bg").lightness() > 128


def current_style() -> str:
    """The look in use. System 6 has no colour, so it always draws the 3-band one."""
    return STYLE_BANDS if is_mono() else settings.get_wave_style()


def set_style(style: str) -> None:
    if style != settings.get_wave_style():
        settings.set_wave_style(style)
        notifier.changed.emit()


def set_background(mode: str) -> None:
    if mode != settings.get_wave_background():
        settings.set_wave_background(mode)
        notifier.changed.emit()


def cache_token() -> tuple:
    """Changes whenever anything that affects how a waveform is drawn does."""
    return (
        current_style(),
        settings.get_wave_background(),
        id(theme.current_palette()),
    )


def normalized(bands: np.ndarray, style: str) -> np.ndarray:
    """`bands` scaled so the loud parts fill the strip (a whole track, averaged, is quiet).

    The 3-band look scales on its loudest band, the other two on the overall height
    they draw; a stray peak (above the 98th percentile) is allowed to clip.
    """
    if not len(bands):
        return bands
    level = bands.max(axis=1) if style == STYLE_BANDS else overall_height(bands)
    loudest = float(np.percentile(level, _TARGET_PERCENTILE))
    if loudest > 0.05:
        return np.clip(bands / loudest, 0.0, 1.0)
    return bands


def _rgb(key: str) -> np.ndarray:
    c = color(key)
    return np.array([c.redF(), c.greenF(), c.blueF()], dtype=np.float32)


def style_columns(bands: np.ndarray, style: str):
    """(heights, body colours, core colours or None) for the RGB and blue looks."""
    heights = overall_height(bands)
    if style == STYLE_RGB:
        body = rgb_colors(bands)
        if is_light():
            body = body * _LIGHT_RGB_DIM
        return heights, body, None
    if style == STYLE_BLUE:
        body = np.broadcast_to(_rgb("wave_blue"), (len(bands), 3))
        core = body + (_rgb("wave_blue_core") - body) * whiteness(bands)[:, None]
        return heights, body, core
    raise ValueError(f"not a single-shape style: {style!r}")


def render_columns(
    bands: np.ndarray, style: str, *, rows: int, extent: float, mirrored: bool
) -> QImage:
    """The RGB or blue look as an image, one pixel column per row of `bands`."""
    heights, body, core = style_columns(bands, style)
    pixels = np.ascontiguousarray(
        raster_columns(heights, body, core, rows=rows, extent=extent, mirrored=mirrored)
    )
    image = QImage(
        pixels.data,
        pixels.shape[1],
        pixels.shape[0],
        pixels.shape[1] * 4,
        QImage.Format.Format_RGBA8888,
    )
    return image.copy()  # the copy owns its pixels: `pixels` is about to go


def phrase_color(label: str, index: int) -> QColor:
    if is_mono():
        return QColor(_PHRASE_MONO[index % 2])
    return QColor(_PHRASE_RGB.get(label.split()[0], "#707070"))


def hot_cue_color(slot: int) -> QColor:
    return QColor(HOT_CUE_RGB[(slot - 1) % len(HOT_CUE_RGB)])
