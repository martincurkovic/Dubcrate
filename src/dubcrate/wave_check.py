"""Compare the waveforms the CDJ wrote to its card with `wave_model`'s (Qt-free, numpy only).

Every disc the player has played leaves its own waveform records on the card, a free check of the
model on music it was not fitted on: given the disc's audio, `check_file` says for each played track how
close the model comes. Only the columns the player has filled (a track played part way leaves zeros) are
compared, and on the player's own absolute scale (`scale_to_top=False`), since that is what it wrote.

`dubcrate card check-waveforms` is the command. Replaces `wavefit.py`, which assumed a full-range peak or
RMS on 100 columns read high nibble first (all three wrong: `dev_docs/cdj1000mk3-card.md`, "Disc 2").
"""

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from dubcrate import cdj_card as cc
from dubcrate import wave_model


@dataclass(frozen=True)
class Comparison:
    """How close the model is to one track's record, over the columns the player filled."""

    track: int
    columns: int  # compared
    exact: float  # shares of those columns, 0 to 1
    within1: float
    within2: float
    mean_error: float  # in values (2 dB each)
    bias: float  # model minus player: positive when the model reads high
    player_max: int
    model_max: int


def compare(
    track: int, player: Sequence[int], model: Sequence[int]
) -> Comparison | None:
    """The model's 100 values against the player's; None when the player filled none of them."""
    p, m = np.asarray(player), np.asarray(model)
    filled = p > 0
    if not filled.any():
        return None
    d = (m - p)[filled]
    return Comparison(
        track,
        int(filled.sum()),
        float(np.mean(d == 0)),
        float(np.mean(np.abs(d) <= 1)),
        float(np.mean(np.abs(d) <= 2)),
        float(np.mean(np.abs(d))),
        float(np.mean(d)),
        int(p.max()),
        int(m[filled].max()),
    )


def audio_files(folder: Path) -> dict[int, Path]:
    """The audio files in a folder by the track number each name begins with (`1 Audio Track.aiff`,
    `03 Title.flac`): what macOS shows for an audio CD, or an export. The first file wins a number."""
    found: dict[int, Path] = {}
    for path in sorted(folder.iterdir()):
        match = re.match(r"\s*(\d+)", path.name)
        if match and path.is_file() and not path.name.startswith("."):
            found.setdefault(int(match.group(1)), path)
    return found


def check_file(
    file: cc.TrackFile,
    audio_of: Callable[[int], np.ndarray | None],
    tracks: Iterable[int] | None = None,
) -> tuple[list[Comparison], list[str]]:
    """Compare every played track of a disc's file (or just `tracks`); returns (comparisons, notes).

    `audio_of(track)` gives the track's audio as the player decodes it (stereo, 44.1 kHz) or None when
    there is none; a track with no audio, or that the player did not play, is said in the notes."""
    wanted = None if tracks is None else set(tracks)
    found: list[Comparison] = []
    notes: list[str] = []
    for slot, record in enumerate(file.fine):
        track = slot + 1
        if wanted is not None and track not in wanted:
            continue
        if not any(record.data):
            notes.append(f"track {track}: the player has not recorded it")
            continue
        audio = audio_of(track)
        if audio is None:
            notes.append(f"track {track}: no audio to compare with")
            continue
        model = wave_model.column_values(audio, scale_to_top=False)
        if (result := compare(track, record.time_values(), model)) is not None:
            found.append(result)
    return found, notes


def summary(comparisons: Sequence[Comparison]) -> Comparison | None:
    """All the compared columns pooled (each track weighted by its columns); track 0."""
    if not comparisons:
        return None
    n = sum(c.columns for c in comparisons)

    def pooled(field: str) -> float:
        return sum(getattr(c, field) * c.columns for c in comparisons) / n

    return Comparison(
        0,
        n,
        pooled("exact"),
        pooled("within1"),
        pooled("within2"),
        pooled("mean_error"),
        pooled("bias"),
        max(c.player_max for c in comparisons),
        max(c.model_max for c in comparisons),
    )


def describe(comparisons: Sequence[Comparison], notes: Sequence[str] = ()) -> list[str]:
    """A table in words: one line a track, then the pooled line."""
    lines = [
        "track  columns  exact  within1  within2  mean error  bias    max (player/model)"
    ]
    for c in comparisons:
        lines.append(_row(f"{c.track:5d}", c))
    if total := summary(comparisons):
        lines.append(_row("  all", total))
    lines += [f"  {note}" for note in notes]
    return lines


def _row(label: str, c: Comparison) -> str:
    return (
        f"{label}  {c.columns:7d}  {c.exact:5.0%}  {c.within1:7.0%}  {c.within2:7.0%}"
        f"  {c.mean_error:10.2f}  {c.bias:+5.2f}   {c.player_max:2d} / {c.model_max:2d}"
    )
