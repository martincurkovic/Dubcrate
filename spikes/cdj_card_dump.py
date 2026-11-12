"""Dump, check and diff CDJ-1000MK3 card files (``PIONEER/MPJ/*.PDJ`` and friends).

Throwaway analysis tool for ``dev_docs/cdj1000mk3-card.md``. Everything it assumes comes
from reading the firmware, not from a real card, so a first run on a real file is a test
of that document. It only reads files.

    uv run spikes/cdj_card_dump.py dump CDJ00001.PDJ [--kind cd|mp3]
    uv run spikes/cdj_card_dump.py diff before.PDJ after.PDJ

``dump`` prints the header, finds every known chunk tag (at the offsets the firmware
uses, and anywhere else, in case it is wrong), shows the 16-byte disc ID, the non-empty
entries of the cue/loop and hot cue chunks, and tests the checksum guess: the sum of
the bytes before ``CSUM`` as 16-bit big-endian words, low 16 bits.
"""

from __future__ import annotations

import argparse
from pathlib import Path

TAGS = (b"INFO", b"DID ", b"CN11", b"HC11", b"W001", b"W002", b"CSUM", b"INDX")
# Fixed offsets from the template builders (0xAC0E4996 audio CD, 0xAC0E5DA6 MP3).
OFFSETS = {
    "cd": {
        "INFO": 0x08,
        "DID ": 0x10,
        "CN11": 0x28,
        "HC11": 0x804,
        "W001": 0x848,
        "W002": 0x1DF8,
    },
    "mp3": {"INFO": 0x08, "DID ": 0x10, "CN11": 0x28, "HC11": 0x33C4, "W001": 0x3558},
}
ENTRY_SIZE = {"cd": 20, "mp3": 132}  # guessed from the chunk sizes


def guess_kind(path: Path) -> str:
    return "mp3" if path.name.upper().startswith("MPJ") else "cd"


def checksum(data: bytes, end: int) -> int:
    """Sum of 16-bit big-endian words in data[:end & ~1], low 16 bits."""
    return (
        sum(int.from_bytes(data[i : i + 2], "big") for i in range(0, end & ~1, 2))
        & 0xFFFF
    )


def find_all(data: bytes, tag: bytes) -> list[int]:
    found, start = [], 0
    while (i := data.find(tag, start)) >= 0:
        found.append(i)
        start = i + 1
    return found


def chunk_at(data: bytes, offsets: dict[str, int], position: int) -> str:
    """The chunk a byte position falls in, by the expected offsets."""
    name = "header"
    for tag, offset in sorted(offsets.items(), key=lambda item: item[1]):
        if position >= offset:
            name = tag.strip()
    return name


def dump(path: Path, kind: str) -> None:
    data = path.read_bytes()
    offsets = OFFSETS[kind]
    print(f"{path.name}: {len(data)} bytes, treated as {kind}")
    print(f"  header {data[:8]!r}")
    for tag in TAGS:
        found = find_all(data, tag)
        expected = offsets.get(tag.decode())
        note = ""
        if expected is not None:
            note = (
                "  as expected"
                if expected in found
                else f"  NOT at expected {expected:#x}"
            )
        print(f"  {tag.decode()!r}: {[hex(i) for i in found[:6]]}{note}")
    did = data[0x18:0x28]
    print(f"  DID payload (16 bytes at 0x18): {did.hex(' ')}")
    size = ENTRY_SIZE[kind]
    for tag in ("CN11", "HC11"):
        start = offsets[tag] + 8
        end = {"CN11": offsets["HC11"], "HC11": offsets["W001"]}[tag]
        entries = [data[i : i + size] for i in range(start, end - size + 1, size)]
        used = [(n, e) for n, e in enumerate(entries) if any(e)]
        print(f"  {tag}: {len(entries)} slots of {size} bytes, {len(used)} non-empty")
        for n, entry in used[:12]:
            print(f"    [{n}] {entry.hex(' ')}")
    csums = find_all(data, b"CSUM")
    if csums:
        at = csums[-1]
        actual = data[at + 8 : at + 10]
        want = checksum(data, at)
        status = "MATCH" if actual == want.to_bytes(2, "big") else "different"
        print(
            f"  CSUM at {at:#x}: stored {actual.hex()}, computed {want:04x} -> {status}"
        )
        print(f"  CSUM chunk bytes: {data[at : at + 12].hex(' ')}")


def diff(before: Path, after: Path, kind: str) -> None:
    a, b = before.read_bytes(), after.read_bytes()
    offsets = OFFSETS[kind]
    print(f"{before.name} ({len(a)} bytes) vs {after.name} ({len(b)} bytes)")
    ranges, start = [], None
    for i in range(max(len(a), len(b))):
        same = i < len(a) and i < len(b) and a[i] == b[i]
        if not same and start is None:
            start = i
        elif same and start is not None:
            ranges.append((start, i))
            start = None
    if start is not None:
        ranges.append((start, max(len(a), len(b))))
    print(f"{len(ranges)} differing ranges")
    for lo, hi in ranges[:60]:
        print(
            f"  {lo:#06x}-{hi:#06x} ({chunk_at(a, offsets, lo)}): "
            f"{a[lo:hi][:24].hex(' ')}  ->  {b[lo:hi][:24].hex(' ')}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("dump")
    one.add_argument("file", type=Path)
    two = sub.add_parser("diff")
    two.add_argument("before", type=Path)
    two.add_argument("after", type=Path)
    for p in (one, two):
        p.add_argument("--kind", choices=("cd", "mp3"))
    args = parser.parse_args()
    if args.command == "dump":
        dump(args.file, args.kind or guess_kind(args.file))
    else:
        diff(args.before, args.after, args.kind or guess_kind(args.before))


if __name__ == "__main__":
    main()
