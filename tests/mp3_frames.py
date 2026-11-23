"""A hand-built first frame of an MP3, as LAME writes it (shared by the cue timing tests)."""


def frame(
    maker=b"LAME", delay=576, tag=b"Info", crc=False, mono=False, version=3
) -> bytes:
    """An MP3's first frame as LAME writes it: a header, side info, the Info / Xing tag and its LAME extension."""
    byte1 = {3: 0xFA, 2: 0xF2, 0: 0xE2}[version] | (
        0 if crc else 1
    )  # MPEG1, MPEG2, MPEG2.5, Layer III
    head = bytes([0xFF, byte1, 0x90, 0xC0 if mono else 0x00])
    side = (17 if mono else 32) if version == 3 else (9 if mono else 17)
    pad = 0
    packed = bytes([delay >> 4, ((delay & 15) << 4) | (pad >> 8), pad & 255])
    ext = maker + b"3.100" + bytes(12) + packed
    flags = (15).to_bytes(4, "big")
    body = bytes(2 if crc else 0) + bytes(side) + tag + flags + bytes(4 + 4 + 100 + 4)
    return head + body + ext + bytes(100)
