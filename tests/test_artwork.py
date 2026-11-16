"""Embedded cover art: the readers for ID3, FLAC, MP4, Ogg, WAV and AIFF (built by hand), and real files made by ffmpeg."""

import base64
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


def ogg_pages(packets, serial=1, other=None):
    """Packets as Ogg pages, a long one carried over several (no checksums: they are not read)."""
    out, sequence = b"", 0
    for number, packet in enumerate(packets):
        step = 255 * 200  # (a page that carries on has only full segments)
        pieces = [packet[i : i + step] for i in range(0, len(packet), step)] or [b""]
        for index, piece in enumerate(pieces):
            last = index == len(pieces) - 1
            laces = [255] * (len(piece) // 255) + ([len(piece) % 255] if last else [])
            flags = (1 if index else 0) | (2 if number == 0 else 0)
            out += b"OggS" + bytes([0, flags]) + b"\0" * 8 + struct.pack("<II", serial, sequence) + b"\0" * 4  # fmt: skip
            out += bytes([len(laces)]) + bytes(laces) + piece
            sequence += 1
        if other is not None and number == 0:  # a page of another stream in between
            out += b"OggS" + bytes([0, 2]) + b"\0" * 8 + struct.pack("<II", other, 0) + b"\0" * 4 + bytes([1, 3]) + b"xyz"  # fmt: skip
    return out


def vorbis_comments(*pictures, vendor=b"test"):
    entries = [b"TITLE=Song"] + [b"METADATA_BLOCK_PICTURE=" + base64.b64encode(p) for p in pictures]  # fmt: skip
    out = struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", len(entries))
    for entry in entries:
        out += struct.pack("<I", len(entry)) + entry
    return out


def test_an_opus_cover_is_found_and_the_front_one_preferred(tmp_path):
    packets = [b"OpusHead" + b"\x01" * 11, b"OpusTags" + vorbis_comments(flac_picture(OTHER, kind=0), flac_picture(COVER))]  # fmt: skip
    assert read_artwork(write(tmp_path, "a.opus", ogg_pages(packets))) == COVER


def test_a_vorbis_cover_bigger_than_a_page_is_put_back_together(tmp_path):
    big = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 1000  # (over four pages)
    packets = [b"\x01vorbis" + b"\0" * 23, b"\x03vorbis" + vorbis_comments(flac_picture(big)) + b"\x01", b"\x05vorbis" + b"\0" * 50]  # fmt: skip
    assert read_artwork(write(tmp_path, "a.ogg", ogg_pages(packets))) == big


def test_an_ogg_cover_is_found_among_the_pages_of_another_stream(tmp_path):
    packets = [b"OpusHead" + b"\x01" * 11, b"OpusTags" + vorbis_comments(flac_picture(COVER))]  # fmt: skip
    assert read_artwork(write(tmp_path, "a.opus", ogg_pages(packets, other=9))) == COVER  # fmt: skip


def test_an_ogg_flac_cover_is_found_as_a_comment_or_a_block_of_its_own(tmp_path):
    def block(kind, body, last=False):
        return (
            bytes([kind | (0x80 if last else 0)]) + len(body).to_bytes(3, "big") + body
        )

    head = b"\x7fFLAC\x01\x00\x00\x02fLaC" + block(0, b"\0" * 34)
    comment = block(4, vorbis_comments(flac_picture(COVER)), last=True)
    assert read_artwork(write(tmp_path, "a.oga", ogg_pages([head, comment]))) == COVER
    picture = block(6, flac_picture(COVER), last=True)
    assert read_artwork(write(tmp_path, "b.oga", ogg_pages([head, picture]))) == COVER


def test_an_ogg_with_no_picture_or_a_bad_one_has_no_artwork(tmp_path):
    none = [b"OpusHead" + b"\x01" * 11, b"OpusTags" + vorbis_comments()]
    assert read_artwork(write(tmp_path, "a.opus", ogg_pages(none))) is None
    bad = b"OpusTags" + vorbis_comments(b"nonsense")
    assert read_artwork(write(tmp_path, "b.opus", ogg_pages([none[0], bad]))) is None


def riff(tag, chunks, order):
    form, kind = (b"RIFF", b"WAVE") if order == "little" else (b"FORM", b"AIFF")
    body = kind
    for name, data in chunks:
        body += name + len(data).to_bytes(4, order) + data + (b"\0" if len(data) % 2 else b"")  # fmt: skip
    return form + len(body).to_bytes(4, order) + body


@pytest.mark.parametrize(
    ("order", "name", "id3_name"),
    [
        ("little", "a.wav", b"id3 "),
        ("little", "b.wav", b"ID3 "),
        ("big", "c.aiff", b"ID3 "),
    ],
)
def test_a_wav_or_aiff_cover_is_found_in_its_id3_chunk(tmp_path, order, name, id3_name):  # fmt: skip
    tag = id3([frame(b"APIC", apic(COVER))])
    chunks = [
        (b"fmt ", b"\0" * 17),
        (b"data", b"\x01" * 1001),
        (id3_name, tag),
    ]  # (odd sizes: padded)
    assert read_artwork(write(tmp_path, name, riff(None, chunks, order))) == COVER


def test_a_wav_or_aiff_without_an_id3_chunk_or_with_a_bad_one_has_no_artwork(tmp_path):  # fmt: skip
    plain = riff(None, [(b"fmt ", b"\0" * 16), (b"data", b"\x01" * 100)], "little")
    assert read_artwork(write(tmp_path, "a.wav", plain)) is None
    bad = riff(None, [(b"fmt ", b"\0" * 16), (b"id3 ", b"not a tag at all")], "little")
    assert read_artwork(write(tmp_path, "b.wav", bad)) is None
    runaway = b"RIFF\x00\x00\x00\x00WAVE" + b"data" + b"\xff\xff\xff\xff" + b"\0" * 20
    assert read_artwork(write(tmp_path, "c.wav", runaway)) is None


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
        b"RIFF\x00\x00\x00\x00WAVEid3 \x10\x00\x00\x00ID3",
        b"OggS",
        b"OggS\x00\x02" + b"\x00" * 30,
        b"OggS" + b"\xff" * 40,
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
        ("a.aiff", ["-c:a", "pcm_s16be", "-write_id3v2", "1"]),
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


@needs_ffmpeg
def test_an_opus_file_ffmpeg_wrote_with_a_picture_comment_is_read_back(tmp_path):
    big = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 800  # (several Ogg pages)
    comment = base64.b64encode(flac_picture(big)).decode()
    out = tmp_path / "a.opus"
    done = ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "libopus", "-metadata", f"METADATA_BLOCK_PICTURE={comment}", str(out))  # fmt: skip
    if done.returncode != 0 or not out.exists():
        pytest.skip(f"this ffmpeg can't write Opus: {done.stderr.strip()[:80]}")
    assert read_artwork(out) == big
