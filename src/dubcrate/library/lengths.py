"""Measure how long tracks are on a CD, so disc capacity can be exact."""

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dubcrate.cd import measured_sectors
from dubcrate.library.models import Track
from dubcrate.library.store import Library
from dubcrate.render import RenderError, probe

Progress = Callable[[int, int], None]  # (done, total)


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
    todo = [
        t
        for t in lib.tracks_by_ids(dict.fromkeys(track_ids))
        if t.cd_sectors is None and not t.missing
    ]

    def run(track: Track) -> tuple[Track, int | str]:
        try:
            return track, measured_sectors(probe(Path(track.path)).duration)
        except (RenderError, OSError, ValueError) as exc:
            return track, str(exc)

    problems: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for done, (track, result) in enumerate(pool.map(run, todo), start=1):
            if isinstance(result, str):
                problems.append(f"{track.path}: {result}")
            else:
                lib.set_cd_sectors(track.id, result)
            if progress:
                progress(done, len(todo))
    return problems
