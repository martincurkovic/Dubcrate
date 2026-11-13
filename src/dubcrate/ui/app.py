"""Application entry point: `dubcrate-gui` or `python -m dubcrate.ui`."""

import argparse
import logging
import os
import sys

from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication

from dubcrate import logsetup, tools
from dubcrate.library import Library

from . import dialog_log, spinbox_drag, theme
from .main_window import MainWindow
from .splash import StartupSplash
from .startup import maybe_refresh
from .tools_dialog import MissingToolsDialog
from .version import app_version, icon_path

log = logging.getLogger(__name__)

# Where Homebrew puts ffmpeg (Apple Silicon, Intel). An app started from Finder or the Dock is
# given only /usr/bin:/bin:/usr/sbin:/sbin, so without these it can't find the tools it runs
# (when the app has no bundled copy: `tools.find_tool` looks in the bundle first).
TOOL_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")


def extend_tool_path(path: str) -> str:
    """`path` with the usual tool folders added at the end: the user's own order still wins."""
    folders = [folder for folder in path.split(os.pathsep) if folder]
    folders += [folder for folder in TOOL_DIRS if folder not in folders]
    return os.pathsep.join(folders)


def _is_bundled() -> bool:
    """A built app (not a run from source)."""
    return bool(getattr(sys, "frozen", False)) or "__compiled__" in globals()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dubcrate-gui", description="Dubcrate: burn audio CDs for old-style CDJs"
    )
    parser.add_argument(
        "--library", help="library database to use (default: the app data directory)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="log fine detail too (to the log file and the terminal)",
    )
    parser.add_argument(
        "--no-import",
        action="store_true",
        help="don't bring the rekordbox library up to date on startup",
    )
    argv = sys.argv[1:] if argv is None else argv
    if sys.platform == "darwin":
        os.environ["PATH"] = extend_tool_path(os.environ.get("PATH", ""))
    smoke = next(
        (a for a in argv if a == "--smoke-test" or a.startswith("--smoke-test=")), None
    )
    if smoke is not None:  # before anything touches your settings, library or a drive
        from . import smoke_test

        return smoke_test.run(smoke.partition("=")[2] or None)
    args, qt_args = parser.parse_known_args(argv)

    logsetup.setup(verbose=args.verbose)
    logsetup.install_exception_hooks()
    logsetup.enable_crash_log()
    logsetup.log_startup()
    try:
        return _run(args, qt_args)
    except Exception:
        log.critical("Dubcrate failed to start or run", exc_info=True)
        raise
    finally:
        log.info("---- Dubcrate exited ----")
        logsetup.shutdown()


def _run(args: argparse.Namespace, qt_args: list[str]) -> int:
    app = QApplication([sys.argv[0], *qt_args])
    app.setOrganizationName("Dubcrate")
    app.setApplicationName("Dubcrate")
    icon = icon_path()
    if icon:
        app.setWindowIcon(QIcon(str(icon)))
    # The splash goes up first, before the (slower) theme and window: it has its own look and
    # says what is going on while the library opens and the rekordbox library is updated.
    splash = StartupSplash(
        app_version(),
        QPixmap(str(icon)) if icon else None,
        activating=not _is_bundled(),  # from source it brings itself forward, as a splash won't
    )
    splash.show()
    splash.set_status("Opening your library…")
    theme.apply_to_app(app)
    spinbox_drag.install(
        app
    )  # every spin box drags like a knob (one filter, remove this line to undo)
    dialog_log.install()

    with Library.open(args.library) as lib:
        lib.prune_explorer()  # what the Explorer read last time is only a cache (discs keep theirs)
        imported = maybe_refresh(lib, splash.set_status, skip=args.no_import)
        splash.set_status("Checking tools…")
        missing = tools.missing_tools()
        splash.set_status("Loading Dubcrate…")
        window = MainWindow(lib, persist=True, update_check=True)
        window.show()
        splash.finish()
        if imported is not None and imported.message():
            window.statusBar().showMessage(imported.message(), 15000)
        if missing:
            MissingToolsDialog(missing, window).exec()
        return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
