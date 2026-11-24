"""Eject a card or a disc, for the flows that finish with one (Qt-free).

`eject_card(volume)` makes a mounted card safe to take out and `eject_disc()` opens the burner's
tray; the system is chosen in ONE place each (`_card_commands` / `_disc_commands`). Each raises
`EjectError`, an expected error, when the system says no. The runner is injectable so a test never
touches a drive or a card.

- **macOS**: `diskutil eject VOLUME` (writes out anything waiting first), `drutil eject`.
- **Linux**: the card's device is found with `findmnt` / `lsblk`, then `udisksctl unmount -b PARTITION`:
  **the unmount is the flush that makes it safe**, so it is the step that must work. `udisksctl
  power-off -b DISK` follows but is best effort (a built-in card slot refuses it, and the card is
  safe to take out already). The disc: `eject`.
- **Windows**: PowerShell's Shell "Eject" verb on the drive, then a wait for the drive to disappear (the verb
  gives no answer of its own); the disc: the same verb on the first optical drive.

**Only macOS has been tried on a real card or drive**; the Linux and Windows ways are tested only against
stand-in commands.
"""

import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path, PureWindowsPath

TIMEOUT_SECONDS = 60  # ejecting a card flushes it first

Runner = Callable[..., subprocess.CompletedProcess[str]]


class EjectError(RuntimeError):
    """The system couldn't eject (an expected error: the message says why, in plain words)."""


def _run(
    command: list[str], run: Runner, what: str
) -> subprocess.CompletedProcess[str]:
    """Run one command; a missing tool, a hang or a non-zero answer is an `EjectError`."""
    try:
        proc = run(
            command,
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise EjectError(f"The system didn't finish ejecting {what}.") from exc
    except OSError as exc:
        raise EjectError(f"Couldn't run {command[0]}: {exc}") from exc
    if proc.returncode != 0:
        said = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise EjectError(
            f"The system couldn't eject {what}" + (f": {said[-1]}" if said else ".")
        )
    return proc


def _unsupported() -> EjectError:
    return EjectError("Ejecting isn't done on this kind of computer yet.")


# -- the card --------------------------------------------------------------------------


def eject_card(volume: Path, run: Runner = subprocess.run) -> None:
    """Eject a mounted card (its volume's folder, such as /Volumes/DISK_IMG): once this returns it
    is safe to take out."""
    what = f"“{volume.name or volume}”"
    if sys.platform == "darwin":
        _run(["diskutil", "eject", str(volume)], run, what)
    elif sys.platform.startswith("linux"):
        _eject_card_linux(volume, run, what)
    elif sys.platform == "win32":
        _eject_drive_windows(volume, run, what)
    else:
        raise _unsupported()


def _eject_card_linux(volume: Path, run: Runner, what: str) -> None:
    source = _run(
        ["findmnt", "--noheadings", "--output", "SOURCE", "--target", str(volume)],
        run,
        what,
    ).stdout.strip()
    partition = source.splitlines()[0].strip() if source else ""
    if not re.fullmatch(r"/dev/[A-Za-z0-9_/-]+", partition):
        raise EjectError(f"Couldn't tell which device {what} is.")
    _run(["udisksctl", "unmount", "--no-user-interaction", "-b", partition], run, what)
    # The card is written out and unmounted, so it is safe to pull now. Powering the reader down is
    # only tidiness, and a built-in slot or a hub may say no: that is not a failure.
    try:
        parent = _run(
            ["lsblk", "--noheadings", "--output", "PKNAME", partition], run, what
        ).stdout.strip()
        if re.fullmatch(r"[A-Za-z0-9_-]+", parent):
            _run(
                [
                    "udisksctl",
                    "power-off",
                    "--no-user-interaction",
                    "-b",
                    f"/dev/{parent}",
                ],
                run,
                what,
            )
    except EjectError:
        pass


# Windows: the Shell's own Eject verb (what "Safely Remove" does), then wait until the drive is gone.
_WINDOWS_EJECT = """\
$ErrorActionPreference = 'Stop'
$drive = '{drive}'
try {{
    $shell = New-Object -ComObject Shell.Application
    $shell.Namespace(17).ParseName($drive).InvokeVerb('Eject')
}} catch {{
    [Console]::Error.WriteLine('Windows would not eject the drive: ' + $_.Exception.Message)
    exit 1
}}
for ($i = 0; $i -lt 20; $i++) {{
    # (while the card is going away Windows can answer "access denied" instead of "no": that error is
    # not a failure, it is the drive leaving; it stopped an eject that had worked, seen 2026-11-24)
    $present = $false
    try {{ $present = Test-Path -LiteralPath ($drive + '\\') }} catch {{ $present = $false }}
    if (-not $present) {{ exit 0 }}
    Start-Sleep -Milliseconds 500
}}
[Console]::Error.WriteLine('The card is still in use: close whatever has it open and try again.')
exit 1
"""


def _eject_drive_windows(volume: Path, run: Runner, what: str) -> None:
    drive = PureWindowsPath(str(volume)).drive
    if not re.fullmatch(r"[A-Za-z]:", drive):
        raise EjectError(
            f"{what} is not a drive with a letter, so it can't be ejected."
        )
    script = _WINDOWS_EJECT.format(drive=drive.upper())
    _run(_powershell(script), run, what)


def _powershell(script: str) -> list[str]:
    return [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]


# -- the disc --------------------------------------------------------------------------

_WINDOWS_EJECT_DISC = """\
$ErrorActionPreference = 'Stop'
$drive = (Get-CimInstance Win32_CDROMDrive | Select-Object -First 1).Drive
if (-not $drive) {
    Write-Error 'No disc drive was found.'
    exit 1
}
$shell = New-Object -ComObject Shell.Application
$shell.Namespace(17).ParseName($drive).InvokeVerb('Eject')
"""


def eject_disc(run: Runner = subprocess.run) -> None:
    """Open the burner's tray."""
    if sys.platform == "darwin":
        _run(["drutil", "eject"], run, "the disc")
    elif sys.platform.startswith("linux"):
        _run(["eject"], run, "the disc")
    elif sys.platform == "win32":
        _run(_powershell(_WINDOWS_EJECT_DISC), run, "the disc")
    else:
        raise _unsupported()
