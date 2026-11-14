"""The TOC of the disc in the drive (`drutil` is stood in for: no drive here)."""

import subprocess
import sys
from pathlib import Path

import pytest

from dubcrate import cdj_card as cc
from dubcrate import drive_toc
from dubcrate.drive_toc import DriveTocError, parse_drive_toc, read_drive_toc

DATA = Path(__file__).parent / "data" / "cdj_card"


def saved(name="CD2_TOC.txt"):
    return (DATA / name).read_text(encoding="utf-8")


def finished(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess(["drutil", "toc"], code, stdout, stderr)


@pytest.fixture
def on_a_mac(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")


def test_an_audio_disc_gives_its_track_starts_and_lead_out():
    assert parse_drive_toc(saved()) == cc.toc_from_drutil(saved())


def test_it_runs_drutil_toc_without_a_terminal_and_reads_what_it_prints(on_a_mac):
    seen = {}

    def run(command, **kw):
        seen.update(command=command, **kw)
        return finished(saved())

    assert read_drive_toc(run) == cc.toc_from_drutil(saved())
    assert seen["command"] == ["drutil", "toc"]
    assert seen["check"] is False and seen["stdin"] is subprocess.DEVNULL
    assert seen["encoding"] == "utf-8" and seen["errors"] == "replace"
    assert seen["timeout"] == drive_toc.TIMEOUT_SECONDS


def test_no_disc_says_what_drutil_said(on_a_mac):
    def run(command, **kw):
        return finished("", "drutil: Please insert a disc.", code=1)

    with pytest.raises(DriveTocError, match="no readable disc.*Please insert a disc"):
        read_drive_toc(run)


def test_no_output_at_all_is_still_a_plain_message(on_a_mac):
    with pytest.raises(DriveTocError, match="drutil printed nothing"):
        read_drive_toc(lambda *a, **kw: finished())


def test_a_disc_that_is_not_plain_audio_is_refused(on_a_mac):
    data_disc = saved().replace("2ch audio", "data", 1)
    with pytest.raises(DriveTocError, match="isn't a plain audio CD"):
        read_drive_toc(lambda *a, **kw: finished(data_disc))


def test_a_drive_that_does_not_answer_or_a_missing_drutil_is_an_expected_error(
    on_a_mac,
):
    def hangs(*a, **kw):
        raise subprocess.TimeoutExpired("drutil", 60)

    def missing(*a, **kw):
        raise FileNotFoundError("drutil")

    with pytest.raises(DriveTocError, match="didn't answer"):
        read_drive_toc(hangs)
    with pytest.raises(DriveTocError, match="Couldn't run drutil"):
        read_drive_toc(missing)


def test_other_systems_say_it_is_not_done_and_run_nothing(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    def run(*a, **kw):
        raise AssertionError("must not run")

    with pytest.raises(DriveTocError, match="only done on a Mac"):
        read_drive_toc(run)
