"""Measure the loudness of tracks, for level matching (see `dubcrate.loudness`)."""

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dubcrate.library.models import Track
from dubcrate.library.store import Library
from dubcrate.loudness import Loudness, measure
from dubcrate.render import RenderError, ToolMissingError

Progress = Callable[[int, int], None]  # (done, total)


def needs_loudness(track: Track) -> bool:
    """Whether `track` has not been measured yet and its file can be read."""
    return track.loudness is None and not track.missing


def measure_loudness(
    tracks: Iterable[Track], progress: Progress | None = None, workers: int = 4
) -> tuple[dict[int, Loudness], list[str]]:
    """Measure the tracks that need it, in parallel; returns ({track id: loudness}, problems).

    Touches no database, so it is safe on a worker thread. Store the result with
    `Library.set_loudness` on the thread that owns the library (`store_loudness`).
    """
    todo = [t for t in tracks if needs_loudness(t)]

    def run(track: Track) -> tuple[Track, Loudness | str]:
        try:
            return track, measure(Path(track.path))
        except ToolMissingError:
            raise
        except (RenderError, OSError) as exc:
            return track, str(exc)

    found: dict[int, Loudness] = {}
    problems: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for done, (track, result) in enumerate(pool.map(run, todo), start=1):
            if isinstance(result, str):
                problems.append(result)
            else:
                found[track.id] = result
            if progress:
                progress(done, len(todo))
    return found, problems


def store_loudness(lib: Library, found: dict[int, Loudness]) -> None:
    for track_id, loud in found.items():
        lib.set_loudness(track_id, loud.lufs, loud.true_peak_db)


def ensure_loudness(
    lib: Library, track_ids: Iterable[int], progress: Progress | None = None
) -> list[str]:
    """Measure (and record) the tracks among `track_ids` that have no loudness yet. For the command
    line and anything else on the library's own thread; returns messages for files that couldn't be read."""
    found, problems = measure_loudness(lib.tracks_by_ids(track_ids), progress)
    store_loudness(lib, found)
    return problems
