"""What a built app (Nuitka, started from Finder) needs that a run from source does not."""

import os
import subprocess
import sys
import types
from pathlib import Path

from dubcrate import tools
from dubcrate.ui import app, smoke_test, version

# -- finding ffmpeg and cdrdao ----------------------------------------------------------


def test_homebrews_folders_are_added_to_the_path_of_an_app_started_from_finder():
    # Finder gives an app only /usr/bin:/bin:/usr/sbin:/sbin, and ffmpeg lives in Homebrew's
    system = ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    path = app.extend_tool_path(
        os.pathsep.join(system)
    )  # (the function splits on the system's separator)
    folders = path.split(os.pathsep)
    assert folders[:4] == system
    assert "/opt/homebrew/bin" in folders and "/usr/local/bin" in folders


def test_the_users_own_order_wins_and_nothing_is_added_twice():
    given = os.pathsep.join(["/usr/local/bin", "/usr/bin", "/opt/homebrew/bin"])
    assert app.extend_tool_path(given) == given
    assert app.extend_tool_path("").split(os.pathsep)[0] != ""  # (no empty first entry)


# -- the version and the icon of a built app ---------------------------------------------


def test_a_built_app_reports_the_version_the_build_script_wrote(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "dubcrate._build_version",
        types.SimpleNamespace(APP_VERSION="9.8.7"),
    )
    assert version.app_version() == "9.8.7"


def test_about_shows_the_version_the_build_wrote(qapp, monkeypatch):
    from dubcrate.ui.about_dialog import AboutDialog
    from dubcrate.ui.update_check import UpdateChecker

    monkeypatch.setitem(
        sys.modules,
        "dubcrate._build_version",
        types.SimpleNamespace(APP_VERSION="9.8.7"),
    )
    dialog = AboutDialog(None, UpdateChecker(None), None)
    assert dialog.version_label.text() == "Version 9.8.7"


def test_without_a_build_version_the_installed_one_is_used(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "dubcrate._build_version", None
    )  # (not importable)
    assert version.app_version() not in ("", "9.8.7")


def test_the_icon_is_found_beside_the_code_of_a_built_app(tmp_path, monkeypatch):
    ui = tmp_path / "dubcrate" / "ui"
    ui.mkdir(parents=True)
    (ui / "AppIcon.png").write_bytes(b"png")
    monkeypatch.setattr(version, "__file__", str(ui / "app.py"))
    assert version.icon_path() == ui / "AppIcon.png"


def test_the_icon_is_found_in_a_checkout():
    path = version.icon_path()
    assert path is not None and path.name == "AppIcon.png" and path.is_file()


# -- the smoke test CI runs against a freshly built app ------------------------------------


def test_the_smoke_test_passes_and_says_what_it_checked(tmp_path):
    report = tmp_path / "report.txt"
    assert smoke_test.run(str(report), isolate=False) == 0
    text = report.read_text(encoding="utf-8")
    assert text.rstrip().endswith("smoke test passed")
    for what in ("stylesheets", "fonts", "guide", "notices", "icon", "audio decoder",
                 "ca bundle", "sqlcipher", "window"):  # fmt: skip
        assert f"ok    {what}" in text, what
    assert "FAIL" not in text


def test_a_failed_check_is_reported_and_the_exit_code_is_one(tmp_path, monkeypatch):
    def broken():
        raise RuntimeError("the font is missing")

    monkeypatch.setattr(smoke_test, "_check_fonts", broken)
    report = tmp_path / "report.txt"
    assert smoke_test.run(str(report), isolate=False) == 1
    text = report.read_text(encoding="utf-8")
    assert "FAIL  fonts: RuntimeError: the font is missing" in text
    assert text.rstrip().endswith("SMOKE TEST FAILED")
    assert "ok    stylesheets" in text  # every problem is listed, not just the first


def test_the_missing_command_line_tools_are_a_note_not_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "BUNDLE_DIR", tmp_path / "no-bundle")
    monkeypatch.setattr(smoke_test.shutil, "which", lambda name: None)
    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    report = tmp_path / "report.txt"
    assert smoke_test.run(str(report), isolate=False) == 0
    assert "ffmpeg: not found" in report.read_text(encoding="utf-8")


def test_the_built_apps_entry_runs_the_smoke_test_without_touching_your_data(tmp_path):
    """`dubcrate-gui --smoke-test=FILE`, as CI runs the built app: against an empty HOME,
    it must report success and leave no settings, library or generated icons behind
    (the theme creates its empty app-data folder when it is imported, as on every launch)."""
    home = tmp_path / "home"
    home.mkdir()
    report = tmp_path / "report.txt"
    env = {k: v for k, v in os.environ.items() if k != "DUBCRATE_DATA_DIR"}
    env.update(HOME=str(home), QT_QPA_PLATFORM="offscreen")
    result = subprocess.run(
        [sys.executable, "-m", "dubcrate.ui", f"--smoke-test={report}"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    text = report.read_text(encoding="utf-8") if report.exists() else result.stderr
    assert result.returncode == 0, text
    assert text.rstrip().endswith("smoke test passed"), text
    written = [p for p in home.rglob("*") if p.is_file()]
    assert written == [], "the smoke test wrote files into the user's home"
    assert Path(report).is_file()
