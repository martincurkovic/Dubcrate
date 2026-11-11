"""`dubcrate-gui --smoke-test[=report.txt]`: check that a BUILT app has everything it needs, then
exit (0 = fine, 1 = not).

CI runs this against every freshly built app, because a build can succeed and still be missing a
data file (the stylesheet, the guide, a font), a compiled module (SQLCipher, the audio decoder, the
DiscRecording bridge) or a Qt plugin. It never touches your settings, your library, rekordbox or a
drive: settings and generated icons go to a scratch folder, the library is in memory, sound goes to
a silent stand-in, and DiscRecording is only imported, never asked for a drive.
"""

import os
import shutil
import sys
import tempfile
import wave
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtGui import QFontDatabase, QPixmap
from PySide6.QtWidgets import QApplication


class _SilentDevice:  # a stand-in sound card
    def start(self, gen):
        pass

    def stop(self):
        pass

    def close(self):
        pass


def _check_stylesheets() -> str:
    from dubcrate.ui import theme

    app = QApplication.instance()
    previous = theme.current_preference()
    try:
        for look in ("dark", "light", "system6"):
            theme.apply_to_app(app, look)  # reads both templates and every icon
    finally:
        theme.apply_to_app(app, previous)
    svg = Path(theme._ICONS_DIR) / "checkmark.svg"
    assert svg.is_file(), f"missing {svg}"
    assert not QPixmap(str(svg)).isNull(), (
        "Qt can't load SVG images (is its svg plugin bundled?)"
    )
    return "dark, light and System 6 render, SVG images load"


def _check_fonts() -> str:
    from dubcrate.ui import theme

    font = Path(theme._FONTS_DIR) / "ChiKareGo2.ttf"
    assert font.is_file(), f"missing {font}"
    assert QFontDatabase.addApplicationFont(str(font)) >= 0, (
        f"Qt can't load {font.name}"
    )
    return font.name


def _check_guide() -> str:
    from dubcrate.ui import help_dialog

    assert help_dialog.MANUAL_PATH.is_file(), f"missing {help_dialog.MANUAL_PATH}"
    dialog = help_dialog.HelpDialog()
    try:
        assert dialog.viewer.document().characterCount() > 1000, (
            "the guide rendered empty"
        )
    finally:
        dialog.deleteLater()
    return "the user guide renders"


def _check_icon() -> str:
    from dubcrate.ui.version import icon_path

    path = icon_path()
    assert path is not None, "no AppIcon.png beside the code or in assets/icon"
    assert not QPixmap(str(path)).isNull(), f"Qt can't read {path}"
    return path.name


def _check_audio_decoder() -> str:
    from dubcrate.audio.decode import decode_file

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        path = Path(scratch) / "silence.wav"
        with wave.open(str(path), "wb") as f:
            f.setnchannels(2)
            f.setsampwidth(2)
            f.setframerate(44_100)
            f.writeframes(b"\0\0\0\0" * 4410)
        assert len(decode_file(str(path)).samples) == 4410, (
            "miniaudio decoded the wrong length"
        )
    return "miniaudio decodes a WAV"


def _check_sqlcipher() -> str:
    import sqlalchemy
    import sqlcipher3

    import dubcrate.library.rekordbox  # noqa: F401  (pyrekordbox and what it imports)

    connection = sqlcipher3.connect(":memory:")
    connection.execute("PRAGMA key = 'smoke'")
    connection.execute("CREATE TABLE t (x)")
    version = connection.execute("PRAGMA cipher_version").fetchone()[0]
    assert version, (
        "SQLCipher reports no cipher version: it is built without encryption"
    )
    sqlalchemy.create_engine("sqlite://").connect().close()
    return f"SQLCipher {version}, pyrekordbox imports"


def _check_disc_recording() -> str:
    if sys.platform != "darwin":
        return "skipped (not macOS)"
    import DiscRecording
    import objc

    # Looking classes up loads the framework through the bridge; no drive is asked for.
    assert DiscRecording.DRBurn is not None and DiscRecording.DRTrack is not None
    from dubcrate.burn import discrecording  # noqa: F401

    return f"objc {objc.__version__}, DiscRecording loads"


def _check_notices() -> str:
    from dubcrate.ui import about_dialog

    dialog = about_dialog.NoticesDialog()
    try:
        assert dialog.viewer.document().characterCount() > 1000, (
            "the notices rendered empty"
        )
    finally:
        dialog.deleteLater()
    return "the third-party notices render"


def _check_ca_bundle() -> str:
    import certifi

    assert Path(certifi.where()).is_file(), f"no CA bundle at {certifi.where()}: the update check could not verify GitHub"  # fmt: skip
    return "CA bundle present (update check)"


def _check_tools() -> str:
    """ffmpeg and ffprobe read the tracks, cdrdao is the optional second burner: only reported."""
    found = [
        f"{tool}: {shutil.which(tool) or 'not found'}"
        for tool in ("ffmpeg", "ffprobe", "cdrdao")
    ]
    return ", ".join(found)


def _check_window() -> str:
    from dubcrate.audio.player import AudioPlayer
    from dubcrate.library import Library, TrackRecord
    from dubcrate.ui.main_window import MainWindow
    from dubcrate.ui.splash import StartupSplash

    splash = StartupSplash("smoke test")
    try:
        assert not splash.grab().isNull()
    finally:
        splash.deleteLater()
    with Library.open(":memory:") as lib:
        records = [TrackRecord(ext_id="1", path="/nonexistent/1.mp3", title="One", artist="A", duration=60.0)]  # fmt: skip
        lib.replace_source("folder", "/nonexistent", "Smoke", records, [])
        disc = lib.create_disc("Smoke disc")
        lib.add_to_disc(disc, [t.id for t in lib.tracks()])
        window = MainWindow(
            lib, player=AudioPlayer(device_factory=lambda *a: _SilentDevice())
        )
        try:
            window.show()
            window.open_disc(disc)
            QApplication.processEvents()
            assert not window.grab().isNull()
        finally:
            window.close()
            window.deleteLater()
    return "the splash and main window build, with a disc open"


def run(report_path: str | None = None, isolate: bool = True) -> int:
    """Run every check and write the report (to `report_path`, else print it).

    `isolate` sends settings and generated icons to a scratch folder; only the tests, which do
    that already, pass False.
    """
    lines: list[str] = []
    failures = 0

    def check(name, fn):
        nonlocal failures
        try:
            lines.append(f"ok    {name}: {fn()}")
        except Exception as e:  # noqa: BLE001  (report every problem, not just the first)
            failures += 1
            lines.append(f"FAIL  {name}: {e.__class__.__name__}: {e}")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        from dubcrate.ui import theme

        saved = os.environ.get("DUBCRATE_DATA_DIR"), theme._GENERATED_ICONS_DIR
        if isolate:
            os.environ["DUBCRATE_DATA_DIR"] = str(Path(scratch) / "data")
            # (the theme works this folder out when it is imported, before we can set the variable)
            theme._GENERATED_ICONS_DIR = str(Path(scratch) / "data" / "generated_icons")
            QSettings.setDefaultFormat(QSettings.Format.IniFormat)
            QSettings.setPath(
                QSettings.Format.IniFormat,
                QSettings.Scope.UserScope,
                str(Path(scratch) / "settings"),
            )
        try:
            if QApplication.instance() is None:
                app = QApplication([sys.argv[0]])
                app.setOrganizationName("Dubcrate")
                app.setApplicationName("Dubcrate")
            for name, fn in (
                ("stylesheets", _check_stylesheets),
                ("fonts", _check_fonts),
                ("guide", _check_guide),
                ("icon", _check_icon),
                ("audio decoder", _check_audio_decoder),
                ("sqlcipher", _check_sqlcipher),
                ("disc recording", _check_disc_recording),
                ("notices", _check_notices),
                ("ca bundle", _check_ca_bundle),
                ("tools", _check_tools),
                ("window", _check_window),
            ):
                check(name, fn)
        finally:
            if isolate:
                data_dir, theme._GENERATED_ICONS_DIR = saved
                if data_dir is None:
                    os.environ.pop("DUBCRATE_DATA_DIR", None)
                else:
                    os.environ["DUBCRATE_DATA_DIR"] = data_dir

    report = "\n".join(lines) + (
        "\nSMOKE TEST FAILED" if failures else "\nsmoke test passed"
    )
    if report_path:
        Path(report_path).write_text(report + "\n", encoding="utf-8")
    else:
        print(report)
    return 1 if failures else 0
