"""`dubcrate rekordbox ...`: put memory cues edited in Dubcrate into rekordbox's `master.db`.

A dry run unless `--yes`, like `burn` and `card sync`. With `--yes` rekordbox must be closed; the
database's files are copied aside first, the cues are written in one transaction, read back, and the
copy is put back if anything is wrong (`rekordbox_write`). Only memory cues are written. **Tried on
synthetic databases only.**
"""

import argparse
import sys
from pathlib import Path

from dubcrate.library import Library
from dubcrate.rekordbox_write import (
    TrackChange,
    WriteError,
    apply_write,
    default_backup_dir,
    default_database,
    plan_write,
    rekordbox_running,
)


def _stamp(ms: int) -> str:
    minutes, rest = divmod(ms, 60000)
    return f"{minutes}:{rest / 1000:06.3f}"


def _cues(cues: list[tuple[int, str]]) -> str:
    if not cues:
        return "none"
    return ", ".join(
        _stamp(ms) + (f" {label!r}" if label else "") for ms, label in cues
    )


def describe(change: TrackChange) -> list[str]:
    return [
        f"  {change.title or change.path}",
        f"    now:    {_cues(change.before)}",
        f"    after:  {_cues(change.after)}",
    ]


def cmd_write_memory_cues(lib: Library, args: argparse.Namespace) -> int:
    edits = lib.memory_cue_sets()
    if not edits:
        print("No memory cues have been edited in Dubcrate: nothing to write.")
        return 0
    try:
        db = Path(args.db).expanduser() if args.db else default_database()
        plan = plan_write(db, edits)
    except WriteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"rekordbox library: {db}")
    print(f"{len(edits)} track(s) with edited memory cues:")
    for change in plan.changes:
        print("\n".join(describe(change)))
    if plan.unchanged:
        print(f"{len(plan.unchanged)} already match rekordbox (nothing to write).")
    for key, why in plan.skipped:
        print(f"  skipped {key}: {why}")
    if plan.empty:
        if args.yes and plan.unchanged and not args.keep_edits:
            for key in plan.unchanged:
                lib.clear_memory_cue_key(key)
            print("Dropped the edits that rekordbox already has.")
        return 0
    if not args.yes:
        print(
            "\nDry run: nothing was written. Close rekordbox, then run again with --yes."
        )
        if rekordbox_running():
            print("(rekordbox is running now.)")
        return 0
    backup_dir = (
        Path(args.backup_dir).expanduser() if args.backup_dir else default_backup_dir()
    )
    try:
        outcome = apply_write(db, edits, backup_dir)
    except WriteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"\nWrote {len(outcome.written)} track(s): {outcome.cues_added} cue(s) added, "
        f"{outcome.cues_moved} changed, {outcome.cues_removed} removed."
    )
    print(f"The database's files were copied to {outcome.backup} first.")
    if not args.keep_edits:
        for key in outcome.done_keys:
            lib.clear_memory_cue_key(key)
        print(
            "Dubcrate's edits for these tracks are dropped: rekordbox's cues are used again."
        )
    return 0


def add_parsers(sub: argparse._SubParsersAction) -> None:
    rb = sub.add_parser("rekordbox", help="rekordbox's library").add_subparsers(
        dest="action", required=True
    )
    p = rb.add_parser(
        "write-memory-cues",
        help="write the memory cues edited in Dubcrate into rekordbox's master.db (dry run unless --yes)",
    )
    p.add_argument(
        "--db", help="a master.db to use (default: the installed rekordbox's)"
    )
    p.add_argument(
        "--backup-dir", help="default: rekordbox-backups next to the library"
    )
    p.add_argument(
        "--keep-edits",
        action="store_true",
        help="keep Dubcrate's edits after writing (they are dropped by default)",
    )
    p.add_argument(
        "--yes", action="store_true", help="write (rekordbox must be closed)"
    )
    p.set_defaults(fn=cmd_write_memory_cues)
