"""Stopping a long job part of the way (Qt-free).

A job that works through many files (an import, measuring lengths or loudness, analysing waveforms)
is given a `CancelToken` and looks at it between files. The GUI's Cancel button sets it. A cancelled job
raises `Cancelled`, carrying **what it had finished** (`partial`): a length that was measured or a waveform that was
analysed is as good as one from a job that ran to the end, so the caller can keep them, while an import,
whose half is no use, just drops it. Nothing is ever half-stored: jobs touch no database, and the GUI thread stores
a job's results only once the job has handed them over.

`cancellable_map` is the shared loop: a thread pool whose results come back in order, that stops handing
out work the moment the token is set (the files already being read finish: each takes a fraction of a second).
"""

import threading
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any


class Cancelled(Exception):
    """The job was stopped by the user. `partial` is what it had finished, if it has anything to keep."""

    def __init__(self, partial: Any = None):
        super().__init__("cancelled")
        self.partial = partial


class CancelToken:
    """A flag a job checks between files and the GUI sets from its Cancel button. Safe across threads."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._committed = False

    def cancel(self) -> bool:
        """Ask the job to stop. False if it is too late (the job has `commit`ted: it can no longer be
        stopped), True if the job will stop."""
        with self._lock:
            if self._committed:
                return False
            self._event.set()
            return True

    def commit(self) -> None:
        """The job is about to do something that can't be undone or stopped (writing a disc): from here
        `cancel` does nothing. Raises `Cancelled` instead if the user already asked to stop, so a Cancel
        pressed at the last moment is never ignored half way."""
        with self._lock:
            if self._event.is_set():
                raise Cancelled()
            self._committed = True

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self, partial: Any = None) -> None:
        """Raise `Cancelled` if the user asked to stop."""
        if self._event.is_set():
            raise Cancelled(partial)


def cancellable_map[T, R](
    fn: Callable[[T], R],
    items: Iterable[T],
    workers: int,
    cancel: CancelToken | None = None,
) -> Iterator[R]:
    """`fn` over `items` on a thread pool, the results in order as they are ready.

    Once `cancel` is set, no further item is started and `Cancelled` is raised from the iterator (the
    caller catches it to hand back what it has). An exception from `fn` is raised in order, as `map` does."""
    queue = list(items)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fn, item) for item in queue]
        try:
            for future in futures:
                if cancel is not None:
                    cancel.check()
                yield future.result()
        finally:
            for future in futures:
                future.cancel()  # (the ones not started: a cancelled or failed job leaves no queue behind)
