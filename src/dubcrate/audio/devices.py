"""Audio output devices, via miniaudio (adapted from AKAISDS's audio_preview).

Everything here is free of Qt and of the settings module: callers pass the saved
device id in, so the functions are easy to test and to reuse.
"""

import logging

import miniaudio

log = logging.getLogger(__name__)

#: Buffer sizes offered in the settings, in frames: the same list a DAW's buffer-size menu uses.
BUFFER_SIZES = (32, 64, 128, 256, 512, 1024, 2048)
DEFAULT_BUFFER = 1024
SAMPLE_RATE = 44_100


def list_output_devices() -> list[dict]:
    """Every playback device miniaudio can see (each a dict with "name" and "id")."""
    return miniaudio.Devices().get_playbacks()


def device_id_string(device: dict) -> str:
    """A device's opaque id as hex, so it can be saved in plain settings.

    `device["id"]` is a platform-specific cffi union (a CoreAudio UID on macOS, an ALSA
    string on Linux); its bytes are stable across enumerations of the same device.
    """
    return bytes(miniaudio.ffi.buffer(device["id"])).hex()


def find_output_device(id_string: str | None) -> dict | None:
    if not id_string:
        return None
    for device in list_output_devices():
        if device_id_string(device) == id_string:
            return device
    return None


def resolve_output_device(saved_id: str | None) -> dict | None:
    """The device to play through, or None for the system default.

    A saved device can go missing (an interface unplugged since it was chosen); falling
    back to the default beats refusing to play at all.
    """
    if not saved_id:
        return None
    device = find_output_device(saved_id)
    if device is None:
        log.warning(
            "saved output device %r not found; using the system default", saved_id
        )
    return device


def buffer_msec(frames: int, sample_rate: int = SAMPLE_RATE) -> int:
    """miniaudio wants milliseconds; the settings store frames."""
    return max(1, round(frames / sample_rate * 1000))
