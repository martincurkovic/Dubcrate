"""Decode audio files to CD-ready PCM (16-bit / 44.1 kHz / stereo WAV).

ffmpeg does the decoding and resampling; we stream its raw output through
Python so each track can be padded with silence to a whole number of CD
sectors without a second pass over the file.
"""

import json
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from dubcrate.cd import (
    BYTES_PER_SAMPLE_FRAME,
    CHANNELS,
    SAMPLE_RATE,
    SAMPLES_PER_SECTOR,
    samples_to_sectors,
)

_CHUNK = 1 << 20


class RenderError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceInfo:
    path: Path
    duration: float
    sample_rate: int | None
    bits: int | None
    title: str
    artist: str
    album: str


def _require(tool: str) -> str:
    found = shutil.which(tool)
    if not found:
        raise RenderError(f"{tool} not found on PATH")
    return found


def probe(path: Path) -> SourceInfo:
    """Read duration, format and basic tags from an audio file via ffprobe."""
    cmd = [
        _require("ffprobe"), "-v", "error", "-select_streams", "a:0",
        "-show_entries",
        ("stream=sample_rate,bits_per_raw_sample,bits_per_sample"
         ":format=duration:format_tags=title,artist,album"),
        "-of", "json", str(path),
    ]  # fmt: skip
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RenderError(f"ffprobe failed for {path}: {proc.stderr.strip()}")
    data = json.loads(proc.stdout)
    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format", {})
    tags = {k.lower(): v for k, v in fmt.get("tags", {}).items()}

    def _int(value: str | None) -> int | None:
        try:
            return int(value) if value and int(value) > 0 else None
        except ValueError:
            return None

    return SourceInfo(
        path=path,
        duration=float(fmt.get("duration") or 0.0),
        sample_rate=_int(stream.get("sample_rate")),
        bits=_int(stream.get("bits_per_raw_sample"))
        or _int(stream.get("bits_per_sample")),
        title=tags.get("title", path.stem),
        artist=tags.get("artist", ""),
        album=tags.get("album", ""),
    )


def render_track(src: Path, dst: Path) -> int:
    """Render `src` to `dst` as CD-ready WAV; returns the length in sectors.

    The output is padded with digital silence to a whole sector so that track
    boundaries on the disc fall exactly where the TOC says they do.
    """
    cmd = [
        _require("ffmpeg"), "-nostdin", "-v", "error", "-i", str(src),
        "-map", "0:a:0", "-vn",
        # Triangular high-passed dither when reducing bit depth (24-bit -> 16).
        "-af", f"aresample=osr={SAMPLE_RATE}:osf=s16:dither_method=triangular_hp",
        "-ac", str(CHANNELS), "-f", "s16le", "-",
    ]  # fmt: skip
    dst.parent.mkdir(parents=True, exist_ok=True)
    frames = 0
    carry = b""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None and proc.stderr is not None
    with wave.open(str(dst), "wb") as out:
        out.setnchannels(CHANNELS)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        while chunk := proc.stdout.read(_CHUNK):
            chunk = carry + chunk
            usable = len(chunk) - len(chunk) % BYTES_PER_SAMPLE_FRAME
            carry = chunk[usable:]
            out.writeframes(chunk[:usable])
            frames += usable // BYTES_PER_SAMPLE_FRAME
        pad = -frames % SAMPLES_PER_SECTOR
        if pad:
            out.writeframes(bytes(pad * BYTES_PER_SAMPLE_FRAME))
            frames += pad
    stderr = proc.stderr.read().decode(errors="replace").strip()
    if proc.wait() != 0 or frames == 0:
        dst.unlink(missing_ok=True)
        raise RenderError(f"ffmpeg failed for {src}: {stderr or 'no audio decoded'}")
    return samples_to_sectors(frames)
