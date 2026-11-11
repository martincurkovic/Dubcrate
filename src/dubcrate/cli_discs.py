"""`dubcrate disc ...` and `dubcrate burn` commands."""

import argparse
import sys
import tempfile
from pathlib import Path

from dubcrate.build import (
    BuildError,
    build_burn_disc,
    disc_problems,
    media_notes,
    media_problems,
)
from dubcrate.burn import BurnError, BurnOptions, get_burner
from dubcrate.cd import (
    DISC_74_SECTORS,
    DISC_80_SECTORS,
    SECTORS_PER_SECOND,
    format_msf,
)
from dubcrate.library import Library
from dubcrate.library.lengths import measure_tracks
from dubcrate.library.models import DiscSummary
from dubcrate.library.store import LibraryError

_CAPACITY = {74: DISC_74_SECTORS, 80: DISC_80_SECTORS}


def _mmss(sectors: int) -> str:
    seconds = sectors // SECTORS_PER_SECOND
    return f"{seconds // 60}:{seconds % 60:02d}"


def _fit(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _gap(summary: DiscSummary) -> str:
    return (
        "gapless"
        if not summary.pregap_sectors
        else f"{summary.pregap_sectors / 75:g}s gaps"
    )


def _minutes(sectors: int) -> str:
    return f"{sectors // (60 * SECTORS_PER_SECOND)} min"


def _summary_line(s: DiscSummary, approx: bool = False) -> str:
    mark = "~" if approx else ""
    state = (
        f"OVER by {format_msf(-s.free_sectors)}"
        if s.over
        else f"{format_msf(s.free_sectors)} free"
    )
    return (
        f"  {s.track_count} tracks, {mark}{format_msf(s.used_sectors)} of"
        f" {format_msf(s.capacity_sectors)} ({state})"
    )


def _show(lib: Library, disc_id: int) -> None:
    s = lib.disc(disc_id)
    print(f'Disc {s.id} "{s.name}": {_minutes(s.capacity_sectors)}, {_gap(s)}')
    entries = lib.disc_entries(disc_id)
    for e in entries:
        t = e.track
        flags = ("~" if not t.length_exact else " ") + (
            "!" if t.missing or t.stale else " "
        )
        bpm = f"{t.bpm:6.1f}" if t.bpm else "     -"
        print(
            f"  {e.position + 1:2d}. {_mmss(e.sectors):>6s}{flags}"
            f" {_fit(t.artist, 24):24s}  {_fit(t.title, 38):38s}  {bpm}  {t.key or '-':>4s}"
        )
    print(_summary_line(s, approx=any(not e.track.length_exact for e in entries)))
    if any(not e.track.length_exact for e in entries):
        print("  (~ = estimated length; measured when the disc is burned)")
    if any(e.track.missing or e.track.stale for e in entries):
        print("  (! = file missing or track no longer in its source)")


def cmd_new(lib: Library, args: argparse.Namespace) -> int:
    disc_id = lib.create_disc(
        args.name,
        capacity_sectors=_CAPACITY[args.minutes],
        pregap_sectors=round(args.pregap * SECTORS_PER_SECOND),
    )
    print(f"created disc {disc_id}")
    return 0


def cmd_list(lib: Library, _: argparse.Namespace) -> int:
    for s in lib.discs():
        flag = "  OVER" if s.over else ""
        print(
            f"{s.id:3d}  {s.name:30s}  {s.track_count:2d} tracks"
            f"  {format_msf(s.used_sectors)} / {_minutes(s.capacity_sectors)}{flag}"
        )
    return 0


def cmd_show(lib: Library, args: argparse.Namespace) -> int:
    _show(lib, args.disc)
    return 0


def cmd_add(lib: Library, args: argparse.Namespace) -> int:
    if args.node is not None:
        track_ids = [t.id for t in lib.tracks(node_id=args.node)]
    else:
        track_ids = args.track
    problems = measure_tracks(lib, track_ids)
    for problem in problems:
        print(f"warning: couldn't measure {problem}", file=sys.stderr)
    position = args.at - 1 if args.at else None
    result = lib.add_to_disc(
        args.disc, track_ids, position=position, fit=args.fit, strict=args.strict
    )
    print(f"added {len(result.added)} track(s)", end="")
    print(f", {len(result.rejected)} didn't fit" if result.rejected else "")
    _show(lib, args.disc)
    return 0


def cmd_remove(lib: Library, args: argparse.Namespace) -> int:
    lib.remove_from_disc(args.disc, [p - 1 for p in args.position])
    _show(lib, args.disc)
    return 0


def cmd_move(lib: Library, args: argparse.Namespace) -> int:
    lib.move_in_disc(args.disc, args.from_position - 1, args.to_position - 1)
    _show(lib, args.disc)
    return 0


def cmd_set(lib: Library, args: argparse.Namespace) -> int:
    lib.update_disc(
        args.disc,
        name=args.name,
        capacity_sectors=_CAPACITY[args.minutes] if args.minutes else None,
        pregap_sectors=None
        if args.pregap is None
        else round(args.pregap * SECTORS_PER_SECOND),
    )
    _show(lib, args.disc)
    return 0


def cmd_delete(lib: Library, args: argparse.Namespace) -> int:
    lib.delete_disc(args.disc)
    print(f"deleted disc {args.disc}")
    return 0


def cmd_split(lib: Library, args: argparse.Namespace) -> int:
    track_ids = [t.id for t in lib.tracks(node_id=args.node)]
    for problem in measure_tracks(lib, track_ids):
        print(f"warning: couldn't measure {problem}", file=sys.stderr)
    result = lib.split_into_discs(
        track_ids,
        args.name,
        capacity_sectors=_CAPACITY[args.minutes],
        pregap_sectors=round(args.pregap * SECTORS_PER_SECOND),
    )
    for disc_id in result.disc_ids:
        _show(lib, disc_id)
    if result.too_long:
        print(
            f"skipped {len(result.too_long)} track(s) longer than a whole disc",
            file=sys.stderr,
        )
    return 0


def cmd_burn(lib: Library, args: argparse.Namespace) -> int:
    entries = lib.disc_entries(args.disc)
    for problem in measure_tracks(lib, [e.track.id for e in entries]):
        print(f"warning: couldn't measure {problem}", file=sys.stderr)
    _show(lib, args.disc)
    problems = disc_problems(lib, args.disc)
    if problems:
        print("\ncan't burn this disc yet:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2
    if not args.yes:
        mode = "simulate burning" if args.simulate else "burn"
        print(
            f"\ndry run: would {mode} disc {args.disc} with {args.backend} at {args.speed}x."
        )
        print("re-run with --yes to burn (uses up the blank CD-R unless --simulate).")
        return 0
    burner = get_burner(args.backend)
    options = BurnOptions(speed=args.speed, simulate=args.simulate, device=args.device)
    try:
        media = burner.media_info(args.device)
        for note in media_notes(media):
            print(note)
        problems = media_problems(lib.disc(args.disc).used_sectors, media)
        if problems:
            raise BurnError("; ".join(problems))
        with tempfile.TemporaryDirectory(prefix="dubcrate-burn-") as tmp:
            work = Path(args.work).expanduser() if args.work else Path(tmp)
            burn_disc, pregap = build_burn_disc(lib, args.disc, work, progress=print)
            problems = media_problems(burn_disc.used_sectors(pregap), media)
            if problems:  # the rendered lengths are the exact ones
                raise BurnError("; ".join(problems))
            options = BurnOptions(
                speed=options.speed,
                simulate=options.simulate,
                device=options.device,
                pregap_sectors=pregap,
            )
            burner.burn(burn_disc, options, progress=print)
    except (BuildError, BurnError) as exc:
        print(f"burn failed: {exc}", file=sys.stderr)
        return 1
    if not options.simulate:  # a test write puts nothing on a disc
        burn_id = lib.record_burn(
            args.disc, drive=options.device or "", speed=options.speed
        )
        print(f"saved burn record {burn_id}")
    print("done")
    return 0


def add_parsers(sub: argparse._SubParsersAction) -> None:
    capacity = {"type": int, "choices": sorted(_CAPACITY)}

    disc = sub.add_parser("disc", help="plan discs").add_subparsers(
        dest="action", required=True
    )

    p = disc.add_parser("new", help="create a disc")
    p.add_argument("name")
    p.add_argument("--minutes", default=80, **capacity, help="disc size (default 80)")
    p.add_argument(
        "--pregap", type=float, default=0.0, help="seconds between tracks (default 0)"
    )
    p.set_defaults(fn=cmd_new)

    disc.add_parser("list", help="list discs").set_defaults(fn=cmd_list)

    p = disc.add_parser("show", help="show a disc's tracks and capacity")
    p.add_argument("disc", type=int)
    p.set_defaults(fn=cmd_show)

    p = disc.add_parser("add", help="add tracks to a disc")
    p.add_argument("disc", type=int)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--node", type=int, help="add a playlist/folder's tracks, in order"
    )
    group.add_argument("--track", type=int, nargs="+", help="track ids")
    p.add_argument("--fit", action="store_true", help="only add what still fits")
    p.add_argument(
        "--strict",
        action="store_true",
        help="with --fit, stop at the first that doesn't",
    )
    p.add_argument("--at", type=int, help="insert at this position (1-based)")
    p.set_defaults(fn=cmd_add)

    p = disc.add_parser("remove", help="remove tracks by position (1-based)")
    p.add_argument("disc", type=int)
    p.add_argument("position", type=int, nargs="+")
    p.set_defaults(fn=cmd_remove)

    p = disc.add_parser("move", help="move a track (positions are 1-based)")
    p.add_argument("disc", type=int)
    p.add_argument("from_position", type=int)
    p.add_argument("to_position", type=int)
    p.set_defaults(fn=cmd_move)

    p = disc.add_parser("set", help="rename a disc or change its size/gaps")
    p.add_argument("disc", type=int)
    p.add_argument("--name")
    p.add_argument("--minutes", **capacity)
    p.add_argument("--pregap", type=float)
    p.set_defaults(fn=cmd_set)

    p = disc.add_parser("delete", help="delete a disc (its tracks stay in the library)")
    p.add_argument("disc", type=int)
    p.set_defaults(fn=cmd_delete)

    p = disc.add_parser(
        "split", help="split a playlist/folder across as many discs as needed"
    )
    p.add_argument("--node", type=int, required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--minutes", default=80, **capacity)
    p.add_argument("--pregap", type=float, default=0.0)
    p.set_defaults(fn=cmd_split)

    p = sub.add_parser("burn", help="burn a disc (dry run unless --yes)")
    p.add_argument("disc", type=int)
    p.add_argument(
        "--backend", choices=["discrecording", "cdrdao"], default="discrecording"
    )
    p.add_argument("--speed", type=int, default=10)
    p.add_argument("--device")
    p.add_argument("--work", help="keep rendered WAVs here (default: a temp dir)")
    p.add_argument("--simulate", action="store_true", help="laser-off test write")
    p.add_argument("--yes", action="store_true", help="really burn")
    p.set_defaults(fn=cmd_burn)


def run(lib: Library, args: argparse.Namespace) -> int:
    try:
        return args.fn(lib, args)
    except LibraryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
