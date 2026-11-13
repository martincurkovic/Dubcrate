"""Measure how long tracks are on a CD, so disc capacity can be exact."""

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dubcrate.cd import measured_sectors
from dubcrate.library.models import Track
from dubcrate.library.store import Library
from dubcrate.render import RenderError, ToolMissingError, probe

Progress = Callable[[int, int], None]  # (done, total)


def needs_measuring(track: Track) -> bool:
    """Whether `track` has no measured CD length yet and its file can be probed."""
    return track.cd_sectors is None and not track.missing


def probe_lengths(
    tracks: Iterable[Track], progress: Progress | None = None, workers: int = 8
) -> tuple[dict[int, int], list[str]]:
    """Probe tracks in parallel; returns ({track id: sectors}, problem messages).

    Touches no database, so it is safe to run on a worker thread. Store the
    result with `Library.set_cd_sectors` on the thread that owns the library.
    """
    todo = [t for t in tracks if needs_measuring(t)]

    def run(track: Track) -> tuple[Track, int | str]:
        try:
            return track, measured_sectors(probe(Path(track.path)).duration)
        except ToolMissingError:
            raise
        except (RenderError, OSError, ValueError) as exc:
            return track, str(exc)

    lengths: dict[int, int] = {}
    problems: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for done, (track, result) in enumerate(pool.map(run, todo), start=1):
            if isinstance(result, str):
                problems.append(f"{track.path}: {result}")
            else:
                lengths[track.id] = result
            if progress:
                progress(done, len(todo))
    return lengths, problems


def measure_tracks(
    lib: Library,
    track_ids: Iterable[int],
    progress: Progress | None = None,
    workers: int = 8,
) -> list[str]:
    """Probe tracks that have no measured CD length yet and record it.

    Tracks whose file is missing are left on their conservative estimate.
    Returns messages for files that couldn't be read.
    """
    lengths, problems = probe_lengths(
        lib.tracks_by_ids(dict.fromkeys(track_ids)), progress, workers
    )
    for track_id, sectors in lengths.items():
        lib.set_cd_sectors(track_id, sectors)
    return problems
