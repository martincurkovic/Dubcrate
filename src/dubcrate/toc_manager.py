"""The TOC Manager's rules (Qt-free): which disc's TOC may be recorded for which playlist.

The CDJ's card knows a disc by its TOC, and a playlist can have more than one physical copy with a slightly different
one (another machine, another gap setting, another burning program). `Library.add_disc_toc` keeps each copy's TOC
(`card_discs.known_tocs` hands them to Sync). A TOC read from the drive is accepted for a playlist only when it is
plausibly the same disc: the same number of tracks and every track's LENGTH close to the planned one.

Lengths, not positions: a gap before a track makes the track before it longer and moves every later start, so a disc
burned with 2 s gaps ends a half minute later than a gapless one (17 tracks) and absolute positions would turn
exactly the discs this is for away. A different CD with the same number of tracks misses by tens of seconds, on
several tracks.
"""

from collections.abc import Sequence

from dubcrate import cdj_card as cc
from dubcrate.card_discs import Toc, disc_toc
from dubcrate.card_report import msf
from dubcrate.library import Library

# a track may be this much shorter than planned (a drive rounding), or longer (the gap put before the next track:
# DiscRecording's own default is 2 s)
SHORTER_FRAMES = 38  # half a second
LONGER_FRAMES = 4 * cc.FRAMES_PER_SECOND


class TocError(RuntimeError):
    """An expected problem, in plain words: it is shown to the user as it is."""


def lengths(toc: tuple[Sequence[int], int]) -> list[int]:
    starts, lead_out = toc
    return [b - a for a, b in zip(starts, [*starts[1:], lead_out], strict=True)]


def candidate_problems(planned: Toc, actual: tuple[Sequence[int], int]) -> list[str]:
    """Why `actual` (read from a disc) can't be a copy of the disc planned as `planned`; empty when it can."""
    want, found = planned[0], actual[0]
    if len(want) != len(found):
        return [
            f"The disc in the drive has {len(found)} tracks and this playlist {len(want)}: it is not a copy of it."
        ]
    problems = []
    if not 0 <= found[0] - want[0] <= LONGER_FRAMES:
        problems.append(f"track 1 starts at {msf(found[0])}, expected {msf(want[0])}")
    for n, (planned_len, real) in enumerate(
        zip(lengths(planned), lengths(actual), strict=True), 1
    ):
        if not -SHORTER_FRAMES <= real - planned_len <= LONGER_FRAMES:
            problems.append(
                f"track {n} is {_seconds(real)} long, planned {_seconds(planned_len)}"
            )
    if problems:
        problems[0:0] = [
            "That disc's track lengths are too different from this playlist's to be a copy of it:"
        ]
    return problems


def _seconds(frames: int) -> str:
    return f"{frames / cc.FRAMES_PER_SECOND:.1f} s"


def planned_toc(lib: Library, disc_id: int) -> Toc:
    """The TOC the playlist would have as planned now. Needs every track's length measured."""
    summary = lib.disc(disc_id)
    if summary.kind != "audio":
        raise TocError("Only audio discs have a TOC.")
    entries = lib.disc_entries(disc_id)
    if len(entries) < 2:
        raise TocError(
            "A disc of one track can't be synced, so it has no TOC to record."
        )
    if unmeasured := [e for e in entries if not e.track.length_exact]:
        raise TocError(
            f"{len(unmeasured)} of {len(entries)} tracks have no measured length "
            "(their files may be missing)."
        )
    return disc_toc(entries, summary.pregap_sectors)


def record(lib: Library, disc_id: int, actual: tuple[Sequence[int], int]) -> Toc:
    """Check a TOC read from a disc against the playlist and keep it; raises `TocError` with the reasons if it is
    not plausibly a copy. Returns the playlist's planned TOC."""
    planned = planned_toc(lib, disc_id)
    if problems := candidate_problems(planned, actual):
        raise TocError("\n".join([problems[0], *("  " + p for p in problems[1:6])]))
    lib.add_disc_toc(disc_id, planned, actual, source="manual")
    return planned
