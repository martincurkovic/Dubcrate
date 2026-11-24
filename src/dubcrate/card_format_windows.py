"""Format a blank SD card for the CDJ-1000MK3 on Windows (Qt-free; `card_format` chooses it).

PowerShell's storage cmdlets do it: `Get-Disk` lists and describes the disks (no administrator
needed), and one script (`FORMAT_SCRIPT`) clears the disk, initialises it as MBR, makes one FAT16
partition (`-MbrType FAT16`, 32 KB clusters) named `DISK_IMG` and gives it a drive letter. Those
cmdlets need an administrator, so the script is started with `Start-Process -Verb RunAs` (Windows'
own permission prompt, which says nothing if Dubcrate is already elevated), and answers in a
temporary file, because an elevated process's output can't be read back.

The disk is named `disk3` as on a Mac (`Get-Disk -Number 3`). The script **checks again, itself**,
that the disk is the one asked for (its size), isn't a system or boot disk, is on a USB / SD / MMC bus
and is 2 GiB or less, so a mistake up in Python can't be the only thing between a disk and the erase.
What differs from the player's own format: the partition starts at 1 MiB (the player's at sector
32), as with `diskutil`.

**Not tried on a real disk or card**: tested only against stand-in commands.
"""

import json
import subprocess
import tempfile
from pathlib import Path

from dubcrate.card_format import (
    LABEL,
    MAX_BYTES,
    TIMEOUT_SECONDS,
    CardDisk,
    FormatError,
    Runner,
)

BUSES = frozenset(
    {"USB", "SD", "MMC"}
)  # what a card or a reader reports; internal disks are SATA / NVMe

LIST_COMMAND = (
    "Get-Disk | Select-Object Number, FriendlyName, Size, "
    "@{n='BusType';e={[string]$_.BusType}}, IsSystem, IsBoot, IsReadOnly "
    "| ConvertTo-Json -Compress"
)

FORMAT_SCRIPT = """\
param([int]$Number, [uint64]$Size, [uint64]$Max, [string]$Label, [string]$Out)
$ErrorActionPreference = 'Stop'
try {
    $disk = Get-Disk -Number $Number
    $bus = [string]$disk.BusType
    if ($disk.Size -ne $Size) { throw 'That is not the card that was picked any more.' }
    if ($disk.IsSystem -or $disk.IsBoot) { throw 'That disk holds Windows.' }
    if (@('USB', 'SD', 'MMC') -notcontains $bus) { throw 'That is not a removable card.' }
    if ($disk.Size -gt $Max) { throw 'That card is too big for the player.' }
    if ($disk.IsReadOnly) { throw 'The card is write-protected.' }
    if ($disk.IsOffline) { Set-Disk -Number $Number -IsOffline $false }
    if ($disk.PartitionStyle -ne 'RAW') { Clear-Disk -Number $Number -RemoveData -RemoveOEM -Confirm:$false }
    # A card or reader the system calls removable keeps its partition style after Clear-Disk (an empty MBR, not RAW):
    # Initialize-Disk then says "The disk has already been initialized", which failed a real format on 2026-11-24.
    $style = [string](Get-Disk -Number $Number).PartitionStyle
    if ($style -eq 'RAW') { Initialize-Disk -Number $Number -PartitionStyle MBR }
    elseif ($style -ne 'MBR') { Set-Disk -Number $Number -PartitionStyle MBR }
    $part = New-Partition -DiskNumber $Number -UseMaximumSize -AssignDriveLetter -MbrType FAT16
    Format-Volume -Partition $part -FileSystem FAT -NewFileSystemLabel $Label -AllocationUnitSize 32768 -Confirm:$false | Out-Null
    $result = @{ ok = $true; letter = [string]$part.DriveLetter }
} catch {
    $result = @{ ok = $false; error = $_.Exception.Message }
}
$result | ConvertTo-Json -Compress | Set-Content -Path $Out -Encoding UTF8
"""


def device_name(text: str) -> str:
    """`disk3`, from `disk3`, `3` or `\\\\.\\PhysicalDrive3`."""
    name = text.strip()
    for prefix in ("\\\\.\\PhysicalDrive", "PhysicalDrive", "disk", "Disk"):
        if name.startswith(prefix) and name[len(prefix) :].isdigit():
            return f"disk{int(name[len(prefix) :])}"
    if name.isdigit():
        return f"disk{int(name)}"
    raise FormatError(f"{text!r} is not a disk name such as disk3.")


def _number(device: str) -> int:
    return int(device_name(device).removeprefix("disk"))


def _flag(value: object) -> bool:
    return value in (True, 1, "True", "true")


def parse_get_disk(data: str) -> list[CardDisk]:
    """`Get-Disk ... | ConvertTo-Json` as disks: one disk comes as an object, several as a list."""
    if not data.strip():
        return []
    try:
        found = json.loads(data.lstrip("\ufeff"))
    except ValueError as exc:
        raise FormatError("The system's disk list couldn't be read.") from exc
    if isinstance(found, dict):
        found = [found]
    if not isinstance(found, list):
        raise FormatError("The system's disk list couldn't be read.")
    disks = []
    for row in found:
        try:
            bus = str(row.get("BusType") or "")
            disks.append(
                CardDisk(
                    device=f"disk{int(row['Number'])}",
                    name=str(row.get("FriendlyName") or "").strip(),
                    size=int(row.get("Size") or 0),
                    protocol=bus,
                    whole=True,
                    removable=bus.upper() in BUSES,
                    virtual="virtual" in bus.lower(),
                    system=_flag(row.get("IsSystem")) or _flag(row.get("IsBoot")),
                    writable=not _flag(row.get("IsReadOnly")),
                )
            )
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise FormatError("The system's disk list couldn't be read.") from exc
    return disks


def _powershell(
    run: Runner, command: str, what: str, timeout: int = TIMEOUT_SECONDS
) -> subprocess.CompletedProcess[str]:
    try:
        return run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise FormatError(f"The system didn't finish {what}.") from exc
    except OSError as exc:
        raise FormatError(f"Couldn't run PowerShell: {exc}") from exc


def _all_disks(run: Runner) -> list[CardDisk]:
    proc = _powershell(run, LIST_COMMAND, "listing the disks")
    if proc.returncode != 0:
        said = (proc.stderr or "").strip().splitlines()
        raise FormatError(
            "Listing the disks failed" + (f": {said[-1]}" if said else ".")
        )
    return parse_get_disk(proc.stdout)


def list_disks(run: Runner = subprocess.run) -> list[CardDisk]:
    """The removable whole disks (a USB reader, a card slot) for the user to choose from."""
    return [d for d in _all_disks(run) if d.removable and not d.system]


def inspect(device: str, run: Runner = subprocess.run) -> CardDisk:
    """Ask the system about one disk (`disk3`), whether or not it could be formatted."""
    name = device_name(device)
    for disk in _all_disks(run):
        if disk.device == name:
            return disk
    raise FormatError(f"{name} is not a disk this computer has.")


def _quoted(text: str) -> str:
    """A PowerShell single-quoted string."""
    return "'" + text.replace("'", "''") + "'"


def _argument(text: str) -> str:
    """One argument for `Start-Process -ArgumentList`, which joins them with spaces: quote it for that."""
    return _quoted(f'"{text}"')


def elevated_command(script: Path, disk: CardDisk, out: Path) -> str:
    """The PowerShell that runs `FORMAT_SCRIPT` as an administrator, waiting for it to end."""
    args = [
        "'-NoProfile'",
        "'-ExecutionPolicy'",
        "'Bypass'",
        "'-File'",
        _argument(str(script)),
        "'-Number'",
        f"'{_number(disk.device)}'",
        "'-Size'",
        f"'{disk.size}'",
        "'-Max'",
        f"'{MAX_BYTES}'",
        "'-Label'",
        _argument(LABEL),
        "'-Out'",
        _argument(str(out)),
    ]
    return (
        "Start-Process -FilePath powershell -Verb RunAs -Wait -WindowStyle Hidden "
        f"-ArgumentList {','.join(args)}"
    )


def drive_root(letter: str) -> Path:
    return Path(f"{letter.upper()}:/")


def format_disk(disk: CardDisk, run: Runner = subprocess.run) -> Path:
    """Erase and format the disk, and give its drive's root (`E:\\`). Raises `FormatError`."""
    with tempfile.TemporaryDirectory(prefix="dubcrate-format-") as work:
        script = Path(work) / "format.ps1"
        out = Path(work) / "result.json"
        # a BOM so Windows PowerShell 5.1 reads the file as UTF-8
        script.write_text(FORMAT_SCRIPT, encoding="utf-8-sig")
        proc = _powershell(
            run, elevated_command(script, disk, out), "formatting the card"
        )
        if proc.returncode != 0:
            raise FormatError(
                "Windows' administrator permission wasn't given, so nothing was erased."
            )
        try:
            result = json.loads(out.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise FormatError(
                "The formatting did not report back: look at the card in Explorer "
                "before trying again."
            ) from exc
    if not result.get("ok"):
        raise FormatError(
            f"Formatting failed: {result.get('error') or 'unknown error'}"
        )
    letter = str(result.get("letter") or "").strip()
    if len(letter) != 1 or not letter.isalpha():
        raise FormatError("The card was formatted but got no drive letter.")
    return drive_root(letter)
