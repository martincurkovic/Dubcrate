"""Persistent user settings (QSettings: plist / registry / ini per platform)."""

from PySide6.QtCore import QByteArray, QSettings

THEME_SYSTEM = "system"
THEME_LIGHT = "light"
THEME_DARK = "dark"
THEME_SYSTEM6 = "system6"
THEME_VALUES = (THEME_SYSTEM, THEME_LIGHT, THEME_DARK, THEME_SYSTEM6)


def _qs() -> QSettings:
    # Organisation and application names are set once in ui/app.py.
    return QSettings()


def get_theme() -> str:
    value = _qs().value("theme", THEME_SYSTEM)
    return value if value in THEME_VALUES else THEME_SYSTEM


def set_theme(value: str) -> None:
    _qs().setValue("theme", value)


def get_bytes(key: str) -> QByteArray | None:
    """A saved window geometry or splitter state, or None."""
    value = _qs().value(key, None)
    return value if isinstance(value, QByteArray) and not value.isEmpty() else None


def set_bytes(key: str, value: QByteArray) -> None:
    _qs().setValue(key, value)


def get_last_disc() -> int | None:
    value = _qs().value("last_disc", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def set_last_disc(disc_id: int | None) -> None:
    if disc_id is None:
        _qs().remove("last_disc")
    else:
        _qs().setValue("last_disc", int(disc_id))


DISC_MINUTES = (80, 74)
PREGAP_SECONDS = (0, 1, 2, 3)  # 0 is gapless


def get_disc_minutes() -> int:
    """The length new discs are planned for (80 unless changed)."""
    try:
        value = int(_qs().value("discs/minutes", DISC_MINUTES[0]))
    except (TypeError, ValueError):
        return DISC_MINUTES[0]
    return value if value in DISC_MINUTES else DISC_MINUTES[0]


def set_disc_minutes(minutes: int) -> None:
    _qs().setValue("discs/minutes", int(minutes))


def get_pregap_seconds() -> int:
    """Silence between tracks on new discs; gapless (0) unless changed."""
    try:
        value = int(_qs().value("discs/pregap_seconds", 0))
    except (TypeError, ValueError):
        return 0
    return value if value in PREGAP_SECONDS else 0


def set_pregap_seconds(seconds: int) -> None:
    _qs().setValue("discs/pregap_seconds", int(seconds))


def get_audio_device() -> str | None:
    """The saved output device id (hex), or None for the system default."""
    return _qs().value("audio/device", None) or None


def set_audio_device(device_id: str | None) -> None:
    if device_id:
        _qs().setValue("audio/device", device_id)
    else:
        _qs().remove("audio/device")


def get_audio_buffer() -> int:
    from dubcrate.audio.devices import BUFFER_SIZES, DEFAULT_BUFFER

    try:
        value = int(_qs().value("audio/buffer", DEFAULT_BUFFER))
    except (TypeError, ValueError):
        return DEFAULT_BUFFER
    return value if value in BUFFER_SIZES else DEFAULT_BUFFER


def set_audio_buffer(frames: int) -> None:
    _qs().setValue("audio/buffer", int(frames))


def get_volume() -> float:
    try:
        return max(0.0, min(1.0, float(_qs().value("audio/volume", 0.8))))
    except (TypeError, ValueError):
        return 0.8


def set_volume(volume: float) -> None:
    _qs().setValue("audio/volume", float(volume))


def get_hidden_columns() -> list[str]:
    import json

    try:
        names = json.loads(str(_qs().value("tracks/hidden_columns", "[]") or "[]"))
    except ValueError:
        return []
    return [n for n in names if isinstance(n, str)]


def set_hidden_columns(names: list[str]) -> None:
    import json

    _qs().setValue("tracks/hidden_columns", json.dumps(sorted(names)))
