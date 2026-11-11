"""Turn a planned disc from the library into something a burner can burn."""

from collections.abc import Callable
from dataclasses import dataclass
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


@dataclass(frozen=True)
class PlanTrack:
    track_id: int
    path: str
    title: str
    artist: str


@dataclass(frozen=True)
class BurnPlan:
    """A planned disc as plain data, so it can be rendered on a worker thread
    without touching the library (which belongs to the GUI thread)."""

    disc_id: int
    name: str
    performer: str
    pregap_sectors: int
    capacity_sectors: int
    tracks: tuple[PlanTrack, ...]


@dataclass(frozen=True)
class Rendered:
    disc: BurnDisc
    pregap_sectors: int
    lengths: dict[int, int]  # track id -> rendered sectors, to store afterwards


def plan_burn(lib: Library, disc_id: int) -> BurnPlan:
    """Snapshot a disc for burning; raises BuildError if it can't be burned."""
    problems = disc_problems(lib, disc_id)
    if problems:
        raise BuildError("; ".join(problems))
    summary = lib.disc(disc_id)
    entries = lib.disc_entries(disc_id)
    return BurnPlan(
        disc_id=disc_id,
        name=summary.name,
        performer=_performer([e.track.artist for e in entries]),
        pregap_sectors=summary.pregap_sectors,
        capacity_sectors=summary.capacity_sectors,
        tracks=tuple(
            PlanTrack(e.track.id, e.track.path, e.track.title, e.track.artist)
            for e in entries
        ),
    )


def render_plan(
    plan: BurnPlan, workdir: Path, progress: Progress | None = None
) -> Rendered:
    """Render a plan to CD-ready WAVs. Safe on a worker thread: no library access.

    Rendered lengths are authoritative, so the disc is checked against its
    capacity once more here.
    """
    say = progress or (lambda _msg: None)
    burn_disc = BurnDisc(title=plan.name, performer=plan.performer)
    workdir.mkdir(parents=True, exist_ok=True)
    lengths: dict[int, int] = {}
    for number, t in enumerate(plan.tracks, start=1):
        say(f"rendering {number}/{len(plan.tracks)}: {t.title}")
        wav = workdir / f"{number:02d}.wav"
        try:
            sectors = render_track(Path(t.path), wav)
        except RenderError as exc:
            raise BuildError(f"#{number} {t.title}: {exc}") from exc
        lengths[t.track_id] = sectors
        burn_disc.tracks.append(DiscTrack(wav, t.title, t.artist, sectors))

    used = disc_used_sectors((t.sectors for t in burn_disc.tracks), plan.pregap_sectors)
    if used > plan.capacity_sectors:
        raise BuildError(
            f"rendered audio is {format_msf(used)}, over the"
            f" {format_msf(plan.capacity_sectors)} capacity"
        )
    return Rendered(burn_disc, plan.pregap_sectors, lengths)


def build_burn_disc(
    lib: Library, disc_id: int, workdir: Path, progress: Progress | None = None
) -> tuple[BurnDisc, int]:
    """Render a planned disc to CD-ready WAVs; returns (burn disc, pregap sectors).

    Measures lengths first and refuses to render a disc that can't be burned.
    Rendered lengths are saved back to the library. For a worker thread, use
    `plan_burn` here and `render_plan` there instead.
    """
    measure_tracks(lib, [e.track.id for e in lib.disc_entries(disc_id)])
    rendered = render_plan(plan_burn(lib, disc_id), workdir, progress)
    for track_id, sectors in rendered.lengths.items():
        lib.set_cd_sectors(track_id, sectors)
    return rendered.disc, rendered.pregap_sectors
