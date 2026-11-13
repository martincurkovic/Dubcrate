"""Decode an audio file to 44.1 kHz stereo 16-bit PCM held in memory.

miniaudio decodes MP3, WAV, FLAC and Ogg Vorbis. It can't read M4A/AAC or AIFF, so
anything it rejects goes through ffmpeg, which Dubcrate already needs for burning.
Everything is converted to the CD's own format, so what you preview is what a disc
would hold, and playback needs no further conversion.
"""

import subprocess
from dataclasses import dataclass

import miniaudio
import numpy as np

from dubcrate.tools import find_tool, missing_message

from .devices import SAMPLE_RATE

CHANNELS = 2


class DecodeError(RuntimeError):
    pass


@dataclass(frozen=True)
class DecodedAudio:
    samples: np.ndarray  # int16, shape (frames, 2)
    sample_rate: int = SAMPLE_RATE

    @property
    def frames(self) -> int:
        return len(self.samples)

    @property
    def duration(self) -> float:
        return self.frames / self.sample_rate

    def mono(self) -> np.ndarray:
        """float32 mono in -1..1, for analysis."""
        return self.samples.astype(np.float32).mean(axis=1) / 32768.0


def _via_miniaudio(path: str) -> np.ndarray:
    decoded = miniaudio.decode_file(
        path,
        output_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=CHANNELS,
        sample_rate=SAMPLE_RATE,
    )
    return np.frombuffer(decoded.samples, dtype=np.int16).reshape(-1, CHANNELS)


def _via_ffmpeg(path: str) -> np.ndarray:
    ffmpeg = find_tool("ffmpeg")
    if ffmpeg is None:
        raise DecodeError(
            f"Dubcrate can't read this kind of file without ffmpeg. {missing_message('ffmpeg')}"
        )
    cmd = [str(ffmpeg.path), "-nostdin", "-v", "error", "-i", path, "-vn", "-map", "0:a:0",
           "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"]  # fmt: skip
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0 or not proc.stdout:
        raise DecodeError(
            f"ffmpeg couldn't decode {path}: {proc.stderr.decode(errors='replace').strip()}"
        )
    usable = len(proc.stdout) - len(proc.stdout) % (2 * CHANNELS)
    return np.frombuffer(proc.stdout[:usable], dtype=np.int16).reshape(-1, CHANNELS)


def decode_file(path: str) -> DecodedAudio:
    """Decode `path`, raising DecodeError if nothing can read it."""
    try:
        samples = _via_miniaudio(path)
    except (miniaudio.DecodeError, miniaudio.MiniaudioError, OSError):
        samples = _via_ffmpeg(path)
    if len(samples) == 0:
        raise DecodeError(f"no audio in {path}")
    return DecodedAudio(samples)
