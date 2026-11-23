"""Formatting a card on Linux (`lsblk`, `sfdisk`, `mkfs.fat` and `pkexec` are stood in for: no disk is touched)."""

import json
import subprocess

import pytest

from dubcrate import card_format as cf
from dubcrate import card_format_linux as lx

GB = 1000**3
PLAIN = 1_981_808_640  # a typical "2 GB" card


@pytest.fixture(autouse=True)
def on_linux(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")


def finished(out="", code=0, err=""):
    return subprocess.CompletedProcess([], code, out, err)


def block(name="sdb", size=PLAIN, **extra):
    values = {
        "name": name, "type": "disk", "size": size, "rm": True, "ro": False, "tran": "usb",
        "vendor": "Generic ", "model": "STORAGE DEVICE ", "fstype": None, "label": None,
        "mountpoints": [None],
    }  # fmt: skip
    values.update(extra)
    return values


def listing(*devices):
    return json.dumps({"blockdevices": list(devices)})


def sysfs_with(tmp_path, **types):
    for name, kind in types.items():
        (tmp_path / name / "device").mkdir(parents=True)
        (tmp_path / name / "device" / "type").write_text(kind + "\n", encoding="utf-8")
    return tmp_path


def test_a_usb_reader_with_a_plain_card_may_be_formatted(tmp_path):
    (disk,) = lx.parse_lsblk(listing(block()), tmp_path)
    assert (disk.device, disk.name, disk.protocol) == (
        "sdb",
        "Generic STORAGE DEVICE",
        "USB",
    )
    assert disk.removable and not disk.system and disk.writable
    assert cf.problems(disk) == []


def test_only_whole_disks_are_listed(tmp_path):
    data = listing(block("loop0", type="loop"), block("sr0", type="rom"), block())
    assert [d.device for d in lx.parse_lsblk(data, tmp_path)] == ["sdb"]


def test_a_disk_holding_the_system_is_never_a_card(tmp_path):
    root = block(
        "sda", rm=False, tran="sata", size=500 * GB,
        children=[{"name": "sda2", "type": "part", "mountpoints": ["/"]}],
    )  # fmt: skip
    (disk,) = lx.parse_lsblk(listing(root), tmp_path)
    assert disk.system and not disk.removable
    assert any("not a removable card" in why for why in cf.problems(disk))


def test_a_usb_disk_with_a_system_partition_is_refused_too(tmp_path):
    stick = block(
        children=[{"name": "sdb1", "type": "part", "mountpoints": ["/boot/efi"]}]
    )
    assert lx.parse_lsblk(listing(stick), tmp_path)[0].system


def test_a_music_card_mounted_under_media_is_not_a_system_disk(tmp_path):
    card = block(
        children=[{"name": "sdb1", "type": "part", "mountpoints": ["/run/media/me/X"]}]
    )
    assert not lx.parse_lsblk(listing(card), tmp_path)[0].system


def test_an_old_lsblk_says_its_flags_as_strings(tmp_path):
    old = block(rm="1", ro="0", size=str(PLAIN), mountpoint=None)
    (disk,) = lx.parse_lsblk(listing(old), tmp_path)
    assert disk.removable and disk.writable and disk.size == PLAIN


def test_a_built_in_card_slot_is_a_card_only_when_the_kernel_says_sd(tmp_path):
    sysfs = sysfs_with(tmp_path, mmcblk0="SD", mmcblk1="MMC")
    slot = block("mmcblk0", rm=False, tran=None, vendor=None, model=None)
    emmc = block("mmcblk1", rm=False, tran=None, vendor=None, model=None)
    sd, internal = lx.parse_lsblk(listing(slot, emmc), sysfs)
    assert sd.removable and sd.protocol == "Secure Digital"
    assert not internal.removable
    unknown = lx.parse_lsblk(listing(block("mmcblk2", rm=False, tran=None)), sysfs)[0]
    assert not unknown.removable  # no answer from the kernel: not touched


def test_a_big_card_or_a_locked_one_or_an_empty_reader_is_said(tmp_path):
    big = lx.parse_lsblk(listing(block(size=64 * GB)), tmp_path)[0]
    assert any("SDXC" in why for why in cf.problems(big))
    locked = lx.parse_lsblk(listing(block(ro=True)), tmp_path)[0]
    assert any("write-protected" in why for why in cf.problems(locked))
    empty = lx.parse_lsblk(listing(block(size=0)), tmp_path)[0]
    assert any("no card" in why for why in cf.problems(empty))


def test_garbage_from_lsblk_is_an_expected_error(tmp_path):
    with pytest.raises(cf.FormatError, match="couldn't be read"):
        lx.parse_lsblk("not json", tmp_path)


@pytest.mark.parametrize(
    "text", ["sdb", "/dev/sdb", " sdb ", "mmcblk0", "/dev/mmcblk0"]
)
def test_a_disk_is_named_in_any_usual_way(text):
    assert lx.device_name(text) == text.strip().removeprefix("/dev/")


@pytest.mark.parametrize(
    ("text", "whole"),
    [("sdb1", "sdb"), ("/dev/sdc2", "sdc"), ("mmcblk0p1", "mmcblk0")],
)
def test_a_partition_is_refused_with_the_whole_disks_name(text, whole):
    with pytest.raises(cf.FormatError, match=rf"whole card \({whole}\)"):
        lx.device_name(text)


@pytest.mark.parametrize(
    "text", ["/", "sd", "; rm -rf ~", "nvme0n1", "disk4", "/mnt/card", ""]
)
def test_anything_else_is_not_a_disk_name(text):
    with pytest.raises(cf.FormatError, match="not a disk name"):
        lx.device_name(text)


def test_the_first_partitions_name():
    assert lx.partition_name("sdb") == "sdb1"
    assert lx.partition_name("mmcblk0") == "mmcblk0p1"


def test_listing_asks_lsblk_and_keeps_the_removable_ones(tmp_path, monkeypatch):
    seen = []

    def run(command, **kw):
        seen.append(command)
        assert kw["check"] is False and kw["stdin"] is subprocess.DEVNULL
        return finished(
            listing(block("sda", rm=False, tran="sata", size=GB * 500), block())
        )

    assert [d.device for d in cf.list_disks(run)] == ["sdb"]
    assert seen[0][0] == "lsblk" and "--json" in seen[0] and "--bytes" in seen[0]


def test_an_lsblk_without_the_plural_column_is_asked_again():
    seen = []

    def run(command, **kw):
        seen.append(command[-1])
        if command[-1].endswith("MOUNTPOINTS"):
            return finished(code=1, err="lsblk: unknown column: MOUNTPOINTS")
        return finished(listing(block()))

    assert [d.device for d in cf.list_disks(run)] == ["sdb"]
    assert seen[0].endswith("MOUNTPOINTS") and seen[1].endswith("MOUNTPOINT")


def test_inspect_answers_for_a_disk_that_is_not_removable_too():
    run = lambda command, **kw: finished(listing(block("sda", rm=False, tran="sata")))
    assert cf.inspect("/dev/sda", run).removable is False
    with pytest.raises(cf.FormatError, match="not a disk this computer has"):
        cf.inspect("sdz", run)


class FakeSystem:
    """lsblk, pkexec's script and udisksctl, as a runner: it records the commands."""

    def __init__(
        self, card=None, after=None, script_code=0, script_err="", udisks=None
    ):
        self.card = card or listing(block())
        self.after = after or json.dumps(
            {"blockdevices": [{"name": "sdb1", "type": "part", "fstype": "vfat", "label": "DISK_IMG", "mountpoints": ["/run/media/me/DISK_IMG"]}]}
        )  # fmt: skip
        self.script_code, self.script_err = script_code, script_err
        self.udisks = udisks
        self.seen = []
        self.formatted = False

    def __call__(self, command, **kw):
        self.seen.append(command)
        if command[0] == "lsblk":
            return finished(self.after if command[-1] == "/dev/sdb1" else self.card)
        if "sh" in command[:2]:
            self.formatted = True
            return finished(code=self.script_code, err=self.script_err)
        if command[0] == "udisksctl":
            return self.udisks or finished(
                "Mounted /dev/sdb1 at /run/media/me/DISK_IMG\n"
            )
        raise AssertionError(command)

    def script(self):
        return next(c for c in self.seen if "sh" in c[:2])


@pytest.fixture
def as_root(monkeypatch):
    monkeypatch.setattr("os.geteuid", lambda: 0, raising=False)


def test_a_card_is_formatted_by_one_script_with_the_disk_passed_in_not_pasted(
    as_root, tmp_path, monkeypatch
):
    mount = tmp_path / "DISK_IMG"
    mount.mkdir()
    system = FakeSystem(
        after=json.dumps(
            {
                "blockdevices": [
                    {"name": "sdb1", "fstype": "vfat", "mountpoints": [str(mount)]}
                ]
            }
        )
    )
    done = cf.format_card("sdb", system)
    script = system.script()
    assert script[:3] == ["sh", "-c", lx.FORMAT_SCRIPT]
    assert script[4:] == ["/dev/sdb", "/dev/sdb1", "DISK_IMG"]
    assert done.volume == mount and done.folder == mount / "PIONEER" / "CDJ"
    assert done.folder.is_dir() and list(done.folder.iterdir()) == []
    assert not any(c[0] == "udisksctl" for c in system.seen)  # it was mounted already


def test_the_script_matches_the_players_own_layout():
    text = lx.FORMAT_SCRIPT
    assert (
        "start=32, type=6" in text and "label: dos" in text
    )  # MBR, type 0x06 from sector 32
    assert "mkfs.fat -F 16 -a -R 8 -n" in text  # FAT16, 8 reserved sectors, named
    assert text.index("umount") < text.index("sfdisk") < text.index("mkfs.fat")


def test_the_old_boot_sector_is_zeroed_before_partitioning_and_the_partition_unmounted():
    # else the desktop mounts the new partition at once (the old FAT is still there) and mkfs says busy
    text = lx.FORMAT_SCRIPT
    assert text.index("dd if=/dev/zero") < text.index("sfdisk")
    assert text.rindex("umount") > text.index("sfdisk")  # the new partition too
    assert text.rindex("umount") < text.index("mkfs.fat")
    assert text.rindex("udevadm settle") > text.index("mkfs.fat")


def test_a_refused_disk_is_checked_before_anything_is_erased(as_root):
    system = FakeSystem(card=listing(block(size=64 * GB)))
    with pytest.raises(cf.FormatError, match="SDXC"):
        cf.format_card("sdb", system)
    assert not system.formatted and all(c[0] == "lsblk" for c in system.seen)


def test_a_system_disk_is_refused_even_if_removable(as_root):
    stick = block(children=[{"name": "sdb1", "type": "part", "mountpoints": ["/home"]}])
    system = FakeSystem(card=listing(stick))
    with pytest.raises(cf.FormatError, match="not a removable card"):
        cf.format_card("sdb", system)
    assert not system.formatted


def test_a_card_swapped_since_it_was_listed_is_not_erased(as_root):
    listed = lx.parse_lsblk(listing(block()))[0]
    system = FakeSystem(card=listing(block(model="ANOTHER ", size=1_000_000_000)))
    with pytest.raises(cf.FormatError, match="not the card that was picked any more"):
        cf.format_card("sdb", system, expect=listed)
    assert not system.formatted


def test_a_failed_script_says_its_last_line(as_root):
    system = FakeSystem(
        script_code=1, script_err="wipefs: error\nsfdisk: cannot open /dev/sdb"
    )
    with pytest.raises(cf.FormatError, match="Formatting failed: sfdisk: cannot open"):
        cf.format_card("sdb", system)


def test_a_card_that_did_not_come_out_fat_is_said_so(as_root):
    system = FakeSystem(
        after=json.dumps(
            {"blockdevices": [{"name": "sdb1", "fstype": "ext4", "mountpoints": []}]}
        )
    )
    with pytest.raises(cf.FormatError, match="ext4, not FAT16"):
        cf.format_card("sdb", system)


def test_an_unmounted_card_is_mounted_through_udisks(as_root, tmp_path):
    mount = tmp_path / "DISK_IMG"
    mount.mkdir()
    system = FakeSystem(
        after=json.dumps(
            {
                "blockdevices": [
                    {"name": "sdb1", "fstype": "vfat", "mountpoints": [None]}
                ]
            }
        ),
        udisks=finished(f"Mounted /dev/sdb1 at {mount}\n"),
    )
    assert cf.format_card("sdb", system).volume == mount
    assert [
        "udisksctl",
        "mount",
        "--no-user-interaction",
        "-b",
        "/dev/sdb1",
    ] in system.seen


def test_a_card_the_desktop_mounted_meanwhile_is_found_through_udisks_too(
    as_root, tmp_path
):
    mount = tmp_path / "DISK_IMG"
    mount.mkdir()
    system = FakeSystem(
        after=json.dumps(
            {
                "blockdevices": [
                    {"name": "sdb1", "fstype": "vfat", "mountpoints": [None]}
                ]
            }
        ),
        udisks=finished(
            err=f"Error mounting /dev/sdb1: Device /dev/sdb1 is already mounted at `{mount}'.\n",
            code=1,
        ),
    )
    assert cf.format_card("sdb", system).volume == mount


def test_a_card_that_will_not_mount_is_said(as_root):
    system = FakeSystem(
        after=json.dumps(
            {"blockdevices": [{"name": "sdb1", "fstype": "vfat", "mountpoints": []}]}
        ),
        udisks=finished(err="Error: no permission", code=1),
    )
    with pytest.raises(cf.FormatError, match="would not mount"):
        cf.format_card("sdb", system)


def test_a_desktop_session_asks_for_the_password_with_pkexec(monkeypatch, tmp_path):
    monkeypatch.setattr("os.geteuid", lambda: 1000, raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    mount = tmp_path / "DISK_IMG"
    mount.mkdir()
    system = FakeSystem(
        after=json.dumps(
            {
                "blockdevices": [
                    {"name": "sdb1", "fstype": "vfat", "mountpoints": [str(mount)]}
                ]
            }
        )
    )
    cf.format_card("sdb", system)
    assert system.script()[0] == "pkexec"


def test_a_dismissed_password_window_is_said_and_nothing_was_erased(monkeypatch):
    monkeypatch.setattr("os.geteuid", lambda: 1000, raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    system = FakeSystem(script_code=126)
    with pytest.raises(
        cf.FormatError, match="password wasn't given, so nothing was erased"
    ):
        cf.format_card("sdb", system)


def test_without_a_desktop_or_root_it_says_to_run_it_as_root(monkeypatch):
    monkeypatch.setattr("os.geteuid", lambda: 1000, raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    system = FakeSystem()
    with pytest.raises(cf.FormatError, match="run this as root"):
        cf.format_card("sdb", system)
    assert not system.formatted


def test_a_missing_tool_is_an_expected_error():
    def run(command, **kw):
        raise FileNotFoundError(2, "No such file", command[0])

    with pytest.raises(cf.FormatError, match="Couldn't run lsblk"):
        cf.list_disks(run)


def test_the_eject_hint_is_for_the_system():
    assert "udisksctl" in cf.eject_hint()


def test_a_file_system_udev_has_not_reported_yet_is_waited_for(
    as_root, tmp_path, monkeypatch
):
    mount = tmp_path / "DISK_IMG"
    mount.mkdir()
    slept = []
    monkeypatch.setattr(lx, "_sleep", slept.append)
    blank = json.dumps(
        {"blockdevices": [{"name": "sdb1", "fstype": None, "mountpoints": [None]}]}
    )
    ready = json.dumps(
        {
            "blockdevices": [
                {"name": "sdb1", "fstype": "vfat", "mountpoints": [str(mount)]}
            ]
        }
    )
    system = FakeSystem(after=blank)
    answers = iter([blank, blank, ready])
    original = system.__call__

    def call(command, **kw):
        if command[0] == "lsblk" and command[-1] == "/dev/sdb1":
            return finished(next(answers))
        return original(command, **kw)

    done = cf.format_card("sdb", call)
    assert done.volume == mount and len(slept) == 2


def test_a_card_that_never_shows_a_file_system_is_said_unformatted(
    as_root, monkeypatch
):
    monkeypatch.setattr(lx, "_sleep", lambda _s: None)
    system = FakeSystem(
        after=json.dumps(
            {"blockdevices": [{"name": "sdb1", "fstype": None, "mountpoints": []}]}
        )
    )
    with pytest.raises(cf.FormatError, match="unformatted, not FAT16"):
        cf.format_card("sdb", system)
