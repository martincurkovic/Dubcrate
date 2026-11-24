"""Which burn backend a computer uses, and what it says when it can't burn at all."""

import sys

import pytest

from dubcrate import burn, tools
from dubcrate.burn import cdrdao


@pytest.mark.parametrize(
    ("platform", "backend"),
    [("darwin", "discrecording"), ("linux", "cdrdao"), ("win32", "cdrdao")],
)
def test_the_default_backend_is_decided_by_the_platform_in_one_place(
    monkeypatch, platform, backend
):
    monkeypatch.setattr(burn.sys, "platform", platform)
    assert burn.default_backend() == backend
    assert backend in burn.BACKENDS


def test_discrecording_is_only_offered_on_a_mac(monkeypatch):
    monkeypatch.setattr(burn.sys, "platform", "linux")
    assert "only works on a Mac" in burn.backend_problem("discrecording")
    monkeypatch.setattr(burn.sys, "platform", "darwin")
    assert burn.backend_problem("discrecording") is None


@pytest.fixture
def no_cdrdao(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "BUNDLE_DIR", tmp_path / "no-bundle")
    monkeypatch.setattr(tools.shutil, "which", lambda _name: None)


def test_a_missing_cdrdao_is_explained_with_how_to_get_it(no_cdrdao, monkeypatch):
    monkeypatch.setattr(cdrdao.sys, "platform", "linux")
    problem = burn.backend_problem("cdrdao")
    assert "was not found" in problem and "sudo apt install cdrdao" in problem
    assert "sudo dnf install cdrdao" in problem
    monkeypatch.setattr(cdrdao.sys, "platform", "darwin")
    assert "brew install cdrdao" in burn.backend_problem("cdrdao")
    monkeypatch.setattr(cdrdao.sys, "platform", "win32")
    assert "Windows installer includes it" in burn.backend_problem("cdrdao")


def test_cdrdao_is_found_in_the_bundle_or_on_path(no_cdrdao, tmp_path, monkeypatch):
    assert burn.backend_problem("cdrdao") is not None and cdrdao.find_cdrdao() is None
    on_path = tmp_path / "usr" / "cdrdao"
    on_path.parent.mkdir()
    on_path.write_text("#!/bin/sh\n")
    on_path.chmod(0o755)
    monkeypatch.setattr(tools.shutil, "which", lambda _name: str(on_path))
    assert cdrdao.find_cdrdao() == str(on_path)
    assert burn.backend_problem("cdrdao") is None


def test_an_unknown_backend_is_a_problem_not_a_crash():
    assert "Unknown burn backend" in burn.backend_problem("nero")


def test_burning_without_cdrdao_says_how_to_get_it(no_cdrdao, tmp_path):
    from dubcrate.burn import BurnError, BurnOptions, Disc

    with pytest.raises(BurnError, match="cdrdao was not found"):
        cdrdao.CdrdaoBurner().burn(Disc(title="t", performer="p"), BurnOptions(), print)


# -- what the macOS burner asks DiscRecording to do when the burn ends -----------------------------------------


@pytest.mark.skipif(sys.platform != "darwin", reason="DiscRecording is macOS only")
@pytest.mark.parametrize("eject,expected", [(True, "eject"), (False, "mount")])
def test_the_burner_says_whether_to_eject_or_leave_the_disc_in_the_drive(
    monkeypatch, eject, expected
):
    from types import SimpleNamespace

    from dubcrate.burn import discrecording
    from dubcrate.burn.base import BurnOptions

    seen = {}

    class FakeBurn:
        @classmethod
        def burnForDevice_(cls, device):
            return cls()

        def setProperties_(self, props):
            seen.update(props)

        def writeLayout_(self, layout):
            pass

        def status(self):
            return {"state": "done"}

    fake = SimpleNamespace(
        DRBurn=FakeBurn, DRDeviceBurnSpeedMax=0, DRDeviceBurnSpeedCD1x=1,
        DRBurnRequestedSpeedKey="speed", DRBurnUnderrunProtectionKey="underrun", DRBurnVerifyDiscKey="verify",
        DRBurnTestingKey="testing", DRBurnAppendableKey="appendable", DRBurnCompletionActionKey="action",
        DRBurnCompletionActionEject="eject", DRBurnCompletionActionMount="mount",
        DRStatusStateKey="state", DRStatusPercentCompleteKey="percent", DRStatusStateDone="done",
        DRStatusStateFailed="failed",
    )  # fmt: skip
    monkeypatch.setattr(discrecording, "DR", fake)
    monkeypatch.setattr(discrecording, "_pump", lambda _s: None)
    discrecording.DiscRecordingBurner()._run(
        object(), BurnOptions(eject=eject), {}, [], lambda _t: None
    )
    assert seen["action"] == expected


# -- the drives a burner sees, and which one is used ---------------------------------------------------


def test_the_first_drive_is_used_until_one_is_picked_or_remembered():
    from dubcrate.burn import Drive, choose_drive

    one, two = Drive("disk3", "LITE-ON eSAU108"), Drive("disk4", "ASUS SDRW-08D2S")
    assert (
        choose_drive([], "disk3", "LITE-ON eSAU108") is None
    )  # (no drives: nothing to choose)
    assert choose_drive([one, two]) is one
    assert (
        choose_drive([one, two], remembered="ASUS SDRW-08D2S") is two
    )  # (by name: the id can change)
    assert (
        choose_drive([one, two], current="disk3", remembered="ASUS SDRW-08D2S") is one
    )  # (this window's pick wins)
    assert (
        choose_drive([one, two], current="disk9", remembered="nothing like it") is one
    )  # (both gone: the first)
    assert choose_drive([one], remembered="ASUS SDRW-08D2S") is one


def test_cdrdao_lists_the_drives_its_scan_finds(monkeypatch):
    monkeypatch.setattr(
        cdrdao,
        "scan_devices",
        lambda: [
            ("/dev/sr0", "HL-DT-ST BD-RE  WH16NS40"),
            ("/dev/sr1", "LITE-ON eSAU108"),
        ],
    )
    drives = cdrdao.CdrdaoBurner().drives()
    assert [(d.id, d.name) for d in drives] == [
        ("/dev/sr0", "HL-DT-ST BD-RE  WH16NS40 (/dev/sr0)"),
        ("/dev/sr1", "LITE-ON eSAU108 (/dev/sr1)"),
    ]
    monkeypatch.setattr(cdrdao, "scan_devices", list)
    assert cdrdao.CdrdaoBurner().drives() == []


@pytest.mark.skipif(sys.platform != "darwin", reason="DiscRecording is macOS only")
def test_discrecording_lists_its_drives_by_bsd_name_and_display_name(monkeypatch):
    from dubcrate.burn import discrecording

    class Device:
        def __init__(self, bsd, name):
            self._bsd, self._name = bsd, name

        def bsdName(self):
            return self._bsd

        def displayName(self):
            return self._name

    monkeypatch.setattr(
        discrecording,
        "list_devices",
        lambda: [Device("disk3", "LITE-ON eSAU108"), Device("disk4", None)],
    )
    drives = discrecording.DiscRecordingBurner().drives()
    assert [(d.id, d.name) for d in drives] == [
        ("disk3", "LITE-ON eSAU108"),
        ("disk4", "disk4"),  # (no display name: the BSD name is all there is)
    ]


def test_a_drive_remembered_by_name_is_found_whether_or_not_the_account_could_describe_it():
    from dubcrate.burn.base import Drive

    described = Drive("E:\\", "slimtype, eSAU108   3, HL09 (E:\\)")
    bare = Drive("E:\\", "E:\\")  # (what a standard Windows account is shown)
    other = Drive("F:\\", "F:\\")
    assert burn.choose_drive([other, described], remembered=described.name) is described
    assert (
        burn.choose_drive([other, bare], remembered=described.name) is bare
    )  # (remembered as an administrator)
    assert (
        burn.choose_drive([other, described], remembered=bare.name) is described
    )  # (and the other way round)
    assert (
        burn.choose_drive([other, described], remembered="gone") is other
    )  # (nothing matches: the first)
