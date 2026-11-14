"""Eject a card or a disc, for the flows that finish with one (Qt-free).

macOS only so far (`diskutil eject` for a card, `drutil eject` for the disc in the burner); each
raises `EjectError`, an expected error, on any other system or when the system says no. The runner is
injectable so a test never touches a drive or a card. **Not tried on Linux or Windows.**
"""

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

TIMEOUT_SECONDS = 60  # ejecting a card flushes it first


class EjectError(RuntimeError):
    """The system couldn't eject (an expected error: the message says why, in plain words)."""


def _run(
    command: list[str],
    run: Callable[..., subprocess.CompletedProcess[str]],
    what: str,
) -> None:
    if sys.platform != "darwin":
        raise EjectError(f"Ejecting {what} is only done on a Mac so far.")
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


def eject_card(
    volume: Path,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    """Eject a mounted card (its volume's folder, such as /Volumes/DISK_IMG): macOS writes out
    anything still waiting first, so it is safe to take out when this returns."""
    _run(["diskutil", "eject", str(volume)], run, f"“{volume.name or volume}”")


def eject_disc(
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    """Open the burner's tray."""
    _run(["drutil", "eject"], run, "the disc")
