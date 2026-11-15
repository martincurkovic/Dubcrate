"""Make an ISO 9660 + Joliet image of an MP3 disc's folder (Qt-free), in Python, with `pycdlib`.

An MP3 CD is a data disc; where Dubcrate can't write one itself (everything but macOS), an image
is what any burning program takes. This needs no tool on any system (it used to run `xorriso`,
`genisoimage`, `mkisofs` or, on a Mac, `hdiutil`, none of which Windows has).

Joliet is what lets a CD player see long file names, so every file and folder is written with its
real name there, and with Rock Ridge (`rock_ridge="1.09"`) for Linux readers, as `xorriso -J -r` did.
The plain ISO 9660 names underneath are made up: `001.MP3;1`, `D001`, numbered in the order of the
real names, so a reader that ignores Joliet (a very old player) still lists the tracks in order.
The volume label is ASCII (ISO 9660's), up to 16 characters like `mp3.volume_name`.

The image was checked by listing it with `xorriso` and `isoinfo` (Joliet and Rock Ridge), 2026-11-15.
**Not yet burned and played** on a CDJ or any player.
"""

import logging
import unicodedata
from pathlib import Path

import pycdlib
from pycdlib.pycdlibexception import PyCdlibException

log = logging.getLogger(__name__)

APPLICATION = "DUBCRATE"  # who made it, in the image's own header
MIN_DIGITS = 3  # `001.MP3;1`: a folder holds up to 999 files


class IsoError(RuntimeError):
    """An image could not be made; the message is for the user."""


def volume_ident(volume: str) -> str:
    """ISO 9660's label: ASCII, capitals, no more than 16 characters (an accent loses its mark)."""
    ascii_only = (
        unicodedata.normalize("NFKD", volume).encode("ascii", "ignore").decode("ascii")
    )
    kept = "".join(c if c.isalnum() or c in " _-" else " " for c in ascii_only)
    return " ".join(kept.split()).upper()[:16].strip() or "MP3 DISC"


def _children(folder: Path) -> tuple[list[Path], list[Path]]:
    """(sub-folders, files) directly inside `folder`, each by name, A to Z."""
    try:
        entries = sorted(folder.iterdir(), key=lambda p: p.name.casefold())
    except OSError as exc:
        raise IsoError(f"“{folder}” couldn't be read: {exc.strerror or exc}") from exc
    return [p for p in entries if p.is_dir()], [p for p in entries if p.is_file()]


def _add_folder(
    iso: pycdlib.PyCdlib, folder: Path, iso_dir: str, joliet_dir: str
) -> None:
    """Add what is in `folder` below `iso_dir` / `joliet_dir` (both "" for the top)."""
    dirs, files = _children(folder)
    digits = max(MIN_DIGITS, len(str(max(len(dirs), len(files), 1))))
    for number, sub in enumerate(dirs, 1):
        iso_path = f"{iso_dir}/D{number:0{digits}d}"
        joliet_path = f"{joliet_dir}/{sub.name}"
        iso.add_directory(iso_path, rr_name=sub.name, joliet_path=joliet_path)
        _add_folder(iso, sub, iso_path, joliet_path)
    for number, file in enumerate(files, 1):
        iso.add_file(
            str(file),
            f"{iso_dir}/{number:0{digits}d}.MP3;1",
            rr_name=file.name,
            joliet_path=f"{joliet_dir}/{file.name}",
        )


def build_iso(folder: Path, volume: str, out: Path) -> Path:
    """Write `out` (an .iso file) holding the contents of `folder`, with volume name `volume`.

    `folder` and what is in it are only read; `out` is written under another name and moved into
    place, so a failure leaves nothing half made. Raises `IsoError`.
    """
    if not folder.is_dir():
        raise IsoError(f"“{folder}” is not a folder.")
    iso = pycdlib.PyCdlib()
    partial = out.with_name(out.name + ".part")
    try:
        iso.new(
            interchange_level=3,
            joliet=3,
            rock_ridge="1.09",
            vol_ident=volume_ident(volume),
            app_ident_str=APPLICATION,
        )
        _add_folder(iso, folder, "", "")
        partial.unlink(missing_ok=True)
        iso.write(str(partial))
        partial.replace(out)
    except IsoError:
        partial.unlink(missing_ok=True)
        raise
    except (PyCdlibException, OSError, UnicodeError) as exc:
        partial.unlink(missing_ok=True)
        log.warning("The ISO image could not be made: %s", exc)
        raise IsoError(f"The disc image could not be made: {exc}") from exc
    finally:
        try:
            iso.close()
        except PyCdlibException:  # (it was never opened, or already closed)
            pass
    if out.stat().st_size == 0:  # pragma: no cover
        out.unlink(missing_ok=True)
        raise IsoError("The disc image could not be made: it came out empty.")
    return out
