"""Make a card image for the write test: a COPY of an image with one disc's cues set by us.

Throwaway tool for `dev_docs/card-sync.md` (Phase 0). It copies BASE to OUT (never touches BASE),
mounts only the copy read-write, writes the disc's file (and an index entry if the disc is new)
with `dubcrate.cdj_card`, and detaches. Put OUT on the card with `cdj_card_restore.sh`, load the
disc in the player, and see whether it shows the cues. Put the card back with its own image.

    uv run spikes/cdj_card_testimage.py BASE.img OUT.img --toc "CD1 TOC.txt" \\
        --cue 1:20 --cue 2:45.5 --loop 1:50:52

--toc     the disc's `drutil toc` output (its ID and track starts come from it); required
--cue     TRACK:SECONDS, a memory cue that long into the track (repeat for more)
--loop    TRACK:START:END in seconds into the track
--add     keep the disc's cues already in the file and add these (default: replace them)
--waveform  keep (default) or drop the file's waveform records, to test a file without them

If the TOC is not in the index yet the disc is added to it, and a disc with no file gets a new
one. Paths: BASE and OUT are given as written (images live in ~/cdj-card/). macOS only.
"""

import argparse
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from dubcrate import cdj_card as cc


def run(*cmd: str) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, encoding="utf-8").stdout


def attach(image: Path) -> tuple[str, str]:
    """Attach an image read-write without mounting; return the whole disk and its FAT slice."""
    out = run(
        "hdiutil",
        "attach",
        "-nomount",
        "-imagekey",
        "diskimage-class=CRawDiskImage",
        str(image),
    )
    slices = [line.split()[0] for line in out.splitlines() if "FAT" in line]
    if len(slices) != 1:
        sys.exit(
            f"expected one FAT partition in {image.name}, found {len(slices)}:\n{out}"
        )
    return slices[0].rsplit("s", 1)[0], slices[0]


def mount_point(device: str) -> Path:
    info = plistlib.loads(
        subprocess.run(
            ["diskutil", "info", "-plist", device], check=True, capture_output=True
        ).stdout
    )
    return Path(info["MountPoint"])


def position(starts: list[int], track: int, seconds: float) -> int:
    if not 1 <= track <= len(starts):
        sys.exit(f"track {track}: this disc has {len(starts)} tracks")
    return starts[track - 1] + round(seconds * cc.FRAMES_PER_SECOND)


def edit(card: Path, args: argparse.Namespace) -> None:
    starts, lead_out = cc.toc_from_drutil(args.toc.read_text(encoding="utf-8"))
    disc_id = cc.DiscId.from_toc(starts, lead_out)
    index, files = cc.read_card(card)
    known = index.find(disc_id) is not None
    entry = index.add(disc_id)
    file = files.get(entry.name) or cc.TrackFile.new(disc_id)
    cues = list(file.cues) if args.add else []
    for spec in args.cue:
        track, seconds = spec.split(":")
        cues.append(
            cc.CueEntry.memory(
                len(cues), position(starts, int(track), float(seconds)), int(track)
            )
        )
    for spec in args.loop:
        track, start, end = spec.split(":")
        cues.append(
            cc.CueEntry.loop(
                len(cues),
                position(starts, int(track), float(start)),
                position(starts, int(track), float(end)),
                int(track),
            )
        )
    file.cues = cues
    if args.waveform == "drop":
        file.coarse, file.fine = [], []
    cc.write_card(card, index, {entry.name: file})
    print(
        f"{entry.name}: {'existing disc' if known else 'NEW disc, added to the index'}, "
        f"{len(file.cues)} cues, waveform records {len(file.coarse)}"
    )
    for cue in file.cues:
        kind = "loop" if cue.is_loop else "cue "
        print(
            f"  #{cue.index} {kind} track {cue.track} position {cue.position}"
            + (f" to {cue.end}" if cue.end else "")
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("base", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--toc", type=Path, required=True)
    parser.add_argument("--cue", action="append", default=[])
    parser.add_argument("--loop", action="append", default=[])
    parser.add_argument("--add", action="store_true")
    parser.add_argument("--waveform", choices=("keep", "drop"), default="keep")
    args = parser.parse_args()
    if not (args.cue or args.loop):
        sys.exit("give at least one --cue or --loop")
    if args.out.exists():
        sys.exit(f"{args.out} already exists; pick another name")
    shutil.copyfile(args.base, args.out)
    disk, partition = attach(args.out)
    try:
        run("diskutil", "mount", "-mountOptions", "nobrowse", partition)
        card = mount_point(partition)
        try:
            edit(card, args)
        finally:
            run("diskutil", "unmount", partition)
    except BaseException:
        args.out.unlink(missing_ok=True)  # a half-made test image is worse than none
        raise
    finally:
        subprocess.run(["hdiutil", "detach", disk], check=False, capture_output=True)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    try:
        main()
    except cc.CardFormatError as exc:
        sys.exit(str(exc))
