"""Regenerate the pictures the user guide and the README show (src/dubcrate/ui/help/screenshots/): offscreen, at twice their on-screen size.

    uv run python scripts/make_screenshots.py                        # make the pictures that don't exist yet
    uv run python scripts/make_screenshots.py --list                 # every picture, where it goes and whether it exists
    uv run python scripts/make_screenshots.py --only NAME [NAME...]  # just these (with or without .png)
    uv run python scripts/make_screenshots.py --overwrite            # redo every one
    uv run python scripts/make_screenshots.py --out DIR              # somewhere else (then nothing is "skipped")
    uv run python scripts/make_screenshots.py --out DIR --scheme system6   # the same pictures in another theme, to look at it

What is photographed: a made-up music collection (scripts/demo_music.py: synthesised house loops with invented titles, artists
and tags, written to a temporary folder, about a minute) imported into a temporary library, with a few discs planned from
it. Nothing of anyone's is read: not a rekordbox library, not your music, not your drives (the Explorer is pointed at a
pretend home folder). The settings, the log and the library go to a scratch folder, the player gets a silent stand-in for
the sound card, and the burn window a stand-in burner: nothing real is written, played or burned. Two things are staged,
because a folder import has no rekordbox analysis: the track list's Preview column is filled in (a plain folder's
waveform appears only once a track is loaded), and there are no phrases, cues or beat grid on the waveforms.
An existing picture is only replaced with --overwrite.
Pictures are window CONTENTS (no title bar), 2x, which is what ui/help_dialog.py's SCREENSHOT_PIXEL_RATIO expects.
Adding one: write a `@shot` function in scripts/screenshot_recipes.py; `tests/test_make_screenshots.py` checks that every
picture manual.md mentions has one.
"""

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Nothing of the owner's may be touched: all of this is set before Qt, the log or the theme looks.
_SCRATCH = Path(tempfile.mkdtemp(prefix="dubcrate-shots-"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = "2"  # a 2x picture, like a Retina screenshot
os.environ["DUBCRATE_DATA_DIR"] = str(_SCRATCH / "data")
os.environ["DUBCRATE_LOG_DIR"] = str(_SCRATCH / "logs")

sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

PICTURES = (
    ROOT / "src" / "dubcrate" / "ui" / "help" / "screenshots"
)  # the guide ships inside the app; the README points here too

import screenshot_recipes as recipes


def run(names, folder, overwrite, scheme=None):
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication

    from dubcrate.ui import spinbox_drag, theme

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        str(_SCRATCH / "settings"),
    )
    app = QApplication.instance() or QApplication(sys.argv)
    app.setOrganizationName("DubcrateShots")
    app.setApplicationName("DubcrateShots")
    spinbox_drag.install(app)

    made = skipped = failed = 0
    demo = None
    for name in names:
        s = recipes.SHOTS[name]
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{name}.png"
        if target.exists() and not overwrite:
            print(f"skip  {name}.png (exists; --overwrite to replace)")
            skipped += 1
            continue
        if demo is None:  # (only when something is to be made)
            demo = recipes.Demo(_SCRATCH)
        chosen = scheme or s.scheme
        theme.apply_to_app(app, chosen)
        ctx = recipes.Context(app, chosen, demo)
        try:
            result = s.recipe(ctx)
            pixmap = result if isinstance(result, QPixmap) else result.grab()
            if pixmap.isNull() or pixmap.width() < 40:
                raise RuntimeError("the window came out empty")
            pixmap.save(str(target))
            print(f"saved {name}.png  {pixmap.width()}x{pixmap.height()}  ->  {folder}")
            made += 1
        except Exception as e:  # noqa: BLE001 - one picture failing must not stop the others
            import traceback

            traceback.print_exc()
            print(f"FAIL  {name}: {e}")
            failed += 1
        finally:
            ctx.cleanup()
    if demo is not None:
        demo.close()
    print(f"\n{made} saved, {skipped} skipped, {failed} failed")
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="NAME",
        help="just these pictures (names as in --list, with or without .png)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace pictures that already exist (default: leave them)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="write everything here instead of the README and guide folders",
    )
    parser.add_argument(
        "--scheme",
        help="photograph every picture in this theme (dark, light, system6) instead of the recipe's own: for looking at a theme, use with --out",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list the pictures and whether each exists, then stop",
    )
    args = parser.parse_args(argv)

    folder = args.out or PICTURES

    try:
        if args.list:
            for name, s in recipes.SHOTS.items():
                mark = "exists" if (folder / f"{name}.png").exists() else "missing"
                print(f"{name:26} {mark:8} {s.description}")
            return 0
        names = list(recipes.SHOTS)
        if args.only:
            wanted = [n.removesuffix(".png") for n in args.only]
            unknown = [n for n in wanted if n not in recipes.SHOTS]
            if unknown:
                print(
                    "unknown picture(s):",
                    ", ".join(unknown),
                    "\nknown:",
                    ", ".join(recipes.SHOTS),
                )
                return 2
            names = wanted
        return run(names, folder, args.overwrite, args.scheme)
    finally:
        shutil.rmtree(_SCRATCH, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
