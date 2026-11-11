"""Application entry point: `dubcrate-gui` or `python -m dubcrate.ui`."""

import argparse
import sys
from importlib import metadata
from pathlib import Path

from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication

from dubcrate.library import Library

from . import theme
from .main_window import MainWindow
from .splash import StartupSplash
from .startup import maybe_refresh


def _icon_path() -> Path | None:
    """The app icon, from a checkout's assets/icon (a built app bundles its own)."""
    candidate = Path(__file__).resolve().parents[3] / "assets" / "icon" / "AppIcon.png"
    return candidate if candidate.is_file() else None


def _version() -> str:
    try:
        return metadata.version("dubcrate")
    except metadata.PackageNotFoundError:
        return "development"


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
        "--no-import",
        action="store_true",
        help="don't bring the rekordbox library up to date on startup",
    )
    args, qt_args = parser.parse_known_args(sys.argv[1:] if argv is None else argv)

    app = QApplication([sys.argv[0], *qt_args])
    app.setOrganizationName("Dubcrate")
    app.setApplicationName("Dubcrate")
    icon = _icon_path()
    if icon:
        app.setWindowIcon(QIcon(str(icon)))
    # The splash goes up first, before the (slower) theme and window: it has its own look and
    # says what is going on while the library opens and the rekordbox library is updated.
    splash = StartupSplash(
        _version(),
        QPixmap(str(icon)) if icon else None,
        activating=not _is_bundled(),  # from source it brings itself forward, as a splash won't
    )
    splash.show()
    splash.set_status("Opening your library…")
    theme.apply_to_app(app)

    with Library.open(args.library) as lib:
        imported = maybe_refresh(lib, splash.set_status, skip=args.no_import)
        splash.set_status("Loading Dubcrate…")
        window = MainWindow(lib, persist=True)
        window.show()
        splash.finish()
        if imported is not None and imported.message():
            window.statusBar().showMessage(imported.message(), 15000)
        return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
