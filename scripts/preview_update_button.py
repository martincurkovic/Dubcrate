"""Open Dubcrate's real window with a pretend update available, to see the status bar's button.

Nothing is fetched and nothing is saved: the update checker is told it found a release, exactly
as it would after reading GitHub's list, so the "Update available" button and the notice it opens
are the real ones. Useful while the repository is private (GitHub answers 404, so a real check
finds nothing).

    uv run scripts/preview_update_button.py                       # a scratch library, dark theme
    uv run scripts/preview_update_button.py --theme light --version 0.3.0
    uv run scripts/preview_update_button.py --dialog              # also open the notice at once
    uv run scripts/preview_update_button.py --library ~/some.db   # look at it with your own data
    uv run scripts/preview_update_button.py --screenshot /tmp/bar.png   # no window: save a picture
"""

import argparse
import os
import sys
from pathlib import Path

NOTES = """## What's new

- A **pretend** release, made by `scripts/preview_update_button.py`.
- Nothing was downloaded, and nothing will be.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--version",
        default="9.9.9",
        help="the version to pretend is out (default 9.9.9)",
    )
    parser.add_argument("--theme", choices=["dark", "light", "system6"], default="dark")
    parser.add_argument(
        "--library", help="a library database to open (default: an empty scratch one)"
    )
    parser.add_argument(
        "--dialog", action="store_true", help="also open the update notice"
    )
    parser.add_argument("--message", default="Added 2 tracks to “Disc 2”", help="a status message to show beside it")  # fmt: skip
    parser.add_argument("--screenshot", metavar="PNG", help="save the window to this file and exit (offscreen)")  # fmt: skip
    args = parser.parse_args(argv)

    if args.screenshot:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"  # (before Qt starts)

    from PySide6.QtWidgets import QApplication

    from dubcrate.library import Library
    from dubcrate.ui import theme
    from dubcrate.ui.main_window import MainWindow
    from dubcrate.updates import UpdateInfo

    app = QApplication([sys.argv[0]])
    app.setOrganizationName("Dubcrate")
    app.setApplicationName("Dubcrate")
    theme.apply_to_app(
        app, args.theme
    )  # (this look only: the saved choice is left alone)

    info = UpdateInfo(
        version=args.version,
        tag=f"v{args.version}",
        name=f"Dubcrate {args.version}",
        html_url=f"https://github.com/martincurkovic/Dubcrate/releases/tag/v{args.version}",
        notes=NOTES,
    )
    with Library.open(args.library or ":memory:") as lib:
        window = MainWindow(lib)  # (no persist: your window layout isn't touched)
        window.resize(1200, 760)
        window.show()
        # what UpdateChecker does when a check finds a release
        window.updates.available = info
        window.statusBar().set_update(info)
        if args.message:
            window.statusBar().showMessage(args.message)
        if args.dialog:
            window.updates.show_available()
        if args.screenshot:
            app.processEvents()
            Path(args.screenshot).expanduser().parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(Path(args.screenshot).expanduser()))
            print(f"saved {args.screenshot}")
            return 0
        return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
