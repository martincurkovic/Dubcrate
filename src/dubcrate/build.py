"""Turn a planned disc from the library into something a burner can burn."""

import logging
import sys
import wave
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

import numpy as np

from dubcrate import mp3, wave_model
from dubcrate.burn.base import DataDisc, DiscTrack, MediaInfo
from dubcrate.burn.base import Disc as BurnDisc
from dubcrate.cancel import CancelToken
from dubcrate.cd import MAX_TRACKS, disc_used_sectors, format_msf
from dubcrate.library.lengths import measure_tracks
from dubcrate.library.levels import ensure_loudness
from dubcrate.library.models import DiscImage, DiscKind
from dubcrate.library.store import Library
from dubcrate.loudness import (
    PEAK_CEILING_DB,
    Gain,
    Loudness,
    Target,
    describe_gains,
    gain_for,
)
from dubcrate.mp3 import format_amount
from dubcrate.render import RenderError, ToolMissingError, render_mp3, render_track

Progress = Callable[[str], None]

log = logging.getLogger(__name__)


class BuildError(RuntimeError):
    pass


def disc_problems(lib: Library, disc_id: int) -> list[str]:
    """Reasons the disc can't be burned as planned (empty list means it can)."""
    summary = lib.disc(disc_id)
    entries = lib.disc_entries(disc_id)
    problems: list[str] = []
    if not entries:
        problems.append("the disc is empty")
    if summary.kind == "audio" and len(entries) > MAX_TRACKS:
        problems.append(f"{len(entries)} tracks; a CD holds at most {MAX_TRACKS}")
    for e in entries:
        label = f"#{e.position + 1} {e.artist} - {e.title}".strip()
        if e.track.stale:
            problems.append(f"{label}: no longer in its source")
        if e.track.missing:
            problems.append(f"{label}: {_missing_file_text(e.track.path)}")
    if summary.kind == "mp3":
        dirs = {d.id: d.name for d in lib.disc_dirs(disc_id)}
        counts: dict[int | None, int] = {}
        for e in entries:
            counts[e.dir_id] = counts.get(e.dir_id, 0) + 1
        for dir_id, n in counts.items():
            if n > mp3.MAX_FILES_PER_FOLDER:
                where = f"the folder “{dirs[dir_id]}”" if dir_id else "the disc's root"
                problems.append(
                    f"{n} files in {where}; a folder holds at most {mp3.MAX_FILES_PER_FOLDER}"
                )
    if summary.over:
        problems.append(
            f"over capacity by {format_amount(-summary.free_sectors, summary.kind)}"
            f" ({format_amount(summary.used_sectors, summary.kind)}"
            f" of {format_amount(summary.capacity_sectors, summary.kind)})"
        )
    return problems


def media_problems(
    used_sectors: int, media: MediaInfo, kind: DiscKind = "audio"
) -> list[str]:
    """Reasons the disc in the drive can't take `used_sectors` (of audio, or of MP3 data).

    Only what the drive is sure about counts: an unknown (None) or a free
    space of zero is not a reason to refuse, because a drive that can't say
    isn't the same as a disc that can't hold it.
    """
    if media.present is False:
        return ["no disc in the drive"]
    free = media.free_sectors
    if free and used_sectors > free:
        over = format_amount(used_sectors - free, kind)
        return [
            f"disc over capacity by {over}, please use a larger disc or remove tracks to fit"
        ]
    return []


def media_notes(media: MediaInfo, kind: DiscKind = "audio") -> list[str]:
    """What to tell the user about the disc in the drive, before burning."""
    notes = []
    if media.blank is False:
        notes.append("the disc in the drive doesn't look blank")
    if media.free_sectors:
        notes.append(
            f"the disc in the drive holds {format_amount(media.free_sectors, kind)}"
        )
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
    dir_id: int | None = None  # the folder on an MP3 disc; None: the root
    gain_db: float = (
        0.0  # level matching: a plain volume change for this track (audio CDs)
    )
    wave_needed: bool = (
        False  # no CDJ waveform cached yet: work it out from the rendered audio
    )


@dataclass(frozen=True)
class PlanDir:
    """A folder on an MP3 disc, as plain data."""

    id: int
    parent_id: int | None
    name: str


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
    kind: DiscKind = "audio"
    mp3_bitrate: int | None = None
    dirs: tuple[PlanDir, ...] = ()  # parents before their children
    levels_note: str = ""  # what level matching did, in words ("" when it is off)
    levels_short: str = (
        ""  # the same without the target, for the burn window's one line
    )


@dataclass(frozen=True)
class Rendered:
    disc: BurnDisc | DataDisc
    pregap_sectors: int
    lengths: dict[
        int, int
    ]  # track id -> rendered CD sectors, to store afterwards (audio only)
    waveforms: dict[int, list[int]] = field(
        default_factory=dict
    )  # track id -> the CDJ waveform

    @property
    def used_sectors(self) -> int:
        """What the rendered disc really takes, in its own unit (audio or data sectors)."""
        if isinstance(self.disc, DataDisc):
            return self.disc.sectors
        return disc_used_sectors(
            (t.sectors for t in self.disc.tracks), self.pregap_sectors
        )


def plan_burn(lib: Library, disc_id: int) -> BurnPlan:
    """Snapshot a disc for burning; raises BuildError if it can't be burned.

    The text for the CD is the disc's own (what was edited for it), else the track's.
    """
    problems = disc_problems(lib, disc_id)
    if problems:
        raise BuildError("; ".join(problems))
    summary = lib.disc(disc_id)
    entries = lib.disc_entries(disc_id)
    return BurnPlan(
        disc_id=disc_id,
        name=summary.name,
        performer=_performer([e.artist for e in entries]),
        pregap_sectors=summary.pregap_sectors,
        capacity_sectors=summary.capacity_sectors,
        tracks=tuple(
            PlanTrack(
                e.track.id,
                e.track.path,
                e.title,
                e.artist,
                e.dir_id,
                wave_needed=summary.kind == "audio"
                and e.track.waveform is None
                and not e.track.missing,
            )
            for e in entries
        ),
        kind=summary.kind,
        mp3_bitrate=summary.mp3_bitrate,
        dirs=tuple(PlanDir(d.id, d.parent_id, d.name) for d in lib.disc_dirs(disc_id)),
    )


def apply_levels(
    plan: BurnPlan, measured: dict[int, Loudness], target: Target
) -> BurnPlan:
    """`plan` with a gain on each track that has been measured (`measured`: track id to loudness), so
    that they come out at `target` (a loudness, or each one's peak). A track that was not measured keeps no gain, and the
    note says so. Only audio CDs are matched: an MP3 disc is returned as it is."""
    if plan.kind != "audio":
        return plan
    gains: dict[int, Gain] = {}
    tracks = []
    for t in plan.tracks:
        loud = measured.get(t.track_id)
        if loud is None:
            tracks.append(replace(t, gain_db=0.0))
            continue
        gains[t.track_id] = gain_for(loud, target)
        tracks.append(replace(t, gain_db=gains[t.track_id].db))
    unmeasured = len(plan.tracks) - sum(1 for t in plan.tracks if t.track_id in gains)
    # (a track repeated on the disc is one measurement, listed each time it plays)
    listed = [gains[t.track_id] for t in plan.tracks if t.track_id in gains]
    what = "Peak-normalised" if target.peak else "Levels matched"
    to = f"to {PEAK_CEILING_DB:g} dBTP" if target.peak else f"to {target}"
    gains_text = describe_gains(listed, unmeasured, target)
    note = f"{what} {to}: {gains_text}"
    short = gains_text[:1].upper() + gains_text[1:]
    return replace(plan, tracks=tuple(tracks), levels_note=note, levels_short=short)


def render_plan(
    plan: BurnPlan,
    workdir: Path,
    progress: Progress | None = None,
    cancel: CancelToken | None = None,
) -> Rendered:
    """Render a plan to CD-ready WAVs. Safe on a worker thread: no library access.

    Rendered lengths are authoritative, so the disc is checked against its
    capacity once more here. If `cancel` is set, the track being made is stopped and removed and
    `Cancelled` is raised: nothing is written anywhere but `workdir`, which the caller owns.
    """
    say = progress or (lambda _msg: None)
    if plan.kind == "mp3":
        return _render_mp3_plan(plan, workdir, say, cancel)
    burn_disc = BurnDisc(title=plan.name, performer=plan.performer)
    workdir.mkdir(parents=True, exist_ok=True)
    lengths: dict[int, int] = {}
    waveforms: dict[int, list[int]] = {}
    for number, t in enumerate(plan.tracks, start=1):
        if cancel is not None:
            cancel.check()
        gain = f" ({t.gain_db:+.1f} dB)" if t.gain_db else ""
        say(f"rendering {number}/{len(plan.tracks)}: {t.title}{gain}")
        wav = workdir / f"{number:02d}.wav"
        try:
            sectors = render_track(Path(t.path), wav, t.gain_db, cancel)
        except ToolMissingError as exc:
            raise BuildError(str(exc)) from exc
        except RenderError as exc:
            raise BuildError(f"#{number} {t.title}: {exc}") from exc
        lengths[t.track_id] = sectors
        if (
            t.wave_needed
            and t.track_id not in waveforms
            and (values := _waveform_of(wav)) is not None
        ):
            waveforms[t.track_id] = values
        burn_disc.tracks.append(DiscTrack(wav, t.title, t.artist, sectors))

    used = disc_used_sectors((t.sectors for t in burn_disc.tracks), plan.pregap_sectors)
    if used > plan.capacity_sectors:
        raise BuildError(
            f"rendered audio is {format_msf(used)}, over the"
            f" {format_msf(plan.capacity_sectors)} capacity"
        )
    return Rendered(burn_disc, plan.pregap_sectors, lengths, waveforms)


def _waveform_of(wav: Path) -> list[int] | None:
    """The CDJ waveform (`wave_model`) of a rendered track, so Sync has nothing left to analyse: the burn
    has just decoded the track, so this costs a fraction of a second. **Never an error**: a burn must
    not fail for a waveform; None means Sync works it out later."""
    try:
        with wave.open(str(wav), "rb") as w:
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        return wave_model.column_values(audio.reshape(-1, 2))
    except (OSError, EOFError, wave.Error, ValueError) as exc:
        log.warning("no waveform for %s: %s", wav.name, exc)
        return None


def remember_rendered(
    lib: Library, lengths: Mapping[int, int], waveforms: Mapping[int, Sequence[int]]
) -> None:
    """Keep what a render found out about its tracks: the exact CD lengths and the CDJ waveforms.
    On the thread that owns the library."""
    for track_id, sectors in lengths.items():
        lib.set_cd_sectors(track_id, sectors)
    for track_id, values in waveforms.items():
        lib.set_waveform(track_id, values)


WINDOWS_PATH_LIMIT = 259  # MAX_PATH is 260, counting the terminating NUL


def _missing_file_text(path: str) -> str:
    """Why a track's file is not found; on Windows a path past the limit is the likely reason (Windows says "not
    found" for a file it can't open by a path that long, which the library lists happily: seen 2026-11-24)."""
    if sys.platform == "win32" and len(path) > WINDOWS_PATH_LIMIT:
        return (
            f"file not found, but its path is {len(path)} characters and Windows stops at {WINDOWS_PATH_LIMIT} "
            f"({path}). Move the file (or its folders) somewhere with a shorter path."
        )
    return f"file not found ({path})"


def path_too_long(paths: Iterable[Path]) -> Path | None:
    """The first of `paths` Windows can't make (longer than `WINDOWS_PATH_LIMIT`), else None; always
    None on any other system. An MP3 disc nested 8 folders deep, each with a 60-character name, goes
    past it (Python and ffmpeg both stop at 260 unless a Windows setting that most machines don't have is on)."""
    if sys.platform != "win32":
        return None
    return next((p for p in paths if len(str(p)) > WINDOWS_PATH_LIMIT), None)


def path_too_long_message(path: Path) -> str:
    return (
        f"Windows can't make a file whose whole path is longer than {WINDOWS_PATH_LIMIT} characters, "
        f"and this one is {len(str(path))}: “{path}”. Give the disc's folders shorter names, or nest "
        "them less, then try again."
    )


def image_record(disc: DataDisc) -> DiscImage | None:
    """What to keep of the image an MP3 disc was just burned from (`Library.set_disc_image`), or None when the
    burner didn't say (it has no layout: DiscRecording makes its own image, whose date we don't control)."""
    if disc.layout is None or disc.created is None:
        return None
    layout = disc.layout
    return DiscImage(
        disc.created.isoformat(timespec="seconds"),
        layout.label or disc.title,
        layout.file_count,
        layout.folders,
        layout.last_file_start,
        layout.sectors,
    )


def _render_mp3_plan(
    plan: BurnPlan, workdir: Path, say: Progress, cancel: CancelToken | None = None
) -> Rendered:
    """Stage an MP3 disc: a folder named for the disc, holding its own folders, and in each
    folder one MP3 per track numbered from 1 (so a player that sorts by name keeps the order)."""
    folder = workdir / mp3.volume_name(plan.name)
    where: dict[int | None, Path] = {None: folder}
    for d in plan.dirs:  # parents first
        where[d.id] = where[d.parent_id] / mp3.dir_name(d.name)
    numbers: dict[int | None, int] = {}
    destinations: list[Path] = []
    for t in plan.tracks:
        number = numbers[t.dir_id] = numbers.get(t.dir_id, 0) + 1
        destinations.append(where[t.dir_id] / mp3.file_name(number, t.artist, t.title))
    if too_long := path_too_long([*where.values(), *destinations]):
        raise BuildError(path_too_long_message(too_long))
    folder.mkdir(parents=True, exist_ok=True)
    data = DataDisc(
        title=folder.name, folder=folder, created=datetime.now().astimezone()
    )
    for d in plan.dirs:
        where[d.id].mkdir(
            exist_ok=True
        )  # (empty folders are kept: they are part of the plan)
    used = mp3.FOLDER_OVERHEAD_SECTORS * len(plan.dirs)
    for done, (t, dst) in enumerate(zip(plan.tracks, destinations), start=1):
        if cancel is not None:
            cancel.check()
        say(f"preparing {done}/{len(plan.tracks)}: {t.title}")
        try:
            size = render_mp3(
                Path(t.path),
                dst,
                title=t.title,
                artist=t.artist,
                album=plan.name,
                number=number,
                max_bitrate=plan.mp3_bitrate or mp3.DEFAULT_BITRATE,
                cancel=cancel,
            )
        except ToolMissingError as exc:
            raise BuildError(str(exc)) from exc
        except RenderError as exc:
            raise BuildError(f"#{done} {t.title}: {exc}") from exc
        data.files.append(dst)
        used += mp3.data_sectors(size)
    data.sectors = used + mp3.FILESYSTEM_OVERHEAD_SECTORS
    used = data.sectors
    if used > plan.capacity_sectors:
        raise BuildError(
            f"the MP3 files add up to {mp3.format_size(used)}, over the"
            f" {mp3.format_size(plan.capacity_sectors)} capacity"
        )
    return Rendered(data, 0, {})


def planned_with_levels(lib: Library, disc_id: int, target: Target | None) -> BurnPlan:
    """`plan_burn`, with the levels brought to `target` if that is given (None: off): measures what
    has not been measured yet, which takes a moment. For the library's own thread."""
    plan = plan_burn(lib, disc_id)
    if target is None or plan.kind != "audio":
        return plan
    problems = ensure_loudness(lib, [t.track_id for t in plan.tracks])
    if problems:
        raise BuildError("couldn't measure the levels: " + "; ".join(problems))
    measured = {
        t.id: t.loudness
        for t in lib.tracks_by_ids(t.track_id for t in plan.tracks)
        if t.loudness is not None
    }
    return apply_levels(plan, measured, target)


def build_burn_disc(
    lib: Library,
    disc_id: int,
    workdir: Path,
    progress: Progress | None = None,
    target: Target | None = None,
) -> tuple[BurnDisc | DataDisc, int]:
    """Render a planned disc to CD-ready WAVs (or, for an MP3 disc, a folder of MP3s);
    returns (the disc to burn, pregap sectors).

    Measures lengths first and refuses to render a disc that can't be burned.
    Rendered lengths are saved back to the library. For a worker thread, use
    `plan_burn` here and `render_plan` there instead. `target` turns level matching on.
    """
    if lib.disc(disc_id).kind == "audio":  # an MP3 disc is sized from file sizes
        measure_tracks(lib, [e.track.id for e in lib.disc_entries(disc_id)])
    rendered = render_plan(planned_with_levels(lib, disc_id, target), workdir, progress)
    remember_rendered(lib, rendered.lengths, rendered.waveforms)
    return rendered.disc, rendered.pregap_sectors
