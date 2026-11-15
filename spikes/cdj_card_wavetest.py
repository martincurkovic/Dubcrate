"""Make a card image for the waveform WRITE test: a COPY of an image with waveform records written by us.

Throwaway tool for `dev_docs/card-sync.md` ("Writing a waveform"). It copies BASE to OUT (never touches
BASE), mounts only the copy read-write, writes one disc's file (and an index entry if the disc is new)
with `dubcrate.cdj_card` and `dubcrate.wave_model`, and detaches. Put OUT on a spare card with
`cdj_card_restore.sh`, load the disc in the player and see what it shows. The player is not known to
accept a file with complete waveform records, nor whether it keeps them when the track is played.

    uv run spikes/cdj_card_wavetest.py BASE.img OUT.img --toc "CD5 TOC.txt" --audio ~/cdj-card/cd5-rip \\
        --pattern 1 --model 2 --half 4 [--dry-run]

--toc      the disc's `drutil toc` output (its ID comes from it); required
--audio    a folder holding the disc's tracks as audio files, each named with its track number first
           ("1 Audio Track.aiff", "03 Title.flac"): read for --model and --half
--pattern  TRACK: a sawtooth, 1 to 15 and round again every 15 columns, that nobody could take for music
--model    TRACK: what `wave_model` says the player would draw for the track's audio
--half     TRACK: the model's first 50 columns, the other 50 left at 0 (not played)
--absolute the model on the player's own scale (the default raises a quiet track so its loudest column reaches 15)
(a track given by none of them gets an empty slot, as the player leaves one before a track it did play)

The disc must be new to BASE (a disc with a file already is refused). macOS only; `--dry-run` writes no
image and needs no card, and prints what would be written.
"""

import argparse
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from dubcrate import cdj_card as cc
from dubcrate import tools, wave_check, wave_model, wavetest


def run(*cmd: str) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, encoding="utf-8").stdout


def attach(image: Path) -> tuple[str, str]:
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


def columns_for(
    track: int,
    mode: str,
    files: dict[int, Path],
    ffmpeg: Path,
    scale: bool = True,
) -> list[int]:
    if mode == "pattern":
        return wave_model.pattern_values()
    if track not in files:
        sys.exit(f"no audio file for track {track} in the --audio folder")
    audio = wavetest.decode(files[track], ffmpeg)
    values = wave_model.column_values(audio, scale_to_top=scale)
    print(
        f"  track {track}: {len(audio) / wave_model.RATE:.1f} s from {files[track].name}"
    )
    return values[:50] + [0] * 50 if mode == "half" else values


def plan(args: argparse.Namespace) -> dict[int, tuple[str, list[int]]]:
    wanted = {}
    for mode in ("pattern", "model", "half"):
        for track in getattr(args, mode):
            if track in wanted:
                sys.exit(f"track {track} is given twice")
            wanted[track] = mode
    if not wanted:
        sys.exit("give at least one of --pattern, --model, --half")
    ffmpeg = tools.find_tool("ffmpeg")
    if ffmpeg is None:
        sys.exit("ffmpeg was not found")
    files = wave_check.audio_files(args.audio) if args.audio else {}
    return {
        t: (m, columns_for(t, m, files, ffmpeg.path, not args.absolute))
        for t, m in sorted(wanted.items())
    }


def build(file: cc.TrackFile, tracks: dict[int, tuple[str, list[int]]]) -> None:
    file.coarse, file.fine = [], []
    for n in range(1, max(tracks) + 1):
        if n in tracks:
            coarse, fine = wave_model.records(n, tracks[n][1])
        else:
            coarse = fine = cc.WaveRecord.unplayed()
        file.coarse.append(coarse)
        file.fine.append(fine)


def describe(tracks: dict[int, tuple[str, list[int]]]) -> None:
    for n, (mode, values) in tracks.items():
        print(f"  track {n} ({mode}): {' '.join(f'{v:2d}' for v in values[:30])} ...")


def edit(card: Path, toc: Path, tracks: dict[int, tuple[str, list[int]]]) -> None:
    starts, lead_out = cc.toc_from_drutil(toc.read_text(encoding="utf-8"))
    if max(tracks) > len(starts):
        sys.exit(f"track {max(tracks)}: this disc has {len(starts)} tracks")
    disc_id = cc.DiscId.from_toc(starts, lead_out)
    index, files = cc.read_card(card)
    known = index.find(disc_id) is not None
    entry = index.add(disc_id)
    if files.get(entry.name) is not None:
        sys.exit(
            f"{entry.name} already has a file on this card: use a disc the card has not seen"
        )
    file = cc.TrackFile.new(disc_id)
    build(file, tracks)
    cc.write_card(card, index, {entry.name: file})
    print(
        f"{entry.name}: {'existing disc without a file' if known else 'NEW disc, added to the index'}, waveform slots {len(file.fine)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("base", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--toc", type=Path, required=True)
    parser.add_argument("--audio", type=Path)
    for mode in ("pattern", "model", "half"):
        parser.add_argument(f"--{mode}", type=int, action="append", default=[])
    parser.add_argument("--absolute", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    tracks = plan(args)
    describe(tracks)
    starts, lead_out = cc.toc_from_drutil(args.toc.read_text(encoding="utf-8"))
    print(f"disc: {len(starts)} tracks, {cc.DiscId.from_toc(starts, lead_out)}")
    if args.dry_run:
        print("dry run: no image written")
        return
    if args.out.exists():
        sys.exit(f"{args.out} already exists; pick another name")
    shutil.copyfile(args.base, args.out)
    disk, partition = attach(args.out)
    try:
        run("diskutil", "mount", "-mountOptions", "nobrowse", partition)
        card = mount_point(partition)
        try:
            edit(card, args.toc, tracks)
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
