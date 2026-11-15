"""Embedded cover art: the readers for ID3, FLAC and MP4 (built by hand), and real files made by ffmpeg."""

import struct
import subprocess

import pytest

from dubcrate import artwork, tools
from dubcrate.artwork import read_artwork

COVER = b"\x89PNG\r\n\x1a\n" + b"cover-bytes" * 50
OTHER = b"\xff\xd8\xff\xe0" + b"back-bytes" * 20

needs_ffmpeg = pytest.mark.skipif(
    tools.find_tool("ffmpeg") is None, reason="no ffmpeg here"
)


def syncsafe(n):
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def apic(image, kind=3, mime=b"image/png", description=b"", encoding=0):
    end = b"\x00\x00" if encoding in (1, 2) else b"\x00"
    return (
        bytes([encoding]) + mime + b"\x00" + bytes([kind]) + description + end + image
    )


def frame(frame_id, data, version=3):
    size = struct.pack(">I", len(data)) if version == 3 else syncsafe(len(data))
    return frame_id + size + b"\x00\x00" + data


def id3(frames, version=3, flags=0, padding=0):
    body = b"".join(frames) + b"\x00" * padding
    return b"ID3" + bytes([version, 0, flags]) + syncsafe(len(body)) + body


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return path


@pytest.mark.parametrize("version", [3, 4])
def test_an_id3_cover_is_found_in_v2_3_and_v2_4(tmp_path, version):
    tag = id3([frame(b"TIT2", b"\x00Song", version), frame(b"APIC", apic(COVER), version)], version, padding=64)  # fmt: skip
    assert read_artwork(write(tmp_path, "a.mp3", tag + b"\xff\xfb" * 100)) == COVER


def test_an_id3_v2_2_cover_is_found(tmp_path):
    pic = (
        b"\x00PNG" + bytes([3]) + b"\x00" + COVER
    )  # 'PNG' format, type, empty description
    body = b"PIC" + len(pic).to_bytes(3, "big") + pic
    tag = b"ID3" + bytes([2, 0, 0]) + syncsafe(len(body)) + body
    assert read_artwork(write(tmp_path, "a.mp3", tag)) == COVER


def test_the_front_cover_is_chosen_over_another_picture_and_a_lone_picture_is_used(
    tmp_path,
):
    both = id3(
        [frame(b"APIC", apic(OTHER, kind=4)), frame(b"APIC", apic(COVER, kind=3))]
    )
    assert read_artwork(write(tmp_path, "a.mp3", both)) == COVER
    lone = id3([frame(b"APIC", apic(OTHER, kind=4))])
    assert read_artwork(write(tmp_path, "b.mp3", lone)) == OTHER


def test_a_utf16_description_is_skipped_correctly(tmp_path):
    description = "Cover é".encode("utf-16")  # (with a byte order mark)
    tag = id3([frame(b"APIC", apic(COVER, description=description, encoding=1))])
    assert read_artwork(write(tmp_path, "a.mp3", tag)) == COVER


def test_an_unsynchronised_tag_is_put_back_together(tmp_path):
    image = b"\x89PNG" + b"\xff\xfe" * 30 + b"end"
    body_frames = frame(b"APIC", apic(image))
    escaped = body_frames.replace(
        b"\xff", b"\xff\x00"
    )  # (unsynchronised, as the flag says)
    tag = b"ID3" + bytes([3, 0, 0x80]) + syncsafe(len(escaped)) + escaped
    # (the frame's own size field now counts the unescaped bytes, as a writer makes it)
    assert read_artwork(write(tmp_path, "a.mp3", tag)) == image


def test_a_tag_with_no_picture_or_padding_only_has_no_artwork(tmp_path):
    assert read_artwork(write(tmp_path, "a.mp3", id3([frame(b"TIT2", b"\x00Song")], padding=100))) is None  # fmt: skip
    assert read_artwork(write(tmp_path, "b.mp3", id3([], padding=100))) is None


def flac(pictures):
    info = b"\x00" * 34
    blocks = [(0, info)] + [(6, p) for p in pictures]
    out = b"fLaC"
    for number, (kind, data) in enumerate(blocks):
        last = 0x80 if number == len(blocks) - 1 else 0
        out += bytes([last | kind]) + len(data).to_bytes(3, "big") + data
    return out + b"\xff\xf8" * 20


def flac_picture(image, kind=3, mime=b"image/png", description=b"front"):
    return (
        struct.pack(">I", kind) + struct.pack(">I", len(mime)) + mime
        + struct.pack(">I", len(description)) + description
        + struct.pack(">IIII", 100, 100, 24, 0) + struct.pack(">I", len(image)) + image
    )  # fmt: skip


def test_a_flac_cover_is_found_and_the_front_one_preferred(tmp_path):
    one = flac([flac_picture(COVER)])
    assert read_artwork(write(tmp_path, "a.flac", one)) == COVER
    two = flac([flac_picture(OTHER, kind=0), flac_picture(COVER, kind=3)])
    assert read_artwork(write(tmp_path, "b.flac", two)) == COVER
    assert read_artwork(write(tmp_path, "c.flac", flac([]))) is None


def atom(name, body):
    return struct.pack(">I4s", len(body) + 8, name) + body


def mp4(image, *, moov_last=True):
    covr = atom(b"covr", atom(b"data", struct.pack(">II", 14, 0) + image))
    ilst = atom(
        b"ilst",
        atom(b"\xa9nam", atom(b"data", struct.pack(">II", 1, 0) + b"Song")) + covr,
    )
    meta = atom(b"meta", b"\x00\x00\x00\x00" + atom(b"hdlr", b"\x00" * 24) + ilst)
    moov = atom(b"moov", atom(b"mvhd", b"\x00" * 100) + atom(b"udta", meta))
    ftyp = atom(b"ftyp", b"M4A \x00\x00\x00\x00M4A mp42")
    mdat = atom(b"mdat", b"\x00" * 5000)
    return ftyp + (mdat + moov if moov_last else moov + mdat)


@pytest.mark.parametrize("moov_last", [True, False])
def test_an_mp4_cover_is_found_wherever_the_movie_atom_is(tmp_path, moov_last):
    assert (
        read_artwork(write(tmp_path, "a.m4a", mp4(COVER, moov_last=moov_last))) == COVER
    )


def test_an_mp4_without_a_cover_has_no_artwork(tmp_path):
    ilst = atom(
        b"ilst", atom(b"\xa9nam", atom(b"data", struct.pack(">II", 1, 0) + b"Song"))
    )
    moov = atom(b"moov", atom(b"udta", atom(b"meta", b"\x00" * 4 + ilst)))
    data = atom(b"ftyp", b"M4A \x00\x00\x00\x00") + moov
    assert read_artwork(write(tmp_path, "a.m4a", data)) is None


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"ID3",
        b"ID3\x03\x00\x00\x00\x00\x00\x10",
        b"fLaC",
        b"fLaC\x06\x00\x10\x00",
        b"\x00\x00\x00\x08ftyp",
        b"RIFF" + b"\x00" * 100,
        b"not audio at all",
    ],
)
def test_damaged_or_unknown_files_have_no_artwork_and_never_raise(tmp_path, data):
    assert read_artwork(write(tmp_path, "x.bin", data)) is None


def test_a_missing_file_or_a_huge_tag_has_no_artwork(tmp_path):
    assert read_artwork(tmp_path / "gone.mp3") is None
    huge = b"ID3" + bytes([3, 0, 0]) + syncsafe(artwork.MAX_TAG + 1) + b"\x00" * 64
    assert read_artwork(write(tmp_path, "h.mp3", huge)) is None


def test_a_frame_that_runs_past_the_tag_is_not_read(tmp_path):
    bad = b"APIC" + struct.pack(">I", 999_999) + b"\x00\x00" + apic(COVER)
    assert read_artwork(write(tmp_path, "a.mp3", id3([bad]))) is None


# -- real files, written by ffmpeg ---------------------------------------------------------------


def make_png(path):
    from PySide6.QtGui import QColor, QImage

    image = QImage(64, 64, QImage.Format.Format_RGB32)
    image.fill(QColor("#cc3344"))
    image.save(str(path), "PNG")


def ffmpeg(*args):
    exe = tools.find_tool("ffmpeg")
    return subprocess.run([str(exe.path), "-y", "-loglevel", "error", *args], check=False, capture_output=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)  # fmt: skip


@needs_ffmpeg
@pytest.mark.parametrize(
    ("name", "codec"),
    [
        ("a.mp3", ["-c:a", "libmp3lame"]),
        ("a.flac", ["-c:a", "flac"]),
        ("a.m4a", ["-c:a", "aac"]),
    ],
)
def test_a_cover_ffmpeg_embedded_is_read_back_whole(tmp_path, qapp, name, codec):
    make_png(tmp_path / "cover.png")
    out = tmp_path / name
    done = ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-i", str(tmp_path / "cover.png"), "-map", "0:a", "-map", "1:v", *codec, "-c:v", "copy", "-disposition:v", "attached_pic", str(out))  # fmt: skip
    if done.returncode != 0 or not out.exists():
        pytest.skip(
            f"this ffmpeg can't write {name} with a cover: {done.stderr.strip()[:80]}"
        )
    found = read_artwork(out)
    assert found is not None and found[:8] == b"\x89PNG\r\n\x1a\n"
    assert found == (tmp_path / "cover.png").read_bytes()
