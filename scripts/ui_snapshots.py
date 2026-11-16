"""Renders every window, dialog, message and menu of Dubcrate to images, and builds an HTML gallery to review them in.

    uv run python scripts/ui_snapshots.py                  # everything -> build/ui_snapshots/index.html
    uv run python scripts/ui_snapshots.py --quick          # dark theme only
    uv run python scripts/ui_snapshots.py --only burn --only settings     # scenes whose id contains this
    uv run python scripts/ui_snapshots.py --list           # the scenes, then stop
    uv run python scripts/ui_snapshots.py --scale 1        # 1x images (smaller, quicker) instead of 2x
    uv run python scripts/ui_snapshots.py --round 2        # a second review pass: its notes start clean

It runs the real widgets offscreen, against a made-up music collection (scripts/demo_music.py, about a minute to
make) in a throwaway library, once for each theme (dark, light, Macintosh System 6) and, for windows that can be
resized, each screen size. Every screen is measured for text that does not fit and layouts squeezed below their
minimum, every piece of text on it is listed (tooltips too), and the gallery marks the spots.

Every message box, question, input box and dialog a screen opens is **shown as a picture of its own** instead of
blocking (answered with its safe default: No, Cancel), so the wording of each can be read. The system's own file
choosers and print panels cannot be drawn offscreen; the gallery lists them as "not shown as a picture".

Nothing of yours is touched: not your music, library, settings, rekordbox, drives or the log. What offscreen rendering
cannot show is how a screen looks on real hardware (fonts, scaling and window chrome differ), so treat the pictures as a
layout and wording check, not as ground truth. Open build/ui_snapshots/index.html in a browser; notes live in the browser.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent

# The scale has to be known before Qt starts, so it is read here, ahead of the real argument parser.
_early = argparse.ArgumentParser(add_help=False)
_early.add_argument("--scale", type=int, choices=(1, 2), default=2)
_scale = _early.parse_known_args()[0].scale

# Nothing of the owner's may be touched: all of this is set before Qt, the log or the theme looks.
_SCRATCH = Path(tempfile.mkdtemp(prefix="dubcrate-ui-snapshots-"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = str(_scale)
os.environ["DUBCRATE_DATA_DIR"] = str(_SCRATCH / "data")
os.environ["DUBCRATE_LOG_DIR"] = str(_SCRATCH / "logs")

sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

THEMES = ("dark", "light", "system6")
# what a window is judged against: one taller than the screen minus the menu bar, title bar and dock cannot
# be fully seen, however well it is laid out
SCREENS = (
    ("1280x720", 1280, 720),
    ("1366x768", 1366, 768),
    ("1440x900", 1440, 900),
    ("1920x1080", 1920, 1080),
)
CHROME = 90  # menu bar / title bar plus dock, roughly
SETTLE_MS = 200


def slug(text):
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")


def window_limits(widget):
    """The smallest size the window can be, as far as the user can tell."""
    hint = widget.minimumSizeHint()
    return (
        max(widget.minimumWidth(), hint.width() if widget.layout() else 0),
        max(widget.minimumHeight(), hint.height() if widget.layout() else 0),
    )


def render(widget, path, requested):
    """Shows the widget (at the requested size when it can be resized), measures it, saves it."""
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from ui_snapshot_checks import find_layout_issues
    from ui_snapshot_scenes import Focus

    target = widget.target if isinstance(widget, Focus) else widget
    widget = widget.host if isinstance(widget, Focus) else widget
    if requested == (0, 0):  # "as small as it will go"
        widget.resize(widget.minimumSizeHint())
        requested = None
    elif requested:
        widget.resize(requested[0], requested[1])
    widget.show()
    QTest.qWait(SETTLE_MS)
    QApplication.processEvents()
    issues = find_layout_issues(target)
    target.grab().save(str(path))
    actual = (target.width(), target.height())
    return {
        "issues": [
            {"kind": i.kind, "path": i.path, "detail": i.detail, "rect": list(i.rect)}
            for i in issues
        ],
        "actual": list(actual),
        "grew": bool(requested)
        and (actual[0] > requested[0] or actual[1] > requested[1]),
    }


def run(args):
    import screenshot_recipes as recipes
    import ui_snapshot_scenes as scenes_module
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from ui_snapshot_checks import collect_texts

    from dubcrate.ui import spinbox_drag, theme

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        str(_SCRATCH / "settings"),
    )
    app = QApplication.instance() or QApplication(sys.argv)
    app.setOrganizationName("DubcrateSnapshots")
    app.setApplicationName("DubcrateSnapshots")
    spinbox_drag.install(app)

    out = Path(args.out).resolve()
    images = out / "images"
    if images.is_dir():
        shutil.rmtree(images)
    images.mkdir(parents=True)

    chosen = [
        s
        for s in scenes_module.ordered()
        if not args.only or any(o in s.id for o in args.only)
    ]
    if not chosen:
        sys.exit("no scenes match --only")
    themes = ["dark"] if args.quick else args.themes

    scenes_module.install_modal_capture()
    demo = recipes.Demo(_SCRATCH)

    manifest = {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "round": args.round,
        "themes": themes,
        "scale": _scale,
        "screens": [
            {"label": n, "w": w, "h": h, "usable_h": h - CHROME} for n, w, h in SCREENS
        ],
        "scenes": [],
    }
    records = {
        s.id: {
            "id": s.id,
            "area": s.area,
            "title": s.title,
            "about": s.about,
            "kind": s.kind,
            "variants": [],
            "texts": [],
            "skipped": [],
            "error": None,
            "min_size": None,
            "fixed": False,
        }
        for s in chosen
    }

    total = len(themes) * len(chosen)
    done = 0
    for theme_name in themes:
        theme.apply_to_app(app, theme_name)
        for s in chosen:
            done += 1
            record = records[s.id]
            for index, size in enumerate(s.sizes or (None,)):
                label, width, height = size or ("natural", None, None)
                requested = None if width is None else (width, height)
                QSettings().clear()  # (every screen starts from a fresh install's settings)
                ctx = scenes_module.SnapContext(app, theme_name, demo)
                scenes_module.CURRENT.append(ctx)
                widget = None
                try:
                    widget = s.build(ctx)
                    base = f"{s.id}__{theme_name}__{slug(label)}"
                    variant = {
                        "theme": theme_name,
                        "size": label,
                        "requested": list(requested) if requested else None,
                        "extras": [],
                    }
                    if widget is not None:
                        variant.update(
                            render(widget, images / f"{base}.png", requested)
                        )
                        variant["file"] = f"images/{base}.png"
                        focus = isinstance(widget, scenes_module.Focus)
                        host = widget.host if focus else widget
                        target = widget.target if focus else widget
                        if record["min_size"] is None:
                            record["min_size"] = list(window_limits(host))
                            record["fixed"] = host.minimumSize() == host.maximumSize()
                        if not record["texts"]:
                            record["texts"] = collect_texts(target)
                    else:  # a scene that is only what it raises (a message box): no main image
                        variant.update(
                            {"file": "", "actual": [0, 0], "grew": False, "issues": []}
                        )
                    if (
                        index == 0
                    ):  # the boxes and menus a screen raised: once per theme, not per size
                        for n, extra in enumerate(ctx.extras, start=1):
                            name = f"images/{base}__x{n}-{slug(extra.label)[:40]}.png"
                            extra.pixmap.save(str(out / name))
                            variant["extras"].append(
                                {"label": extra.label, "file": name}
                            )
                            for issue in extra.issues:
                                variant["issues"].append(
                                    {
                                        "kind": issue.kind,
                                        "path": f"[{extra.label}] {issue.path}",
                                        "detail": issue.detail,
                                        "rect": [],
                                    }
                                )
                            if not record["texts"] or theme_name == themes[0]:
                                known = {
                                    (t["kind"], t["text"], t["tip"])
                                    for t in record["texts"]
                                }
                                for t in extra.texts:
                                    if (t["kind"], t["text"], t["tip"]) not in known:
                                        record["texts"].append(
                                            {
                                                **t,
                                                "kind": f"{t['kind']} (in {extra.label})",
                                            }
                                        )
                        for note in ctx.skipped:
                            if note not in record["skipped"]:
                                record["skipped"].append(note)
                    record["variants"].append(variant)
                except Exception:  # noqa: BLE001 - one broken screen must not stop the rest
                    record["error"] = traceback.format_exc()
                    print(f"  ! {s.id} ({theme_name}/{label}) failed", file=sys.stderr)
                finally:
                    scenes_module.CURRENT.remove(ctx)
                    ctx.cleanup()
            print(f"[{done}/{total}] {theme_name}: {s.id}", flush=True)

    manifest["scenes"] = [records[s.id] for s in chosen]
    with open(out / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    from ui_snapshot_gallery import write_gallery

    write_gallery(manifest, str(out))
    demo.close()
    flagged = sum(1 for r in manifest["scenes"] for v in r["variants"] if v["issues"])
    failed = [r["id"] for r in manifest["scenes"] if r["error"]]
    made = sum(
        1 + len(v["extras"]) if v["file"] else len(v["extras"])
        for r in manifest["scenes"]
        for v in r["variants"]
    )
    print(
        f"\n{len(chosen)} scenes, {made} images, {flagged} renders with layout flags,"
        f" {len(failed)} scenes failed {failed or ''}"
    )
    print(f"open {out / 'index.html'}")
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", default=str(ROOT / "build" / "ui_snapshots"))
    parser.add_argument("--quick", action="store_true", help="dark theme only")
    parser.add_argument(
        "--only", action="append", help="scene ids containing this text (repeatable)"
    )
    parser.add_argument(
        "--list", action="store_true", help="list the scenes, then stop"
    )
    parser.add_argument(
        "--scale", type=int, choices=(1, 2), default=2, help="2 (Retina, default) or 1"
    )
    parser.add_argument(
        "--round",
        default="1",
        help="review round: each round keeps its own notes in the gallery (default 1)",
    )
    parser.add_argument("--themes", nargs="+", default=list(THEMES), choices=THEMES)
    args = parser.parse_args(argv)
    try:
        if args.list:
            import ui_snapshot_scenes as scenes_module

            area = None
            for s in scenes_module.ordered():
                if s.area != area:
                    area = s.area
                    print(f"\n{area}")
                print(f"  {s.id:34} {s.title}")
            return 0
        return run(args)
    finally:
        shutil.rmtree(_SCRATCH, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
