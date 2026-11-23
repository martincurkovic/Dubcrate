"""`dubcrate cues timing DISC...`: what offset sync will apply to each track's cues, and how far to trust it.

rekordbox's times on an MP3 are later than the audio on the disc by an amount that depends on the file's first
frame (`cue_timing`). This lists it per track, with the group of the measurements the file falls in, and ends with
one track from each group worth trying on the player. It reads the first few kilobytes of each file and nothing else.
"""

import argparse

from dubcrate import cue_timing
from dubcrate.library import Library

_GROUP_TITLES = {
    "lame": "LAME files (about 51 ms)",
    "ffmpeg": "ffmpeg files (about 25 ms, less certain)",
    "no-info": "no Info frame (no offset)",
    "left-alone": "left alone (encoder or delay not understood)",
    "not-mp3": "not MP3 (not measured, no offset)",
    "unreadable": "can't be read just now",
}


def cmd_timing(lib: Library, args: argparse.Namespace) -> int:
    first: dict[str, str] = {}
    counts: dict[str, int] = {}
    for disc_id in args.disc:
        disc = lib.disc(disc_id)
        print(f"{disc.name} (disc {disc_id}):")
        for number, entry in enumerate(lib.disc_entries(disc_id), 1):
            timing = cue_timing.describe(entry.track.path)
            counts[timing.group] = counts.get(timing.group, 0) + 1
            first.setdefault(timing.group, f"{disc.name} track {number}: {entry.title}")
            name = " - ".join(p for p in (entry.artist, entry.title) if p) or "Untitled"
            mark = "" if timing.sure else "  (less certain)"
            print(
                f"  {number:>2}  {timing.lead_ms:>3} ms  {timing.group:<10} {name}{mark}"
            )
    print()
    for group, title in _GROUP_TITLES.items():
        if group in counts:
            print(f"{counts[group]:>4} track(s): {title}")
    print("\nOne of each to try on the player:")
    for group, title in _GROUP_TITLES.items():
        if group in first:
            print(f"  {title}: {first[group]}")
    return 0


def add_parsers(sub: argparse._SubParsersAction) -> None:
    cues = sub.add_parser("cues", help="cue timing").add_subparsers(
        dest="action", required=True
    )
    p = cues.add_parser(
        "timing",
        help="the offset sync applies to each MP3's cues, and how far to trust it (reads files only)",
    )
    p.add_argument(
        "disc", type=int, nargs="+", help="disc ids (see: dubcrate disc list)"
    )
    p.set_defaults(fn=cmd_timing)
