"""Finding ffmpeg and ffprobe: the bundled copy first, then PATH; the notice when they're gone."""

import stat
import sys

import pytest

from dubcrate import tools
from dubcrate.render import RenderError, ToolMissingError, probe
from dubcrate.ui.tools_dialog import MissingToolsDialog


def _fake_tool(folder, name, version="9.9", mode=0o755):
    """A script that answers -version like ffmpeg does (POSIX only, like the app)."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(f"#!/bin/sh\necho '{name} version {version} Copyright (c) test'\n")
    path.chmod(mode)
    return path


@pytest.fixture
def nowhere(tmp_path, monkeypatch):
    """No bundle and nothing on PATH."""
    monkeypatch.setattr(tools, "BUNDLE_DIR", tmp_path / "no-bundle")
    monkeypatch.setattr(tools.shutil, "which", lambda _name: None)


def test_nothing_found_is_none(nowhere):
    assert tools.find_tool("ffmpeg") is None
    assert tools.missing_tools() == ["ffmpeg", "ffprobe"]


def test_a_tool_on_path_is_found_and_says_so(nowhere, tmp_path, monkeypatch):
    on_path = _fake_tool(tmp_path / "usr", "ffmpeg")
    monkeypatch.setattr(tools.shutil, "which", lambda name: str(on_path) if name == "ffmpeg" else None)  # fmt: skip
    found = tools.find_tool("ffmpeg")
    assert found is not None and found.path == on_path and not found.bundled
    assert found.source == "found on your PATH"
    assert tools.missing_tools() == ["ffprobe"]


def test_the_bundled_copy_wins_over_path(nowhere, tmp_path, monkeypatch):
    bundled = _fake_tool(tmp_path / "bundle", "ffmpeg")
    on_path = _fake_tool(tmp_path / "usr", "ffmpeg")
    monkeypatch.setattr(tools, "BUNDLE_DIR", bundled.parent)
    monkeypatch.setattr(tools.shutil, "which", lambda _name: str(on_path))
    found = tools.find_tool("ffmpeg")
    assert found.path == bundled and found.bundled
    assert found.source == "bundled with Dubcrate"


def test_a_bundled_file_that_is_not_executable_is_skipped(nowhere, tmp_path, monkeypatch):  # fmt: skip
    bundled = _fake_tool(tmp_path / "bundle", "ffmpeg", mode=0o644)
    monkeypatch.setattr(tools, "BUNDLE_DIR", bundled.parent)
    assert tools.find_tool("ffmpeg") is None  # (and nothing falls over)
    bundled.chmod(bundled.stat().st_mode | stat.S_IXUSR)
    assert tools.find_tool("ffmpeg") is not None


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-ins are shell scripts")
def test_version_and_description(nowhere, tmp_path, monkeypatch):
    bundled = _fake_tool(tmp_path / "bundle", "ffmpeg", version="7.1.1")
    monkeypatch.setattr(tools, "BUNDLE_DIR", bundled.parent)
    assert tools.tool_version(tools.find_tool("ffmpeg")) == "7.1.1"
    line = tools.describe("ffmpeg")
    assert line == f"ffmpeg 7.1.1: {bundled} (bundled with Dubcrate)"
    assert tools.describe("ffprobe") == "ffprobe: not found"


def test_a_tool_that_will_not_run_has_no_version(tmp_path):
    broken = tools.Tool("ffmpeg", tmp_path / "gone", bundled=True)
    assert tools.tool_version(broken) == ""


def test_the_words_for_a_missing_tool_say_how_to_fix_it():
    text = tools.missing_message("ffprobe")
    assert text.startswith("ffprobe was not found")
    assert "brew install ffmpeg" in text and "ffmpeg.org" in text


def test_running_a_missing_tool_is_an_expected_error(nowhere, tmp_path):
    with pytest.raises(ToolMissingError, match="brew install ffmpeg") as caught:
        probe(tmp_path / "a.mp3")
    assert isinstance(
        caught.value, RenderError
    )  # callers that catch RenderError still do


def test_a_missing_tool_is_not_reported_as_every_file_being_unreadable(nowhere, tmp_path):  # fmt: skip
    from dubcrate.library.folder import scan_directory

    (tmp_path / "a.mp3").write_bytes(b"x")
    (tmp_path / "b.mp3").write_bytes(b"x")
    with pytest.raises(ToolMissingError):
        scan_directory(tmp_path)


def test_the_startup_notice_names_what_is_missing(qapp):
    dialog = MissingToolsDialog(["ffmpeg", "ffprobe"])
    assert "ffmpeg and ffprobe" in dialog.heading.text()
    assert dialog.command_field.text() == "brew install ffmpeg"
    assert "ffmpeg.org" in dialog.link_label.text()
    dialog.copy_button.click()
    from PySide6.QtWidgets import QApplication

    assert QApplication.clipboard().text() == "brew install ffmpeg"
    dialog.deleteLater()


def test_settings_lists_where_each_tool_is(qapp, nowhere):
    from dubcrate.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog()
    assert dialog.tool_labels["ffmpeg"].text() == "ffmpeg: not found"
    assert dialog.tool_labels["ffprobe"].text() == "ffprobe: not found"
    dialog.deleteLater()
