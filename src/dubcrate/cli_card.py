"""`dubcrate card ...`: look at the CDJ-1000MK3's SD card, back it up, format one, and sync discs' cues to it.

`card sync` is a dry run unless `--yes`, like `burn`: it shows what would change, and with `--yes`
copies the card's files aside first, writes, and reads back. Rules (dev_docs/card-sync.md): every
rekordbox cue becomes a memory cue, the card's hot cues are never touched, cues already on the card
are kept. `card toc` reads the TOC of the disc in the drive (macOS), and `card sync --from-drive` uses
it for the card entry instead of computing it. **No file written this way has been loaded by a player yet.**
"""

import argparse
import sys
from pathlib import Path

from dubcrate import card_format, wave_check
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
from dubcrate.library.waves import ensure_waveforms


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


def _analysis_progress(done: int, total: int) -> None:
    """One line on the terminal while tracks are analysed for their waveforms (the first time takes a while)."""
    if total:
        print(f"\ranalysing waveforms: {done} of {total}", end="", file=sys.stderr)
        if done == total:
            print(file=sys.stderr)


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
    waves = not args.no_waveforms
    try:
        for disc_id in args.disc:
            ids = [e.track.id for e in lib.disc_entries(disc_id)]
            if lib.disc(disc_id).kind == "audio" and drive is None:
                for problem in measure_tracks(lib, ids):
                    print(f"warning: couldn't measure {problem}", file=sys.stderr)
            if waves and lib.disc(disc_id).kind == "audio":
                for problem in ensure_waveforms(lib, ids, _analysis_progress):
                    print(f"warning: no waveform for {problem}", file=sys.stderr)
            inputs.append(prepare_disc(lib, disc_id, rekordbox, drive, waves=waves))
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
        f"\nwrote {len(outcome.written)} file(s) ({outcome.cues_written} cues, "
        f"{outcome.waveforms_written} waveforms); the card's old files are in {outcome.backup}"
        "\nEject the card (Finder, or `diskutil eject`) before taking it out: a card pulled early "
        "can be left unreadable."
    )
    return 0


def cmd_format(_: Library, args: argparse.Namespace) -> int:
    """List the cards that could be formatted, or check one and (with --yes) erase it."""
    try:
        if args.disk is None:
            disks = card_format.list_disks()
            if not disks:
                print("No removable disks found: put the card in its reader.")
                return 1
            for disk in disks:
                why = card_format.problems(disk)
                print(card_format.describe(disk) + (f"  ({why[0]})" if why else "  OK"))
            return 0
        disk = card_format.inspect(args.disk)
        print(card_format.describe(disk))
        if why := card_format.problems(disk):
            for line in why:
                print(f"error: {line}", file=sys.stderr)
            return 1
        print(
            f"\nThis would ERASE everything on {disk.device} and make it a CDJ-1000MK3 card: "
            f"MBR, FAT16, named {card_format.LABEL}, with an empty PIONEER/CDJ folder."
        )
        if not args.yes:
            print("dry run: re-run with --yes to erase it.")
            return 0
        typed = args.confirm
        if typed is None:
            typed = input(f"Type {disk.device} to erase it: ").strip()
        if typed != disk.device:
            print("error: not confirmed; nothing was erased.", file=sys.stderr)
            return 1
        done = card_format.format_card(disk.device)
    except card_format.FormatError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"\nformatted: the card is at {done.volume}, ready for `dubcrate card sync ... "
        f"--card {done.volume}`.\nEject it ({card_format.eject_hint()}) before taking it "
        "out: a card pulled early can be left unreadable."
    )
    return 0


def cmd_check_waveforms(_: Library, args: argparse.Namespace) -> int:
    """How close the model is to what the player wrote for a disc (the card's own records vs the audio)."""
    from dubcrate.audio.decode import DecodeError, decode_file

    try:
        index, files = cc.read_card(Path(args.card).expanduser())
        if args.from_drive:
            starts, lead_out = read_drive_toc()
        else:
            starts, lead_out = cc.toc_from_drutil(
                Path(args.toc).expanduser().read_text(encoding="utf-8")
            )
    except (cc.CardFormatError, DriveTocError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    disc_id = cc.DiscId.from_toc(starts, lead_out)
    entry = index.find(disc_id)
    if entry is None:
        print("error: this disc is not on the card (its table of contents is not in the index).", file=sys.stderr)  # fmt: skip
        return 1
    file = files.get(entry.name)
    if file is None or not file.fine:
        print(f"error: the player has not recorded a waveform for {entry.name} yet: play some of the disc.", file=sys.stderr)  # fmt: skip
        return 1
    audio = wave_check.audio_files(Path(args.audio).expanduser())

    def audio_of(track: int):
        path = audio.get(track)
        if path is None:
            return None
        try:
            return decode_file(str(path)).samples
        except DecodeError as exc:
            print(f"warning: {path.name}: {exc}", file=sys.stderr)
            return None

    found, notes = wave_check.check_file(file, audio_of, args.track or None)
    print(
        f"{entry.name}: {len(file.fine)} waveform slots; the model against the player's own values (absolute scale)"
    )
    print("\n".join(wave_check.describe(found, notes)))
    return 0 if found else 2


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
    p.add_argument(
        "--no-waveforms",
        action="store_true",
        help="cues only: don't write waveforms (by default a track with no waveform on the card gets one, "
        "so the player doesn't analyse it; a waveform already there is never changed)",
    )
    p.add_argument("--yes", action="store_true", help="really write to the card")
    p.set_defaults(fn=cmd_sync)

    p = card.add_parser(
        "format",
        help="erase a blank SD card and make it ready for the CDJ-1000MK3 (macOS; dry run unless --yes)",
    )
    p.add_argument(
        "disk",
        nargs="?",
        help="the whole card, such as disk4 (see: diskutil list); omit to list removable disks",
    )
    p.add_argument("--yes", action="store_true", help="really erase the card")
    p.add_argument(
        "--confirm",
        metavar="DISK",
        help="the disk's name again, instead of being asked to type it",
    )
    p.set_defaults(fn=cmd_format)

    p = card.add_parser(
        "check-waveforms",
        help="how close the model is to the waveforms the player wrote for a disc (needs the disc's audio)",
    )
    p.add_argument("card", help=where)
    p.add_argument("--audio", required=True, help="a folder with the disc's tracks, each file named with its track number first")  # fmt: skip
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--toc", help="the disc's `drutil toc` output (a file), to find it on the card"
    )
    source.add_argument("--from-drive", action="store_true", help="read the disc in the drive's TOC instead")  # fmt: skip
    p.add_argument("--track", type=int, action="append", help="only this track (repeat for more)")  # fmt: skip
    p.set_defaults(fn=cmd_check_waveforms)
