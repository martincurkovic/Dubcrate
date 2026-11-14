"""`dubcrate card ...`: look at the CDJ-1000MK3's SD card, back it up, and sync discs' cues to it.

`card sync` is a dry run unless `--yes`, like `burn`: it shows what would change, and with `--yes`
copies the card's files aside first, writes, and reads back. Rules (dev_docs/card-sync.md): every
rekordbox cue becomes a memory cue, the card's hot cues are never touched, cues already on the card
are kept. `card toc` reads the TOC of the disc in the drive (macOS), and `card sync --from-drive` uses
it for the card entry instead of computing it. **No file written this way has been loaded by a player yet.**
"""

import argparse
import sys
from pathlib import Path

from dubcrate import cdj_card as cc
from dubcrate.card_apply import apply_sync, default_backup_dir
from dubcrate.card_discs import (
    RekordboxCues,
    disc_toc,
    prepare_disc,
    toc_differences,
)
from dubcrate.card_report import describe_card, describe_id, describe_plan, msf
from dubcrate.card_sync import plan_card
from dubcrate.drive_toc import DriveTocError, read_drive_toc
from dubcrate.library import Library
from dubcrate.library.lengths import measure_tracks
from dubcrate.library.rekordbox import RekordboxError


def cmd_show(_: Library, args: argparse.Namespace) -> int:
    try:
        index, files = cc.read_card(Path(args.card).expanduser())
    except cc.CardFormatError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("\n".join(describe_card(index, files, waveforms=args.waveforms)))
    return 0


def _backup_dir(args: argparse.Namespace) -> Path:
    if args.backup_dir:
        return Path(args.backup_dir).expanduser()
    return default_backup_dir()


def cmd_backup(_: Library, args: argparse.Namespace) -> int:
    try:
        where = cc.backup_card(Path(args.card).expanduser(), _backup_dir(args))
    except cc.CardFormatError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"copied the card's files to {where}")
    return 0


def cmd_toc(lib: Library, args: argparse.Namespace) -> int:
    """Print the disc in the drive's TOC and its ID; with a disc, whether the plan computes the same."""
    try:
        starts, lead_out = read_drive_toc()
    except DriveTocError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    ends = [*starts[1:], lead_out]
    for n, (start, end) in enumerate(zip(starts, ends, strict=True), 1):
        print(f"track {n:>2}: starts {msf(start)}, {end - start} frames long")
    print(f"lead-out: {msf(lead_out)}")
    if len(starts) > 1:
        print(describe_id(cc.DiscId.from_toc(starts, lead_out)))
    if args.disc is None:
        return 0
    if lib.disc(args.disc).kind != "audio":
        print("error: that is not an audio disc.", file=sys.stderr)
        return 1
    entries = lib.disc_entries(args.disc)
    for problem in measure_tracks(lib, [e.track.id for e in entries]):
        print(f"warning: couldn't measure {problem}", file=sys.stderr)
    entries = lib.disc_entries(args.disc)
    if unmeasured := [e for e in entries if not e.track.length_exact]:
        print(f"{len(unmeasured)} tracks have no measured length: can't compare.")
        return 1
    planned = disc_toc(entries, lib.disc(args.disc).pregap_sectors)
    if differences := toc_differences(planned, (starts, lead_out)):
        print(f"\nDisc {args.disc} as planned now is NOT the disc in the drive:")
        print("\n".join(f"  {d}" for d in differences))
        return 1
    print(f"\nDisc {args.disc} as planned now has exactly this TOC.")
    return 0


def cmd_sync(lib: Library, args: argparse.Namespace) -> int:
    card = Path(args.card).expanduser()
    if args.from_drive and len(args.disc) != 1:
        print(
            "error: --from-drive is for one disc: the one in the drive.",
            file=sys.stderr,
        )
        return 1
    try:
        index, files = cc.read_card(card)
        drive = read_drive_toc() if args.from_drive else None
    except (cc.CardFormatError, DriveTocError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    inputs = []
    rekordbox = RekordboxCues(
        lib
    )  # rekordbox's database is read once, when first needed
    try:
        for disc_id in args.disc:
            if lib.disc(disc_id).kind == "audio" and drive is None:
                ids = [e.track.id for e in lib.disc_entries(disc_id)]
                for problem in measure_tracks(lib, ids):
                    print(f"warning: couldn't measure {problem}", file=sys.stderr)
            inputs.append(prepare_disc(lib, disc_id, rekordbox, drive))
    except RekordboxError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    runnable = [i for i in inputs if i.request is not None]
    for i in inputs:
        print(f"\nDisc {i.disc_id}, {i.name!r}")
        for problem in i.problems:
            print(f"  can't sync: {problem}")
        for warning in i.warnings:
            print(f"  warning: {warning}")
    plan = plan_card(index, files, [i.request for i in runnable if i.request])
    for i, disc_plan in zip(runnable, plan.discs, strict=True):
        print(
            f"\nDisc {i.disc_id}, {i.name!r}: {i.cue_tracks} tracks with rekordbox cues"
        )
        print("\n".join(describe_plan(disc_plan)))
    if not plan.changes:
        print("\nNothing to write.")
        return 0 if runnable else 2
    if not args.yes:
        print(f"\ndry run: would write {len(plan.writes())} file(s) to {card}.")
        print("re-run with --yes to write (the card's files are copied aside first).")
        return 0
    try:
        outcome = apply_sync(
            card, [i.request for i in runnable if i.request], _backup_dir(args)
        )
    except cc.CardFormatError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"\nwrote {len(outcome.written)} file(s); the card's old files are in {outcome.backup}"
        "\nEject the card (Finder, or `diskutil eject`) before taking it out: a card pulled early "
        "can be left unreadable."
    )
    return 0


def add_parsers(sub: argparse._SubParsersAction) -> None:
    card = sub.add_parser("card", help="the CDJ-1000MK3's SD card").add_subparsers(
        dest="action", required=True
    )
    where = (
        "the card's root, its PIONEER folder or PIONEER/CDJ (a mounted card or a copy)"
    )

    p = card.add_parser(
        "show", help="what is on a card: discs, cues, loops, hot cues, waveforms"
    )
    p.add_argument("card", help=where)
    p.add_argument("--waveforms", action="store_true", help="print the amplitudes too")
    p.set_defaults(fn=cmd_show)

    p = card.add_parser("backup", help="copy the card's files aside (a few kilobytes)")
    p.add_argument("card", help=where)
    p.add_argument("--backup-dir", help="default: card-backups next to the library")
    p.set_defaults(fn=cmd_backup)

    p = card.add_parser(
        "toc",
        help="the TOC of the audio CD in the drive (macOS), and with a disc id, whether it matches",
    )
    p.add_argument(
        "disc", type=int, nargs="?", help="an audio disc id to compare it with"
    )
    p.set_defaults(fn=cmd_toc)

    p = card.add_parser(
        "sync",
        help="add discs' rekordbox cues to the card as memory cues (dry run unless --yes)",
    )
    p.add_argument(
        "disc", type=int, nargs="+", help="audio disc ids (see: dubcrate disc list)"
    )
    p.add_argument("--card", required=True, help=where)
    p.add_argument("--backup-dir", help="default: card-backups next to the library")
    p.add_argument(
        "--from-drive",
        action="store_true",
        help="take the disc's ID from the CD in the drive (one disc), not from its tracks",
    )
    p.add_argument("--yes", action="store_true", help="really write to the card")
    p.set_defaults(fn=cmd_sync)
