"""Decode a Pioneer CDJ-1000MK3 firmware ``.UPD`` file into a flat memory image.

Throwaway analysis tool for ``dev_docs/cdj1000mk3-card.md``. It reads the update
file and writes bytes; it never talks to a player and ships no firmware.

The updater ``CDJ1000MK3_*.EXE`` is an LHa self-extracting archive (``7zz x`` opens
it) holding ``CDJ1K3MN.UPD`` (main CPU) and ``G11.UPD``. Each ``.UPD`` is an
ASCII-ish transport, reverse-engineered here:

- an 18-byte header line, e.g. ``CDJ1K3  MAIN2.20\\r\\n``, then records;
- a record starts with ``0x1f`` and a tag (``10``, ``18``, ``2b`` or ``22 54``),
  then 3 address bytes, 16 data bytes and 1 check byte;
- every byte is stored as ``plain + 0x40`` (mod 256); a stored byte below ``0x40``
  would clash with the framing, so it is sent as two bytes, ``0x3a + high nibble``
  then ``0x30 + low nibble``;
- the address counts bytes and steps 16 per record; a few tail records are not data.

Usage:
    uv run spikes/cdj_upd_decode.py CDJ1K3MN.UPD            # report only
    uv run spikes/cdj_upd_decode.py CDJ1K3MN.UPD -o main.bin   # flat image, gaps 0xff

Run it on a copy, in a scratch directory; keep firmware out of the repository.
"""

from __future__ import annotations

import argparse
from pathlib import Path

HEADER_LEN = 0x12
FRAME = 0x1F
LINE = 16
RECORD_LEN = 3 + LINE + 1  # address, data, check
ESCAPE_FIRST = range(0x3A, 0x3F)
NIBBLE = range(0x30, 0x40)


def unescape(raw: bytes) -> bytes:
    """Undo the transport: ``stored - 0x40``, with the two-byte escape for low values."""
    out = bytearray()
    i = 0
    while i < len(raw):
        c = raw[i]
        if c in ESCAPE_FIRST and i + 1 < len(raw) and raw[i + 1] in NIBBLE:
            stored = ((c - 0x3A) << 4) | (raw[i + 1] - 0x30)
            i += 2
        else:
            stored = c
            i += 1
        out.append((stored - 0x40) & 0xFF)
    return bytes(out)


def read_records(path: Path) -> list[bytes]:
    """The decoded bytes of each record (tag removed): address, data, check."""
    body = path.read_bytes()[HEADER_LEN:]
    starts = [i for i, b in enumerate(body) if b == FRAME]
    ends = starts[1:] + [len(body)]
    records = []
    for start, end in zip(starts, ends, strict=True):
        record = body[start + 1 : end]
        tag, record = record[0], record[1:]
        if tag == 0x22 and record[:1] == b"\x54":
            record = record[1:]
        records.append(unescape(record))
    return records


def build_image(records: list[bytes]) -> dict[int, bytes]:
    """Map address to its 16 data bytes. Raises if two records disagree."""
    lines: dict[int, bytes] = {}
    for record in records:
        if len(record) != RECORD_LEN:
            continue
        address = int.from_bytes(record[:3], "big")
        data = record[3 : 3 + LINE]
        if lines.setdefault(address, data) != data:
            raise ValueError(f"conflicting data at {address:#08x}")
    return lines


def segments(lines: dict[int, bytes]) -> list[tuple[int, int]]:
    """Contiguous (start, end) address ranges."""
    found: list[tuple[int, int]] = []
    for address in sorted(lines):
        if found and found[-1][1] == address:
            found[-1] = (found[-1][0], address + LINE)
        else:
            found.append((address, address + LINE))
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("upd", type=Path)
    parser.add_argument("-o", "--out", type=Path, help="write a flat image here")
    args = parser.parse_args()

    records = read_records(args.upd)
    lines = build_image(records)
    odd = [r for r in records if len(r) != RECORD_LEN]
    ranges = segments(lines)
    print(
        f"{args.upd.name}: {len(records)} records, {len(lines)} lines, {len(odd)} non-data"
    )
    print(
        f"address {ranges[0][0]:#08x} to {ranges[-1][1]:#08x}, {len(ranges)} segments"
    )
    for start, end in ranges:
        print(f"  {start:#08x}-{end:#08x} ({end - start} bytes)")
    if args.out:
        base = ranges[0][0]
        image = bytearray(b"\xff" * (ranges[-1][1] - base))
        for address, data in lines.items():
            image[address - base : address - base + LINE] = data
        args.out.write_bytes(image)
        print(f"wrote {args.out} ({len(image)} bytes), load it at {base:#08x}")


if __name__ == "__main__":
    main()
