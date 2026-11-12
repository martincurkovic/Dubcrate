"""Musical keys in the two notations DJs use, and moving between them (pure).

Camelot: `8A` is A minor, `8B` is C major (the wheel: A for minor, B for major, 1 to 12).
Musical: `A min`, `C maj`, with sharps for the black keys (`G# min`, never `Ab min`).

Whatever a source wrote ("8A", "08A", "Am", "A min", "Abm", "G#m", "Db", "F# major") is read
as a pitch and a mode, so a library with a mixture shows one notation. Text that isn't a key
is left alone.
"""

import re

CAMELOT = "camelot"
MUSICAL = "musical"
NOTATIONS = (CAMELOT, MUSICAL)

_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_LETTERS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
# pitch class -> Camelot number, for minor (A) and major (B) keys
_MINOR_NUMBER = {
    8: 1,
    3: 2,
    10: 3,
    5: 4,
    0: 5,
    7: 6,
    2: 7,
    9: 8,
    4: 9,
    11: 10,
    6: 11,
    1: 12,
}
_MAJOR_NUMBER = {
    11: 1,
    6: 2,
    1: 3,
    8: 4,
    3: 5,
    10: 6,
    5: 7,
    0: 8,
    7: 9,
    2: 10,
    9: 11,
    4: 12,
}
_MINOR_PITCH = {n: pc for pc, n in _MINOR_NUMBER.items()}
_MAJOR_PITCH = {n: pc for pc, n in _MAJOR_NUMBER.items()}

_CAMELOT_RE = re.compile(r"^(\d{1,2})\s*([ABab])$")
_MUSICAL_RE = re.compile(r"^([A-Ga-g])\s*([#♯b♭]?)\s*([A-Za-z]*)$")
_MINOR_WORDS = {"m", "min", "minor"}
_MAJOR_WORDS = {"", "maj", "major"}


def parse_key(text: str) -> tuple[int, bool] | None:
    """(pitch class 0-11 with C = 0, is_minor) for a key written either way, else None."""
    text = text.strip()
    match = _CAMELOT_RE.match(text)
    if match:
        number, minor = int(match.group(1)), match.group(2).upper() == "A"
        table = _MINOR_PITCH if minor else _MAJOR_PITCH
        return (table[number], minor) if number in table else None
    match = _MUSICAL_RE.match(text)
    if not match:
        return None
    letter, accidental, mode = match.groups()
    if mode == "M":  # a capital M alone is "major" (a lowercase m is "minor")
        minor = False
    elif mode.lower() in _MINOR_WORDS:
        minor = True
    elif mode.lower() in _MAJOR_WORDS:
        minor = False
    else:
        return None
    shift = 1 if accidental in ("#", "♯") else -1 if accidental in ("b", "♭") else 0
    return (_LETTERS[letter.upper()] + shift) % 12, minor


def camelot(pitch: int, minor: bool) -> str:
    return (
        f"{(_MINOR_NUMBER if minor else _MAJOR_NUMBER)[pitch]}{'A' if minor else 'B'}"
    )


def musical(pitch: int, minor: bool) -> str:
    return f"{_NOTE_NAMES[pitch]} {'min' if minor else 'maj'}"


def display(text: str, notation: str = CAMELOT) -> str:
    """`text` as a key in `notation` ("camelot" or "musical"); anything that isn't a key as it was."""
    parsed = parse_key(text) if text else None
    if parsed is None:
        return text
    return musical(*parsed) if notation == MUSICAL else camelot(*parsed)


def sort_value(text: str | None) -> int:
    """Where a key goes in a sort by key: round the Camelot wheel (1A, 1B, 2A, 2B ...), which
    puts keys that mix well next to each other, and what isn't a key last."""
    parsed = parse_key(text) if text else None
    if parsed is None:
        return 100
    pitch, minor = parsed
    number = (_MINOR_NUMBER if minor else _MAJOR_NUMBER)[pitch]
    return number * 2 + (0 if minor else 1)
