"""`dubcrate card ...`: look at the CDJ-1000MK3's SD card, back it up, and sync discs' cues to it.

`card sync` is a dry run unless `--yes`, like `burn`: it shows what would change, and with `--yes`
copies the card's files aside first, writes, and reads back. Rules (dev_docs/card-sync.md): every
rekordbox cue becomes a memory cue, the card's hot cues are never touched, cues already on the card
are kept. **No file written this way has been loaded by a player yet.**
"""

import argparse
import sys
from pathlib import Path

from dubcrate import cdj_card as cc
from dubcrate.card_discs import prepare_disc
from dubcrate.card_report import describe_card, describe_plan
from dubcrate.card_sync import plan_card
from dubcrate.library import Library
from dubcrate.library.lengths import measure_tracks
from dubcrate.library.paths import default_library_path


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
    return default_library_path().parent / "card-backups"


def cmd_backup(_: Library, args: argparse.Namespace) -> int:
    try:
        where = cc.backup_card(Path(args.card).expanduser(), _backup_dir(args))
    except cc.CardFormatError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"copied the card's files to {where}")
    return 0


def cmd_sync(lib: Library, args: argparse.Namespace) -> int:
    card = Path(args.card).expanduser()
    try:
        index, files = cc.read_card(card)
    except cc.CardFormatError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    inputs = []
    for disc_id in args.disc:
        if lib.disc(disc_id).kind == "audio":
            ids = [e.track.id for e in lib.disc_entries(disc_id)]
            for problem in measure_tracks(lib, ids):
                print(f"warning: couldn't measure {problem}", file=sys.stderr)
        inputs.append(prepare_disc(lib, disc_id))

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
        backup = cc.backup_card(card, _backup_dir(args))
        cc.write_card(card, plan.index, plan.writes())
    except cc.CardFormatError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"\nwrote {len(plan.writes())} file(s); the card's old files are in {backup}")
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
        "sync",
        help="add discs' rekordbox cues to the card as memory cues (dry run unless --yes)",
    )
    p.add_argument(
        "disc", type=int, nargs="+", help="audio disc ids (see: dubcrate disc list)"
    )
    p.add_argument("--card", required=True, help=where)
    p.add_argument("--backup-dir", help="default: card-backups next to the library")
    p.add_argument("--yes", action="store_true", help="really write to the card")
    p.set_defaults(fn=cmd_sync)
