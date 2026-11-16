"""Memory cues edited in Dubcrate, laid over rekordbox's (Qt-free).

rekordbox's cues are read and never written. A memory cue the user adds, moves or deletes in the player is kept in
Dubcrate's own library instead (`Library.memory_cues` / `set_memory_cues`, keyed by the file) and, for any track
that has such edits, **replaces its memory cues**: `with_edits` is the one place that does it, for the player and
for what Sync writes to the card. Hot cues are always rekordbox's. **Memory loops saved in Dubcrate** (MEMORY with a
loop on: `Library.memory_loops`) are *added* to a track's cues and never replace rekordbox's loops; they are kept in
their own table and are not written to rekordbox. See `dev_docs/rekordbox-cue-writes.md`.
"""

from collections.abc import Sequence

from dubcrate.analysis import Cue
from dubcrate.library import Library
from dubcrate.library.models import Track

MAX_MEMORY_CUES = 10  # the CDJ-1000MK3 shows ten memory cues a track (loops are counted in it: see `memory_count`)


def is_memory(cue: Cue) -> bool:
    """A plain memory cue: not a hot cue and not a loop (a memory loop is rekordbox's and is left alone)."""
    return cue.kind == "memory" and cue.slot is None


def memory_list(cues: Sequence[Cue]) -> list[tuple[int, str]]:
    """The memory cues among `cues` as (milliseconds, label), in time order: what editing starts from."""
    return sorted((round(c.time * 1000), c.label) for c in cues if is_memory(c))


def is_memory_loop(cue: Cue) -> bool:
    """A loop that is not a hot cue's: a memory loop, rekordbox's or saved in Dubcrate."""
    return cue.kind == "loop" and cue.slot is None


def memory_count(cues: Sequence[Cue]) -> int:
    """How many of the ten memory places a track's `cues` use: its memory cues and its memory loops (the card
    keeps both as the same kind of entry, and the manual only says how many points a disc can hold)."""
    return sum(1 for c in cues if is_memory(c) or is_memory_loop(c))


def with_edits(lib: Library, track: Track, cues: Sequence[Cue]) -> list[Cue]:
    """`cues` (a track's, from rekordbox) with its memory cues replaced by the edited ones, if it has any edits."""
    edited = lib.memory_cues(track.path)
    loops = lib.memory_loops(track.path)
    if edited is None and not loops:
        return list(cues)
    kept = list(cues) if edited is None else [c for c in cues if not is_memory(c)]
    mine = [Cue("memory", ms / 1000.0, label=label) for ms, label in edited or ()]
    saved = [
        Cue("loop", start / 1000.0, end=end / 1000.0, label=label, local=True)
        for start, end, label in loops
        if not any(
            _is_same_loop(c, start, end) for c in kept
        )  # (rekordbox has it by now)
    ]
    return sorted([*kept, *mine, *saved], key=lambda c: c.time)


SAME_LOOP_MS = (
    50  # a loop whose start and end are both this close to another's is the same loop
)


def _is_same_loop(cue: Cue, start_ms: int, end_ms: int) -> bool:
    return (
        is_memory_loop(cue)
        and cue.end is not None
        and abs(cue.time * 1000 - start_ms) < SAME_LOOP_MS
        and abs(cue.end * 1000 - end_ms) < SAME_LOOP_MS
    )
