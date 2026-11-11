"""Application entry point: `dubcrate-gui` or `python -m dubcrate.ui`."""

import argparse
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from dubcrate.library import Library

from . import theme
from .main_window import MainWindow


def _icon_path() -> Path | None:
    """The app icon, from a checkout's assets/icon (a built app bundles its own)."""
    candidate = Path(__file__).resolve().parents[3] / "assets" / "icon" / "AppIcon.png"
    return candidate if candidate.is_file() else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dubcrate-gui", description="Dubcrate: burn audio CDs for old-style CDJs"
    )
    parser.add_argument(
        "--library", help="library database to use (default: the app data directory)"
    )
    args, qt_args = parser.parse_known_args(sys.argv[1:] if argv is None else argv)

    app = QApplication([sys.argv[0], *qt_args])
    app.setOrganizationName("Dubcrate")
    app.setApplicationName("Dubcrate")
    icon = _icon_path()
    if icon:
        app.setWindowIcon(QIcon(str(icon)))
    theme.apply_to_app(app)

    with Library.open(args.library) as lib:
        window = MainWindow(lib, persist=True)
        window.show()
        return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
