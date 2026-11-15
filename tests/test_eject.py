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
    # (the volume is a Path: on Windows it prints with backslashes, and this only stands in for a Mac)
    assert seen["command"] == ["diskutil", "eject", str(Path("/Volumes/DISK_IMG"))]
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


def test_a_system_with_no_way_yet_says_so_and_runs_nothing(monkeypatch):
    monkeypatch.setattr(sys, "platform", "freebsd14")

    def run(*a, **kw):
        raise AssertionError("must not run")

    with pytest.raises(eject.EjectError, match="isn't done on this kind of computer"):
        eject.eject_card(Path("/media/card"), run)
    with pytest.raises(eject.EjectError, match="isn't done on this kind of computer"):
        eject.eject_disc(run)


class Linux:
    """findmnt, lsblk and udisksctl as a runner, recording what it was asked."""

    def __init__(
        self, source="/dev/sdb1\n", unmount=None, power_off=None, parent="sdb\n"
    ):
        self.source, self.parent = source, parent
        self.unmount, self.power_off = unmount or finished(), power_off or finished()
        self.seen = []

    def __call__(self, command, **kw):
        self.seen.append(command)
        assert kw["check"] is False and kw["stdin"] is subprocess.DEVNULL
        match command[0], command[1:2]:
            case "findmnt", _:
                return finished(out=self.source)
            case "udisksctl", ["unmount"]:
                return self.unmount
            case "udisksctl", ["power-off"]:
                return self.power_off
            case "lsblk", _:
                return finished(out=self.parent)
            case "eject", _:
                return finished()
        raise AssertionError(command)


@pytest.fixture
def on_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")


def test_linux_unmounts_the_partition_then_powers_the_reader_down(on_linux):
    system = Linux()
    eject.eject_card(Path("/run/media/me/DISK_IMG"), system)
    assert system.seen[0][:5] == [
        "findmnt",
        "--noheadings",
        "--output",
        "SOURCE",
        "--target",
    ]
    assert [
        "udisksctl",
        "unmount",
        "--no-user-interaction",
        "-b",
        "/dev/sdb1",
    ] in system.seen
    assert system.seen[-1] == ["udisksctl", "power-off", "--no-user-interaction", "-b", "/dev/sdb"]  # fmt: skip
    unmounted = system.seen.index(["udisksctl", "unmount", "--no-user-interaction", "-b", "/dev/sdb1"])  # fmt: skip
    asked = system.seen.index(["lsblk", "--noheadings", "--output", "PKNAME", "/dev/sdb1"])  # fmt: skip
    assert asked > unmounted  # (the power-off comes after the flush)


def test_linux_failing_to_unmount_is_an_error_and_nothing_is_powered_off(on_linux):
    system = Linux(unmount=finished(1, err="Error unmounting: target is busy"))
    with pytest.raises(eject.EjectError, match="couldn't eject “DISK_IMG”: Error unmounting: target is busy"):  # fmt: skip
        eject.eject_card(Path("/run/media/me/DISK_IMG"), system)
    assert not any(c[:2] == ["udisksctl", "power-off"] for c in system.seen)


def test_linux_a_reader_that_will_not_power_off_is_not_a_failure(on_linux):
    # a built-in card slot refuses; the card is unmounted, so it is safe to take out
    system = Linux(power_off=finished(1, err="Not authorized to power off"))
    eject.eject_card(Path("/run/media/me/DISK_IMG"), system)  # (no exception)


def test_linux_an_unknown_device_is_said_not_guessed(on_linux):
    for source in ("", "tmpfs\n", "/dev/sdb1; rm -rf ~\n"):
        system = Linux(source=source)
        with pytest.raises(eject.EjectError, match="which device"):
            eject.eject_card(Path("/run/media/me/X"), system)
        assert not any(c[0] == "udisksctl" for c in system.seen)


def test_linux_a_missing_tool_is_an_expected_error(on_linux):
    def missing(command, **kw):
        raise FileNotFoundError(command[0])

    with pytest.raises(eject.EjectError, match="Couldn't run findmnt"):
        eject.eject_card(Path("/run/media/me/X"), missing)


def test_linux_the_disc_is_ejected_with_eject(on_linux):
    system = Linux()
    eject.eject_disc(system)
    assert system.seen == [["eject"]]


@pytest.fixture
def on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")


def test_windows_ejects_the_drive_through_the_shell_and_waits_for_it_to_go(on_windows):
    seen = []
    eject.eject_card(
        Path("E:/"), lambda command, **kw: seen.append(command) or finished()
    )
    (command,) = seen
    assert command[0] == "powershell" and "-NonInteractive" in command
    script = command[-1]
    assert "$drive = 'E:'" in script and "InvokeVerb('Eject')" in script
    assert "Test-Path" in script and "Start-Sleep" in script


def test_windows_a_drive_that_stays_is_said(on_windows):
    run = lambda *a, **kw: finished(
        1, err="Write-Error: The card is still in use: close whatever has it open"
    )
    with pytest.raises(eject.EjectError, match="still in use"):
        eject.eject_card(Path("E:/"), run)


@pytest.mark.parametrize("path", ["/mnt/card", "1:/", "//server/share/card"])
def test_windows_only_a_lettered_drive_root_can_be_ejected_and_nothing_runs(on_windows, path):  # fmt: skip
    def run(*a, **kw):
        raise AssertionError("must not run")

    with pytest.raises(eject.EjectError, match="not a drive with a letter"):
        eject.eject_card(Path(path), run)


def test_windows_the_disc_is_ejected_from_the_first_optical_drive(on_windows):
    seen = []
    eject.eject_disc(lambda command, **kw: seen.append(command) or finished())
    assert "Win32_CDROMDrive" in seen[0][-1] and "InvokeVerb('Eject')" in seen[0][-1]
