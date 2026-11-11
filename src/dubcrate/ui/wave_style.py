"""Colours for the waveform display, from the theme's palette."""

from PySide6.QtGui import QColor

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


def color(key: str) -> QColor:
    return QColor(theme.current_palette()[key])


def is_mono() -> bool:
    return bool(theme.current_palette().get("usage_mono"))


def phrase_color(label: str, index: int) -> QColor:
    if is_mono():
        return QColor(_PHRASE_MONO[index % 2])
    return QColor(_PHRASE_RGB.get(label.split()[0], "#707070"))


def hot_cue_color(slot: int) -> QColor:
    return QColor(HOT_CUE_RGB[(slot - 1) % len(HOT_CUE_RGB)])
