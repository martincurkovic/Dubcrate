"""Burn spike: render tracks to CD PCM and burn a CD-TEXT audio disc.

Throwaway driver for proving the burn pipeline on real hardware. Everything
reusable lives in the `dubcrate` package; this file only wires it to a CLI.

    uv run spikes/burn_spike.py doctor
    uv run spikes/burn_spike.py tones work/tones
    uv run spikes/burn_spike.py plan work/tones/*  --work work/render
    uv run spikes/burn_spike.py toc  work/tones/*  --work work/render
    uv run spikes/burn_spike.py burn work/tones/* --work work/render \
        --backend discrecording            # dry run
    ... --yes                              # actually burns a disc
"""

import argparse
import subprocess
import sys
from pathlib import Path

from dubcrate.burn import BurnError, BurnOptions, Disc, DiscTrack
from dubcrate.burn import cdrdao as cdrdao_backend
from dubcrate.cd import (
    DISC_74_SECTORS,
    DISC_80_SECTORS,
    SECTORS_PER_SECOND,
    format_msf,
)
from dubcrate.render import RenderError, probe, render_track

TONES = [
    # (filename, ffmpeg encode args, sample rate, seconds, freq, title, artist)
    ("01-a.flac", ["-c:a", "flac", "-sample_fmt", "s32"], 48000, 40, 440, "Test Tone A (24-bit/48k)", "Dubcrate"),
    ("02-b.wav", ["-c:a", "pcm_s16le"], 44100, 30, 523, "Café Ünïcode ☆ Test", "Dubcrate Ünit"),
    ("03-c.mp3", ["-c:a", "libmp3lame", "-b:a", "320k"], 44100, 25, 659, "Test Tone C (MP3)", "Dubcrate"),
]  # fmt: skip


def cmd_doctor(_: argparse.Namespace) -> int:
    import shutil

    print("tools:")
    for tool in ("ffmpeg", "ffprobe", "cdrdao", "drutil"):
        print(f"  {tool:8s} {shutil.which(tool) or 'MISSING'}")
    if sys.platform == "darwin":
        from dubcrate.burn import discrecording as dr

        print("DiscRecording drives:")
        for dev in dr.list_devices():
            for key, value in dr.describe_device(dev).items():
                print(f"  {key:18s} {value}")
        print("drutil status:")
        out = subprocess.run(
            ["drutil", "status"], check=False, capture_output=True, text=True
        ).stdout
        print("  " + out.strip().replace("\n", "\n  "))
    if cdrdao_backend.find_cdrdao():
        print("cdrdao scanbus:")
        for dev, desc in cdrdao_backend.scan_devices():
            print(f"  {dev} : {desc}")
    return 0


def cmd_tones(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, enc, rate, secs, freq, title, artist in TONES:
        dst = out / name
        subprocess.run(
            [
                "ffmpeg", "-y", "-nostdin", "-v", "error",
                "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={secs}:sample_rate={rate}",
                "-af", "volume=0.25", "-ac", "2", *enc,
                "-metadata", f"title={title}", "-metadata", f"artist={artist}",
                str(dst),
            ],
            check=True,
        )  # fmt: skip
        print(f"wrote {dst}")
    return 0


def prepare(files: list[str], work: Path, title: str, performer: str) -> Disc:
    disc = Disc(title=title, performer=performer)
    for name in files:
        src = Path(name)
        info = probe(src)
        dst = work / f"{len(disc.tracks) + 1:02d}-{src.stem}.wav"
        sectors = render_track(src, dst)
        disc.tracks.append(
            DiscTrack(dst, info.title, info.artist or performer, sectors)
        )
        fmt = f"{info.sample_rate or '?'} Hz / {info.bits or '?'}-bit"
        print(
            f"  {len(disc.tracks):2d}. {format_msf(sectors)}  {info.title}  [{fmt} -> 44100 Hz / 16-bit]"
        )
    return disc


def pregap_sectors(args: argparse.Namespace) -> int:
    return round(args.pregap * SECTORS_PER_SECOND)


def print_plan(disc: Disc, pregap: int = 0) -> None:
    total = disc.used_sectors(pregap)
    print(
        f"total {format_msf(total)}  ({total} sectors, pregap {pregap} between tracks)"
    )
    for label, cap in (("74 min", DISC_74_SECTORS), ("80 min", DISC_80_SECTORS)):
        verdict = "fits" if total <= cap else "TOO LONG"
        print(f"  {label}: {verdict} ({format_msf(max(cap - total, 0))} free)")


def cmd_plan(args: argparse.Namespace) -> int:
    disc = prepare(args.files, Path(args.work), args.title, args.performer)
    print_plan(disc, pregap_sectors(args))
    return 0


def cmd_toc(args: argparse.Namespace) -> int:
    disc = prepare(args.files, Path(args.work), args.title, args.performer)
    print(cdrdao_backend.build_toc(disc, pregap_sectors(args)))
    return 0


def cmd_burn(args: argparse.Namespace) -> int:
    disc = prepare(args.files, Path(args.work), args.title, args.performer)
    pregap = pregap_sectors(args)
    print_plan(disc, pregap)
    if not disc.fits(DISC_80_SECTORS, pregap):
        print("refusing: disc does not fit on an 80 min CD-R", file=sys.stderr)
        return 2
    if args.backend == "discrecording":
        from dubcrate.burn.discrecording import DiscRecordingBurner

        burner = DiscRecordingBurner()
    else:
        burner = cdrdao_backend.CdrdaoBurner()
    options = BurnOptions(
        speed=args.speed,
        simulate=args.simulate,
        pregap_sectors=pregap,
        device=args.device,
    )
    mode = "SIMULATED " if args.simulate else ""
    if not args.yes:
        print(
            f"\ndry run: would {mode}burn {len(disc.tracks)} tracks with {burner.name} at {args.speed}x."
        )
        print("re-run with --yes to burn (uses up the blank CD-R).")
        return 0
    try:
        burner.burn(disc, options, progress=print)
    except BurnError as exc:
        print(f"burn failed: {exc}", file=sys.stderr)
        return 1
    print("done")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="check tools, drives and media").set_defaults(
        fn=cmd_doctor
    )

    p = sub.add_parser("tones", help="generate synthetic test tracks")
    p.add_argument("out")
    p.set_defaults(fn=cmd_tones)

    for name, fn, help_ in (
        ("plan", cmd_plan, "render and report disc length"),
        ("toc", cmd_toc, "render and print the cdrdao TOC"),
        ("burn", cmd_burn, "render and burn (dry run unless --yes)"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("files", nargs="+")
        p.add_argument("--work", required=True, help="directory for rendered WAVs")
        p.add_argument("--title", default="Dubcrate Test")
        p.add_argument("--performer", default="Dubcrate")
        p.add_argument(
            "--pregap",
            type=float,
            default=0.0,
            help="seconds of silence before tracks 2+ (default 0 = gapless)",
        )
        p.set_defaults(fn=fn)
        if name == "burn":
            p.add_argument(
                "--backend", choices=["discrecording", "cdrdao"], required=True
            )
            p.add_argument("--speed", type=int, default=10)
            p.add_argument("--device", default=None)
            p.add_argument(
                "--simulate", action="store_true", help="laser-off test write"
            )
            p.add_argument("--yes", action="store_true", help="really burn")

    args = parser.parse_args()
    try:
        return args.fn(args)
    except RenderError as exc:
        print(f"render failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
