"""Burn backend that drives the `cdrdao` command line tool.

cdrdao writes in disc-at-once mode from a TOC file, which gives gapless audio
and CD-TEXT. We generate the TOC and let cdrdao do the burning.
"""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from dubcrate.burn.base import BurnError, BurnOptions, Disc, MediaInfo, Progress
from dubcrate.cd import cdtext_clean, cdtext_toc_escape, format_msf
from dubcrate.tools import find_tool


def find_cdrdao() -> str | None:
    """Where `cdrdao` is (the app's own folder, then `PATH`), or None."""
    tool = find_tool("cdrdao")
    return str(tool.path) if tool else None


def install_hint() -> str:
    """How to get cdrdao on this computer, in words for the user. NOT tried on Linux or Windows."""
    if sys.platform == "darwin":
        return "Install it with “brew install cdrdao” in Terminal"
    if sys.platform == "win32":
        return "Windows isn't supported for burning yet"
    return (
        "Install it with your package manager, for example “sudo apt install cdrdao”"
        " (Debian, Ubuntu) or “sudo dnf install cdrdao” (Fedora)"
    )


def build_toc(
    disc: Disc, pregap_sectors: int = 0, relative_to: Path | None = None
) -> str:
    """Render `disc` as a cdrdao TOC with CD-TEXT for the disc and each track.

    cdrdao is gapless unless told otherwise, so a PREGAP is only emitted for
    tracks 2 and up when `pregap_sectors` is non-zero. The files are named by full path, or,
    with `relative_to`, relative to that folder (for a TOC that is exported beside its files).
    """

    def text_block(title: str, performer: str) -> list[str]:
        return [
            "  LANGUAGE 0 {",
            f'    TITLE "{cdtext_toc_escape(cdtext_clean(title))}"',
            f'    PERFORMER "{cdtext_toc_escape(cdtext_clean(performer))}"',
            "  }",
        ]

    lines = ["CD_DA", "CD_TEXT {", "  LANGUAGE_MAP { 0 : EN }"]
    lines += text_block(disc.title, disc.performer)
    lines.append("}")
    for index, track in enumerate(disc.tracks):
        lines += ["", "TRACK AUDIO", "NO COPY", "NO PRE_EMPHASIS", "CD_TEXT {"]
        lines += text_block(track.title, track.performer)
        lines.append("}")
        if index > 0 and pregap_sectors:
            lines.append(f"PREGAP {format_msf(pregap_sectors)}")
        if relative_to is None:
            name = str(track.wav.resolve())
        else:  # (forward slashes: cdrdao reads them on every system)
            name = Path(os.path.relpath(track.wav, relative_to)).as_posix()
        lines.append(f'FILE "{cdtext_toc_escape(name)}" 0')
    return "\n".join(lines) + "\n"


def scan_devices() -> list[tuple[str, str]]:
    """Return (device, description) pairs from `cdrdao scanbus`."""
    exe = find_cdrdao()
    if not exe:
        return []
    proc = subprocess.run([exe, "scanbus"], check=False, capture_output=True, text=True)
    pairs = []
    for line in (proc.stdout + proc.stderr).splitlines():
        match = re.match(r"^(\S+)\s*:\s*(.+)$", line.strip())
        if match and "/" in match.group(1):
            pairs.append((match.group(1), match.group(2).strip()))
    return pairs


class CdrdaoBurner:
    name = "cdrdao"

    def media_info(self, device: str | None) -> MediaInfo:
        return MediaInfo()  # not read from cdrdao yet: everything unknown

    def burn_data(self, disc, options: BurnOptions, progress: Progress) -> None:
        raise BurnError("the cdrdao backend can only burn audio CDs, not MP3 CDs")

    def burn(self, disc: Disc, options: BurnOptions, progress: Progress) -> None:
        exe = find_cdrdao()
        if not exe:
            raise BurnError(f"cdrdao was not found. {install_hint()}.")
        device = options.device
        if device is None:
            found = scan_devices()
            if not found:
                raise BurnError(
                    "cdrdao found no drives (run `cdrdao scanbus` to debug)"
                )
            device = found[0][0]
        with tempfile.TemporaryDirectory(prefix="dubcrate-") as tmp:
            toc = Path(tmp) / "disc.toc"
            toc.write_text(build_toc(disc, options.pregap_sectors), encoding="ascii")
            cmd = [exe, "write", "--device", device, "-n"]
            if options.speed:
                cmd += ["--speed", str(options.speed)]
            if options.simulate:
                cmd.append("--simulate")
            if options.eject:
                cmd.append("--eject")
            cmd.append(str(toc))
            progress(f"$ {' '.join(cmd)}")
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                progress(line.rstrip())
            if proc.wait() != 0:
                raise BurnError(f"cdrdao exited with status {proc.returncode}")
