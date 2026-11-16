"""Lengths shown in millimetres, centimetres or inches (pure, no Qt).

One global setting (`settings.get_units`, Settings > Appearance) picks what the app shows: the label window's sizes
and its paper list. **What is stored never changes: it is always millimetres** (`LabelOptions.width_mm` and the
rest), so switching units loses nothing and a value keeps its exact size however often the unit is changed.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Unit:
    key: str  # what is stored in the settings
    name: str  # what the Settings window says
    suffix: str  # after a number
    mm: float  # millimetres in one of them
    decimals: int  # places a spin box shows
    step: float  # what a click on a spin box's arrow adds


MM = Unit("mm", "Millimetres", "mm", 1.0, 1, 1.0)
CM = Unit("cm", "Centimetres", "cm", 10.0, 2, 0.1)
INCH = Unit("in", "Inches", "in", 25.4, 2, 0.05)
UNITS = (MM, CM, INCH)
DEFAULT = MM


def unit(key: str | None) -> Unit:
    """The unit with this key; millimetres for any other (a setting from the future, say)."""
    return next((u for u in UNITS if u.key == key), DEFAULT)


def from_mm(mm: float, u: Unit) -> float:
    return mm / u.mm


def to_mm(value: float, u: Unit) -> float:
    return value * u.mm


def number(mm: float, u: Unit) -> str:
    """A length as a number in `u` with no trailing zeros: `210`, `29.7`, `8.27`."""
    return f"{round(from_mm(mm, u), u.decimals):g}"


def length_text(mm: float, u: Unit) -> str:
    return f"{number(mm, u)} {u.suffix}"


def page_text(name: str, width_mm: float, height_mm: float, u: Unit) -> str:
    """A paper size as the label window's list says it: `A4 (210 × 297 mm)`, `A4 (21 × 29.7 cm)`."""
    return f"{name} ({number(width_mm, u)} × {number(height_mm, u)} {u.suffix})"
