import shutil
import subprocess
import wave

import pytest

from dubcrate.cd import SAMPLE_RATE, SAMPLES_PER_SECTOR
from dubcrate.render import probe, render_track

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg not installed",
)


def _tone(path, rate, seconds, enc):
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
         f"sine=frequency=440:duration={seconds}:sample_rate={rate}", "-ac", "1", *enc, str(path)],
        check=True,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("name", "rate", "enc"),
    [
        ("a.flac", 48_000, ["-c:a", "flac", "-sample_fmt", "s32"]),
        ("b.wav", 44_100, ["-c:a", "pcm_s16le"]),
        ("c.wav", 96_000, ["-c:a", "pcm_s24le"]),
    ],
)
def test_render_is_cd_format_and_sector_aligned(tmp_path, name, rate, enc):
    src = tmp_path / name
    _tone(src, rate, 1.37, enc)
    dst = tmp_path / "out.wav"
    sectors = render_track(src, dst)
    with wave.open(str(dst), "rb") as w:
        assert w.getframerate() == SAMPLE_RATE
        assert w.getnchannels() == 2
        assert w.getsampwidth() == 2
        assert w.getnframes() % SAMPLES_PER_SECTOR == 0
        assert w.getnframes() == sectors * SAMPLES_PER_SECTOR
    assert abs(sectors / 75 - 1.37) < 0.05


def test_probe_reads_tags(tmp_path):
    src = tmp_path / "t.flac"
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
         "sine=duration=1", "-metadata", "title=Hello", "-metadata", "artist=Me", str(src)],
        check=True,
    )  # fmt: skip
    info = probe(src)
    assert (info.title, info.artist) == ("Hello", "Me")
    assert 0.9 < info.duration < 1.1
