"""Format a blank SD card for the CDJ-1000MK3, so cues can be synced before it is ever in a player (Qt-free).

The player formats a card itself and leaves it empty: MBR, one FAT16 partition, 32 KB clusters,
volume `DISK_IMG` (read off `00-blank.img`, `dev_docs/cdj1000mk3-card.md`). This makes the same on
a Mac with `diskutil`, then the empty `PIONEER/CDJ` folder, and leaves the card mounted so
`card sync` can write to it at once. **Destructive**, so what may be erased is narrow (`problems`):
one whole removable disk, not a disk image, writable, **and a plain SD card: 2 GiB or less**. The
player reads SD only, not SDHC or SDXC; a card's class follows from its size (SD is at most 2 GiB,
SDHC above that to 32 GB, SDXC above that), which is all a Mac reports through a card reader.

What differs from the player's own format: `diskutil` puts the partition 1 MiB in (sector 2048,
the player's is at 32) and keeps 1 reserved sector (the player's 8). The player is untested on a
card made this way, and **has not been shown the pre-made `PIONEER/CDJ` folder either**; the
two-experiment checklist is in `dev_docs/card-sync.md`.

Each system has its own way, one module apiece, chosen in `_backend` (the one place that tests the
platform): macOS `diskutil` (here), Linux `lsblk` + `sfdisk` + `mkfs.fat` (`card_format_linux`, which
can match the player's layout exactly: partition at sector 32, 8 reserved sectors) and Windows
PowerShell's storage cmdlets (`card_format_windows`). **The Linux and Windows ones are tried
only against stand-in commands, never a real disk or card.** The runner is injectable, so a test
never touches a disk.
"""

import os
import plistlib
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from dubcrate import cdj_card as cc

LABEL = "DISK_IMG"  # the volume name the player gives its own cards
MAX_BYTES = 2 * 1024**3  # plain SD ends at 2 GiB; SDHC is above it
SDHC_MAX_BYTES = 32 * 1000**3  # SDHC is "up to 32 GB", SDXC above (marketing gigabytes)
TIMEOUT_SECONDS = 300  # a format of a slow card

Runner = Callable[..., subprocess.CompletedProcess[str]]


class FormatError(RuntimeError):
    """A card can't be formatted (an expected error: the message says why, in plain words)."""


@dataclass(frozen=True)
class CardDisk:
    """A whole disk as `diskutil info` describes it."""

    device: str  # "disk4"
    name: str  # the media's name, such as "Generic STORAGE DEVICE"
    size: int  # bytes
    protocol: str  # "USB", "Secure Digital", ...
    whole: bool
    removable: bool
    virtual: bool  # a mounted disk image
    system: bool  # holds the running system
    writable: bool

    @property
    def node(self) -> str:
        return f"/dev/{self.device}"

    @property
    def card_class(self) -> str:
        """`SD`, `SDHC` or `SDXC`, from the size alone."""
        if self.size <= MAX_BYTES:
            return "SD"
        return "SDHC" if self.size <= SDHC_MAX_BYTES else "SDXC"


def size_text(size: int) -> str:
    return f"{size / 1000**3:.1f} GB" if size >= 10**9 else f"{size / 1000**2:.0f} MB"


def describe(disk: CardDisk) -> str:
    return (
        f"{disk.device}: {disk.name or 'unnamed disk'}, {size_text(disk.size)}, "
        f"{disk.protocol or 'unknown bus'}"
    )


def parse_disk_info(data: bytes) -> CardDisk:
    """A `diskutil info -plist` answer as a `CardDisk`."""
    try:
        info = plistlib.loads(data)
    except (plistlib.InvalidFileException, ValueError, TypeError) as exc:
        raise FormatError("The system's disk information couldn't be read.") from exc
    if not isinstance(info, dict) or "DeviceIdentifier" not in info:
        raise FormatError("The system's disk information couldn't be read.")
    return CardDisk(
        device=str(info["DeviceIdentifier"]),
        name=str(info.get("MediaName") or ""),
        size=int(info.get("TotalSize") or info.get("Size") or 0),
        protocol=str(info.get("BusProtocol") or ""),
        whole=bool(info.get("WholeDisk")),
        removable=bool(info.get("RemovableMedia")),
        virtual=info.get("VirtualOrPhysical") == "Virtual"
        or info.get("BusProtocol") == "Disk Image",
        system=bool(info.get("OSInternalMedia") or info.get("SystemImage")),
        writable=bool(info.get("WritableMedia", True)),
    )


def problems(disk: CardDisk, *, allow_images: bool = False) -> list[str]:
    """Why this disk may not be formatted as a CDJ card, in plain words; empty when it may.

    `allow_images` is for the tests of the real `diskutil` on a disk image, never the commands.
    """
    out = []
    if not disk.whole:
        out.append(f"{disk.device} is a part of a disk: give the whole card.")
    if disk.system or not disk.removable:
        out.append(
            f"{disk.device} is not a removable card: it is a disk the computer runs from or keeps "
            "its files on, and is never erased here."
        )
    if disk.virtual and not allow_images:
        out.append(f"{disk.device} is a disk image, not a card.")
    if not disk.writable:
        out.append(
            f"{disk.device} is write-protected: slide the card's lock switch to unlocked."
        )
    if disk.size > MAX_BYTES:
        out.append(
            f"{disk.device} is {size_text(disk.size)}, so it is an {disk.card_class} card: "
            f"the CDJ-1000MK3 reads only plain SD cards, which are 2 GB or less."
        )
    if disk.size <= 0:
        out.append(f"{disk.device} has no card in it.")
    return out


def _diskutil(run: Runner, *args: str) -> subprocess.CompletedProcess[str]:
    if sys.platform != "darwin":
        raise FormatError("Formatting a card is only done on a Mac so far.")
    try:
        proc = run(
            ["diskutil", *args],
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise FormatError("The system didn't finish formatting the card.") from exc
    except OSError as exc:
        raise FormatError(f"Couldn't run diskutil: {exc}") from exc
    if proc.returncode != 0:
        said = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise FormatError(
            f"diskutil {args[0]} failed" + (f": {said[-1]}" if said else ".")
        )
    return proc


def _plist(run: Runner, *args: str) -> bytes:
    proc = _diskutil(run, *args)
    return proc.stdout.encode("utf-8")


def device_name(text: str) -> str:
    """`disk4`, from `disk4`, `/dev/disk4` or `/dev/rdisk4`; a partition (`disk4s1`) is refused."""
    name = text.strip().removeprefix("/dev/").removeprefix("r")
    if re.fullmatch(r"disk\d+", name):
        return name
    if re.fullmatch(r"disk\d+s\d+", name):
        raise FormatError(
            f"{name} is a part of a disk: give the whole card ({name.rsplit('s', 1)[0]})."
        )
    raise FormatError(f"{text!r} is not a disk name such as disk4.")


def _backend():
    """The module that formats on this system, or None for macOS (this module's own `diskutil` code).

    Raises `FormatError` on a system with no way yet."""
    if sys.platform == "darwin":
        return None
    if sys.platform.startswith("linux"):
        from dubcrate import card_format_linux

        return card_format_linux
    if sys.platform == "win32":
        from dubcrate import card_format_windows

        return card_format_windows
    raise FormatError("Formatting a card isn't done on this kind of computer yet.")


def eject_hint() -> str:
    """How to eject a card on this system, in a few words for a message."""
    if sys.platform == "darwin":
        return "Finder, or `diskutil eject`"
    if sys.platform == "win32":
        return "the taskbar's Safely Remove Hardware"
    return "your file manager, or `udisksctl power-off`"


def inspect(device: str, run: Runner = subprocess.run) -> CardDisk:
    """Ask the system about a disk (`disk4`, `/dev/disk4`; `sdb`; `disk3` on Windows)."""
    if (backend := _backend()) is not None:
        return backend.inspect(device, run)
    name = device_name(device)
    return parse_disk_info(_plist(run, "info", "-plist", f"/dev/{name}"))


def list_disks(run: Runner = subprocess.run) -> list[CardDisk]:
    """The removable whole disks, a card slot's or a reader's, for the user to choose from."""
    if (backend := _backend()) is not None:
        return backend.list_disks(run)
    listing = plistlib.loads(_plist(run, "list", "-plist"))
    found = []
    for name in listing.get("WholeDisks", []):
        try:
            disk = inspect(name, run)
        except FormatError:
            continue  # a disk that went away while we looked
        if disk.removable and not disk.system:
            found.append(disk)
    return found


@dataclass(frozen=True)
class Formatted:
    disk: CardDisk
    volume: Path  # where the card is mounted now, ready for `card sync`
    folder: Path  # its empty PIONEER/CDJ


def _format_mac(disk: CardDisk, run: Runner) -> Path:
    """`diskutil`'s way: partition and format, and mount if it did not by itself. Returns the mount point."""
    _diskutil(run, "partitionDisk", disk.node, "MBR", "MS-DOS FAT16", LABEL, "100%")
    partition = _plist(run, "info", "-plist", f"{disk.node}s1")
    info = plistlib.loads(partition)
    if info.get("FilesystemName") != "MS-DOS FAT16":
        raise FormatError(
            f"The card came out as {info.get('FilesystemName') or 'unformatted'}, "
            "not FAT16: it was not made ready."
        )
    mount = str(info.get("MountPoint") or "")
    if not mount:
        _diskutil(run, "mount", f"{disk.node}s1")
        mount = str(
            plistlib.loads(_plist(run, "info", "-plist", f"{disk.node}s1")).get(
                "MountPoint"
            )
            or ""
        )
    if not mount:
        raise FormatError("The card was formatted but would not mount.")
    return Path(mount)


def format_card(
    device: str,
    run: Runner = subprocess.run,
    *,
    allow_images: bool = False,
    expect: CardDisk | None = None,
) -> Formatted:
    """Erase a card, make it MBR + FAT16 named `DISK_IMG`, and make `PIONEER/CDJ` on it.

    Checks the disk again just before (a disk number can change hands between a list and this),
    refusing anything `problems` names, and, with `expect` (the disk as it was listed), a disk that is no
    longer that one (another card in the reader). The card is **left mounted**, so a sync can follow; it
    must still be ejected through the system before it is pulled. Raises `FormatError`.
    """
    disk = inspect(device, run)
    if expect is not None and (disk.name, disk.size) != (expect.name, expect.size):
        raise FormatError(
            f"{disk.device} is not the card that was picked any more (it is now "
            f"{describe(disk)}): nothing was erased. Look at the list again."
        )
    if found := problems(disk, allow_images=allow_images):
        raise FormatError(found[0])
    backend = _backend()
    volume = (
        _format_mac(disk, run) if backend is None else backend.format_disk(disk, run)
    )
    try:
        folder = cc.make_card_folders(volume)
    except OSError as exc:
        raise FormatError(
            f"The card was formatted but its folders couldn't be made: {exc}"
        ) from exc
    for sidecar in (
        volume / f"._{cc.CARD_DIR[0]}",
        folder.parent / f"._{cc.CARD_DIR[1]}",
    ):
        sidecar.unlink(
            missing_ok=True
        )  # (macOS's copy of the folders' attributes: only clutter)
    if hasattr(os, "sync"):
        os.sync()
    return Formatted(disk, volume, folder)
