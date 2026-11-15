"""Formatting a card on Windows (PowerShell is stood in for: no disk is touched)."""

import json
import re
import subprocess

import pytest

from dubcrate import card_format as cf
from dubcrate import card_format_windows as win

GB = 1000**3
PLAIN = 1_981_808_640


@pytest.fixture(autouse=True)
def on_windows(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")


def finished(out="", code=0, err=""):
    return subprocess.CompletedProcess([], code, out, err)


def row(number=3, size=PLAIN, **extra):
    values = {
        "Number": number, "FriendlyName": "Generic STORAGE DEVICE", "Size": size, "BusType": "USB",
        "IsSystem": False, "IsBoot": False, "IsReadOnly": False,
    }  # fmt: skip
    values.update(extra)
    return values


def test_a_plain_card_in_a_usb_reader_may_be_formatted():
    (disk,) = win.parse_get_disk(json.dumps(row()))  # one disk is an object, not a list
    assert (disk.device, disk.name, disk.protocol) == (
        "disk3",
        "Generic STORAGE DEVICE",
        "USB",
    )
    assert disk.removable and not disk.system and disk.writable
    assert cf.problems(disk) == []


def test_several_disks_come_as_a_list_and_a_bom_is_ignored():
    data = "\ufeff" + json.dumps(
        [row(0, 500 * GB, BusType="NVMe", IsSystem=True, IsBoot=True), row()]
    )
    system, card = win.parse_get_disk(data)
    assert system.system and not system.removable
    assert card.removable


def test_no_disks_is_an_empty_list_and_garbage_is_an_expected_error():
    assert win.parse_get_disk("  \n") == []
    with pytest.raises(cf.FormatError, match="couldn't be read"):
        win.parse_get_disk("not json")
    with pytest.raises(cf.FormatError, match="couldn't be read"):
        win.parse_get_disk(json.dumps([{"Size": 5}]))


@pytest.mark.parametrize("bus", ["SD", "MMC", "usb"])
def test_card_buses_are_removable(bus):
    assert win.parse_get_disk(json.dumps(row(BusType=bus)))[0].removable


@pytest.mark.parametrize("bus", ["SATA", "NVMe", "RAID"])
def test_internal_buses_are_not(bus):
    (disk,) = win.parse_get_disk(json.dumps(row(BusType=bus)))
    assert not disk.removable
    assert any("not a removable card" in why for why in cf.problems(disk))


def test_a_mounted_disk_image_is_not_a_card():
    (disk,) = win.parse_get_disk(json.dumps(row(BusType="File Backed Virtual")))
    assert disk.virtual and not disk.removable


def test_big_locked_and_empty_are_said():
    assert any(
        "SDXC" in w
        for w in cf.problems(win.parse_get_disk(json.dumps(row(size=64 * GB)))[0])
    )
    assert any(
        "write-protected" in w
        for w in cf.problems(win.parse_get_disk(json.dumps(row(IsReadOnly=True)))[0])
    )
    assert any(
        "no card" in w
        for w in cf.problems(win.parse_get_disk(json.dumps(row(size=0)))[0])
    )


@pytest.mark.parametrize(
    "text", ["disk3", "Disk3", "3", " 3 ", "\\\\.\\PhysicalDrive3", "PhysicalDrive3"]
)
def test_a_disk_is_named_in_any_usual_way(text):
    assert win.device_name(text) == "disk3"


@pytest.mark.parametrize(
    "text", ["C:", "C:\\", "disk", "; Clear-Disk", "disk3s1", "-1", ""]
)
def test_anything_else_is_not_a_disk_name(text):
    with pytest.raises(cf.FormatError, match="not a disk name"):
        win.device_name(text)


def test_listing_asks_get_disk_and_keeps_the_removable_ones():
    seen = []

    def run(command, **kw):
        seen.append(command)
        assert command[0] == "powershell" and kw["check"] is False
        return finished(
            json.dumps([row(0, GB * 500, BusType="NVMe", IsBoot=True), row()])
        )

    assert [d.device for d in cf.list_disks(run)] == ["disk3"]
    assert "Get-Disk" in seen[0][-1]


def test_inspect_answers_for_a_disk_that_is_not_removable_too():
    run = lambda command, **kw: finished(json.dumps([row(0, BusType="SATA")]))
    assert cf.inspect("disk0", run).removable is False
    with pytest.raises(cf.FormatError, match="not a disk this computer has"):
        cf.inspect("disk9", run)


class FakePowerShell:
    """`Get-Disk`, and the elevated format script: it reads the command, writes the result file."""

    def __init__(self, disks=None, result=None, code=0):
        self.disks = disks if disks is not None else [row()]
        self.result = {"ok": True, "letter": "e"} if result is None else result
        self.code = code
        self.seen = []
        self.script_text = None

    def __call__(self, command, **kw):
        text = command[-1]
        self.seen.append(text)
        if text.startswith("Get-Disk"):
            return finished(json.dumps(self.disks))
        assert text.startswith("Start-Process") and "-Verb RunAs" in text
        if self.code:
            return finished(
                code=self.code, err="The operation was canceled by the user."
            )
        out = re.search(r"'-Out','\"(.+?)\"'", text).group(1)
        script = re.search(r"'-File','\"(.+?)\"'", text).group(1)
        with open(script, encoding="utf-8-sig") as f:
            self.script_text = f.read()
        with open(out, "w", encoding="utf-8-sig") as f:
            json.dump(self.result, f)
        return finished()


def test_a_card_is_formatted_by_an_elevated_script_with_its_checks_in_it(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(win, "drive_root", lambda letter: tmp_path / letter.upper())
    (tmp_path / "E").mkdir()
    system = FakePowerShell()
    done = cf.format_card("disk3", system)
    assert (
        done.volume == tmp_path / "E"
        and done.folder == tmp_path / "E" / "PIONEER" / "CDJ"
    )
    assert done.folder.is_dir()
    command = system.seen[-1]
    assert "'-Number','3'" in command and f"'-Size','{PLAIN}'" in command
    assert f"'-Max','{cf.MAX_BYTES}'" in command and "'\"DISK_IMG\"'" in command


def test_the_script_formats_mbr_fat16_and_checks_the_disk_itself():
    text = win.FORMAT_SCRIPT
    assert "-PartitionStyle MBR" in text and "-MbrType FAT16" in text
    assert "-FileSystem FAT " in text and "-AllocationUnitSize 32768" in text
    for guard in (
        "IsSystem",
        "IsBoot",
        "$disk.Size -ne $Size",
        "-gt $Max",
        "IsReadOnly",
    ):
        assert guard in text
    assert (
        text.index("throw") < text.index("Clear-Disk") < text.index("Initialize-Disk")
    )


def test_the_script_file_is_the_one_run(tmp_path, monkeypatch):
    monkeypatch.setattr(win, "drive_root", lambda letter: tmp_path)
    system = FakePowerShell()
    disk = win.inspect("disk3", system)
    win.format_disk(disk, system)
    assert system.script_text == win.FORMAT_SCRIPT


def test_a_refused_disk_is_checked_before_anything_is_erased():
    system = FakePowerShell(disks=[row(size=64 * GB)])
    with pytest.raises(cf.FormatError, match="SDXC"):
        cf.format_card("disk3", system)
    assert all(text.startswith("Get-Disk") for text in system.seen)


def test_a_card_swapped_since_it_was_listed_is_not_erased():
    listed = win.parse_get_disk(json.dumps(row()))[0]
    system = FakePowerShell(
        disks=[row(size=1_000_000_000, FriendlyName="Another card")]
    )
    with pytest.raises(cf.FormatError, match="not the card that was picked any more"):
        cf.format_card("disk3", system, expect=listed)
    assert all(text.startswith("Get-Disk") for text in system.seen)


def test_a_declined_permission_prompt_is_said_and_nothing_was_erased():
    with pytest.raises(
        cf.FormatError, match="permission wasn't given, so nothing was erased"
    ):
        cf.format_card("disk3", FakePowerShell(code=1))


def test_the_scripts_own_complaint_is_said():
    system = FakePowerShell(result={"ok": False, "error": "Access denied"})
    with pytest.raises(cf.FormatError, match="Formatting failed: Access denied"):
        cf.format_card("disk3", system)


def test_no_drive_letter_is_said():
    with pytest.raises(cf.FormatError, match="no drive letter"):
        cf.format_card("disk3", FakePowerShell(result={"ok": True, "letter": ""}))


def test_paths_with_spaces_and_quotes_are_quoted_for_start_process(tmp_path):
    script = tmp_path / "O'Brien and sons" / "format.ps1"
    command = win.elevated_command(
        script, win.parse_get_disk(json.dumps(row()))[0], tmp_path / "r.json"
    )
    assert "'\"" + str(script).replace("'", "''") + "\"'" in command


def test_a_missing_powershell_is_an_expected_error():
    def run(command, **kw):
        raise FileNotFoundError(2, "No such file", command[0])

    with pytest.raises(cf.FormatError, match="Couldn't run PowerShell"):
        cf.list_disks(run)


def test_the_eject_hint_is_for_the_system():
    assert "Safely Remove" in cf.eject_hint()
