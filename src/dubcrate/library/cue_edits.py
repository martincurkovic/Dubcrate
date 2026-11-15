"""Memory cues edited in Dubcrate, laid over rekordbox's (Qt-free).

rekordbox's cues are read and never written. A memory cue the user adds, moves or deletes in the player is kept in
Dubcrate's own library instead (`Library.memory_cues` / `set_memory_cues`, keyed by the file) and, for any track
that has such edits, **replaces its memory cues**: `with_edits` is the one place that does it, for the player and
for what Sync writes to the card. Hot cues and loops are always rekordbox's. See `dev_docs/rekordbox-cue-writes.md`.
"""

from collections.abc import Sequence

from dubcrate.analysis import Cue
from dubcrate.library import Library
from dubcrate.library.models import Track

MAX_MEMORY_CUES = 10  # the CDJ-1000MK3 shows ten memory cues a track


def is_memory(cue: Cue) -> bool:
    """A plain memory cue: not a hot cue and not a loop (a memory loop is rekordbox's and is left alone)."""
    return cue.kind == "memory" and cue.slot is None


def memory_list(cues: Sequence[Cue]) -> list[tuple[int, str]]:
    """The memory cues among `cues` as (milliseconds, label), in time order: what editing starts from."""
    return sorted((round(c.time * 1000), c.label) for c in cues if is_memory(c))


def with_edits(lib: Library, track: Track, cues: Sequence[Cue]) -> list[Cue]:
    """`cues` (a track's, from rekordbox) with its memory cues replaced by the edited ones, if it has any edits."""
    edited = lib.memory_cues(track.path)
    if edited is None:
        return list(cues)
    kept = [c for c in cues if not is_memory(c)]
    mine = [Cue("memory", ms / 1000.0, label=label) for ms, label in edited]
    return sorted([*kept, *mine], key=lambda c: c.time)
