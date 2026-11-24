"""The table of contents of the audio CD in the drive (Qt-free).

A disc's ID on the CDJ's card is its TOC, so the TOC the disc really has is the safe source: a
disc is editable after it is burned and Dubcrate keeps no record of what was burned. This is the
one place that reads a drive; `read_drive_toc(run=...)` takes the runner so a test never touches a
drive. The platform is chosen in `_read_once`, and every way ends in the same entries
(`Entry`: track, control, minute, second, frame; the lead-out is track 0xAA) checked by `toc_from_entries`:

- **macOS**: `drutil toc`'s text (`parse_drive_toc`).
- **Linux**: the kernel's own `CDROMREADTOCHDR` / `CDROMREADTOCENTRY` ioctls on `/dev/cdrom` or `/dev/sr0`
  (`linux_entries`): no tool to install, but the user needs read access to the drive (the `cdrom` group).
- **Windows**: `IOCTL_CDROM_READ_TOC` on the first optical drive through `ctypes` (`windows_entries`; its
  buffer is read by `parse_windows_toc`).

**Only macOS has been tried on a drive**; the Linux and Windows readers are tested against made-up answers.

`drutil toc`'s output was seen for audio CDs (`tests/data/cdj_card/CD*_TOC.txt`). With no disc it
printed the drive's two header lines, then "No media, please insert a CD to read its TOC/ATIP."; **the
same drive, with a disc in it, answered "No media" once between two good reads** (2026-11-14), so
a read that finds no TOC is tried again a couple of times. A data disc and a blank one are **not
seen**: anything that doesn't read as an audio TOC is refused with `drutil`'s own first line of output.
"""

import errno
import os
import struct
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NamedTuple

from dubcrate import cdj_card as cc

TIMEOUT_SECONDS = 60  # a drive spinning up can take a while
ATTEMPTS = 3  # a drive that says "No media" with a disc in it is asked again
RETRY_SECONDS = 2.0


class DriveTocError(RuntimeError):
    """The drive's TOC can't be read (an expected error: the message says why, in plain words)."""


def data_disc_device(exists: Callable[[str], bool] = os.path.exists) -> Path:
    """The drive's device, for reading a data disc's own files (the MP3 disc's image). Only Linux can say: the other
    systems mount a data disc, and the raw device needs a name and rights there that aren't worked out."""
    if sys.platform.startswith("linux"):
        path = next((d for d in LINUX_DEVICES if exists(d)), None)
        if path is not None:
            return Path(path)
        raise DriveTocError("No optical drive was found.")
    raise DriveTocError(
        "Reading the disc in the drive isn't built for this system yet: choose its image file instead."
    )


def can_read_toc() -> bool:
    """Whether this system can read a disc's TOC from the drive."""
    return (
        sys.platform == "darwin"
        or sys.platform == "win32"
        or sys.platform.startswith("linux")
    )


def read_drive_toc(
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = ATTEMPTS,
    wait: float = RETRY_SECONDS,
) -> tuple[list[int], int]:
    """Track starts and the lead-out, in absolute frames, of the audio CD in the drive.

    A read that finds no readable disc is tried again (`attempts` in all, `wait` seconds apart),
    since a drive that has just read a disc can answer "No media" once, and a disc just burned
    takes a while to be readable. A disc that isn't a plain audio CD is refused at once."""
    if not can_read_toc():
        raise DriveTocError(
            "Reading a disc's TOC from the drive isn't done on this kind of computer yet."
        )
    for attempt in range(1, attempts + 1):
        try:
            return _read_once(run)
        except _NoDisc as exc:
            if attempt == attempts:
                raise DriveTocError(str(exc)) from None
            sleep(wait)
    raise AssertionError("unreachable")  # pragma: no cover


class _NoDisc(DriveTocError):
    """A read that found no readable disc (the only failure that is tried again)."""


def _read_once(
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> tuple[list[int], int]:
    if sys.platform.startswith("linux"):
        return toc_from_entries(linux_entries())
    if sys.platform == "win32":
        return toc_from_entries(windows_entries())
    try:
        proc = run(
            ["drutil", "toc"],
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise DriveTocError(
            f"The drive didn't answer within {TIMEOUT_SECONDS} seconds."
        ) from exc
    except OSError as exc:
        raise DriveTocError(f"Couldn't run drutil: {exc}") from exc
    return parse_drive_toc(proc.stdout or "", proc.stderr or "")


def parse_drive_toc(stdout: str, stderr: str = "") -> tuple[list[int], int]:
    """`toc_from_drutil` for an audio disc, else a `DriveTocError` quoting what `drutil` said."""
    tracks = [line for line in stdout.splitlines() if "Track" in line and ":" in line]
    try:
        starts, lead_out = cc.toc_from_drutil(stdout)
    except cc.CardFormatError:
        raise _NoDisc(
            f"There's no readable disc in the drive ({_said(stdout, stderr)})."
        ) from None
    if any("audio" not in line for line in tracks):
        raise DriveTocError(
            "The disc in the drive isn't a plain audio CD, and only those have an ID on the card."
        )
    return starts, lead_out


def _said(stdout: str, stderr: str) -> str:
    """The first line `drutil` printed that says something about the disc, for the message.

    With no disc in the drive it prints only the drive's own two lines (seen: a header, then the
    drive's vendor, product and revision), which say nothing about the disc and are skipped."""
    lines = [
        line.strip()
        for line in (*stderr.splitlines(), *stdout.splitlines())
        if line.strip() and line.strip() != "drutil toc"
    ]
    if lines and lines[0].startswith("Vendor"):
        lines = lines[2:]  # the header and the drive's own line
    if lines:
        return lines[0]
    return "the drive reported no disc" if stdout.strip() else "drutil printed nothing"


# -- the entries of a TOC, as the kernel's and Windows' drivers give them -----------------

LEAD_OUT = 0xAA  # the track number both systems give the lead-out
DATA_CONTROL = 0x04  # in a track's control nibble: it is a data track, not audio


class Entry(NamedTuple):
    """One line of a TOC: a track (or the lead-out, number 0xAA) and where it starts on the disc."""

    number: int
    control: int  # the 4-bit control field; bit 2 set: data
    minute: int
    second: int
    frame: int


def toc_from_entries(entries: Sequence[Entry]) -> tuple[list[int], int]:
    """Track starts and the lead-out in absolute frames, from a drive's entries, as `read_drive_toc`
    gives them. A disc with a data track (an enhanced or mixed one) is refused: only a plain audio CD
    has an ID on the card."""
    tracks = sorted(
        (e for e in entries if e.number != LEAD_OUT), key=lambda e: e.number
    )
    lead_out = [e for e in entries if e.number == LEAD_OUT]
    if not tracks or len(lead_out) != 1:
        raise _NoDisc(
            "There's no readable disc in the drive (it gave no table of contents)."
        )
    if any(e.control & DATA_CONTROL for e in tracks):
        raise DriveTocError(
            "The disc in the drive isn't a plain audio CD, and only those have an ID on the card."
        )
    return (
        [cc.frames(e.minute, e.second, e.frame) for e in tracks],
        cc.frames(lead_out[0].minute, lead_out[0].second, lead_out[0].frame),
    )


# Linux: <linux/cdrom.h>
CDROMREADTOCHDR = 0x5305
CDROMREADTOCENTRY = 0x5306
CDROM_MSF = 0x02
_TOC_ENTRY = "BBBx4sB3x"  # struct cdrom_tocentry: track, adr|ctrl<<4, format, address (4), datamode
LINUX_DEVICES = ("/dev/cdrom", "/dev/sr0", "/dev/sr1")
ENOMEDIUM = getattr(
    errno, "ENOMEDIUM", 123
)  # "no medium found" (Linux's number; macOS has no name for it)


def linux_entries(
    device: str | None = None,
    *,
    os_open: Callable[..., int] = os.open,
    ioctl: Callable[..., object] | None = None,
    exists: Callable[[str], bool] = os.path.exists,
) -> list[Entry]:
    """The TOC from the kernel's CD-ROM driver (no tool needed). `device` defaults to the first of
    `/dev/cdrom`, `/dev/sr0`, `/dev/sr1` that exists. Raises `DriveTocError` (an empty drive is the
    retried `_NoDisc`)."""
    path = device or next((d for d in LINUX_DEVICES if exists(d)), None)
    if path is None:
        raise DriveTocError(
            "No disc drive was found (looked for /dev/cdrom and /dev/sr0)."
        )
    try:
        fd = os_open(
            path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
        )  # (non-blocking: an empty drive must not hang)
    except OSError as exc:
        raise _linux_error(path, exc) from exc
    try:
        header = bytearray(2)
        ioctl(fd, CDROMREADTOCHDR, header)
        first, last = header
        entries = []
        for track in (*range(first, last + 1), LEAD_OUT):
            buffer = bytearray(struct.pack(_TOC_ENTRY, track, 0, CDROM_MSF, b"", 0))
            ioctl(fd, CDROMREADTOCENTRY, buffer)
            number, adr_control, _format, address, _mode = struct.unpack(
                _TOC_ENTRY, bytes(buffer)
            )
            entries.append(
                Entry(number, adr_control >> 4, address[0], address[1], address[2])
            )
        return entries
    except OSError as exc:
        raise _linux_error(path, exc) from exc
    finally:
        try:
            os.close(fd)
        except OSError:  # pragma: no cover
            pass


CDROM_DRIVE_STATUS = 0x5326
CDSL_CURRENT = 0x7FFFFFFF  # "the slot the drive is on" (a changer has more than one)
_CDS_NO_DISC, _CDS_TRAY_OPEN, _CDS_NOT_READY, _CDS_DISC_OK = 1, 2, 3, 4

# What `disc_state` says: a cheap look that never spins the drive up or reads the disc.
DISC = "disc"  # a disc is in and ready
EMPTY = "empty"  # nothing in the drive, or the tray is open
LOADING = "loading"  # a disc is going in or spinning up: look again in a moment


def disc_state(
    device: str | None = None,
    *,
    os_open: Callable[..., int] = os.open,
    ioctl: Callable[..., object] | None = None,
    exists: Callable[[str], bool] = os.path.exists,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str | None:
    """Whether a disc is in the drive: `DISC`, `EMPTY`, `LOADING`, or None when this system can't say (or
    there is no drive to ask). The burn window asks every couple of seconds to notice a disc being put in
    or taken out without a click on Check Again, so it must be cheap and must not wake the drive: Linux
    asks the kernel (`CDROM_DRIVE_STATUS`, non-blocking), macOS runs `drutil status`, Windows says None.
    One helper per system; only Linux has been run on a real drive (macOS and Windows: NOT tried)."""
    if sys.platform.startswith("linux"):
        return _linux_disc_state(device, os_open, ioctl, exists)
    if sys.platform == "darwin":
        return _mac_disc_state(run)
    return None


def _linux_disc_state(device, os_open, ioctl, exists) -> str | None:
    path = device if device and device.startswith("/dev/") else None
    path = path or next((d for d in LINUX_DEVICES if exists(d)), None)
    if path is None:
        return None
    try:
        fd = os_open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None  # (not allowed, or gone: the full check says why)
    try:
        if ioctl is None:
            import fcntl  # (Linux only: imported when there is a drive to ask)

            ioctl = fcntl.ioctl
        status = ioctl(fd, CDROM_DRIVE_STATUS, CDSL_CURRENT)
    except OSError:
        return None
    finally:
        try:
            os.close(fd)
        except OSError:  # pragma: no cover
            pass
    if status == _CDS_DISC_OK:
        return DISC
    if status in (_CDS_NO_DISC, _CDS_TRAY_OPEN):
        return EMPTY
    if status == _CDS_NOT_READY:
        return LOADING
    return None


def _mac_disc_state(run) -> str | None:
    try:
        proc = run(
            ["drutil", "status"],
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (proc.stdout or "") + (proc.stderr or "")
    if "No Media Inserted" in text:
        return EMPTY
    if "Type:" in text:
        return DISC
    return None


def _linux_error(path: str, exc: OSError) -> DriveTocError:
    if exc.errno in (ENOMEDIUM, errno.ENXIO, errno.EIO):
        return _NoDisc(
            f"There's no readable disc in the drive ({path}: {exc.strerror or 'no medium'})."
        )
    if exc.errno in (errno.EACCES, errno.EPERM):
        return DriveTocError(
            f"Dubcrate isn't allowed to read {path}: add your user to the drive's group "
            "(`cdrom` or `optical`), then log in again."
        )
    return DriveTocError(f"Couldn't read the disc in {path}: {exc.strerror or exc}.")


# Windows: IOCTL_CDROM_READ_TOC
IOCTL_CDROM_READ_TOC = 0x00024000
TOC_BUFFER_BYTES = (
    4 + 100 * 8
)  # CDROM_TOC: length, first, last, then up to 100 8-byte track entries


def parse_windows_toc(data: bytes) -> list[Entry]:
    """The entries in a `CDROM_TOC` buffer: a big-endian length, the first and last track, then 8
    bytes a track (reserved, adr|control<<4, track, reserved, then 0, minute, second, frame)."""
    if len(data) < 4:
        raise _NoDisc(
            "There's no readable disc in the drive (the drive gave no table of contents)."
        )
    (length,) = struct.unpack(">H", data[:2])
    count = max((length - 2) // 8, 0)
    entries = []
    for i in range(min(count, (len(data) - 4) // 8)):
        _r, adr_control, number, _r2, _zero, minute, second, frame = struct.unpack_from(
            "8B", data, 4 + 8 * i
        )
        entries.append(Entry(number, adr_control >> 4, minute, second, frame))
    return entries


def windows_entries(
    drive: str | None = None,
) -> list[Entry]:  # pragma: no cover (needs Windows)
    """The TOC of the first optical drive (or `drive`, such as `E:`), from the driver, through
    `ctypes`. **Not tried on a real drive.**"""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]  # fmt: skip
    kernel32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]  # fmt: skip
    if drive is None:
        mask = kernel32.GetLogicalDrives()
        optical = [
            f"{chr(65 + i)}:"
            for i in range(26)
            if mask & (1 << i)
            and kernel32.GetDriveTypeW(f"{chr(65 + i)}:\\") == 5  # DRIVE_CDROM
        ]
        if not optical:
            raise DriveTocError("No disc drive was found.")
        drive = optical[0]
    handle = kernel32.CreateFileW(
        f"\\\\.\\{drive}", 0x80000000, 3, None, 3, 0, None
    )  # read; share read|write; existing
    if handle in (None, ctypes.c_void_p(-1).value):
        raise DriveTocError(
            f"Couldn't open the disc drive {drive} ({ctypes.WinError(ctypes.get_last_error())})."
        )
    try:
        buffer = ctypes.create_string_buffer(TOC_BUFFER_BYTES)
        returned = wintypes.DWORD()
        if not kernel32.DeviceIoControl(
            handle,
            IOCTL_CDROM_READ_TOC,
            None,
            0,
            buffer,
            TOC_BUFFER_BYTES,
            ctypes.byref(returned),
            None,
        ):
            code = ctypes.get_last_error()
            if code in (21, 1112):  # ERROR_NOT_READY, ERROR_NO_MEDIA_IN_DRIVE
                raise _NoDisc(f"There's no readable disc in the drive ({drive}).")
            raise DriveTocError(
                f"Couldn't read the disc in {drive} ({ctypes.WinError(code)})."
            )
        return parse_windows_toc(buffer.raw[: returned.value])
    finally:
        kernel32.CloseHandle(handle)
