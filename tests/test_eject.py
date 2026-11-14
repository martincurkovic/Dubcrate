"""Ejecting a card or a disc (the system's tools are stood in for: no card, no drive)."""

import subprocess
import sys
from pathlib import Path

import pytest

from dubcrate import eject


def finished(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


@pytest.fixture
def on_a_mac(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")


def test_a_card_is_ejected_by_its_volume_without_a_terminal(on_a_mac):
    seen = {}

    def run(command, **kw):
        seen.update(command=command, **kw)
        return finished()

    eject.eject_card(Path("/Volumes/DISK_IMG"), run)
    assert seen["command"] == ["diskutil", "eject", "/Volumes/DISK_IMG"]
    assert seen["check"] is False and seen["stdin"] is subprocess.DEVNULL
    assert seen["timeout"] == eject.TIMEOUT_SECONDS


def test_the_disc_in_the_burner_is_ejected_with_drutil(on_a_mac):
    seen = {}
    eject.eject_disc(lambda command, **kw: seen.update(command=command) or finished())
    assert seen["command"] == ["drutil", "eject"]


def test_a_refusal_is_said_with_the_systems_last_line(on_a_mac):
    run = lambda *a, **kw: finished(
        1, err="Volume DISK_IMG on disk7 failed to eject\ndisk is busy"
    )
    with pytest.raises(
        eject.EjectError, match="couldn't eject “DISK_IMG”: disk is busy"
    ):
        eject.eject_card(Path("/Volumes/DISK_IMG"), run)
    with pytest.raises(eject.EjectError, match="couldn't eject the disc\\.$"):
        eject.eject_disc(lambda *a, **kw: finished(1))


def test_a_tool_that_hangs_or_is_missing_is_an_expected_error(on_a_mac):
    def hangs(*a, **kw):
        raise subprocess.TimeoutExpired("diskutil", 60)

    def missing(*a, **kw):
        raise FileNotFoundError("diskutil")

    with pytest.raises(eject.EjectError, match="didn't finish ejecting"):
        eject.eject_card(Path("/Volumes/X"), hangs)
    with pytest.raises(eject.EjectError, match="Couldn't run diskutil"):
        eject.eject_card(Path("/Volumes/X"), missing)


def test_other_systems_say_it_is_not_done_and_run_nothing(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    def run(*a, **kw):
        raise AssertionError("must not run")

    with pytest.raises(eject.EjectError, match="only done on a Mac"):
        eject.eject_card(Path("/media/card"), run)
    with pytest.raises(eject.EjectError, match="only done on a Mac"):
        eject.eject_disc(run)
