"""Make the waveform test disc, or read what the player recorded from it.

    uv run spikes/wave_test_disc.py make OUTDIR
        writes the disc's tracks (lossless, checked sample by sample), manifest.json and a README
        into a new OUTDIR (keep it outside the repository: it is about 300 MB)
    uv run spikes/wave_test_disc.py fit MANIFEST CARD
        reads the disc's file from the card (its root, its PIONEER folder, or a copy of PIONEER/CDJ) and
        prints what the player's waveform values follow, and what each value stands for

`dev_docs/wave-test-disc.md` is the whole procedure. The logic is `dubcrate.wavetest` and `dubcrate.wavefit`.
"""

import argparse
import json
import sys
from pathlib import Path

from dubcrate import cdj_card as cc
from dubcrate import tools, wavefit, wavetest


def make(args: argparse.Namespace) -> int:
    ffmpeg = tools.find_tool("ffmpeg")
    if ffmpeg is None:
        print("error: ffmpeg was not found", file=sys.stderr)
        return 1
    tracks = wavetest.build_tracks()
    try:
        wavetest.write_disc(Path(args.out).expanduser(), tracks, ffmpeg.path)
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    seconds = sum(t.seconds for t in tracks)
    print(
        f"\n{len(tracks)} tracks, {seconds / 60:.1f} minutes. Read {Path(args.out) / 'README.txt'} next."
    )
    return 0


def fit(args: argparse.Namespace) -> int:
    tracks = wavetest.tracks_from_manifest(
        json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    )
    try:
        index, files = cc.read_card(Path(args.card).expanduser())
    except cc.CardFormatError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    wanted = cc.DiscId.from_toc(*cc.toc_from_lengths([t.sectors for t in tracks]))
    entry = index.find(wanted)
    if (
        entry is None
    ):  # the TOC the disc really has can differ a little: take the one with this many tracks
        like = [e for e in index.entries if e.disc_id.track_count == len(tracks)]
        if len(like) != 1:
            print(
                f"error: no disc of {len(tracks)} tracks (exactly one) on the card",
                file=sys.stderr,
            )
            return 1
        entry = like[0]
        print(
            f"note: the card's entry for the disc ({entry.file_name}) differs from the planned TOC"
        )
    file = files.get(entry.name)
    if file is None:
        print(
            f"error: {entry.file_name} has no file yet: the disc has not been played",
            file=sys.stderr,
        )
        return 1
    _fit, lines = wavefit.analyse(tracks, file)
    print("\n".join(lines))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("make", help="write the disc's files")
    p.add_argument("out")
    p.set_defaults(fn=make)
    p = sub.add_parser("fit", help="read the player's result")
    p.add_argument("manifest")
    p.add_argument("card")
    p.set_defaults(fn=fit)
    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
