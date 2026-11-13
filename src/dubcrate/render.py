"""Decode audio files to CD-ready PCM (16-bit / 44.1 kHz / stereo WAV).

ffmpeg does the decoding and resampling; we stream its raw output through
Python so each track can be padded with silence to a whole number of CD
sectors without a second pass over the file.
"""

import json
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
from dubcrate.mp3 import LOSSY_CODECS, cbr_target, is_mp3, scan_mp3
from dubcrate.tools import find_tool, missing_message

_CHUNK = 1 << 20


class RenderError(RuntimeError):
    pass


class ToolMissingError(RenderError):
    """ffmpeg or ffprobe can't be found. An expected failure: the message is for the user."""


@dataclass(frozen=True)
class SourceInfo:
    path: Path
    duration: float
    sample_rate: int | None
    bits: int | None
    title: str
    artist: str
    album: str
    genre: str = ""
    year: int | None = None
    comment: str = ""
    bpm: float | None = None
    key: str = ""
    bitrate: int | None = None  # kbit/s
    codec: str = ""


def _first(tags: dict[str, str], *names: str) -> str:
    for name in names:
        if tags.get(name):
            return tags[name].strip()
    return ""


def _parse_bpm(value: str) -> float | None:
    try:
        bpm = float(value)
    except ValueError:
        return None
    return bpm if 20 <= bpm <= 400 else None


def _parse_year(value: str) -> int | None:
    head = value[:4]
    return int(head) if head.isdigit() and 1000 <= int(head) <= 2999 else None


def _require(tool: str) -> str:
    found = find_tool(tool)
    if found is None:
        raise ToolMissingError(missing_message(tool))
    return str(found.path)


def probe(path: Path) -> SourceInfo:
    """Read duration, format and basic tags from an audio file via ffprobe."""
    cmd = [
        _require("ffprobe"), "-v", "error", "-select_streams", "a:0",
        "-show_entries",
        ("stream=codec_name,sample_rate,bits_per_raw_sample,bits_per_sample"
         ":format=duration,bit_rate:format_tags"),
        "-of", "json", str(path),
    ]  # fmt: skip
    # (ffprobe writes UTF-8; `text=True` alone would decode it with the system's code page, so on Windows
    # an accent in a tag came out as mojibake)
    proc = subprocess.run(
        cmd, check=False, capture_output=True, encoding="utf-8", errors="replace"
    )
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

    # ffprobe exits 0 for some corrupt files but reports no duration for them.
    duration = float(fmt.get("duration") or 0.0)
    if duration <= 0:
        raise RenderError(f"no playable audio found in {path}")

    return SourceInfo(
        path=path,
        duration=duration,
        sample_rate=_int(stream.get("sample_rate")),
        bits=_int(stream.get("bits_per_raw_sample"))
        or _int(stream.get("bits_per_sample")),
        title=tags.get("title", path.stem),
        artist=tags.get("artist", ""),
        album=tags.get("album", ""),
        genre=_first(tags, "genre"),
        year=_parse_year(_first(tags, "date", "year", "tdrc", "tyer")),
        comment=_first(tags, "comment", "description"),
        bpm=_parse_bpm(_first(tags, "bpm", "tbpm", "tmpo")),
        key=_first(tags, "initialkey", "initial_key", "tkey", "key"),
        bitrate=(_int(fmt.get("bit_rate")) or 0) // 1000 or None,
        codec=stream.get("codec_name", ""),
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


def render_mp3(
    src: Path,
    dst: Path,
    *,
    title: str,
    artist: str,
    album: str = "",
    number: int | None = None,
    max_bitrate: int = 320,
) -> int:
    """Write `src` to `dst` as an MP3 for an MP3 CD; returns the file's size in bytes.

    Always constant bitrate (CDJs are slow to load variable-bitrate files). The rate is
    `max_bitrate` (320), or less for a lossy source that is below it: that source's own rate,
    at the next standard step up (`mp3.cbr_target`). An MP3 that is already constant bitrate at
    or under `max_bitrate` is copied without re-encoding (a second lossy pass would only lose
    quality); a variable-bitrate one is encoded to constant. Tags are written fresh from the
    disc's own text (title and artist as edited for this disc) and cover art is left off.
    `src` is only read.
    """
    cmd = [
        _require("ffmpeg"), "-nostdin", "-v", "error", "-y", "-i", str(src),
        "-map", "0:a:0", "-vn", "-map_metadata", "-1",
        "-metadata", f"title={title}", "-metadata", f"artist={artist}",
    ]  # fmt: skip
    if album:
        cmd += ["-metadata", f"album={album}"]
    if number is not None:
        cmd += ["-metadata", f"track={number}"]
    rate = _mp3_plan(src, max_bitrate)
    if rate is None:
        cmd += ["-c:a", "copy"]
    else:
        # (-b:a without -abr is constant bitrate in libmp3lame)
        cmd += ["-c:a", "libmp3lame", "-b:a", f"{rate}k", "-ar", "44100", "-ac", "2"]
    cmd += ["-id3v2_version", "3", "-write_id3v1", "1", "-f", "mp3", str(dst)]
    dst.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        cmd, check=False, capture_output=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        dst.unlink(missing_ok=True)
        raise RenderError(
            f"ffmpeg failed for {src}: {proc.stderr.strip() or 'nothing was written'}"
        )
    result = scan_mp3(dst)
    if (
        result is None or not result.cbr
    ):  # never leave a variable-bitrate file on a disc
        dst.unlink(missing_ok=True)
        raise RenderError(f"{src.name}: the MP3 made from it is not constant bitrate")
    return dst.stat().st_size


def _mp3_plan(src: Path, max_bitrate: int) -> int | None:
    """None to copy `src` as it is, else the constant bitrate (kbit/s) to encode it at."""
    if is_mp3(src):
        scan = scan_mp3(src)
        if scan is not None and scan.cbr and scan.bitrate <= max_bitrate:
            return None
        source = scan.bitrate if scan is not None else probe(src).bitrate
        return cbr_target(source, max_bitrate)
    info = probe(src)
    # a lossless source says nothing by its file's rate: it goes at the ceiling
    return (
        cbr_target(info.bitrate, max_bitrate)
        if info.codec in LOSSY_CODECS
        else max_bitrate
    )
