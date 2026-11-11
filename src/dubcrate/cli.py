"""Command line front end for the Dubcrate library (until there's a GUI)."""

import argparse
import sys
from pathlib import Path

from dubcrate.cd import format_msf
from dubcrate.library import ImportSummary, Library
from dubcrate.library.folder import scan_folder
from dubcrate.library.rekordbox import RekordboxError, read_master_db


def _fit(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _duration(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def _report(summary: ImportSummary) -> None:
    print(
        f"added {summary.added}, updated {summary.updated}, removed {summary.removed};"
        f" {summary.nodes} browse nodes"
    )
    for note in summary.notes:
        print(f"note: {note}")


def cmd_import_folder(lib: Library, args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser().resolve()

    interactive = sys.stderr.isatty()

    def progress(done: int, total: int) -> None:
        if interactive:
            print(f"\rscanning {done}/{total}", end="", file=sys.stderr, flush=True)

    scan = scan_folder(root, progress=progress)
    if interactive:
        print(file=sys.stderr)
    notes = [f"unreadable: {path}: {why}" for path, why in scan.errors[:20]]
    if len(scan.errors) > 20:
        notes.append(f"...and {len(scan.errors) - 20} more unreadable files")
    _report(
        lib.replace_source(
            "folder", str(root), args.name or root.name, scan.tracks, scan.nodes, notes
        )
    )
    return 0


def cmd_import_rekordbox(lib: Library, args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser() if args.db else None
    try:
        rb = read_master_db(db_path, progress=lambda msg: print(msg, file=sys.stderr))
    except RekordboxError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _report(
        lib.replace_source(
            "rekordbox",
            str(rb.source),
            args.name or "rekordbox",
            rb.tracks,
            rb.nodes,
            rb.notes,
        )
    )
    return 0


def cmd_sources(lib: Library, _: argparse.Namespace) -> int:
    for s in lib.sources():
        print(
            f"{s.id:3d}  {s.kind:9s}  {s.track_count:6d} tracks  {s.name}  ({s.path})"
        )
    return 0


def cmd_nodes(lib: Library, args: argparse.Namespace) -> int:
    depth: dict[int, int] = {}
    for n in lib.nodes(args.source):
        level = depth.get(n.parent_id, -1) + 1 if n.parent_id else 0
        depth[n.id] = level
        marker = "+" if n.is_folder else "-"
        print(f"{n.id:6d}  {'  ' * level}{marker} {n.name}  ({n.track_count})")
    return 0


def cmd_tracks(lib: Library, args: argparse.Namespace) -> int:
    kwargs = {"source_id": args.source, "node_id": args.node, "search": args.search}
    total = lib.count_tracks(**kwargs)
    tracks = lib.tracks(
        **kwargs, sort=args.sort, descending=args.desc, limit=args.limit
    )
    for t in tracks:
        bpm = f"{t.bpm:6.1f}" if t.bpm else "     -"
        flag = "!" if t.missing else " "
        print(
            f"{t.id:6d}{flag} {_fit(t.artist, 24):24s}  {_fit(t.title, 38):38s}"
            f"  {bpm}  {t.key or '-':>4s}  {_duration(t.duration):>6s}"
        )
    shown = f"{len(tracks)} of {total}" if len(tracks) < total else str(total)
    print(
        f"{shown} tracks ({format_msf(round(sum(t.duration for t in tracks) * 75))} shown)"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dubcrate", description=__doc__)
    parser.add_argument("--library", help="library database (default: app data dir)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("import-folder", help="import a folder of audio files")
    p.add_argument("path")
    p.add_argument("--name", help="display name (default: folder name)")
    p.set_defaults(fn=cmd_import_folder)

    p = sub.add_parser(
        "import-rekordbox", help="import a rekordbox library (read-only)"
    )
    p.add_argument("--db", help="path to master.db (default: installed rekordbox)")
    p.add_argument("--name", help="display name (default: rekordbox)")
    p.set_defaults(fn=cmd_import_rekordbox)

    sub.add_parser("sources", help="list imported sources").set_defaults(fn=cmd_sources)

    p = sub.add_parser("nodes", help="show a source's playlist/folder tree")
    p.add_argument("source", type=int)
    p.set_defaults(fn=cmd_nodes)

    p = sub.add_parser("tracks", help="list tracks")
    p.add_argument("--source", type=int)
    p.add_argument("--node", type=int)
    p.add_argument("--search")
    p.add_argument(
        "--sort",
        choices=[
            "title",
            "artist",
            "album",
            "genre",
            "bpm",
            "key",
            "duration",
            "year",
            "rating",
            "path",
        ],
    )
    p.add_argument("--desc", action="store_true")
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(fn=cmd_tracks)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with Library.open(args.library) as lib:
        return args.fn(lib, args)


if __name__ == "__main__":
    raise SystemExit(main())
