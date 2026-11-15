"""Analyse tracks' CDJ waveforms, for the card (see `dubcrate.wave_model`).

Like `levels.py`: `measure_waveforms` touches no database, so it is safe on a worker thread; the
result is stored with `Library.set_waveform` on the thread that owns the library. A waveform is
cached on the track until its file changes size. It is analysed from the **source file**: the
model scales a quiet track up (`wave_model.column_values(scale_to_top=True)`), so the waveform does
not depend on the gain a burn applied.
"""

from collections.abc import Callable, Iterable

from dubcrate import wave_model
from dubcrate.audio.decode import DecodeError, decode_file
from dubcrate.cancel import Cancelled, CancelToken, cancellable_map
from dubcrate.library.models import Track
from dubcrate.library.store import Library

Progress = Callable[[int, int], None]  # (done, total)


def needs_waveform(track: Track) -> bool:
    """Whether `track` has no waveform yet and its file can be read."""
    return track.waveform is None and not track.missing


def analyse(path: str) -> list[int]:
    """One file's 100 values. Raises `DecodeError` (an expected error: a message in plain words)."""
    return wave_model.column_values(decode_file(path).samples)


def measure_waveforms(
    tracks: Iterable[Track],
    progress: Progress | None = None,
    workers: int = 2,
    cancel: CancelToken | None = None,
) -> tuple[dict[int, list[int]], list[str]]:
    """Analyse the tracks that need it, a few at a time; returns ({track id: values}, problems).

    Two at a time by default: a track is decoded to memory and analysed a column at a time, but a
    long one still holds a few hundred megabytes while it is. If `cancel` is set it raises `Cancelled`
    whose `partial` is what was analysed so far (still good)."""
    todo = [t for t in tracks if needs_waveform(t)]

    def run(track: Track) -> tuple[Track, list[int] | str]:
        try:
            return track, analyse(track.path)
        except (DecodeError, OSError, ValueError) as exc:
            return track, f"{track.title or track.path}: {exc}"

    found: dict[int, list[int]] = {}
    problems: list[str] = []
    try:
        for done, (track, result) in enumerate(
            cancellable_map(run, todo, workers, cancel), start=1
        ):
            if isinstance(result, str):
                problems.append(result)
            else:
                found[track.id] = result
            if progress:
                progress(done, len(todo))
    except Cancelled:
        raise Cancelled((found, problems)) from None
    return found, problems


def store_waveforms(lib: Library, found: dict[int, list[int]]) -> None:
    for track_id, values in found.items():
        lib.set_waveform(track_id, values)


def ensure_waveforms(
    lib: Library, track_ids: Iterable[int], progress: Progress | None = None
) -> list[str]:
    """Analyse (and record) the tracks among `track_ids` that have no waveform yet. For the command
    line and anything else on the library's own thread; returns messages for files that couldn't be read."""
    found, problems = measure_waveforms(lib.tracks_by_ids(track_ids), progress)
    store_waveforms(lib, found)
    return problems
