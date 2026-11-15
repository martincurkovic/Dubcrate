"""`dubcrate doctor` (the tools and drives are stood in for: nothing is run, nothing is burned)."""

import sys
from pathlib import Path

import pytest

from dubcrate import cli, cli_doctor, tools
from dubcrate.burn import cdrdao as cdrdao_backend


def fake_tools(monkeypatch, present):
    found = {n: tools.Tool(n, Path(f"/bin/{n}"), bundled=False) for n in present}
    monkeypatch.setattr(tools, "find_tool", lambda name: found.get(name))
    monkeypatch.setattr(tools, "tool_version", lambda _tool: "9.9")
    monkeypatch.setattr(cdrdao_backend, "find_cdrdao", lambda: None)
    monkeypatch.setattr(sys, "platform", "linux")  # (no DiscRecording to ask)


def test_it_says_where_each_tool_is_and_what_is_missing(monkeypatch, capsys, tmp_path):
    fake_tools(monkeypatch, {"ffmpeg", "ffprobe", "drutil"})
    assert cli.main(["--library", str(tmp_path / "x.db"), "doctor"]) == 0
    out = capsys.readouterr().out
    assert "ffmpeg 9.9: /bin/ffmpeg" in out and "ffprobe 9.9" in out
    assert "drutil: /bin/drutil" in out and "cdrdao: not found" in out


def test_a_missing_ffmpeg_is_an_error_with_the_way_out(monkeypatch, capsys, tmp_path):
    fake_tools(monkeypatch, {"ffprobe"})
    assert cli.main(["--library", str(tmp_path / "x.db"), "doctor"]) == 1
    out = capsys.readouterr().out
    assert "ffmpeg: not found" in out and "missing: ffmpeg" in out and "Install them" in out  # fmt: skip


def test_the_drive_part_is_only_asked_on_a_mac(monkeypatch):
    fake_tools(monkeypatch, {"ffmpeg", "ffprobe"})
    said = []
    cli_doctor.cmd_doctor(None, None, said.append)
    assert not any("DiscRecording" in line for line in said)


@pytest.mark.parametrize("name", cli_doctor.OPTIONAL)
def test_the_optional_tools_are_not_required(name):
    assert name not in tools.REQUIRED
