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

from dubcrate.burn.base import (
    BurnError,
    BurnOptions,
    DataDisc,
    Disc,
    MediaInfo,
    Progress,
)
from dubcrate.cd import (
    SECTORS_PER_SECOND,
    cdtext_clean,
    cdtext_toc_escape,
    format_msf,
)
from dubcrate.iso import IsoError, build_iso
from dubcrate.tools import find_tool

# The CD standard wants every track to be at least 4 seconds long, and cdrdao refuses a shorter one
# ("Use option --force to ignore the warnings"), so a burn says so before it starts.
MIN_TRACK_SECTORS = 4 * SECTORS_PER_SECOND


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


def build_data_toc(iso: Path, relative_to: Path | None = None) -> str:
    """A cdrdao TOC that writes `iso` as the one data track of an MP3 CD (no CD-TEXT: it is a data
    disc). Named by full path, or relative to `relative_to`."""
    if relative_to is None:
        name = str(iso.resolve())
    else:
        name = Path(os.path.relpath(iso, relative_to)).as_posix()
    return f'CD_ROM\n\nTRACK MODE1\nDATAFILE "{cdtext_toc_escape(name)}"\n'


def scan_devices() -> list[tuple[str, str]]:
    """Return (device, description) pairs from `cdrdao scanbus`."""
    exe = find_cdrdao()
    if not exe:
        return []
    proc = subprocess.run(
        [exe, "scanbus"],
        check=False,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    pairs = []
    for line in (proc.stdout + proc.stderr).splitlines():
        match = re.match(r"^(\S+)\s*:\s*(.+)$", line.strip())
        if match and "/" in match.group(1):
            pairs.append((match.group(1), match.group(2).strip()))
    return pairs


# What `cdrdao disk-info` prints, from the strings in cdrdao 1.2.4's own binary (the labels are
# exact; the values after them are read loosely). NOT checked against a real drive.
_DISK_INFO_LINE = re.compile(
    r"^(CD-RW|Total Capacity|CD-R medium|Recording Speed|CD-R empty|Toc Type|Sessions|Appendable)"
    r"\s*:\s*(.*)$",
    re.MULTILINE,
)
_DRIVE_LINE = re.compile(
    r"^\S+: (.*?)\s*Rev:", re.MULTILINE
)  # "/dev/sr0: VENDOR MODEL  Rev: X"


def parse_disk_info(text: str) -> MediaInfo:
    """What `cdrdao disk-info` said about the drive and the disc in it. Anything it did not say
    stays unknown (None): an unknown is never a reason to refuse a burn."""
    drive_match = _DRIVE_LINE.search(text)
    drive = " ".join(drive_match.group(1).split()) if drive_match else ""
    fields = {m.group(1): m.group(2).strip() for m in _DISK_INFO_LINE.finditer(text)}
    if "CD-R empty" not in fields:
        return MediaInfo(drive=drive)
    blank = fields["CD-R empty"].lower().startswith("yes")
    free = None
    capacity = re.search(r"(\d+):(\d+):(\d+)", fields.get("Total Capacity", ""))
    if blank and capacity:
        minutes, seconds, frames = (int(n) for n in capacity.groups())
        free = (minutes * 60 + seconds) * SECTORS_PER_SECOND + frames
    return MediaInfo(drive=drive, present=True, blank=blank, free_sectors=free)


def explain_failure(
    output: str, device: str | None = None, status: int | None = None
) -> str:
    """cdrdao's own output turned into words for the user, when it failed. The patterns are
    messages from cdrdao 1.2.4 (its binary's strings, and what it said without a drive); what a
    real drive says is NOT checked."""
    where = f" {device}" if device else ""
    low = output.lower()
    if "permission denied" in low:
        return (
            f"Dubcrate isn't allowed to use the drive{where}. On Linux, the drive belongs to a group"
            f" (see `ls -l{where or ' /dev/sr0'}`, often `cdrom` or `optical`): add yourself to it with"
            " `sudo usermod -aG GROUP $USER`, then log out and in again."
        )
    if "unable to open scsi device" in low and "no such file" in low:
        return f"There is no drive at{where or ' that address'}. Is the burner plugged in and switched on?"
    if "not empty and not appendable" in low:
        return "The disc in the drive isn't blank. Put in a blank CD-R."
    if (
        "insert a recordable medium" in low
        or "cannot determine status of inserted medium" in low
        or "cannot determine disk status" in low
    ):
        return "There is no usable blank disc in the drive. Put in a blank CD-R."
    if "not supported by device" in low and "writing speed" in low:
        return (
            "The drive can't write at that speed. Choose another in Settings > Burning."
        )
    if "buffer under run" in low:
        return "The drive ran out of data while writing (a buffer under-run). Try a lower speed."
    if "shorter than 4 seconds" in low:
        return "A track is shorter than 4 seconds, which the CD standard doesn't allow."
    errors = [
        line[6:].strip() for line in output.splitlines() if line.startswith("ERROR:")
    ]
    if errors:
        return f"cdrdao said: {errors[-1]}"
    return f"cdrdao failed{f' (status {status})' if status is not None else ''}."


class _ProgressFilter:
    """cdrdao prints "Wrote 12 of 703 MB (Buffer 98%)." many times a second; the log gets one
    line every 10 percent instead, and everything else as it is."""

    def __init__(self, emit: Progress):
        self._emit = emit
        self._next = 10

    def feed(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        match = re.match(r"Wrote (\d+) of (\d+) MB", line)
        if match is None:
            self._emit(line)
            return
        done, total = int(match.group(1)), int(match.group(2))
        percent = 100 * done // total if total else 0
        if percent >= self._next:
            self._emit(f"Writing: {percent}% ({done} of {total} MB)")
            self._next = percent // 10 * 10 + 10


class CdrdaoBurner:
    name = "cdrdao"

    def _exe(self) -> str:
        exe = find_cdrdao()
        if not exe:
            raise BurnError(f"cdrdao was not found. {install_hint()}.")
        return exe

    def _device(self, device: str | None) -> str:
        if device is not None:
            return device
        found = scan_devices()
        if not found:
            raise BurnError(
                "cdrdao found no drives. Is the burner plugged in? On Linux your user may also need"
                " to be in the drive's group (`cdrom` or `optical`); `cdrdao scanbus` shows what it sees."
            )
        return found[0][0]

    def media_info(self, device: str | None) -> MediaInfo:
        """The drive's name and whether the disc in it is blank and how much it holds, from
        `cdrdao disk-info`. Unreadable parts stay unknown. NOT tried on a real drive."""
        exe = self._exe()
        device = self._device(device)
        try:
            proc = subprocess.run(
                [exe, "disk-info", "--device", device],
                check=False,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=60,
            )
        except subprocess.TimeoutExpired as exc:
            raise BurnError(f"The drive {device} did not answer in a minute.") from exc
        text = proc.stdout + proc.stderr
        info = parse_disk_info(text)
        if info.blank is None and proc.returncode != 0:
            low = text.lower()
            if (
                "insert a recordable medium" in low
                or "cannot determine status of inserted medium" in low
            ):
                return MediaInfo(drive=info.drive, present=False)
            raise BurnError(explain_failure(text, device, proc.returncode))
        return info

    def burn_data(
        self, disc: DataDisc, options: BurnOptions, progress: Progress
    ) -> None:
        """Burn an MP3 CD: make an ISO 9660 + Joliet image of the disc's folder, then write it
        as one data track (a `CD_ROM` TOC with `TRACK MODE1`). NOT tried on a real drive."""
        exe = self._exe()
        device = self._device(options.device)
        with tempfile.TemporaryDirectory(prefix="dubcrate-") as tmp:
            progress(f"making the disc image of “{disc.title}”…")
            try:
                iso = build_iso(disc.folder, disc.title, Path(tmp) / "disc.iso")
            except IsoError as exc:
                raise BurnError(str(exc)) from exc
            toc = Path(tmp) / "disc.toc"
            toc.write_text(build_data_toc(iso), encoding="ascii")
            self._write(exe, toc, device, options, progress)

    def burn(self, disc: Disc, options: BurnOptions, progress: Progress) -> None:
        exe = self._exe()
        for number, track in enumerate(disc.tracks, start=1):
            if track.sectors < MIN_TRACK_SECTORS:
                raise BurnError(
                    f"Track {number} “{track.title}” is shorter than 4 seconds. The CD standard"
                    " needs at least that, and cdrdao won't write it: take it off the disc."
                )
        device = self._device(options.device)
        with tempfile.TemporaryDirectory(prefix="dubcrate-") as tmp:
            toc = Path(tmp) / "disc.toc"
            toc.write_text(build_toc(disc, options.pregap_sectors), encoding="ascii")
            self._write(exe, toc, device, options, progress)

    def _write(
        self, exe: str, toc: Path, device: str, options: BurnOptions, progress: Progress
    ) -> None:
        cmd = [exe, "write", "--device", device, "-n"]
        if options.speed:
            cmd += ["--speed", str(options.speed)]
        if options.simulate:
            cmd.append("--simulate")
        if options.eject:
            cmd.append("--eject")
        cmd.append(str(toc))
        progress(f"$ {' '.join(cmd)}")
        # (stdin is closed: cdrdao asks "hit enter" when it wants a disc, and nobody is typing)
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        assert proc.stdout is not None
        seen: list[str] = []
        filtered = _ProgressFilter(progress)
        for line in proc.stdout:  # (text mode: a "\r" ends a line too)
            seen.append(line.rstrip())
            filtered.feed(line)
        if proc.wait() != 0:
            raise BurnError(explain_failure("\n".join(seen), device, proc.returncode))
