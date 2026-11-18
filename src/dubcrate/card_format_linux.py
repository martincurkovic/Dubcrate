"""Format a blank SD card for the CDJ-1000MK3 on Linux (Qt-free; `card_format` chooses it).

`lsblk` lists and describes the disks, `sfdisk` makes the partition table and `mkfs.fat` the file
system, in ONE elevated shell script so the password is asked once (`pkexec` in a desktop session,
or run Dubcrate as root). Unlike `diskutil`, this can match the player's own format exactly, as read
off `00-blank.img`: an MBR, one type-0x06 partition from sector 32, FAT16 with 8 reserved sectors (`-a`: without it `mkfs.fat` aligns the data area and
raises that to 64),
volume `DISK_IMG` (the cluster size is left to `mkfs.fat`, which gives 32 KB on a card of 1 to 2 GB,
as the player does). The card is then mounted through `udisksctl` (as the user, so the files are
the user's), unless the desktop mounted it already.

What may be erased is `card_format.problems`, the same as on a Mac, with a few Linux facts that
feed it: a disk with any partition mounted at a system place (`/`, `/boot`, `/home`, swap, ...) is
a system disk and never erased; a built-in card slot (`mmcblk`) is a card only when sysfs says its
type is `SD` (an `MMC` one is the computer's own eMMC).

**Not tried on a real disk or card**: written from `lsblk`'s documented JSON and the tools' manuals,
and tested only against stand-in commands. `dev_docs/linux-test-plan.md` has the checklist.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from dubcrate.card_format import (
    LABEL,
    TIMEOUT_SECONDS,
    CardDisk,
    FormatError,
    Runner,
)

SYSFS_BLOCK = Path("/sys/block")
SYSTEM_MOUNTS = frozenset(
    {
        "/",
        "/boot",
        "/boot/efi",
        "/boot/firmware",
        "/efi",
        "/usr",
        "/var",
        "/home",
        "/etc",
        "[SWAP]",
    }
)  # a disk with a partition mounted at one of these is the computer's own

_COLUMNS = "NAME,TYPE,SIZE,RM,RO,TRAN,VENDOR,MODEL,FSTYPE,LABEL"

# Run as root with the disk node, its first partition and the volume name as $1 $2 $3 (never pasted in).
FORMAT_SCRIPT = """\
set -e
dev="$1"
part="$2"
label="$3"
for node in $(lsblk -nrpo NAME "$dev"); do
    umount "$node" 2>/dev/null || true
done
wipefs -a "$dev"
printf 'label: dos\\nstart=32, type=6\\n' | sfdisk --quiet "$dev"
if command -v udevadm >/dev/null 2>&1; then udevadm settle || true; fi
tries=0
while [ ! -b "$part" ] && [ "$tries" -lt 20 ]; do
    sleep 0.5
    tries=$((tries + 1))
done
if [ ! -b "$part" ]; then
    echo "the new partition $part did not appear" >&2
    exit 1
fi
wipefs -a "$part"
mkfs.fat -F 16 -a -R 8 -n "$label" "$part"
sync
"""


def device_name(text: str) -> str:
    """`sdb`, from `sdb` or `/dev/sdb`; a partition (`sdb1`, `mmcblk0p1`) is refused."""
    name = text.strip().removeprefix("/dev/")
    if re.fullmatch(r"sd[a-z]+|mmcblk\d+", name):
        return name
    if match := re.fullmatch(r"(sd[a-z]+)\d+|(mmcblk\d+)p\d+", name):
        whole = match.group(1) or match.group(2)
        raise FormatError(f"{name} is a part of a disk: give the whole card ({whole}).")
    raise FormatError(f"{text!r} is not a disk name such as sdb or mmcblk0.")


def partition_name(device: str) -> str:
    """The first partition's name: `sdb1`, `mmcblk0p1`."""
    return f"{device}p1" if device[-1].isdigit() else f"{device}1"


def _flag(value: object) -> bool:
    """lsblk says booleans as `true`/`false` in new versions and "1"/"0" in old ones."""
    return value in (True, 1, "1", "true")


def sd_type(device: str, sysfs: Path = SYSFS_BLOCK) -> str:
    """What the kernel says a built-in card slot holds: `SD` for a card, `MMC` for the computer's own."""
    try:
        return (sysfs / device / "device" / "type").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _mountpoints(node: dict) -> list[str]:
    found = []
    for key in ("mountpoints", "mountpoint"):
        value = node.get(key)
        if isinstance(value, str):
            value = [value]
        found += [m for m in value or [] if m]
    for child in node.get("children") or []:
        found += _mountpoints(child)
    return found


def _holds_system(mounts: list[str]) -> bool:
    return any(m in SYSTEM_MOUNTS or m.startswith("/boot/") for m in mounts)


def parse_lsblk(data: str, sysfs: Path = SYSFS_BLOCK) -> list[CardDisk]:
    """`lsblk --json` as the whole disks it lists (loop devices, partitions and the like are not disks)."""
    try:
        listing = json.loads(data)
        nodes = listing["blockdevices"]
    except (ValueError, KeyError, TypeError) as exc:
        raise FormatError("The system's disk list couldn't be read.") from exc
    disks = []
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "disk":
            continue
        name = str(node.get("name") or "")
        tran = str(node.get("tran") or "")
        builtin_card = name.startswith("mmcblk") and sd_type(name, sysfs) == "SD"
        protocol = "Secure Digital" if builtin_card else tran.upper()
        make = " ".join(
            part.strip() for part in (node.get("vendor"), node.get("model")) if part
        )
        disks.append(
            CardDisk(
                device=name,
                name=make,
                size=int(node.get("size") or 0),
                protocol=protocol,
                whole=True,
                removable=_flag(node.get("rm")) or tran == "usb" or builtin_card,
                virtual=False,
                system=_holds_system(_mountpoints(node)),
                writable=not _flag(node.get("ro")),
                mounts=tuple(_mountpoints(node)),
            )
        )
    return disks


def _run(
    run: Runner, command: list[str], what: str
) -> subprocess.CompletedProcess[str]:
    try:
        return run(
            command,
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise FormatError(f"The system didn't finish {what}.") from exc
    except OSError as exc:
        raise FormatError(f"Couldn't run {command[0]}: {exc}") from exc


def _lsblk(run: Runner, *args: str) -> str:
    """lsblk's JSON; the mount point column is `MOUNTPOINTS` in new versions, `MOUNTPOINT` in old ones."""
    for column in ("MOUNTPOINTS", "MOUNTPOINT"):
        proc = _run(
            run,
            ["lsblk", "--json", "--bytes", "--output", f"{_COLUMNS},{column}", *args],
            "listing the disks",
        )
        if proc.returncode == 0:
            return proc.stdout
    said = (proc.stderr or "").strip().splitlines()
    raise FormatError("lsblk failed" + (f": {said[-1]}" if said else "."))


def list_disks(
    run: Runner = subprocess.run, sysfs: Path = SYSFS_BLOCK
) -> list[CardDisk]:
    """The removable whole disks (a USB reader, a card slot) for the user to choose from."""
    return [d for d in parse_lsblk(_lsblk(run), sysfs) if d.removable and not d.system]


def inspect(
    device: str, run: Runner = subprocess.run, sysfs: Path = SYSFS_BLOCK
) -> CardDisk:
    """Ask the system about one disk (`sdb`, `/dev/sdb`), whether or not it could be formatted."""
    name = device_name(device)
    for disk in parse_lsblk(_lsblk(run), sysfs):
        if disk.device == name:
            return disk
    raise FormatError(f"{name} is not a disk this computer has.")


def elevation() -> list[str]:
    """What goes before a command to run it as root: nothing as root, `pkexec` in a desktop session."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return []
    desktop = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    if desktop and shutil.which("pkexec"):
        return ["pkexec"]
    raise FormatError(
        "Formatting a card needs administrator rights, and there is no password window to ask "
        "with: run this as root (`sudo dubcrate card format ...`)."
    )


def _mount_point(run: Runner, part: str) -> tuple[str, str]:
    """(file system, where it is mounted or "") of the partition, as lsblk sees it."""
    listing = json.loads(_lsblk(run, f"/dev/{part}"))
    node = listing["blockdevices"][0]
    mounts = _mountpoints(node)
    return str(node.get("fstype") or ""), mounts[0] if mounts else ""


def format_disk(disk: CardDisk, run: Runner = subprocess.run) -> Path:
    """Erase and format the disk, mount it, and give the mount point. Raises `FormatError`."""
    part = partition_name(disk.device)
    command = [
        *elevation(),
        "sh",
        "-c",
        FORMAT_SCRIPT,
        "dubcrate-format",
        f"/dev/{disk.device}",
        f"/dev/{part}",
        LABEL,
    ]
    proc = _run(run, command, "formatting the card")
    if proc.returncode in (126, 127) and command[0] == "pkexec":
        raise FormatError(
            "The administrator password wasn't given, so nothing was erased."
        )
    if proc.returncode != 0:
        said = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise FormatError("Formatting failed" + (f": {said[-1]}" if said else "."))
    try:
        fstype, mount = _mount_point(run, part)
    except (FormatError, ValueError, KeyError, IndexError) as exc:
        raise FormatError(
            "The card was formatted but the system's answer about it couldn't be read."
        ) from exc
    if fstype != "vfat":
        raise FormatError(
            f"The card came out as {fstype or 'unformatted'}, not FAT16: it was not made ready."
        )
    if not mount:
        mount = _udisks_mount(run, part)
    return Path(mount)


def _udisks_mount(run: Runner, part: str) -> str:
    proc = _run(
        run,
        ["udisksctl", "mount", "--no-user-interaction", "-b", f"/dev/{part}"],
        "mounting the card",
    )
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    # "Mounted /dev/sdb1 at /run/media/me/DISK_IMG" (or "... is already mounted at `...'.")
    match = re.search(
        r"(?:Mounted .+? at|already mounted at) [`']?(.+?)'?\.?\s*$", text, re.MULTILINE
    )
    if match and (proc.returncode == 0 or "already mounted" in text):
        return match.group(1)
    raise FormatError(
        f"The card was formatted but would not mount: mount /dev/{part} in your file manager."
    )
