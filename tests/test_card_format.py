"""Formatting a blank card (`diskutil` is stood in for: no card is ever touched)."""

import plistlib
import subprocess
import sys

import pytest

from dubcrate import card_format as cf
from dubcrate import cdj_card as cc
from dubcrate import cli_card

GB = 1000**3


@pytest.fixture
def on_a_mac(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")


def info(device="disk4", size=2013265920, **extra):
    """What `diskutil info -plist` says about a plain 2 GB card in a reader."""
    values = {
        "DeviceIdentifier": device,
        "MediaName": "Generic STORAGE DEVICE",
        "TotalSize": size,
        "BusProtocol": "USB",
        "WholeDisk": True,
        "RemovableMedia": True,
        "VirtualOrPhysical": "Physical",
        "OSInternalMedia": False,
        "WritableMedia": True,
    }
    values.update(extra)
    return plistlib.dumps(values)


def disk(**extra):
    return cf.parse_disk_info(info(**extra))


def finished(out="", code=0, err=""):
    if isinstance(out, bytes):
        out = out.decode("utf-8")
    return subprocess.CompletedProcess([], code, out, err)


def test_a_plain_two_gigabyte_card_may_be_formatted():
    card = disk()
    assert card.card_class == "SD"
    assert cf.problems(card) == []


@pytest.mark.parametrize(
    ("size", "card_class"),
    [
        (2 * 1024**3, "SD"),
        (2 * 1024**3 + 1, "SDHC"),
        (4 * GB, "SDHC"),
        (64 * GB, "SDXC"),
    ],
)
def test_a_cards_class_follows_from_its_size(size, card_class):
    assert disk(size=size).card_class == card_class


def test_an_sdhc_or_sdxc_card_is_refused_and_the_reason_says_why():
    (why,) = cf.problems(disk(size=32 * GB))
    assert "SDHC" in why and "plain SD" in why
    (why,) = cf.problems(disk(size=128 * GB))
    assert "SDXC" in why


@pytest.mark.parametrize(
    ("extra", "word"),
    [
        ({"RemovableMedia": False}, "not a removable card"),
        ({"OSInternalMedia": True, "RemovableMedia": True}, "not a removable card"),
        ({"WholeDisk": False}, "part of a disk"),
        ({"VirtualOrPhysical": "Virtual"}, "disk image"),
        ({"WritableMedia": False}, "write-protected"),
        ({"TotalSize": 0}, "no card"),
    ],
)
def test_what_is_never_erased(extra, word):
    assert any(word in line for line in cf.problems(disk(**extra)))


def test_a_disk_image_is_allowed_only_when_the_tests_ask():
    image = disk(VirtualOrPhysical="Virtual", BusProtocol="Disk Image")
    assert cf.problems(image)
    assert cf.problems(image, allow_images=True) == []


def test_a_built_in_card_slot_counts_as_removable():
    slot = disk(BusProtocol="Secure Digital", Internal=True)
    assert cf.problems(slot) == []


@pytest.mark.parametrize("text", ["disk4", "/dev/disk4", "/dev/rdisk4", " disk4 "])
def test_a_disk_is_named_in_any_usual_way(text):
    assert cf.device_name(text) == "disk4"


@pytest.mark.parametrize("text", ["disk4s1", "/dev/disk4s2"])
def test_a_partition_is_refused_with_the_whole_disks_name(text):
    with pytest.raises(cf.FormatError, match=r"whole card \(disk4\)"):
        cf.device_name(text)


@pytest.mark.parametrize("text", ["/", "disk", "; rm -rf ~", "/Volumes/DISK_IMG", ""])
def test_anything_else_is_not_a_disk_name(text):
    with pytest.raises(cf.FormatError, match="not a disk name"):
        cf.device_name(text)


def fake_diskutil(tmp_path, card=None, partition_name="MS-DOS FAT16", mounted=True):
    """A runner that answers like `diskutil` and records what it was told to do."""
    seen = []
    volume = tmp_path / "DISK_IMG"

    def run(command, **kw):
        seen.append(command)
        assert kw["check"] is False and kw["stdin"] is subprocess.DEVNULL
        match command[1:]:
            case ["info", "-plist", "/dev/disk4"]:
                return finished(card or info())
            case ["info", "-plist", "/dev/disk4s1"]:
                return finished(
                    plistlib.dumps(
                        {
                            "FilesystemName": partition_name,
                            "MountPoint": str(volume) if mounted else "",
                        }
                    )
                )
            case ["partitionDisk", *_]:
                volume.mkdir(exist_ok=True)
                return finished()
            case ["mount", *_]:
                return finished()
        raise AssertionError(command)

    return run, seen, volume


def test_a_card_is_formatted_mbr_fat16_named_and_given_its_folder(tmp_path, on_a_mac):
    run, seen, volume = fake_diskutil(tmp_path)
    done = cf.format_card("disk4", run)
    assert ["diskutil", "partitionDisk", "/dev/disk4", "MBR", "MS-DOS FAT16", "DISK_IMG", "100%"] in seen  # fmt: skip
    assert done.volume == volume
    assert done.folder == volume / "PIONEER" / "CDJ"
    assert done.folder.is_dir() and list(done.folder.iterdir()) == []
    assert not any(c[1] == "eject" for c in seen)  # left mounted for the sync


def test_a_refused_disk_is_checked_before_anything_is_erased(tmp_path, on_a_mac):
    run, seen, _ = fake_diskutil(tmp_path, card=info(size=64 * GB))
    with pytest.raises(cf.FormatError, match="SDXC"):
        cf.format_card("disk4", run)
    assert all(c[1] == "info" for c in seen)


def test_a_card_that_did_not_come_out_fat16_is_said_so(tmp_path, on_a_mac):
    run, _, _ = fake_diskutil(tmp_path, partition_name="MS-DOS FAT32")
    with pytest.raises(cf.FormatError, match="FAT32, not FAT16"):
        cf.format_card("disk4", run)


def test_an_unmounted_card_is_mounted_by_asking(tmp_path, on_a_mac):
    run, seen, _ = fake_diskutil(tmp_path, mounted=False)
    with pytest.raises(cf.FormatError, match="would not mount"):
        cf.format_card("disk4", run)
    assert ["diskutil", "mount", "/dev/disk4s1"] in seen


def test_diskutils_complaint_is_said_with_its_last_line(tmp_path, on_a_mac):
    def run(command, **kw):
        if command[1] == "partitionDisk":
            return finished(code=1, err="Error\nCouldn't unmount disk")
        return finished(info())

    with pytest.raises(cf.FormatError, match="partitionDisk failed: Couldn't unmount"):
        cf.format_card("disk4", run)


def test_it_is_for_a_mac_only(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(cf.FormatError, match="only done on a Mac"):
        cf.format_card("disk4", lambda *a, **kw: finished())


def test_only_removable_disks_are_listed(on_a_mac):
    listing = plistlib.dumps({"WholeDisks": ["disk0", "disk4"]}).decode()

    def run(command, **kw):
        match command[1:]:
            case ["list", "-plist"]:
                return finished(listing)
            case ["info", "-plist", "/dev/disk0"]:
                return finished(info("disk0", RemovableMedia=False))
            case ["info", "-plist", "/dev/disk4"]:
                return finished(info())
        raise AssertionError(command)

    assert [d.device for d in cf.list_disks(run)] == ["disk4"]


def test_a_card_made_this_way_reads_as_a_card_with_no_discs(tmp_path):
    assert cc.find_cards([tmp_path]) == []
    cc.make_card_folders(tmp_path)
    assert cc.find_cards([tmp_path]) == [tmp_path]
    index, files = cc.read_card(tmp_path)
    assert index.entries == [] and files == {}


def test_a_sync_can_write_to_the_empty_folder(tmp_path):
    cc.make_card_folders(tmp_path)
    index = cc.IndexFile()
    cc.write_card(tmp_path, index, {})
    assert (tmp_path / "PIONEER" / "CDJ" / cc.INDEX_NAME).is_file()
    assert cc.read_card(tmp_path)[0].entries == []


class Args:
    def __init__(self, disk="disk4", yes=False, confirm=None):
        self.disk, self.yes, self.confirm = disk, yes, confirm


def test_the_command_is_a_dry__run_without_yes(monkeypatch, capsys):
    monkeypatch.setattr(cf, "inspect", lambda device: disk())
    monkeypatch.setattr(cf, "format_card", lambda *a, **k: pytest.fail("erased"))
    assert cli_card.cmd_format(None, Args()) == 0
    out = capsys.readouterr().out
    assert "ERASE everything on disk4" in out and "dry run" in out


def test_the_command_refuses_an_sdhc_card_even_with_yes(monkeypatch, capsys):
    monkeypatch.setattr(cf, "inspect", lambda device: disk(size=32 * GB))
    monkeypatch.setattr(cf, "format_card", lambda *a, **k: pytest.fail("erased"))
    assert cli_card.cmd_format(None, Args(yes=True, confirm="disk4")) == 1
    assert "SDHC" in capsys.readouterr().err


def test_the_disk_must_be_typed_again_to_erase(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cf, "inspect", lambda device: disk())
    monkeypatch.setattr(cf, "format_card", lambda *a, **k: pytest.fail("erased"))
    monkeypatch.setattr("builtins.input", lambda prompt: "disk5")
    assert cli_card.cmd_format(None, Args(yes=True)) == 1
    assert "nothing was erased" in capsys.readouterr().err


def test_a_confirmed_command_formats_and_says_where_the_card_is(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.setattr(cf, "inspect", lambda device: disk())
    monkeypatch.setattr(
        cf,
        "format_card",
        lambda device: cf.Formatted(disk(), tmp_path, tmp_path / "PIONEER" / "CDJ"),
    )
    assert cli_card.cmd_format(None, Args(yes=True, confirm="disk4")) == 0
    out = capsys.readouterr().out
    assert f"--card {tmp_path}" in out and "Eject it" in out


def test_a_card_swapped_since_it_was_listed_is_not_erased(tmp_path, on_a_mac):
    listed = disk()
    run, seen, _ = fake_diskutil(
        tmp_path, card=info(size=1_000_000_000, MediaName="Another card")
    )
    with pytest.raises(cf.FormatError, match="not the card that was picked any more"):
        cf.format_card("disk4", run, expect=listed)
    assert all(c[1] == "info" for c in seen)  # nothing was erased
    run, seen, _ = fake_diskutil(tmp_path)
    assert cf.format_card(
        "disk4", run, expect=listed
    ).volume  # the same card goes ahead
