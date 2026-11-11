"""Turn a planned disc from the library into something a burner can burn."""

from collections.abc import Callable
from pathlib import Path

from dubcrate.burn.base import Disc as BurnDisc
from dubcrate.burn.base import DiscTrack, MediaInfo
from dubcrate.cd import MAX_TRACKS, disc_used_sectors, format_msf
from dubcrate.library.lengths import measure_tracks
from dubcrate.library.store import Library
from dubcrate.render import RenderError, render_track

Progress = Callable[[str], None]


class BuildError(RuntimeError):
    pass


def disc_problems(lib: Library, disc_id: int) -> list[str]:
    """Reasons the disc can't be burned as planned (empty list means it can)."""
    summary = lib.disc(disc_id)
    entries = lib.disc_entries(disc_id)
    problems: list[str] = []
    if not entries:
        problems.append("the disc is empty")
    if len(entries) > MAX_TRACKS:
        problems.append(f"{len(entries)} tracks; a CD holds at most {MAX_TRACKS}")
    for e in entries:
        label = f"#{e.position + 1} {e.track.artist} - {e.track.title}".strip()
        if e.track.stale:
            problems.append(f"{label}: no longer in its source")
        if e.track.missing:
            problems.append(f"{label}: file not found ({e.track.path})")
    if summary.over:
        problems.append(
            f"over capacity by {format_msf(-summary.free_sectors)}"
            f" ({format_msf(summary.used_sectors)} of {format_msf(summary.capacity_sectors)})"
        )
    return problems


def media_problems(used_sectors: int, media: MediaInfo) -> list[str]:
    """Reasons the disc in the drive can't take `used_sectors` of audio.

    Only what the drive is sure about counts: an unknown (None) or a free
    space of zero is not a reason to refuse, because a drive that can't say
    isn't the same as a disc that can't hold it.
    """
    if media.present is False:
        return ["no disc in the drive"]
    free = media.free_sectors
    if free and used_sectors > free:
        holds, needs = format_msf(free), format_msf(used_sectors)
        return [f"the disc in the drive holds {holds} but this disc needs {needs}"]
    return []


def media_notes(media: MediaInfo) -> list[str]:
    """What to tell the user about the disc in the drive, before burning."""
    notes = []
    if media.blank is False:
        notes.append("the disc in the drive doesn't look blank")
    if media.free_sectors:
        notes.append(f"the disc in the drive holds {format_msf(media.free_sectors)}")
    return notes


def _performer(artists: list[str]) -> str:
    distinct = {a for a in artists if a}
    if len(distinct) == 1:
        return distinct.pop()
    return "Various Artists"


def build_burn_disc(
    lib: Library, disc_id: int, workdir: Path, progress: Progress | None = None
) -> tuple[BurnDisc, int]:
    """Render a planned disc to CD-ready WAVs; returns (burn disc, pregap sectors).

    Measures lengths first and refuses to render a disc that can't be burned.
    Rendered lengths are authoritative: they are saved back to the library, and
    the disc is checked against capacity once more before returning.
    """
    say = progress or (lambda _msg: None)
    summary = lib.disc(disc_id)
    entries = lib.disc_entries(disc_id)
    measure_tracks(lib, [e.track.id for e in entries])
    problems = disc_problems(lib, disc_id)
    if problems:
        raise BuildError("; ".join(problems))

    burn_disc = BurnDisc(
        title=summary.name,
        performer=_performer([e.track.artist for e in lib.disc_entries(disc_id)]),
    )
    workdir.mkdir(parents=True, exist_ok=True)
    for e in lib.disc_entries(disc_id):
        say(f"rendering {e.position + 1}/{summary.track_count}: {e.track.title}")
        wav = workdir / f"{e.position + 1:02d}.wav"
        try:
            sectors = render_track(Path(e.track.path), wav)
        except RenderError as exc:
            raise BuildError(f"#{e.position + 1} {e.track.title}: {exc}") from exc
        lib.set_cd_sectors(e.track.id, sectors)
        burn_disc.tracks.append(DiscTrack(wav, e.track.title, e.track.artist, sectors))

    used = disc_used_sectors(
        (t.sectors for t in burn_disc.tracks), summary.pregap_sectors
    )
    if used > summary.capacity_sectors:
        raise BuildError(
            f"rendered audio is {format_msf(used)}, over the"
            f" {format_msf(summary.capacity_sectors)} capacity"
        )
    return burn_disc, summary.pregap_sectors
