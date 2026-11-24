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
from datetime import datetime
from pathlib import Path

from dubcrate import cdj_card as cc
from dubcrate import cdj_card_mp3 as m3
from dubcrate import cue_timing, iso, mp3
from dubcrate.analysis import Cue
from dubcrate.audio.decode import DecodeError
from dubcrate.build import BuildError, BurnPlan, plan_burn, render_plan
from dubcrate.burn.base import DataDisc
from dubcrate.cancel import CancelToken
from dubcrate.card_discs import (
    WAVE_CARD,
    WAVE_NONE,
    WAVE_PENDING,
    WAVE_WRITE,
    RekordboxCues,
    WaveRow,
)
from dubcrate.card_sync import map_cues
from dubcrate.library import Library
from dubcrate.library.models import DiscDir, DiscEntry, DiscImage, DiscSummary
from dubcrate.mp3_sync import Mp3DiscInput, Mp3TrackInput, has_cue
from dubcrate.render import copies_mp3

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
    # The image the disc was burned from (`Library.disc_image`), or None: with it the disc's ID can be made, so the
    # card needs no entry for it yet.
    image: DiscImage | None = None

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
    request = Mp3Request(disc_id, plan, image=lib.disc_image(disc_id))
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
    `lead_seconds`, which is the Info frame the player counts as audio (for any file that has one) and the encoder
    delay too (`cue_timing.player_lead_ms`). A memory cue, a hot cue (any slot) and a loop all become memory cues and loops, as for an
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
        recorded = request.image
        # (the date it was burned with, so the image is the burned one's twin and makes the same ID)
        created = datetime.fromisoformat(recorded.created) if recorded else None
        image = iso.build_iso(data.folder, data.title, workdir / "disc.iso", created)
        layout = iso.image_layout(image, data.folder)
    except BuildError as exc:
        raise Mp3SyncError(str(exc)) from exc
    except iso.IsoError as exc:
        raise Mp3SyncError(str(exc)) from exc
    where = layout.by_path()
    disc = Mp3DiscInput(
        request.name, layout.file_count, layout.last_file_start, layout.sectors, []
    )
    if recorded is not None:
        disc.made_id, why = _made_id(recorded, layout, image, data.title)
        if why:
            disc.notes.append(why)
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
        lead = cue_timing.player_lead_ms(path) / 1000.0
        cue_positions, notes = positions(
            request.cues.get(planned.track_id, ()), lead, table.length_frames
        )
        disc.notes += [f"“{planned.title}”: {n}" for n in notes]
        wave = request.waves.get(planned.track_id)
        if wave is None and waves:
            try:
                wave = analyse(str(path))
                analysed[planned.track_id] = wave
            except (
                DecodeError,
                OSError,
                ValueError,
            ) as exc:  # (as `measure_waveforms`: say so, go on)
                log.warning("no waveform for %s: %s", planned.title, exc)
                disc.notes.append(f"“{planned.title}”: no waveform ({exc}).")
        disc.tracks.append(
            Mp3TrackInput(
                placed.folder, placed.file, planned.title, table, wave, cue_positions
            )
        )
    return Prepared(disc, analysed)


def _made_id(
    recorded: DiscImage, layout: iso.ImageLayout, image: Path, label: str
) -> tuple[m3.Mp3DiscId | None, str]:
    """(the ID the player makes for the disc, or None, and why not). The image just made has the burned image's
    date, so its ID is the burned one's, **if the disc is as it was burned**: the label and the numbers the ID has
    in it must be what was recorded at the burn, else the disc was edited (or rendered differently) since, and an
    entry made now would be for a disc that doesn't exist."""
    # (the label is the Joliet one as written; a record made by the first version kept the playlist's name instead,
    # which is the same label as far as this builder goes)
    now = (
        recorded.label if recorded.label in (layout.label, label) else layout.label,
        layout.file_count,
        layout.folders,
        layout.last_file_start,
        layout.sectors,
    )
    then = (
        recorded.label,
        recorded.file_count,
        recorded.folders,
        recorded.last_file_start,
        recorded.sectors,
    )
    if now != then:
        return None, (
            "The disc is not as it was burned (its files, their order or its name have changed, or it was rendered "
            "differently), so the player's entry for it can't be made here: play the disc once, then sync."
        )
    try:
        return (
            m3.Mp3DiscId.made_from(
                iso.joliet_descriptor(image),
                file_count=layout.file_count,
                folder_count=layout.folders,
                last_file_start=layout.last_file_start,
            ),
            "",
        )
    except (m3.Mp3CardError, iso.IsoError) as exc:
        return None, f"The player's ID for the disc couldn't be made: {exc}"


# The player's lead (`cue_timing.player_lead_ms`) for a file `render_mp3` ENCODES (any source that isn't a
# constant-bitrate MP3 within the disc's rate): ffmpeg writes its own Info frame. Measured 2026-11-24 with a WAV, a
# FLAC and a variable-bitrate MP3 as the source: 51 ms for each. A file it COPIES keeps its source's Info frame, so
# its lead is the source's own (checked on 12 of the owner's MP3s: the same before and after).
ENCODED_LEAD_MS = 51


@dataclass(frozen=True)
class Mp3Slot:
    """Where a track of an MP3 disc is on the card: the player names a file by (folder, file), and `track` is its
    1-based place on the disc. `lead_ms` is how much later the player's time is than the audio in the rendered
    file, and `length_frames` (1/75 s, 0 if unknown) is about how long the track is: a card entry says how long the
    track was when it was made, so a cue whose length is far from it belongs to another track."""

    track: int
    lead_ms: int
    length_frames: int = 0


_leads: dict[tuple[str, int, int, int], int] = {}


def rendered_lead_ms(path: str | Path, bitrate: int | None) -> int:
    """The player's lead for the file `render_mp3` makes from `path` at this disc's top bitrate, without rendering:
    a file that is copied has its source's, one that is encoded has `ENCODED_LEAD_MS`. Kept per file until it changes
    (reading a file's frames is the slow part). Raises `OSError` if the file can't be read."""
    rate = bitrate or mp3.DEFAULT_BITRATE
    where = Path(path)
    info = where.stat()
    key = (str(where), info.st_size, info.st_mtime_ns, rate)
    if key not in _leads:
        if len(_leads) > 20000:
            _leads.clear()
        _leads[key] = (
            cue_timing.player_lead_ms(where)
            if copies_mp3(where, rate)
            else ENCODED_LEAD_MS
        )
    return _leads[key]


def _lead_key(path: str | Path, bitrate: int | None) -> tuple[str, int, int, int]:
    info = Path(path).stat()
    return (
        str(Path(path)),
        info.st_size,
        info.st_mtime_ns,
        bitrate or mp3.DEFAULT_BITRATE,
    )


def lead_items(entries: Sequence[DiscEntry]) -> list[tuple[str, int | None]]:
    """(file, the disc's top bitrate) for every track whose lead `mp3_slots` will ask for."""
    return [(e.track.path, e.mp3_bitrate) for e in entries if not e.track.missing]


def leads_ready(items: Sequence[tuple[str, int | None]]) -> bool:
    """Whether every lead in `items` is already worked out (a file that can't be read counts as ready: it has no
    lead to wait for). Cheap: a `stat` each."""
    for path, bitrate in items:
        try:
            if _lead_key(path, bitrate) not in _leads:
                return False
        except OSError:
            continue
    return True


def warm_leads(items: Sequence[tuple[str, int | None]]) -> None:
    """Work out the leads in `items`, which reads each MP3 in full. **Run this on a worker**: with it done
    `mp3_slots` is quick, which is what the GUI thread needs. Touches no library, and ignores unreadable files."""
    for path, bitrate in items:
        try:
            rendered_lead_ms(path, bitrate)
        except OSError:
            continue


def player_frame(seconds: float, lead_ms: int) -> int:
    """A time in audio as the player's position (1/75 s): later by the lead, as `positions` puts it."""
    return round((seconds + lead_ms / 1000.0) * 75)


def mp3_slots(
    entries: Sequence[DiscEntry], dirs: Sequence[DiscDir]
) -> dict[tuple[int, int], Mp3Slot]:
    """Each (folder, file) the player will name on this disc, from the disc as it is now, with no rendering.

    The numbers are what `iso.image_layout` finds on the burned image: folder 0 is the root and the others count
    from 1 in the order of their written names (`mp3.dir_name`, A to Z without case), a file counts from 1 in its
    folder in the order of its written name (`mp3.file_name`, which starts with its place in the folder). A track
    whose file can't be read has no slot (its lead isn't known). Raises `Mp3SyncError` for a folder inside a folder,
    which isn't supported."""
    if any(d.parent_id is not None for d in dirs):
        raise Mp3SyncError("Folders inside folders aren't supported on the card yet.")
    folder_numbers = {
        d.id: n
        for n, d in enumerate(
            sorted(dirs, key=lambda d: mp3.dir_name(d.name).casefold()), 1
        )
    }
    groups: dict[int | None, list[DiscEntry]] = {}
    for entry in sorted(entries, key=lambda e: e.position):
        groups.setdefault(entry.dir_id, []).append(entry)
    slots: dict[tuple[int, int], Mp3Slot] = {}
    for dir_id, inside in groups.items():
        folder = folder_numbers.get(dir_id, 0) if dir_id is not None else 0
        written = sorted(
            (
                (mp3.file_name(n, e.artist, e.title).casefold(), e)
                for n, e in enumerate(inside, 1)
            ),
            key=lambda pair: pair[0],
        )
        for file, (_name, entry) in enumerate(written, 1):
            track = entry.track
            try:
                lead = rendered_lead_ms(track.path, entry.mp3_bitrate)
            except OSError:
                continue  # (the file can't be read just now: its lead isn't known, so its cues are left alone)
            frames = track.cd_sectors or round((track.duration or 0) * 75)
            slots[(folder, file)] = Mp3Slot(entry.position + 1, lead, frames)
    return slots


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
    # Burned by Dubcrate (a record of the image is kept), so the player's entry can be made before it has seen the disc.
    can_make: bool = False

    @property
    def writable(self) -> bool:
        """Whether it can be ticked: a card is chosen, nothing rules the disc out, and either the card has the player's
        MP3 files or the entry can be made."""
        return (
            not self.problems and self.has_card and (self.card_has_mp3 or self.can_make)
        )

    @property
    def status(self) -> str:
        if self.problems:
            return "Can't sync"
        if not self.has_card:
            return "Choose a card"
        if not self.card_has_mp3 and not self.can_make:
            return "Play an MP3 disc in the player first"
        return self.last or "MP3 disc: checked when you sync"

    def detail(self, tracks: int | None = None) -> list[str]:
        count = tracks if tracks is not None else self.disc.track_count
        out = [self.path, f"{count} track{'s' if count != 1 else ''}, an MP3 disc"]
        out += [f"Can't sync: {p}" for p in self.problems]
        if (
            not self.problems
            and self.has_card
            and not self.card_has_mp3
            and not self.can_make
        ):
            out.append(
                "The card has no MP3 disc files yet: the player makes them when it first plays an MP3 disc."
            )
        elif not self.problems:
            out.append(
                "Waveforms and cues are worked out when you press Sync. "
                + (
                    "Burned here: the card's entry for it is made if the player hasn't seen it yet."
                    if self.can_make
                    else "The player must have played this disc once, so the card has an entry for it."
                )
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
                lib.disc_image(disc.id) is not None,
            )
        )
    return rows


def mp3_wave_rows(
    lib: Library,
    disc_id: int,
    card: tuple[m3.Mp3Index, dict[str, m3.Mp3TrackFile | None]] | None = None,
    cues: RekordboxCues | None = None,
) -> list[WaveRow]:
    """The Sync tab's tracks table for an MP3 disc: each track with its folder, length, waveform and cues.

    Without the card, which waveforms it needs isn't known until the disc's files are made, so a track shows the
    waveform the library has (what a sync writes if the card has none) or says it is worked out when you sync.
    **With the card** (`card` is the MP3 index and files, `cues` is where Dubcrate's cues come from) and the disc's
    recorded image, which is how the card's file for it is found without rendering, a track shows **the waveform the
    card already has** for it (the player draws that one) and its cues: the ones on the card, red marks along the
    strip for them and for the ones a sync would add (as `mp3_sync` decides: within `DUPLICATE_FRAMES` is the same
    cue). A disc that isn't on the card, has no recorded image or no longer matches it shows what it can."""
    entries = lib.disc_entries(disc_id)
    dirs = lib.disc_dirs(disc_id)
    folders = {d.id: d.name for d in dirs}
    file = None
    slots_of: dict[int, tuple[tuple[int, int], Mp3Slot]] = {}
    if card is not None:
        file, _why = card_file_for(lib, disc_id, card, entries, dirs)
        if file is not None:
            slots_of = {
                s.track: (key, s) for key, s in mp3_slots(entries, dirs).items()
            }
    rows = []
    for entry in entries:
        folder = folders.get(entry.dir_id) if entry.dir_id is not None else None
        title = " – ".join(p for p in (entry.artist, entry.title) if p) or "Untitled"
        number = entry.position + 1
        own = entry.track.waveform
        here = slots_of.get(number)
        theirs = _card_wave(file, here[0]) if file is not None and here else None
        if entry.track.missing and theirs is None:
            state, values, note = WAVE_NONE, (), "its file is missing"
        elif theirs is not None:
            state, values = WAVE_CARD, tuple(theirs.time_values())
            note = (
                "already on the card: kept"
                if all(theirs.data)
                else "the player has part of it: a sync fills in the rest"
            )
        elif own is not None:
            state, values = WAVE_WRITE, tuple(own)
            note = "" if file is not None else "analysed: written if the card has none"
        else:
            state, values, note = WAVE_PENDING, (), "worked out when you sync"
        seconds = float(entry.track.duration or 0)
        text, marks = "", ()
        if cues is not None:
            text, marks, said = _cue_state(
                file, here, cues.for_track(entry.track), seconds
            )
            note = "; ".join(p for p in (note, said) if p)
        rows.append(
            WaveRow(
                number,
                title,
                state,
                values,
                note,
                name=f"[{folder}] {entry.title}" if folder else entry.title,
                artist=entry.artist,
                seconds=seconds,
                cues=text,
                marks=marks,
            )
        )
    return rows


NOT_RECORDED = "no record of the image the disc was burned from"
CHANGED = "the disc has changed since it was burned"
NOT_ON_CARD = "the card has no file for it"


def card_file_for(
    lib: Library,
    disc_id: int,
    card: tuple[m3.Mp3Index, dict[str, m3.Mp3TrackFile | None]],
    entries: Sequence[DiscEntry],
    dirs: Sequence[DiscDir],
) -> tuple[m3.Mp3TrackFile | None, str]:
    """(the card's file for this disc, "") or (None, why): found by the image the disc was burned from, without
    rendering anything. `why` is `NOT_RECORDED`, `CHANGED` (its file or folder count no longer matches the record)
    or `NOT_ON_CARD`. The one rule the Sync tab's table and Import Cues both go by."""
    image = lib.disc_image(disc_id)
    if image is None:
        return None, NOT_RECORDED
    if (len(entries), len(dirs)) != (image.file_count, image.folders):
        return None, CHANGED
    index, files = card
    found = index.find(image.file_count, image.last_file_start, image.sectors)
    file = files.get(found[0].name) if len(found) == 1 else None
    return (file, "") if file is not None else (None, NOT_ON_CARD)


def _card_wave(file: m3.Mp3TrackFile, key: tuple[int, int]) -> cc.WaveRecord | None:
    """The player's (or an earlier sync's) fine waveform for a track, as a record to draw, or None."""
    for record in file.fine:
        if record.key == key and any(record.data):
            return cc.WaveRecord(0, record.data)
    return None


def _cue_state(
    file: m3.Mp3TrackFile | None,
    here: tuple[tuple[int, int], Mp3Slot] | None,
    wanted: Sequence[Cue],
    seconds: float,
) -> tuple[str, tuple[float, ...], str]:
    """(the track's cues in a word, marks 0 to 1 along it, a note) for the table: Dubcrate's `wanted` cues against
    what the card has for the track. With no card file, only how many a sync would write."""
    if file is None or here is None:
        count = len(wanted)
        return (str(count) if count else "–"), (), ""
    key, slot = here
    shifted = [
        (
            player_frame(c.time, slot.lead_ms),
            player_frame(c.end, slot.lead_ms) if c.end else 0,
        )
        for c in wanted
    ]
    missing = [(p, e) for p, e in shifted if not has_cue(file, key, p, e)]
    on_card = [e for e in (*file.cues, *file.hot) if e is not None and (e.folder, e.file) == key]  # fmt: skip
    # where things fall in the track: the card's cues (its time is the player's, so the lead comes off) and ours to add
    lead = round(slot.lead_ms * 75 / 1000)
    span = (seconds * 75) or slot.length_frames
    places = [e.position - lead for e in on_card] + [p - lead for p, _e in missing]
    marks = tuple(sorted({round(min(1.0, max(0.0, p / span)), 4) for p in places})) if span else ()  # fmt: skip
    if missing:
        text = f"{len(missing)} to add"
        said = f"{len(missing)} cue{'s' if len(missing) != 1 else ''} to add"
    elif wanted:
        text, said = "on the card", "cues are on the card"
    else:
        text, said = "–", ""
    return text, marks, said
