"""An MP3 disc from the library to a card plan: what to gather, and what to work out (the library half of
`mp3_sync`).

Two stages, because the library belongs to the GUI thread and the rest is slow:

1. `collect_request` (on the library's thread): the disc as plain data, with each track's cues (in audio time, with
   the memory cues edited in Dubcrate laid over rekordbox's, as for an audio disc) and its cached waveform.
2. `prepare_disc` (on a worker, nothing touches the library): renders the disc's files as a burn does, makes the image
   and reads its layout (the three numbers the card's disc ID is checked by), reads every file's frame headers, turns
   the cues into the player's positions, and analyses any waveform not cached. Returns what `plan_mp3_disc` needs and
   the waveforms found, for the library's thread to store.

**The rendered files, not the library's, are what a cue's byte offset is in**: ffmpeg rewrites every file's tag and Info
frame on its way to a disc (`render.render_mp3`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from dubcrate import cdj_card_mp3 as m3
from dubcrate import cue_timing, iso
from dubcrate.analysis import Cue
from dubcrate.audio.decode import DecodeError
from dubcrate.build import BuildError, BurnPlan, plan_burn, render_plan
from dubcrate.burn.base import DataDisc
from dubcrate.cancel import CancelToken
from dubcrate.card_discs import RekordboxCues
from dubcrate.card_sync import map_cues
from dubcrate.library import Library
from dubcrate.library.models import DiscSummary
from dubcrate.mp3_sync import Mp3DiscInput, Mp3TrackInput

log = logging.getLogger(__name__)


class Mp3SyncError(RuntimeError):
    """Why an MP3 disc can't be synced, in plain words (an expected error)."""


@dataclass
class Mp3Request:
    """A disc as plain data, gathered on the library's thread."""

    disc_id: int
    plan: BurnPlan
    cues: dict[int, list[Cue]] = field(
        default_factory=dict
    )  # track id -> cues in audio time
    waves: dict[int, list[int] | None] = field(
        default_factory=dict
    )  # track id -> the cached waveform

    @property
    def name(self) -> str:
        return self.plan.name


@dataclass
class Prepared:
    disc: Mp3DiscInput
    analysed: dict[int, list[int]] = field(
        default_factory=dict
    )  # track id -> a waveform just worked out


def collect_request(
    lib: Library, disc_id: int, rekordbox: RekordboxCues | None = None
) -> Mp3Request:
    """The disc's plan, cues and cached waveforms. Raises `Mp3SyncError` if it isn't an MP3 disc that can be burned."""
    if lib.disc(disc_id).kind != "mp3":
        raise Mp3SyncError(
            "That is an audio disc: it is synced the other way (cues by its TOC)."
        )
    try:
        plan = plan_burn(lib, disc_id)
    except BuildError as exc:
        raise Mp3SyncError(str(exc)) from exc
    if any(d.parent_id is not None for d in plan.dirs):
        raise Mp3SyncError(
            "Folders inside folders aren't supported on the card yet: how the player numbers them isn't known."
        )
    rekordbox = rekordbox or RekordboxCues(lib)
    request = Mp3Request(disc_id, plan)
    by_id = {t.id: t for t in lib.tracks_by_ids(t.track_id for t in plan.tracks)}
    for planned in plan.tracks:
        track = by_id[planned.track_id]
        request.cues[track.id] = list(rekordbox.for_track(track))
        request.waves[track.id] = list(track.waveform) if track.waveform else None
    return request


def positions(
    cues: Sequence[Cue], lead_seconds: float, length_frames: int
) -> tuple[list[tuple[int, int]], list[str]]:
    """Cues in audio time as (position, end) in 1/75 s frames **in the player's time for this file**: later by
    `lead_seconds`, which is the Info frame the player counts as audio (and for a file with an encoder delay the
    delay too, `cue_timing`). A memory cue, a hot cue (any slot) and a loop all become memory cues and loops, as for an
    audio disc (`card_sync.map_cues`, which also drops what can't be placed). **The player's time is not yet measured
    against ours** (a click track would): this is the rekordbox convention, and may be a few tens of milliseconds off."""
    shifted = [
        replace(
            c,
            time=c.time + lead_seconds,
            end=None if c.end is None else c.end + lead_seconds,
        )
        for c in cues
    ]
    mapped = map_cues({1: shifted}, [0], length_frames)
    return [(w.position, w.end) for w in mapped.cues], list(mapped.notes)


def prepare_disc(
    request: Mp3Request,
    workdir: Path,
    say: Callable[[str], None] | None = None,
    cancel: CancelToken | None = None,
    waves: bool = True,
    analyse: Callable[[str], list[int]] | None = None,
) -> Prepared:
    """Stage two, on a worker: render, lay out the image, read the frames, place the cues. `workdir` is the caller's.

    Raises `Mp3SyncError` for anything that stops the disc being synced (a file that isn't MPEG-1 Layer III, a folder
    inside a folder) and `Cancelled`. A waveform not cached is analysed from the rendered file, as a burn does,
    unless `waves` is False."""
    say = say or (lambda _m: None)
    if analyse is None:
        from dubcrate.library.waves import analyse as analyse_file

        analyse = analyse_file
    try:
        rendered = render_plan(request.plan, workdir, say, cancel)
        data = rendered.disc
        if not isinstance(data, DataDisc):  # pragma: no cover
            raise Mp3SyncError("That is not an MP3 disc.")
        say("making the disc image to find where its files are…")
        image = iso.build_iso(data.folder, data.title, workdir / "disc.iso")
        layout = iso.image_layout(image, data.folder)
    except BuildError as exc:
        raise Mp3SyncError(str(exc)) from exc
    except iso.IsoError as exc:
        raise Mp3SyncError(str(exc)) from exc
    where = layout.by_path()
    disc = Mp3DiscInput(
        request.name, layout.file_count, layout.last_file_start, layout.sectors, []
    )
    analysed: dict[int, list[int]] = {}
    for done, (planned, path) in enumerate(
        zip(request.plan.tracks, data.files, strict=True), 1
    ):
        if cancel is not None:
            cancel.check()
        say(f"reading {done}/{len(data.files)}: {planned.title}")
        placed = where[path]
        try:
            table = m3.frame_table(path)
        except m3.Mp3FrameError as exc:
            raise Mp3SyncError(f"“{planned.title}”: {exc}") from exc
        lead = cue_timing.lead_ms(path) / 1000.0
        cue_positions, notes = positions(
            request.cues.get(planned.track_id, ()), lead, table.length_frames
        )
        disc.notes += [f"“{planned.title}”: {n}" for n in notes]
        wave = request.waves.get(planned.track_id)
        if wave is None and waves:
            try:
                wave = analyse(str(path))
                analysed[planned.track_id] = wave
            except DecodeError as exc:  # (say so and go on without it)
                log.warning("no waveform for %s: %s", planned.title, exc)
                disc.notes.append(f"“{planned.title}”: no waveform ({exc}).")
        disc.tracks.append(
            Mp3TrackInput(
                placed.folder, placed.file, planned.title, table, wave, cue_positions
            )
        )
    return Prepared(disc, analysed)


@dataclass
class Mp3DiscRow:
    """An MP3 disc as the Sync tab's list shows it: the same names an audio disc's survey has (`disc`, `status`,
    `detail`, `writable`) so the list code treats them alike. What a sync would do for it isn't known until its
    files are rendered (that is slow, and it needs the card's entry), so the row says it is checked at sync time;
    `last` is what the last sync in this session did, if there was one."""

    disc: DiscSummary
    path: str  # "Folder / Disc"
    problems: list[str] = field(
        default_factory=list
    )  # why it can never be synced (a folder inside a folder)
    has_card: bool = False  # a card is chosen
    card_has_mp3: bool = (
        False  # ... and it has MP3 disc files (the player has played an MP3 disc)
    )
    last: str = ""

    @property
    def writable(self) -> bool:
        """Whether it can be ticked: a card with the player's MP3 files is chosen and nothing rules the disc out."""
        return not self.problems and self.has_card and self.card_has_mp3

    @property
    def status(self) -> str:
        if self.problems:
            return "Can't sync"
        if not self.has_card:
            return "Choose a card"
        if not self.card_has_mp3:
            return "Play an MP3 disc in the player first"
        return self.last or "MP3 disc: checked when you sync"

    def detail(self, tracks: int | None = None) -> list[str]:
        count = tracks if tracks is not None else self.disc.track_count
        out = [self.path, f"{count} track{'s' if count != 1 else ''}, an MP3 disc"]
        out += [f"Can't sync: {p}" for p in self.problems]
        if not self.problems and self.has_card and not self.card_has_mp3:
            out.append(
                "The card has no MP3 disc files yet: the player makes them when it first plays an MP3 disc."
            )
        elif not self.problems:
            out.append(
                "What a sync adds is worked out when you press Sync (the disc's files are made as a burn makes them "
                "to find where each is). The player must have played this disc once, so the card has an entry for it."
            )
        return out


def survey_mp3_discs(
    lib: Library,
    paths: dict[int, str],
    card_state: tuple[bool, bool],
    last: dict[int, str] | None = None,
) -> list[Mp3DiscRow]:
    """Every MP3 disc with a track as a row. `card_state` is (a card is chosen, it has MP3 disc files)."""
    has_card, card_has_mp3 = card_state
    rows = []
    for disc in lib.discs():
        if disc.kind != "mp3" or disc.track_count == 0:
            continue
        problems = []
        if any(d.parent_id is not None for d in lib.disc_dirs(disc.id)):
            problems.append(
                "folders inside folders aren't supported on the card yet: how the player numbers them isn't known."
            )
        rows.append(
            Mp3DiscRow(
                disc,
                paths.get(disc.id, disc.name),
                problems,
                has_card,
                card_has_mp3,
                (last or {}).get(disc.id, ""),
            )
        )
    return rows
