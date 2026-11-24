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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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


def _volume_date(when: datetime) -> pycdlib.dates.VolumeDescriptorDate:
    """`when` as an ISO 9660 volume date: 16 digits (hundredths 00), then the zone in quarter hours."""
    when = when.astimezone() if when.tzinfo is None else when
    offset = when.utcoffset()
    quarters = 0 if offset is None else int(offset.total_seconds() // 900)
    date = pycdlib.dates.VolumeDescriptorDate()
    date.parse(
        when.strftime("%Y%m%d%H%M%S00").encode("ascii") + bytes([quarters & 0xFF])
    )
    return date


def build_iso(
    folder: Path, volume: str, out: Path, created: datetime | None = None
) -> Path:
    """Write `out` (an .iso file) holding the contents of `folder`, with volume name `volume`.

    `created` is the volume's creation date (now if None). **The CDJ puts it in an MP3 disc's ID**
    (`cdj_card_mp3.Mp3DiscId.made_from`), so a disc is burned from an image made with a date that is kept
    (`DataDisc.created`, `Library.set_disc_image`), and the same date makes the same ID again later.

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
        if created is not None:
            for descriptor in (iso.pvd, iso.joliet_vd):
                descriptor.volume_creation_date = _volume_date(created)
                descriptor.volume_modification_date = _volume_date(created)
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


SECTOR = 2048  # an ISO 9660 logical block


@dataclass(frozen=True)
class ImageFile:
    """One file of an image, as the CDJ numbers it: `folder` 0 is the root, then one number per folder in the
    order of their names (`D001` is 1); `file` counts from 1 in its folder (`001.MP3`)."""

    folder: int
    file: int
    path: Path  # the staged file it was made from
    extent: int  # its first sector on the image
    size: int  # bytes


@dataclass(frozen=True)
class ImageLayout:
    """What the CDJ's disc ID is made from on an MP3 disc, read off the image: the number of files, the first
    sector of the last file, and the image's size in sectors (the three fields of the ID that are understood).
    So this is also how a card's entry is told to belong to this exact image."""

    sectors: int
    files: tuple[ImageFile, ...]
    # the folders under the root (what the CDJ counts: the root is not)
    folders: int = 0
    # the Joliet volume label as written (what the CDJ's ID sums), which is not always the name asked for
    label: str = ""

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def last_file_start(self) -> int:
        return max((f.extent for f in self.files), default=0)

    def by_path(self) -> dict[Path, ImageFile]:
        return {f.path: f for f in self.files}


def image_layout(image: Path, folder: Path) -> ImageLayout:
    """The layout of `image`, which `build_iso` made from `folder` (which is only read for its names).

    The numbers come from the image itself: each file's `001.MP3;1`, in `D001`, is looked up and its first
    sector and size read. One level of folders only: what the player numbers a folder inside a folder is not
    known, so that raises `IsoError` rather than guess."""
    files: list[ImageFile] = []
    top_dirs, top_files = _children(folder)
    if any(_children(sub)[0] for sub in top_dirs):
        raise IsoError(
            "Folders inside folders aren't supported on the card yet: how the player numbers them isn't known."
        )
    iso = pycdlib.PyCdlib()
    try:
        iso.open(str(image))
        digits = max(MIN_DIGITS, len(str(max(len(top_dirs), len(top_files), 1))))
        groups = [(0, "", top_files, digits)]
        for number, sub in enumerate(top_dirs, 1):
            inside = _children(sub)[1]
            groups.append(
                (
                    number,
                    f"/D{number:0{digits}d}",
                    inside,
                    max(MIN_DIGITS, len(str(max(len(inside), 1)))),
                )
            )
        for folder_number, iso_dir, inside, width in groups:
            for number, path in enumerate(inside, 1):
                record = iso.get_record(iso_path=f"{iso_dir}/{number:0{width}d}.MP3;1")
                files.append(
                    ImageFile(
                        folder_number,
                        number,
                        path,
                        record.extent_location(),
                        record.get_data_length(),
                    )
                )
    except (PyCdlibException, OSError) as exc:
        raise IsoError(f"The disc image couldn't be read: {exc}") from exc
    finally:
        try:
            iso.close()
        except PyCdlibException:
            pass
    return ImageLayout(
        image.stat().st_size // SECTOR,
        tuple(files),
        len(top_dirs),
        joliet_label(joliet_descriptor(image)),
    )


def joliet_descriptor(image: Path) -> bytes:
    """The image's Joliet volume descriptor (a 2048-byte sector), which the CDJ reads its disc ID from.
    Raises `IsoError` if the image has none."""
    try:
        with image.open("rb") as f:
            for sector in range(16, 32):
                f.seek(sector * SECTOR)
                block = f.read(SECTOR)
                if len(block) < SECTOR or block[1:6] != b"CD001" or block[0] == 255:
                    break
                if block[0] == 2:
                    return block
    except OSError as exc:
        raise IsoError(f"The disc image couldn't be read: {exc}") from exc
    raise IsoError("The disc image has no Joliet volume descriptor.")


def joliet_label(svd: bytes) -> str:
    """The Joliet volume label of a descriptor (UCS-2, big endian), without its padding."""
    return svd[40:72].decode("utf-16-be", errors="replace").rstrip(" \x00")


def joliet_label_field(label: str) -> bytes:
    """The 32 bytes `build_iso` (pycdlib) writes for a Joliet label: UCS-2 and then UCS-2 spaces. The CDJ's ID sums
    these bytes, so a disc whose field is anything else (another maker's padding) can't have its ID made here."""
    text = label.encode("utf-16-be")
    return text + b"\x00\x20" * ((32 - len(text)) // 2)


@dataclass(frozen=True)
class ImageSummary:
    """What an image (a file, or the disc in a drive) says about itself, as far as the CDJ's ID goes."""

    created: datetime  # the volume's creation date, in its own zone
    label: str  # the Joliet label
    label_as_ours: bool  # its 32 bytes are what `build_iso` writes for that label
    file_count: int  # the MP3 files (what the CDJ counts)
    folders: int  # the folders under the root
    last_file_start: int  # the first sector of the file that starts last
    sectors: int  # the image's size in sectors


NESTED = "Folders inside folders aren't supported on the card yet: how the player numbers them isn't known."


def read_image(path: Path) -> ImageSummary:
    """Read an image or a drive's disc (`path` is an `.iso` or a device), only what is needed (the descriptors and
    the directories, not the files). One level of folders, as `image_layout`. Raises `IsoError`."""
    iso = pycdlib.PyCdlib()
    try:
        with path.open("rb") as fp:
            svd = _joliet_from(fp)
            iso.open_fp(fp)
            try:
                size = iso.pvd.space_size
                files: list[int] = []
                folders = 0
                for child in iso.list_children(iso_path="/"):
                    if child.is_dot() or child.is_dotdot():
                        continue
                    if child.is_dir():
                        folders += 1
                        name = child.file_identifier().decode("ascii")
                        inside = [
                            c
                            for c in iso.list_children(iso_path=f"/{name}")
                            if not (c.is_dot() or c.is_dotdot())
                        ]
                        if any(c.is_dir() for c in inside):
                            raise IsoError(NESTED)
                        files += [c.extent_location() for c in inside if _is_mp3(c)]
                    elif _is_mp3(child):
                        files.append(child.extent_location())
            finally:
                iso.close()
    except (PyCdlibException, OSError, UnicodeError) as exc:
        raise IsoError(f"The disc couldn't be read: {exc}") from exc
    label = joliet_label(svd)
    return ImageSummary(
        _descriptor_date(svd),
        label,
        svd[40:72] == joliet_label_field(label),
        len(files),
        folders,
        max(files, default=0),
        size,
    )


def _is_mp3(record) -> bool:
    name = record.file_identifier().decode("ascii", errors="replace").upper()
    return name.endswith((".MP3;1", ".MP3"))


def _joliet_from(fp) -> bytes:
    for sector in range(16, 32):
        fp.seek(sector * SECTOR)
        block = fp.read(SECTOR)
        if len(block) < SECTOR or block[1:6] != b"CD001" or block[0] == 255:
            break
        if block[0] == 2:
            return block
    raise IsoError(
        "The disc has no Joliet volume descriptor (it is not an MP3 disc made like ours)."
    )


def _descriptor_date(svd: bytes) -> datetime:
    digits = svd[813:829]
    if not digits.isdigit():
        raise IsoError("The disc's creation date is not a date.")
    quarters = int.from_bytes(svd[829:830], "big", signed=True)
    zone = timezone(timedelta(minutes=15 * quarters))
    parts = [
        int(digits[a:b])
        for a, b in ((0, 4), (4, 6), (6, 8), (8, 10), (10, 12), (12, 14))
    ]
    try:
        return datetime(*parts, tzinfo=zone)
    except ValueError as exc:
        raise IsoError("The disc's creation date is not a date.") from exc
