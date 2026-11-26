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


# -- a Mac's MP3 disc is burned from Dubcrate's own image -------------------------------------------------

mac_only = pytest.mark.skipif(
    sys.platform != "darwin", reason="DiscRecording is macOS only"
)


class FakeDevice:
    def __init__(self, bsd="disk3", name="LITE-ON eSAU108"):
        self._bsd, self._name = bsd, name

    def bsdName(self):
        return self._bsd

    def displayName(self):
        return self._name


def stage(tmp_path, files=2):
    folder = tmp_path / "VOLUME"
    folder.mkdir()
    for n in range(1, files + 1):
        (folder / f"{n:02d} A - B.mp3").write_bytes(b"x" * 5000)
    return folder


@mac_only
@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({}, ["-notest", "-noverify", "-eject", "-noappendable", "-speed", "10"]),
        ({"simulate": True, "verify": True, "eject": False, "speed": 4},
         ["-test", "-verify", "-mount", "-noappendable", "-speed", "4"]),
        ({"speed": None}, ["-notest", "-noverify", "-eject", "-noappendable"]),
    ],
)  # fmt: skip
def test_the_drutil_command_uses_the_spellings_its_manual_gives(options, expected):
    from pathlib import Path

    from dubcrate.burn.base import BurnOptions
    from dubcrate.burn.discrecording import drutil_burn_command

    cmd = drutil_burn_command(Path("/t/disc.iso"), 2, BurnOptions(**options))
    assert cmd == ["drutil", "-drive", "2", "burn", *expected, "/t/disc.iso"]


@mac_only
def test_burning_an_mp3_disc_builds_our_image_and_hands_it_to_drutil(tmp_path, monkeypatch):  # fmt: skip
    from datetime import datetime

    from dubcrate import iso
    from dubcrate.build import image_record
    from dubcrate.burn import BurnOptions, DataDisc, discrecording

    monkeypatch.setattr(discrecording, "list_devices", lambda: [FakeDevice("disk3"), FakeDevice("disk4", "Other")])  # fmt: skip
    monkeypatch.setattr(
        discrecording,
        "describe_device",
        lambda dev: {"name": dev.displayName(), "support": "x", "media_present": True, "media_free_blocks": 359847},
    )  # fmt: skip
    seen = {}

    def burn(image, drive, options, progress):
        seen["drive"] = drive
        seen["sectors"] = image.stat().st_size // 2048
        seen["layout"] = iso.image_layout(image, folder)
        seen["date"] = iso.joliet_descriptor(image)[813:829]
        seen["size_ok"] = image.stat().st_size % 2048 == 0

    monkeypatch.setattr(
        discrecording.DiscRecordingBurner, "_burn_image", staticmethod(burn)
    )
    folder = stage(tmp_path)
    when = datetime.fromisoformat("2026-11-23T19:47:51+00:00")
    disc = DataDisc("VOLUME", folder, created=when)
    discrecording.DiscRecordingBurner().burn_data(
        disc, BurnOptions(device="disk4"), print
    )
    assert (
        seen["drive"] == 2 and seen["size_ok"]
    )  # (drutil numbers its drives from 1, in the same order)
    assert (
        seen["date"] == b"2026112319475100"
    )  # (the date we chose is in the image that is burned)
    assert disc.created == when and disc.layout is not None
    record = image_record(
        disc
    )  # (so Sync can make the card's entry before the player has seen the disc)
    assert record is not None
    assert (record.file_count, record.folders, record.sectors) == (
        2,
        0,
        seen["sectors"],
    )
    assert record.last_file_start == seen["layout"].last_file_start
    assert record.created.startswith("2026-11-23T19:47:51")


@mac_only
def test_a_mac_burn_without_a_date_makes_one_and_a_nested_folder_is_burned_but_not_recorded(tmp_path, monkeypatch):  # fmt: skip
    from dubcrate.build import image_record
    from dubcrate.burn import BurnOptions, DataDisc, discrecording

    monkeypatch.setattr(discrecording, "list_devices", lambda: [FakeDevice()])
    monkeypatch.setattr(discrecording, "describe_device", lambda dev: {"name": "d", "support": "x", "media_present": True, "media_free_blocks": 0})  # fmt: skip
    burned = []
    monkeypatch.setattr(discrecording.DiscRecordingBurner, "_burn_image", staticmethod(lambda *a: burned.append(a)))  # fmt: skip
    folder = stage(tmp_path)
    disc = DataDisc("VOLUME", folder)
    discrecording.DiscRecordingBurner().burn_data(disc, BurnOptions(), print)
    assert disc.created is not None and image_record(disc) is not None
    (folder / "inner" / "deeper").mkdir(parents=True)
    (folder / "inner" / "deeper" / "03 C - D.mp3").write_bytes(b"y" * 3000)
    nested = DataDisc("VOLUME", folder)
    discrecording.DiscRecordingBurner().burn_data(nested, BurnOptions(), print)
    assert len(burned) == 2 and nested.layout is None and image_record(nested) is None


@mac_only
def test_a_mac_burn_refuses_an_empty_drive_and_a_disc_that_is_too_big(tmp_path, monkeypatch):  # fmt: skip
    from dubcrate.burn import BurnError, BurnOptions, DataDisc, discrecording

    monkeypatch.setattr(discrecording, "list_devices", lambda: [FakeDevice()])
    state = {"media_present": False, "media_free_blocks": 0}
    monkeypatch.setattr(discrecording, "describe_device", lambda dev: {"name": "d", "support": "x", **state})  # fmt: skip
    burned = []
    monkeypatch.setattr(discrecording.DiscRecordingBurner, "_burn_image", staticmethod(lambda *a: burned.append(a)))  # fmt: skip
    folder = stage(tmp_path)
    burner = discrecording.DiscRecordingBurner()
    with pytest.raises(BurnError, match="no disc in the drive"):
        burner.burn_data(DataDisc("VOLUME", folder), BurnOptions(), print)
    state.update(media_present=True, media_free_blocks=10)
    with pytest.raises(BurnError, match="holds 10 sectors"):
        burner.burn_data(DataDisc("VOLUME", folder), BurnOptions(), print)
    assert burned == []  # (nothing was handed to drutil)


class FakeProc:
    def __init__(self, lines, status=0):
        self.stdout = iter(lines)
        self._status = status

    def wait(self):
        return self._status


@mac_only
def test_drutil_s_output_becomes_progress_and_a_failure_is_an_error(tmp_path, monkeypatch):  # fmt: skip
    from dubcrate.burn import BurnError, BurnOptions, discrecording

    ran = []

    def popen(cmd, **kwargs):
        ran.append((cmd, kwargs))
        return FakeProc(["Please wait...\n", "Burning 3%\r", "Burning 8%\r", "Burning 12%\r", "Burning 99%\n", "Finished\n"])  # fmt: skip

    monkeypatch.setattr(discrecording.subprocess, "Popen", popen)
    said = []
    discrecording.DiscRecordingBurner._burn_image(tmp_path / "disc.iso", 1, BurnOptions(), said.append)  # fmt: skip
    assert said[0].startswith("$ drutil -drive 1 burn")
    assert said[1:] == [
        "Please wait...",
        "burning 0%",
        "burning 10%",
        "burning 90%",
        "Finished",
    ]
    _cmd, kwargs = ran[0]
    assert kwargs["stdin"] is discrecording.subprocess.DEVNULL and kwargs["encoding"] == "utf-8"  # fmt: skip

    monkeypatch.setattr(discrecording.subprocess, "Popen", lambda cmd, **k: FakeProc(["Burn failed: no disc\n"], 0))  # fmt: skip
    with pytest.raises(BurnError, match="drutil burn failed: Burn failed: no disc"):
        discrecording.DiscRecordingBurner._burn_image(tmp_path / "disc.iso", 1, BurnOptions(), print)  # fmt: skip
    monkeypatch.setattr(discrecording.subprocess, "Popen", lambda cmd, **k: FakeProc([], 1))  # fmt: skip
    with pytest.raises(BurnError, match="exit status 1"):
        discrecording.DiscRecordingBurner._burn_image(tmp_path / "disc.iso", 1, BurnOptions(), print)  # fmt: skip
