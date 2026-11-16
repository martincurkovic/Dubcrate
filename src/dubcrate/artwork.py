"""A track's embedded cover art, read from the file itself (Qt-free; only reads, never writes).

`read_artwork(path)` gives the raw bytes of the picture (a JPEG or PNG, which Qt decodes) or None. It looks in the
places DJ libraries keep covers, found by the start of the file:

- **ID3v2** (MP3, and anything else with a tag at its start): the `APIC` frame (`PIC` in v2.2), preferring the
  front cover.
- **FLAC**: a `PICTURE` metadata block, preferring the front cover.
- **MP4 / M4A**: the `covr` atom of `moov/udta/meta/ilst`.
- **Ogg** (Vorbis, Opus, Ogg FLAC): a `METADATA_BLOCK_PICTURE` comment, which is a base64 FLAC picture block (an
  Ogg FLAC file may also carry the block itself, as a packet of its own).
- **WAV and AIFF**: an `ID3 ` chunk holding an ID3v2 tag, read as above.

Only the bytes needed are read (the tag, or the atoms' headers by seeking), a tag over `MAX_TAG` is not read at all,
and anything malformed is "no artwork", never an error.
"""

import base64
import struct
from pathlib import Path

MAX_TAG = 32 * 1024 * 1024  # an ID3 tag bigger than this is not read
FRONT_COVER = 3  # picture type 3 in ID3 and FLAC


def read_artwork(path: str | Path) -> bytes | None:
    """The cover picture embedded in the file, or None (no picture, an unknown kind of file, a damaged one)."""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            f.seek(0)
            if head[:3] == b"ID3":
                return _from_id3(f)
            if head[:4] == b"fLaC":
                return _from_flac(f)
            if head[4:8] == b"ftyp":
                return _from_mp4(f)
            if head[:4] == b"OggS":
                return _from_ogg(f)
            if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
                return _from_chunks(f, "little")
            if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
                return _from_chunks(f, "big")
    except (OSError, struct.error, ValueError, IndexError):
        return None
    return None


# -- ID3v2 ------------------------------------------------------------------------------------


def _syncsafe(data: bytes) -> int:
    value = 0
    for byte in data:
        value = (value << 7) | (byte & 0x7F)
    return value


def _unsynchronise(data: bytes) -> bytes:
    """Undo ID3 unsynchronisation: every 0xFF 0x00 was 0xFF."""
    return data.replace(b"\xff\x00", b"\xff")


def _terminator(data: bytes, start: int, encoding: int) -> int:
    """Where a text field that starts at `start` ends (its terminator's first byte)."""
    if encoding in (
        1,
        2,
    ):  # UTF-16: the terminator is two zero bytes on an even boundary
        i = start
        while i + 1 < len(data):
            if data[i] == 0 and data[i + 1] == 0:
                return i
            i += 2
        return len(data)
    end = data.find(b"\x00", start)
    return len(data) if end < 0 else end


def _picture_from_apic(frame: bytes, v22: bool) -> tuple[int, bytes] | None:
    """(picture type, image bytes) from an APIC / PIC frame's body."""
    encoding = frame[0]
    if v22:  # PIC: a three letter image format, then the type
        position = 4
    else:  # APIC: a MIME type ending in a zero byte, then the type
        end = frame.find(b"\x00", 1)
        if end < 0:
            return None
        position = end + 1
    kind = frame[position]
    position += 1
    end = _terminator(frame, position, encoding)
    position = end + (2 if encoding in (1, 2) else 1)
    image = frame[position:]
    return (kind, image) if image else None


def _from_id3(f) -> bytes | None:
    header = f.read(10)
    version, flags = header[3], header[5]
    size = _syncsafe(header[6:10])
    if version not in (2, 3, 4) or size > MAX_TAG:
        return None
    body = f.read(size)
    if len(body) < size:
        return None
    if (
        flags & 0x80 and version < 4
    ):  # (v2.4 unsynchronises each frame, which is rare and not handled)
        body = _unsynchronise(body)
    position = 0
    if (
        flags & 0x40 and version in (3, 4) and len(body) >= 6
    ):  # an extended header to skip
        skip = (
            _syncsafe(body[:4])
            if version == 4
            else struct.unpack(">I", body[:4])[0] + 4
        )
        position = skip
    found: list[tuple[int, bytes]] = []
    id_length, head_length = (3, 6) if version == 2 else (4, 10)
    while position + head_length <= len(body):
        frame_id = body[position : position + id_length]
        if not frame_id.strip(b"\x00"):
            break  # padding
        if version == 2:
            length = int.from_bytes(body[position + 3 : position + 6], "big")
        elif version == 3:
            length = struct.unpack(">I", body[position + 4 : position + 8])[0]
        else:
            length = _syncsafe(body[position + 4 : position + 8])
        start = position + head_length
        if length <= 0 or start + length > len(body):
            break
        if frame_id in (b"APIC", b"PIC"):
            picture = _picture_from_apic(body[start : start + length], version == 2)
            if picture is not None:
                found.append(picture)
        position = start + length
    return _best(found)


def _best(found: list[tuple[int, bytes]]) -> bytes | None:
    """The front cover if there is one, else the first picture."""
    for kind, image in found:
        if kind == FRONT_COVER:
            return image
    return found[0][1] if found else None


# -- FLAC ---------------------------------------------------------------------------------------


def _from_flac(f) -> bytes | None:
    f.seek(4)
    found: list[tuple[int, bytes]] = []
    while True:
        header = f.read(4)
        if len(header) < 4:
            break
        last, kind = bool(header[0] & 0x80), header[0] & 0x7F
        length = int.from_bytes(header[1:4], "big")
        if kind == 6 and length <= MAX_TAG:  # PICTURE
            block = f.read(length)
            picture = _flac_picture(block)
            if picture is not None:
                found.append(picture)
        else:
            f.seek(length, 1)
        if last:
            break
    return _best(found)


def _flac_picture(block: bytes) -> tuple[int, bytes] | None:
    kind, mime_length = struct.unpack(">II", block[:8])
    position = 8 + mime_length
    (description_length,) = struct.unpack(">I", block[position : position + 4])
    position += 4 + description_length + 16  # width, height, depth and colours
    (data_length,) = struct.unpack(">I", block[position : position + 4])
    position += 4
    image = block[position : position + data_length]
    return (kind, image) if image else None


# -- MP4 / M4A --------------------------------------------------------------------------------------


def _atoms(f, start: int, end: int):
    """The (name, body start, body end) of each atom between `start` and `end`."""
    position = start
    while position + 8 <= end:
        f.seek(position)
        header = f.read(8)
        if len(header) < 8:
            return
        size, name = struct.unpack(">I4s", header)
        body = position + 8
        if size == 1:  # a 64-bit size follows
            size = struct.unpack(">Q", f.read(8))[0]
            body += 8
        elif size == 0:  # to the end of the file
            size = end - position
        if size < 8 or position + size > end:
            return
        yield name, body, position + size
        position += size


def _from_mp4(f) -> bytes | None:
    f.seek(0, 2)
    total = f.tell()
    path = [b"moov", b"udta", b"meta", b"ilst", b"covr"]
    start, end = 0, total
    for name in path:
        found = None
        for atom, body, atom_end in _atoms(f, start, end):
            if atom == name:
                found = (
                    body + 4 if name == b"meta" else body,
                    atom_end,
                )  # (meta has version and flags first)
                break
        if found is None:
            return None
        start, end = found
    for atom, body, atom_end in _atoms(f, start, end):
        if atom == b"data" and atom_end - body > 8 and atom_end - body <= MAX_TAG:
            f.seek(body + 8)  # the data type and locale
            return f.read(atom_end - body - 8)
    return None


# -- Ogg (Vorbis, Opus, Ogg FLAC) -------------------------------------------------------------------


def _ogg_packets(f):
    """The packets of the file's first logical stream, in order (a packet may run over several pages)."""
    serial = None
    packet = bytearray()
    total = 0
    while True:
        header = f.read(27)
        if len(header) < 27 or header[:4] != b"OggS":
            return
        page_serial = header[14:18]
        if serial is None:
            serial = page_serial
        table = f.read(header[26])
        if len(table) < header[26]:
            return
        body = f.read(sum(table))
        if len(body) < sum(table):
            return
        if page_serial != serial:
            continue
        position = 0
        for lacing in table:
            packet += body[position : position + lacing]
            position += lacing
            total += lacing
            if total > MAX_TAG:
                return
            if lacing < 255:
                yield bytes(packet)
                packet = bytearray()


def _comment_pictures(comments: bytes) -> list[tuple[int, bytes]]:
    """The pictures in a Vorbis comment block: each `METADATA_BLOCK_PICTURE=` is a base64 FLAC picture."""
    (vendor,) = struct.unpack("<I", comments[:4])
    position = 4 + vendor
    (count,) = struct.unpack("<I", comments[position : position + 4])
    position += 4
    found: list[tuple[int, bytes]] = []
    for _ in range(count):
        if position + 4 > len(comments):
            break
        (length,) = struct.unpack("<I", comments[position : position + 4])
        entry = comments[position + 4 : position + 4 + length]
        position += 4 + length
        key, _, value = entry.partition(b"=")
        if key.upper() != b"METADATA_BLOCK_PICTURE":
            continue
        try:
            picture = _flac_picture(base64.b64decode(value))
        except (ValueError, struct.error):
            continue  # (one bad picture doesn't hide the others)
        if picture is not None:
            found.append(picture)
    return found


def _from_ogg(f) -> bytes | None:
    found: list[tuple[int, bytes]] = []
    for number, packet in enumerate(_ogg_packets(f)):
        if number == 0:
            continue  # the identification header
        if packet[:7] == b"\x03vorbis":
            found += _comment_pictures(packet[7:])
            break  # (the next packet is the codec's setup: no more tags)
        if packet[:8] == b"OpusTags":
            found += _comment_pictures(packet[8:])
            break
        # Ogg FLAC: each header packet is a FLAC metadata block (a type byte, 3 bytes of length, the body)
        kind = packet[0] & 0x7F
        if kind == 4:  # the Vorbis comment, without the "vorbis" prefix
            found += _comment_pictures(packet[4:])
        elif kind == 6:
            picture = _flac_picture(packet[4:])
            if picture is not None:
                found.append(picture)
        if packet[0] & 0x80 or number > 64:  # the last block, or no end in sight
            break
    return _best(found)


# -- WAV and AIFF: an ID3 tag in a chunk ---------------------------------------------------------------


def _from_chunks(f, order: str) -> bytes | None:
    """The picture in the `ID3 ` chunk of a RIFF/WAVE (little-endian sizes) or FORM/AIFF (big-endian) file."""
    f.seek(0, 2)
    total = f.tell()
    position = 12
    while position + 8 <= total:
        f.seek(position)
        header = f.read(8)
        size = int.from_bytes(header[4:8], order)
        body = position + 8
        if header[:4].lower() == b"id3 ":
            f.seek(body)
            if f.read(3) != b"ID3":
                return None
            f.seek(body)
            return _from_id3(f)
        position = body + size + (size & 1)  # (chunks are padded to an even length)
    return None
