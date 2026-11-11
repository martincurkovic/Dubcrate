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
