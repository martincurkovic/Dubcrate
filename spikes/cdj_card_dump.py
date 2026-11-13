"""Dump and diff CDJ-1000MK3 card files (`PIONEER/CDJ/*.PDJ`) with `dubcrate.cdj_card`.

Throwaway analysis tool for `dev_docs/cdj1000mk3-card.md`. It only reads files.

    uv run spikes/cdj_card_dump.py dump PATH
    uv run spikes/cdj_card_dump.py diff BEFORE AFTER

PATH is a card (its root, `PIONEER` or `PIONEER/CDJ` folder, e.g. an image mounted read-only,
see the doc) or one `.PDJ` file. `diff` compares two of either as text, field by field, so a
change shows as the cue or record that changed and not as bytes. A file that fails to parse
(wrong checksum, an unknown layout) says so, which is the thing to look at first when the player
writes something new.
"""

import argparse
import difflib
import sys
from pathlib import Path

from dubcrate import cdj_card as cc
from dubcrate.card_report import describe_card, describe_index, describe_track_file


def render(path: Path) -> list[str]:
    if path.is_file():
        data = path.read_bytes()
        try:
            if data[8:12] == b"INDX":
                return describe_index(cc.IndexFile.parse(data))
            return describe_track_file(cc.TrackFile.parse(data))
        except cc.CardFormatError as exc:
            return [f"{path.name}: DOES NOT PARSE: {exc} ({len(data)} bytes)"]
    return describe_card(*cc.read_card(path))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("dump")
    one.add_argument("path", type=Path)
    two = sub.add_parser("diff")
    two.add_argument("before", type=Path)
    two.add_argument("after", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "dump":
            print("\n".join(render(args.path)))
        else:
            diff = difflib.unified_diff(
                render(args.before),
                render(args.after),
                "before",
                "after",
                lineterm="",
                n=1,
            )
            print("\n".join(diff) or "no differences")
    except cc.CardFormatError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
