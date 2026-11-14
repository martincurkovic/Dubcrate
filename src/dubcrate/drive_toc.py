"""The table of contents of the audio CD in the drive, from macOS's `drutil toc` (Qt-free).

A disc's ID on the CDJ's card is its TOC, so the TOC the disc really has is the safe source: a
disc is editable after it is burned and Dubcrate keeps no record of what was burned. This is the
one place that runs `drutil`; `read_drive_toc(run=...)` takes the runner so a test never touches a
drive. Only a Mac has it (Linux and Windows: not done, `DriveTocError` says so).

`drutil toc`'s output was seen for audio CDs only (`tests/data/cdj_card/CD*_TOC.txt`). What it
prints for a data disc, a blank one or no disc is **not seen**: anything that doesn't read as an
audio TOC is refused with `drutil`'s own first line of output, so the owner can see what it said.
"""

import subprocess
import sys
from collections.abc import Callable

from dubcrate import cdj_card as cc

TIMEOUT_SECONDS = 60  # a drive spinning up can take a while


class DriveTocError(RuntimeError):
    """The drive's TOC can't be read (an expected error: the message says why, in plain words)."""


def read_drive_toc(
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[list[int], int]:
    """Track starts and the lead-out, in absolute frames, of the audio CD in the drive."""
    if sys.platform != "darwin":
        raise DriveTocError(
            "Reading a disc's TOC from the drive is only done on a Mac so far."
        )
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
        raise DriveTocError(
            f"There's no readable disc in the drive ({_said(stdout, stderr)})."
        ) from None
    if any("audio" not in line for line in tracks):
        raise DriveTocError(
            "The disc in the drive isn't a plain audio CD, and only those have an ID on the card."
        )
    return starts, lead_out


def _said(stdout: str, stderr: str) -> str:
    """The first line `drutil` printed that says anything, for the message."""
    for line in (*stderr.splitlines(), *stdout.splitlines()):
        if line.strip() and line.strip() != "drutil toc":
            return line.strip()
    return "drutil printed nothing"
