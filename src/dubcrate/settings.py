"""Persistent user settings (QSettings: plist / registry / ini per platform)."""

from PySide6.QtCore import QByteArray, QSettings

from dubcrate import keys, labels, mp3

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


def get_mp3_bitrate() -> int:
    """The highest constant bitrate new MP3 discs use, in kbit/s (320 unless changed)."""
    try:
        value = int(_qs().value("discs/mp3_bitrate", mp3.DEFAULT_BITRATE))
    except (TypeError, ValueError):
        return mp3.DEFAULT_BITRATE
    return value if value in mp3.BITRATES else mp3.DEFAULT_BITRATE


def set_mp3_bitrate(kbps: int) -> None:
    _qs().setValue("discs/mp3_bitrate", int(kbps))


def get_key_notation() -> str:
    """How keys are shown: "camelot" (8A, the default) or "musical" (A min)."""
    value = str(_qs().value("display/key_notation", keys.CAMELOT))
    return value if value in keys.NOTATIONS else keys.CAMELOT


def set_key_notation(notation: str) -> None:
    _qs().setValue("display/key_notation", notation)


BURN_SPEEDS = (4, 8, 10, 12, 16, 20, 24, 32, 48)
DEFAULT_BURN_SPEED = 10  # old players read slow burns more reliably


def get_burn_speed() -> int:
    """The burn speed (the CD "x" factor) a burn starts with."""
    try:
        value = int(_qs().value("burn/speed", DEFAULT_BURN_SPEED))
    except (TypeError, ValueError):
        return DEFAULT_BURN_SPEED
    return value if value in BURN_SPEEDS else DEFAULT_BURN_SPEED


def set_burn_speed(speed: int) -> None:
    _qs().setValue("burn/speed", int(speed))


def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, str):  # an ini file keeps a bool as text
        return value.strip().lower() not in ("false", "0", "no", "off")
    return bool(value) if value is not None else default


def get_label_options() -> labels.LabelOptions:
    """The index label settings last used (see `dubcrate.labels`), each kept inside its limits."""
    default = labels.LabelOptions()
    qs = _qs()
    both = qs.value("labels/show_bpm_key")  # one box for both, before they were split

    def number(key: str, fallback: float) -> float:
        try:
            return float(qs.value(f"labels/{key}", fallback))
        except (TypeError, ValueError):
            return fallback

    return labels.LabelOptions(
        page=str(qs.value("labels/page", default.page)),
        font_pt=number("font_pt", default.font_pt),
        width_mm=number("width_mm", default.width_mm),
        height_mm=number("height_mm", default.height_mm),
        per_disc=int(number("per_disc", default.per_disc)),
        columns=int(number("columns", default.columns)),
        copies=int(number("copies", default.copies)),
        margin_mm=number("margin_mm", default.margin_mm),
        gap_mm=number("gap_mm", default.gap_mm),
        show_bpm=_as_bool(qs.value("labels/show_bpm", both), default.show_bpm),
        show_key=_as_bool(qs.value("labels/show_key", both), default.show_key),
        show_duration=_as_bool(qs.value("labels/show_duration"), default.show_duration),
        outline=_as_bool(qs.value("labels/outline"), default.outline),
    ).clamped()


def set_label_options(options: labels.LabelOptions) -> None:
    qs = _qs()
    for key, value in vars(options.clamped()).items():
        qs.setValue(f"labels/{key}", value)
    qs.remove("labels/show_bpm_key")


WAVE_STYLES = ("bands", "rgb", "blue")
WAVE_BACKGROUNDS = ("theme", "dark")


def get_wave_style() -> str:
    """The waveform look: the 3-band one (rekordbox's default), RGB, or blue."""
    value = str(_qs().value("waveform/style", WAVE_STYLES[0]))
    return value if value in WAVE_STYLES else WAVE_STYLES[0]


def set_wave_style(style: str) -> None:
    _qs().setValue("waveform/style", style)


def get_wave_background() -> str:
    """ "theme": the waveform follows the theme (light in the light theme); "dark": always dark."""
    value = str(_qs().value("waveform/background", WAVE_BACKGROUNDS[0]))
    return value if value in WAVE_BACKGROUNDS else WAVE_BACKGROUNDS[0]


def set_wave_background(mode: str) -> None:
    _qs().setValue("waveform/background", mode)


BEAT_JUMP_SIZES = (1, 2, 4, 8, 16, 32)  # beats
DEFAULT_BEAT_JUMP = 4  # one bar


def get_beat_jump() -> int:
    """How many beats the left and right arrow keys jump."""
    try:
        value = int(_qs().value("playback/beat_jump", DEFAULT_BEAT_JUMP))
    except (TypeError, ValueError):
        return DEFAULT_BEAT_JUMP
    return value if value in BEAT_JUMP_SIZES else DEFAULT_BEAT_JUMP


def set_beat_jump(beats: int) -> None:
    _qs().setValue("playback/beat_jump", int(beats))


def get_auto_import() -> bool:
    """Whether Dubcrate re-reads your rekordbox library each time it starts (on by default)."""
    value = _qs().value("import/auto_rekordbox", True)
    if isinstance(value, str):  # an ini file keeps a bool as text
        return value.strip().lower() not in ("false", "0", "no", "off")
    return bool(value)


def set_auto_import(enabled: bool) -> None:
    _qs().setValue("import/auto_rekordbox", bool(enabled))


def get_auto_update_check() -> bool:
    """Whether Dubcrate asks GitHub for a newer version when it starts (on by default). It is the
    only time the app uses the network."""
    value = _qs().value("updates/auto_check", True)
    if isinstance(value, str):  # an ini file keeps a bool as text
        return value.strip().lower() not in ("false", "0", "no", "off")
    return bool(value)


def set_auto_update_check(enabled: bool) -> None:
    _qs().setValue("updates/auto_check", bool(enabled))


def get_last_update_check() -> float:
    """Unix time of the last update check that got an answer, or 0 if there has not been one."""
    try:
        return float(_qs().value("updates/last_check", 0))
    except (TypeError, ValueError):
        return 0.0


def set_last_update_check(timestamp: float) -> None:
    _qs().setValue("updates/last_check", float(timestamp))


def get_skipped_update() -> str | None:
    """The version whose start-up notice was dismissed with "Skip This Version", if any."""
    return str(_qs().value("updates/skipped", "") or "") or None


def set_skipped_update(version: str) -> None:
    _qs().setValue("updates/skipped", version)


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


def get_visible_columns() -> list[str] | None:
    """The track table's visible columns, by title; None if they were never chosen
    (the default columns then show)."""
    import json

    raw = _qs().value("tracks/visible_columns", None)
    if raw is None:
        return None
    try:
        names = json.loads(str(raw))
    except ValueError:
        return None
    return [n for n in names if isinstance(n, str)] if isinstance(names, list) else None


def set_visible_columns(names: list[str]) -> None:
    import json

    _qs().setValue("tracks/visible_columns", json.dumps(names))


def get_hidden_columns() -> list[str]:
    """The columns hidden in a layout saved before there were optional columns (that
    version recorded what was hidden, not what was shown). Only read, to carry it over."""
    import json

    try:
        names = json.loads(str(_qs().value("tracks/hidden_columns", "[]") or "[]"))
    except ValueError:
        return []
    return [n for n in names if isinstance(n, str)]


def get_column_order() -> list[str]:
    """The track table's columns, left to right, by title (empty: the default order)."""
    import json

    try:
        names = json.loads(str(_qs().value("tracks/column_order", "[]") or "[]"))
    except ValueError:
        return []
    return [n for n in names if isinstance(n, str)]


def set_column_order(names: list[str]) -> None:
    import json

    _qs().setValue("tracks/column_order", json.dumps(names))
