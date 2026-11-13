"""Export a disc as plain files that any burning program can write (Qt-free, library-free).

Dubcrate burns itself on a Mac; on other systems (and for anyone who prefers their own burning
program) the same disc comes out as a folder:

- an audio CD: one 16-bit / 44.1 kHz stereo WAV per track, each a whole number of CD sectors so
  tracks join exactly, `disc.toc` (the cdrdao table of contents, with the CD-TEXT), and `README.txt`;
- an MP3 CD: the folder that goes on the disc, named for it, with `README.txt` beside it (not in it).

The export is a new folder inside the one you choose and never overwrites anything. It renders
through `build.render_plan`, the same code a burn uses, so what is exported is what would be burned.
"""

import shutil
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from dubcrate import mp3
from dubcrate.build import BuildError, BurnPlan, Progress, render_plan
from dubcrate.burn.base import DataDisc, Disc, DiscTrack
from dubcrate.burn.cdrdao import build_toc
from dubcrate.cd import format_clock, format_msf
from dubcrate.iso import IsoError, build_iso


class ExportError(RuntimeError):
    """A failure the user can cause or that is outside our control; the message says what to do."""


@dataclass(frozen=True)
class ExportResult:
    folder: Path  # the new folder, holding everything
    readme: Path
    toc: Path | None  # an audio disc's cdrdao table of contents
    disc_folder: Path | None  # an MP3 disc's contents (inside `folder`)
    iso: Path | None  # an MP3 disc's image, if a tool to make one was found
    lengths: dict[
        int, int
    ]  # track id -> rendered CD sectors (audio), to store in the library


def _ascii(text: str) -> str:
    """`text` without accents or anything outside ASCII (file names in a cdrdao TOC stay plain)."""
    folded = unicodedata.normalize("NFKD", text)
    return "".join(c for c in folded if ord(c) < 128)


def wav_name(number: int, artist: str, title: str) -> str:
    """`01 Artist - Title.wav`, ASCII only, numbered so any program keeps the order."""
    return Path(mp3.file_name(number, _ascii(artist), _ascii(title))).with_suffix(".wav").name  # fmt: skip


def new_export_folder(parent: Path, disc_name: str) -> Path:
    """Create and return `parent/<name> export` (or `... export 2` and so on, if that exists)."""
    if not parent.is_dir():
        raise ExportError(f"“{parent}” is not a folder.")
    base = f"{mp3.dir_name(disc_name)} export"
    for n in range(1, 1000):
        folder = parent / (base if n == 1 else f"{base} {n}")
        try:
            folder.mkdir()
        except FileExistsError:
            continue
        except OSError as exc:
            raise ExportError(f"Couldn't make a folder in “{parent}”: {exc.strerror or exc}.") from exc  # fmt: skip
        return folder
    raise ExportError(f"Too many exports of “{disc_name}” in “{parent}”: remove some.")


def export_plan(
    plan: BurnPlan, parent: Path, progress: Progress | None = None
) -> ExportResult:
    """Render `plan` into a new folder inside `parent`. Safe on a worker thread.

    Raises `ExportError` (or `BuildError`, whose message is also for the user). A folder that
    could not be finished is removed again: it was made here, so it holds nothing of yours."""
    folder = new_export_folder(Path(parent), plan.name)
    try:
        rendered = render_plan(plan, folder, progress)
        if isinstance(rendered.disc, DataDisc):
            result = _finish_mp3(plan, folder, rendered.disc)
        else:
            result = _finish_audio(plan, folder, rendered.disc, rendered.lengths)
    except (BuildError, ExportError):
        shutil.rmtree(folder, ignore_errors=True)
        raise
    except OSError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise ExportError(f"Couldn't write the export to “{parent}”: {exc.strerror or exc}.") from exc  # fmt: skip
    return result


def _finish_audio(plan: BurnPlan, folder: Path, disc: Disc, lengths: dict[int, int]) -> ExportResult:  # fmt: skip
    renamed = Disc(disc.title, disc.performer)
    for number, track in enumerate(disc.tracks, start=1):
        wav = folder / wav_name(number, track.performer, track.title)
        track.wav.rename(wav)
        renamed.tracks.append(
            DiscTrack(wav, track.title, track.performer, track.sectors)
        )
    toc = folder / "disc.toc"
    toc.write_text(build_toc(renamed, plan.pregap_sectors, relative_to=folder), encoding="ascii")  # fmt: skip
    readme = folder / "README.txt"
    readme.write_text(_audio_readme(plan, renamed), encoding="utf-8")
    return ExportResult(folder, readme, toc, None, None, lengths)


def _finish_mp3(plan: BurnPlan, folder: Path, disc: DataDisc) -> ExportResult:
    """The folder is what the disc holds; the ISO image of it (ISO 9660 + Joliet) is made too when a
    tool for it exists, and a missing one is only said in the README (the folder is still good)."""
    iso: Path | None = None
    note = ""
    try:
        iso = build_iso(disc.folder, mp3.volume_name(plan.name), folder / "disc.iso")
    except IsoError as exc:
        note = str(exc)
    readme = folder / "README.txt"
    readme.write_text(_mp3_readme(plan, disc, iso, note), encoding="utf-8")
    return ExportResult(folder, readme, None, disc.folder, iso, {})


_NOT_TRIED = "These steps have been checked against the programs' documentation, not tried on every system."


def _audio_readme(plan: BurnPlan, disc: Disc) -> str:
    gap = "no gap between tracks" if plan.pregap_sectors == 0 else f"{plan.pregap_sectors / 75:g} s between tracks"  # fmt: skip
    return f"""Dubcrate export: {plan.name}
{"=" * (17 + len(plan.name))}

An audio CD of {len(disc.tracks)} tracks, {format_clock(disc.total_sectors)} ({format_msf(disc.total_sectors)}), {gap}.
{plan.levels_note + chr(10) if plan.levels_note else ""}
In this folder
  NN Artist - Title.wav   each track as 16-bit, 44.1 kHz stereo, a whole number of CD sectors long,
                          so tracks join exactly. The number is the track's place on the disc.
  disc.toc                the table of contents for cdrdao, with the CD-TEXT (the disc's and each
                          track's title and artist). It names the WAV files by their names here.

Burn it with cdrdao (Linux and macOS: https://cdrdao.sourceforge.net)
  Open a terminal IN THIS FOLDER, because the TOC names the files relative to it, then:

    cdrdao scanbus                                   (lists your drives; use the device it shows)
    cdrdao write --device DEVICE --speed 10 --simulate disc.toc     (a test: the laser is off)
    cdrdao write --device DEVICE --speed 10 disc.toc                (the real burn)

  Use a blank CD-R. 10x to 16x suits older CD players. Some drives want the disc taken out and put
  back in after the test before the real burn.

With another program (K3b, Brasero, Windows, ...)
  Add the WAV files in the order of their numbers as an AUDIO CD, written disc-at-once with no gap
  between tracks. The program only writes the CD-TEXT if it supports it; otherwise type the titles
  in by hand (they are in the file names and in disc.toc).

{_NOT_TRIED}
"""


def _mp3_readme(plan: BurnPlan, disc: DataDisc, iso: Path | None, note: str) -> str:
    name = disc.folder.name
    image = (
        "  disc.iso  the same disc as an image (ISO 9660 with Joliet): burn this with any program's\n"
        '            "burn image" (Brasero, K3b, Windows, Finder), or: xorriso -as cdrecord -v dev=/dev/sr0 speed=10 disc.iso\n'
        if iso
        else f"  (No disc.iso was made: {note})\n"
    )
    return f"""Dubcrate export: {plan.name}
{"=" * (17 + len(plan.name))}

An MP3 CD: {len(plan.tracks)} files, {mp3.format_size(disc.sectors)} on the disc.

In this folder
  {name}/   exactly what goes on the disc, with its folders and numbered file names, so a player
  {" " * len(name)}   that sorts by name keeps your order. Burn the CONTENTS of this folder as a data CD.
  (This README is outside it on purpose, so it does not end up on the disc.)
{image}
A data CD for a CD player (ISO 9660 with Joliet), by any of these
  macOS     Finder: File > New Burn Folder, drag the folder's contents in, Burn.
  Windows   Put the folder's contents in the disc drive's window (Explorer offers "Burn to disc").
  Linux     K3b or Brasero: a new data project with the folder's contents.
  Any       Make an image first and burn that:
              xorriso -as mkisofs -J -V "{mp3.volume_name(plan.name)}" -o disc.iso "{name}"
            then use the program's "burn image" and pick disc.iso.

Use a blank CD-R, and a lower speed if your player is fussy.

{_NOT_TRIED}
"""
