"""Pure disc-packing algorithms, free of any database or file access.

Everything here works on lists of track lengths in sectors and returns indexes
into those lists, so callers map the results back to their own tracks.
"""

from collections.abc import Sequence

from dubcrate.cd import MAX_TRACKS


def fit_tracks(
    lengths: Sequence[int],
    *,
    used: int,
    count: int,
    capacity: int,
    pregap: int = 0,
    max_tracks: int = MAX_TRACKS,
    strict: bool = False,
) -> tuple[list[int], list[int]]:
    """Choose which of `lengths` (in order) still fit on a disc.

    `used` and `count` describe what's already on the disc. Tracks keep their
    relative order. By default a track that doesn't fit is skipped and later,
    shorter ones may still go on; with `strict` the first misfit ends it.
    Returns (accepted indexes, rejected indexes).
    """
    accepted: list[int] = []
    rejected: list[int] = []
    stopped = False
    for index, length in enumerate(lengths):
        cost = length + (pregap if count > 0 else 0)
        if not stopped and count < max_tracks and used + cost <= capacity:
            accepted.append(index)
            used += cost
            count += 1
        else:
            rejected.append(index)
            stopped = stopped or strict
    return accepted, rejected


def split_sequential(
    lengths: Sequence[int],
    *,
    capacity: int,
    pregap: int = 0,
    max_tracks: int = MAX_TRACKS,
) -> tuple[list[list[int]], list[int]]:
    """Cut `lengths` into consecutive discs, keeping order.

    A new disc starts whenever the next track won't fit. Tracks that are longer
    than an empty disc can never fit and are returned separately.
    Returns (groups of indexes, too-long indexes).
    """
    groups: list[list[int]] = []
    too_long: list[int] = []
    current: list[int] = []
    used = 0
    for index, length in enumerate(lengths):
        if length > capacity:
            too_long.append(index)
            continue
        cost = length + (pregap if current else 0)
        if current and (used + cost > capacity or len(current) >= max_tracks):
            groups.append(current)
            current, used, cost = [], 0, length
        current.append(index)
        used += cost
    if current:
        groups.append(current)
    return groups, too_long
