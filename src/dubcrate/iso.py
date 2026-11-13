"""Make an ISO 9660 + Joliet image of an MP3 disc's folder (Qt-free), by running a tool.

An MP3 CD is a data disc; where Dubcrate can't write one itself (everything but macOS), an image
is what any burning program takes. The tools, in order: `xorriso` (`xorriso -as mkisofs`),
`genisoimage` and `mkisofs` (the same options), and on a Mac `hdiutil`, which is always there.
Joliet is what lets a CD player see long file names; Rock Ridge (`-r`) is added for Linux readers.
"""

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from dubcrate.tools import find_tool


class IsoError(RuntimeError):
    """An image could not be made; the message is for the user."""


@dataclass(frozen=True)
class IsoTool:
    name: str
    path: Path


# (the tools that take mkisofs's options)
_MKISOFS = ("xorriso", "genisoimage", "mkisofs")


def find_iso_tool() -> IsoTool | None:
    """The first tool that can make the image, or None."""
    for name in _MKISOFS:
        tool = find_tool(name)
        if tool:
            return IsoTool(name, tool.path)
    if sys.platform == "darwin":
        tool = find_tool("hdiutil")
        if tool:
            return IsoTool("hdiutil", tool.path)
    return None


def missing_message() -> str:
    """What to tell someone who has no tool to make an ISO."""
    if sys.platform == "win32":
        return "Making an ISO image needs xorriso, which Windows doesn't come with: it isn't supported yet."
    return (
        "Dubcrate makes an ISO image with xorriso (or genisoimage). Install it with your package manager,"
        " for example “sudo apt install xorriso”, then try again."
    )


def build_iso(folder: Path, volume: str, out: Path) -> Path:
    """Write `out` (an .iso file) holding the contents of `folder`, with volume name `volume`.

    `folder` and what is in it are only read. Raises `IsoError`.
    """
    tool = find_iso_tool()
    if tool is None:
        raise IsoError(missing_message())
    if not folder.is_dir():
        raise IsoError(f"“{folder}” is not a folder.")
    out.unlink(missing_ok=True)
    if tool.name == "hdiutil":
        cmd = [str(tool.path), "makehybrid", "-iso", "-joliet", "-default-volume-name", volume, "-o", str(out), str(folder)]  # fmt: skip
    elif tool.name == "xorriso":
        cmd = [str(tool.path), "-as", "mkisofs", "-quiet", "-J", "-r", "-V", volume, "-o", str(out), str(folder)]  # fmt: skip
    else:
        cmd = [str(tool.path), "-quiet", "-J", "-r", "-V", volume, "-o", str(out), str(folder)]  # fmt: skip
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True, stdin=subprocess.DEVNULL)  # fmt: skip
    if (
        not out.exists()
        and tool.name == "hdiutil"
        and out.with_suffix(".iso.iso").exists()
    ):
        out.with_suffix(".iso.iso").rename(
            out
        )  # (hdiutil adds its own extension to a name that has none)
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise IsoError(f"{tool.name} could not make the image: {detail[-1] if detail else 'it wrote nothing'}")  # fmt: skip
    return out
