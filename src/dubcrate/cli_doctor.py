"""`dubcrate doctor`: what Dubcrate can find on this machine (tools, drives, the disc in the drive).

The first thing to run when a burn or a card command misbehaves, and the first step of the hardware
checks in `dev_docs/burning.md`. Prints only; nothing is written and nothing is burned.
"""

import argparse
import subprocess
import sys
from collections.abc import Callable

from dubcrate import tools
from dubcrate.burn import cdrdao as cdrdao_backend

Say = Callable[[str], None]

OPTIONAL = (
    "cdrdao",
    "drutil",
)  # not needed everywhere: cdrdao burns off a Mac, drutil is a Mac's


def tool_lines() -> list[str]:
    """One line per tool: where it is and which version, or that it is not found."""
    lines = [tools.describe(name) for name in tools.REQUIRED]
    for name in OPTIONAL:
        found = tools.find_tool(name)
        lines.append(f"{name}: {found.path}" if found else f"{name}: not found")
    return lines


def drive_lines() -> list[str]:
    """The drives DiscRecording sees on a Mac (what it says each can write), and `drutil status`."""
    from dubcrate.burn import discrecording as dr  # (PyObjC: a Mac only)

    out = ["DiscRecording drives:"]
    devices = dr.list_devices()
    if not devices:
        out.append("  none found: is the burner plugged in and switched on?")
    for device in devices:
        for key, value in dr.describe_device(device).items():
            out.append(f"  {key:18s} {value}")
    drutil = tools.find_tool("drutil")
    if drutil is not None:
        status = subprocess.run(
            [str(drutil.path), "status"],
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        ).stdout
        out.append("drutil status:")
        out += ["  " + line for line in status.strip().splitlines()]
    return out


def cdrdao_lines() -> list[str]:
    if not cdrdao_backend.find_cdrdao():
        return []
    out = ["cdrdao scanbus:"]
    out += [f"  {dev} : {desc}" for dev, desc in cdrdao_backend.scan_devices()]
    return out


def cmd_doctor(_lib, _args: argparse.Namespace, say: Say = print) -> int:
    say("tools:")
    for line in tool_lines():
        say(f"  {line}")
    lines = (drive_lines() if sys.platform == "darwin" else []) + cdrdao_lines()
    for line in lines:
        say(line)
    missing = tools.missing_tools()
    if missing:
        say(f"\nmissing: {', '.join(missing)}. {tools.missing_message(missing[0])}")
        return 1
    return 0


def add_parsers(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "doctor", help="what Dubcrate finds here: tools, drives, the disc in the drive"
    )
    p.set_defaults(fn=cmd_doctor)
