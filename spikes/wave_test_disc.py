"""Make the waveform test disc, or read what the player recorded from it.

    uv run spikes/wave_test_disc.py make OUTDIR
        writes the disc's tracks (lossless, checked sample by sample), manifest.json and a README
        into a new OUTDIR (keep it outside the repository: it is about 300 MB)
    uv run spikes/wave_test_disc.py probes OUTDIR [--first N]
        the second disc's ten probe tracks (they follow N - 1 real tracks on the disc; default N = 10)
(what the player wrote is read back with `dubcrate card check-waveforms CARD --toc TOC --audio DIR`)

`dev_docs/wave-test-disc.md` is the whole procedure. The logic is `dubcrate.wavetest`; the model is `dubcrate.wave_model`.
"""

import argparse
import sys
from pathlib import Path

from dubcrate import tools, wavetest


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


def probes(args: argparse.Namespace) -> int:
    ffmpeg = tools.find_tool("ffmpeg")
    if ffmpeg is None:
        print("error: ffmpeg was not found", file=sys.stderr)
        return 1
    tracks = wavetest.build_probe_tracks(args.first)
    try:
        wavetest.write_disc(
            Path(args.out).expanduser(),
            tracks,
            ffmpeg.path,
            title=wavetest.PROBE_DISC_TITLE,
        )
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    seconds = sum(t.seconds for t in tracks)
    print(f"\n{len(tracks)} probe tracks, {seconds / 60:.1f} minutes.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("make", help="write the disc's files")
    p.add_argument("out")
    p.set_defaults(fn=make)
    p = sub.add_parser("probes", help="write the second disc's probe tracks")
    p.add_argument("out")
    p.add_argument(
        "--first", type=int, default=10, help="the number of the first probe track"
    )
    p.set_defaults(fn=probes)
    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
