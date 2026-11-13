"""Finding the command-line tools Dubcrate runs: `ffmpeg` and `ffprobe` (Qt-free).

Everything that runs one asks `find_tool`, so the start-up check, the log, Settings, the smoke
test and the code that actually runs the tool can't disagree about which copy is used.

The release bundles both, in `BUNDLE_DIR` (the build script puts them there: `BUILDING.md`).
That folder is looked in first, then `PATH`, so a run from source, or from a build that has no
bundle, still uses Homebrew's. They are only ever run as subprocesses, never linked.
"""

import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# What Dubcrate needs to read, play and write tracks. (A rekordbox import needs neither.)
REQUIRED = ("ffmpeg", "ffprobe")

BUNDLE_DIR = Path(__file__).parent / "bin"

DOWNLOAD_URL = "https://ffmpeg.org/download.html"


@dataclass(frozen=True)
class Tool:
    name: str
    path: Path
    bundled: bool  # the copy shipped with Dubcrate, rather than one found on PATH

    @property
    def source(self) -> str:
        return "bundled with Dubcrate" if self.bundled else "found on your PATH"


def install_command() -> str:
    """The command that installs ffmpeg and ffprobe on this computer (NOT tried on Linux or Windows)."""
    if sys.platform == "darwin":
        return "brew install ffmpeg"
    if sys.platform == "win32":
        return "winget install Gyan.FFmpeg"
    return "sudo apt install ffmpeg"


def _file_name(name: str) -> str:
    """The file a bundled tool is stored as (Windows programs end in .exe)."""
    return f"{name}.exe" if sys.platform == "win32" else name


def find_tool(name: str) -> Tool | None:
    """The bundled copy of `name` if there is one, else the first on `PATH`, else `None`."""
    bundled = BUNDLE_DIR / _file_name(name)
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return Tool(name, bundled, bundled=True)
    found = shutil.which(name)
    return Tool(name, Path(found), bundled=False) if found else None


def missing_tools() -> list[str]:
    """The names in `REQUIRED` that can't be found."""
    return [name for name in REQUIRED if find_tool(name) is None]


def missing_message(name: str) -> str:
    """The words for an operation that needs `name` and can't find it (an expected error)."""
    return (
        f"{name} was not found. Dubcrate needs ffmpeg and ffprobe to read, play and burn"
        f" music. Install them with “{install_command()}” in a terminal (or from"
        f" {DOWNLOAD_URL}), then restart Dubcrate."
    )


def tool_version(tool: Tool) -> str:
    """The version `tool` reports ("7.1.1"), or "" if it won't say."""
    try:
        proc = subprocess.run(
            [str(tool.path), "-version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    first = proc.stdout.splitlines()[0] if proc.stdout else ""
    match = re.match(r"\w+ version (\S+)", first)
    return match.group(1) if match else ""


def describe(name: str) -> str:
    """One line for Settings and the log: where `name` is and which version it is."""
    tool = find_tool(name)
    if tool is None:
        return f"{name}: not found"
    version = tool_version(tool)
    return f"{name} {version or '(version unknown)'}: {tool.path} ({tool.source})"


def log_tools() -> None:
    """Write where each required tool was found to the log."""
    for name in REQUIRED:
        log.info(describe(name))
