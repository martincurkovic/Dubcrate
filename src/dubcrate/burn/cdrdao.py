"""Burn backend that drives the `cdrdao` command line tool.

cdrdao writes in disc-at-once mode from a TOC file, which gives gapless audio
and CD-TEXT. We generate the TOC and let cdrdao do the burning.
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from dubcrate.burn.base import BurnError, BurnOptions, Disc, Progress
from dubcrate.cd import cdtext_clean, cdtext_toc_escape


def find_cdrdao() -> str | None:
    return shutil.which("cdrdao")


def build_toc(disc: Disc) -> str:
    """Render `disc` as a cdrdao TOC with CD-TEXT for the disc and each track."""

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
    for track in disc.tracks:
        lines += ["", "TRACK AUDIO", "NO COPY", "NO PRE_EMPHASIS", "CD_TEXT {"]
        lines += text_block(track.title, track.performer)
        lines.append("}")
        lines.append(f'FILE "{cdtext_toc_escape(str(track.wav.resolve()))}" 0')
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

    def burn(self, disc: Disc, options: BurnOptions, progress: Progress) -> None:
        exe = find_cdrdao()
        if not exe:
            raise BurnError("cdrdao not found on PATH (try: brew install cdrdao)")
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
            toc.write_text(build_toc(disc), encoding="ascii")
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
