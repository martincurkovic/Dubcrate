"""The table of contents of the audio CD in the drive, from macOS's `drutil toc` (Qt-free).

A disc's ID on the CDJ's card is its TOC, so the TOC the disc really has is the safe source: a
disc is editable after it is burned and Dubcrate keeps no record of what was burned. This is the
one place that runs `drutil`; `read_drive_toc(run=...)` takes the runner so a test never touches a
drive. Only a Mac has it (Linux and Windows: not done, `DriveTocError` says so).

`drutil toc`'s output was seen for audio CDs (`tests/data/cdj_card/CD*_TOC.txt`). With no disc it
printed the drive's two header lines, then "No media, please insert a CD to read its TOC/ATIP."; **the
same drive, with a disc in it, answered "No media" once between two good reads** (2026-11-14), so
a read that finds no TOC is tried again a couple of times. A data disc and a blank one are **not
seen**: anything that doesn't read as an audio TOC is refused with `drutil`'s own first line of output.
"""

import subprocess
import sys
import time
from collections.abc import Callable

from dubcrate import cdj_card as cc

TIMEOUT_SECONDS = 60  # a drive spinning up can take a while
ATTEMPTS = 3  # a drive that says "No media" with a disc in it is asked again
RETRY_SECONDS = 2.0


class DriveTocError(RuntimeError):
    """The drive's TOC can't be read (an expected error: the message says why, in plain words)."""


def can_read_toc() -> bool:
    """Whether this system can read a disc's TOC from the drive (a Mac, so far)."""
    return sys.platform == "darwin"


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
    if sys.platform != "darwin":
        raise DriveTocError(
            "Reading a disc's TOC from the drive is only done on a Mac so far."
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
