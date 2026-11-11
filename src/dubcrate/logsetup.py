"""The log file (Qt-free): what a user sends along with a bug report.

`dubcrate.log` in the app's log folder (`~/Library/Logs/Dubcrate` on macOS) records what the app
did (start-up details, imports, burns, update checks) and every warning and error, with the
traceback of a bug. A megabyte, three older ones kept. `crash.log` beside it gets the Python
traceback if the program dies in native code (a segfault). Both hold file paths, never audio.

Every module logs through `logging.getLogger(__name__)`, so its logger is a child of "dubcrate"
and lands here. Modelled on akaiutil-GUI's `logsetup.py`.
"""

import faulthandler
import logging
import logging.handlers
import os
import platform
import sys
import threading
from pathlib import Path

from platformdirs import user_log_dir

from dubcrate.ui.version import app_version

LOG_NAME = "dubcrate.log"
CRASH_NAME = "crash.log"
MAX_BYTES = 1_000_000
BACKUPS = 3
FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
CONSOLE_FORMAT = "%(levelname)s %(name)s: %(message)s"

_logger = logging.getLogger("dubcrate")
_handler: logging.Handler | None = None
_console: logging.Handler | None = None
_crash_file = None


def log_dir() -> Path:
    """$DUBCRATE_LOG_DIR if set (the tests use it), else the platform's per-user log folder."""
    override = os.environ.get("DUBCRATE_LOG_DIR")
    return (
        Path(override) if override else Path(user_log_dir("Dubcrate", appauthor=False))
    )


def log_path() -> Path:
    return log_dir() / LOG_NAME


def setup(verbose: bool = False, directory: Path | None = None) -> Path | None:
    """Start logging to the file, and to the console too (a terminal run shows it as it happens).
    Returns the log file, or None if it can't be written: the app carries on without a file
    rather than failing to start."""
    global _handler, _console
    shutdown()
    level = logging.DEBUG if verbose else logging.INFO
    _logger.setLevel(logging.DEBUG)
    _console = logging.StreamHandler()  # stderr
    _console.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    _console.setLevel(level)
    _logger.addHandler(_console)
    folder = Path(directory) if directory is not None else log_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            folder / LOG_NAME, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"
        )
    except OSError:
        _logger.warning(
            "can't write a log file in %s: logging to the console only", folder
        )
        return None
    handler.setFormatter(logging.Formatter(FORMAT))
    handler.setLevel(level)
    _logger.addHandler(handler)
    _handler = handler
    return folder / LOG_NAME


def shutdown() -> None:
    """Stop writing to the file and console (and let go of them: the tests need that)."""
    global _handler, _console
    for handler in (_handler, _console):
        if handler is not None:
            _logger.removeHandler(handler)
            handler.close()
    _handler = _console = None


def is_verbose() -> bool:
    return _handler is not None and _handler.level <= logging.DEBUG


def log_startup() -> None:
    """The first lines of a log: enough to know what ran on what."""
    try:
        from PySide6 import __version__ as pyside
        from PySide6.QtCore import qVersion

        qt = f"PySide6 {pyside}, Qt {qVersion()}"
    except ImportError:
        qt = "PySide6 not importable"
    bundled = bool(getattr(sys, "frozen", False)) or "__compiled__" in globals()
    _logger.info("---- Dubcrate %s starting ----", app_version())
    _logger.info(
        "%s, Python %s, %s", platform.platform(), platform.python_version(), qt
    )
    _logger.info(
        "%s; log file %s", "built app" if bundled else "from source", log_path()
    )


def install_exception_hooks() -> None:
    """Write uncaught exceptions (on any thread, and from Qt slots) to the log with their
    traceback, then carry on as normal."""
    previous = sys.excepthook

    def hook(kind, value, trace):
        _logger.critical("uncaught exception", exc_info=(kind, value, trace))
        previous(kind, value, trace)

    sys.excepthook = hook
    previous_thread = threading.excepthook

    def thread_hook(args):
        _logger.critical(
            "uncaught exception in thread %s",
            getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
        previous_thread(args)

    threading.excepthook = thread_hook


def enable_crash_log(directory: Path | None = None) -> Path | None:
    """If the program dies in native code (a segfault, an abort) Python can't log it, but
    faulthandler can write every thread's traceback to crash.log as it goes down. A header line
    says which run it was."""
    global _crash_file
    folder = Path(directory) if directory is not None else log_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / CRASH_NAME
        if path.exists() and path.stat().st_size > MAX_BYTES:
            path.unlink()  # one file, no rotation: start again rather than grow for ever
        if _crash_file is not None:
            faulthandler.disable()
            _crash_file.close()
        _crash_file = open(path, "a", encoding="utf-8")  # noqa: SIM115  (faulthandler keeps it)
        _crash_file.write(
            f"--- Dubcrate {app_version()} started, {platform.platform()} ---\n"
        )
        _crash_file.flush()
        faulthandler.enable(file=_crash_file, all_threads=True)
        return path
    except (OSError, RuntimeError, ValueError):
        return None
