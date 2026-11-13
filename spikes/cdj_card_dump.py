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


def msf(position: int) -> str:
    """Absolute frames as the TOC prints them."""
    seconds, frame = divmod(position, cc.FRAMES_PER_SECOND)
    return f"{seconds // 60}:{seconds % 60:02d}.{frame:02d}"


def into_track(d: cc.DiscId, track: int, position: int) -> str:
    """The time into a track, where the ID gives its start (track 1, track 2, the last)."""
    starts = {1: cc.LEAD_IN_FRAMES, 2: d.second_start, d.track_count: d.last_start}
    if track not in starts:
        return ""
    return f" ({msf(position - starts[track])} into the track)"


def render_id(d: cc.DiscId) -> str:
    return (
        f"{d.track_count} tracks, flags {d.flags:#06x}, lead-out {msf(d.lead_out)}, "
        f"track 2 at {msf(d.second_start)}, last track at {msf(d.last_start)}"
    )


def render_track_file(f: cc.TrackFile) -> list[str]:
    out = [f"info {f.info}", f"disc  {render_id(f.disc_id)}"]
    out.append(f"cues  {len(f.cues)}")
    for c in f.cues:
        what = (
            "loop"
            if c.is_loop
            else "cue "
            if c.kind == cc.KIND_CUE
            else f"kind {c.kind:#x}"
        )
        end = f" to {msf(c.end)}" if c.end else ""
        flag = f"  flag {c.flag:#x}" if c.flag else ""
        into = into_track(f.disc_id, c.track, c.position)
        out.append(
            f"  #{c.index} {what} track {c.track} at {msf(c.position)}{into}{end}{flag}"
        )
    for n, h in enumerate(f.hot_cues):
        if h is None:
            out.append(f"hot {'ABC'[n]}  -")
        else:
            out.append(
                f"hot {'ABC'[n]}  slot {h.slot} track {h.track} at {msf(h.position)}"
                f"{into_track(f.disc_id, h.track, h.position)}"
                f"  kind {h.kind:#x} end {h.end} extra {h.extra} tail {h.tail}"
            )
    for tag, records in (("W001", f.coarse), ("W002", f.fine)):
        for r in records:
            levels = (
                r.data.hex()
                if tag == "W001"
                else "".join(f"{n:x}" for n in r.nibbles())
            )
            filled = sum(1 for b in r.data if b)
            out.append(
                f"{tag} track {r.track} reserved {r.reserved.hex()} ({filled}/50 bytes set)"
            )
            out.append(f"  {levels}")
    out.append(f"pad   {f.pad.hex()}")
    return out


def render(path: Path) -> list[str]:
    if path.is_file():
        data = path.read_bytes()
        try:
            if data[8:12] == b"INDX":
                return render_index(cc.IndexFile.parse(data))
            return render_track_file(cc.TrackFile.parse(data))
        except cc.CardFormatError as exc:
            return [f"{path.name}: DOES NOT PARSE: {exc} ({len(data)} bytes)"]
    index, files = cc.read_card(path)
    out = render_index(index)
    for entry in index.entries:
        out.append(f"== {entry.file_name}")
        f = files[entry.name]
        out.extend(["  (no file yet)"] if f is None else render_track_file(f))
    return out


def render_index(index: cc.IndexFile) -> list[str]:
    out = [f"index version {index.version}, {len(index.entries)} discs"]
    out += [f"  {e.name}  {render_id(e.disc_id)}" for e in index.entries]
    return out


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
