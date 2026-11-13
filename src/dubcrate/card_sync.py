"""Plan what to write to the CDJ-1000MK3's card for some discs (Qt-free, no files touched).

The owner's rules (`dev_docs/card-sync.md`): **every rekordbox cue becomes a memory cue** (a loop
a memory loop), the card's three hot cue slots are never written or touched, and cues already on
the card are **kept**: ours are added around them, skipping what is already there.

Three steps, each a pure function:

* `map_cues`: rekordbox's cues for a disc's tracks -> `WantedCue`s at card positions (the
  track's start in the disc's TOC plus the time in the track, in 1/75 s frames).
* `merge_cues`: those into the cues a disc's file already holds.
* `plan_card`: a whole plan for a card (which discs get a new entry, a new file or new cues), with
  what would be written, ready for `cdj_card.write_card`.

Whether the player accepts a file written this way is **not yet shown** (Phase 0 of the plan).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum

from dubcrate import cdj_card as cc
from dubcrate.analysis import Cue

# Two cues at the same track and kind within this many frames (1/75 s) are one: rekordbox's are
# milliseconds and the player's are frames, so a rounding of one frame must not make a second cue.
DUPLICATE_FRAMES = 2


@dataclass(frozen=True)
class WantedCue:
    """A cue to put on the card, at an absolute position; a loop has an `end`."""

    track: int
    position: int
    end: int = 0

    @property
    def is_loop(self) -> bool:
        return self.end > 0


@dataclass
class Mapped:
    cues: list[WantedCue]
    notes: list[str] = field(default_factory=list)  # what was left out, in plain words


def _msf(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


def map_cues(
    cues_by_track: Mapping[int, Sequence[Cue]],
    track_starts: Sequence[int],
    lead_out: int,
) -> Mapped:
    """rekordbox's cues for a disc (by 1-based track number) as cues at card positions.

    A cue's position is the track's start in the disc's TOC plus its time. Memory cues, hot cues
    (any slot) and loops all become cues, a loop keeping its end; labels and colours are dropped
    (the card has no field). Cues that can't be placed (after the track's end, a loop with no
    length) are left out and said so in `notes`; a hot cue and a memory cue at the same place give
    one cue. Ordered by track, then time.
    """
    result = Mapped([])
    for track in sorted(cues_by_track):
        if not 1 <= track <= len(track_starts):
            result.notes.append(
                f"Track {track} is not on this disc, so its cues were skipped."
            )
            continue
        start = track_starts[track - 1]
        stop = track_starts[track] if track < len(track_starts) else lead_out
        for cue in sorted(cues_by_track[track], key=lambda c: c.time):
            where = f"track {track} at {_msf(cue.time)}"
            if cue.time < 0:
                result.notes.append(f"The cue in {where} is before the track starts.")
                continue
            position = start + round(cue.time * cc.FRAMES_PER_SECOND)
            if position >= stop:
                result.notes.append(f"The cue in {where} is past the end of the track.")
                continue
            end = 0
            if cue.kind == "loop":
                end = (
                    start + round(cue.end * cc.FRAMES_PER_SECOND)
                    if cue.end is not None
                    else 0
                )
                if end <= position or end > stop:
                    result.notes.append(f"The loop in {where} has no usable end.")
                    continue
            wanted = WantedCue(track, position, end)
            if any(_same(w.track, w.is_loop, w.position, wanted) for w in result.cues):
                continue
            result.cues.append(wanted)
    return result


def _same(track: int, is_loop: bool, position: int, wanted: WantedCue) -> bool:
    return (
        track == wanted.track
        and is_loop == wanted.is_loop
        and abs(position - wanted.position) <= DUPLICATE_FRAMES
    )


@dataclass
class Merge:
    cues: list[cc.CueEntry]  # the whole new list: what was there, unchanged, then ours
    added: list[cc.CueEntry]
    already_there: int  # wanted cues that were on the card already
    overflow: list[WantedCue]  # wanted, but the disc is at the player's limit


def merge_cues(existing: Sequence[cc.CueEntry], wanted: Sequence[WantedCue]) -> Merge:
    """Add `wanted` after `existing`, which is never changed, reordered or removed.

    A wanted cue within `DUPLICATE_FRAMES` of one already there (same track and kind) is skipped.
    Past `MAX_CUES` the rest is reported in `overflow`, not dropped silently. New entries are
    numbered on from the highest index.
    """
    cues = list(existing)
    result = Merge(cues, [], 0, [])
    for want in wanted:
        if any(_same(c.track, c.is_loop, c.position, want) for c in cues):
            result.already_there += 1
            continue
        if len(cues) >= cc.MAX_CUES:
            result.overflow.append(want)
            continue
        index = max((c.index for c in cues), default=-1) + 1
        entry = (
            cc.CueEntry.loop(index, want.position, want.end, want.track)
            if want.is_loop
            else cc.CueEntry.memory(index, want.position, want.track)
        )
        cues.append(entry)
        result.added.append(entry)
    return result


class Status(Enum):
    NEW_DISC = "not on the card"  # an index entry and a file are made
    NEW_FILE = (
        "on the card, no file yet"  # the player makes a file later; we make one now
    )
    UPDATE = "cues to add"
    IN_SYNC = "already there"
    NOTHING = "no cues to write"  # no entry or file is made


@dataclass
class DiscRequest:
    """One disc to sync: its ID, the cues wanted, and what `map_cues` left out."""

    disc_id: cc.DiscId
    wanted: Sequence[WantedCue]
    notes: Sequence[str] = ()


@dataclass
class DiscPlan:
    status: Status
    disc_id: cc.DiscId
    name: str | None  # the card's file name (`CDJ00001`); None when nothing is written
    file: cc.TrackFile | None  # what to write; None when nothing is
    added: list[cc.CueEntry]
    kept: int  # cues already on the card, left exactly as they are
    already_there: int
    overflow: list[WantedCue]
    notes: list[str]


@dataclass
class CardPlan:
    index: cc.IndexFile  # the index to write (a copy: the one given is never changed)
    discs: list[DiscPlan]

    def writes(self) -> dict[str, cc.TrackFile]:
        """The disc files to write, for `cdj_card.write_card`."""
        return {
            d.name: d.file
            for d in self.discs
            if d.name
            and d.file
            and d.status in (Status.NEW_DISC, Status.NEW_FILE, Status.UPDATE)
        }

    @property
    def changes(self) -> bool:
        return bool(self.writes())


def plan_card(
    index: cc.IndexFile,
    files: Mapping[str, cc.TrackFile | None],
    requests: Sequence[DiscRequest],
) -> CardPlan:
    """Plan a sync of these discs onto a card (`index` and `files` as `cdj_card.read_card` gives).

    Nothing given is changed. A disc with nothing to add gets no entry and no file. A disc already
    on the card keeps every cue, its hot cues and its waveform; only new cues are added.
    """
    work = cc.IndexFile(
        [cc.IndexEntry(e.name, e.disc_id) for e in index.entries], index.version
    )
    current = dict(files)
    plans: list[DiscPlan] = []
    for request in requests:
        entry = work.find(request.disc_id)
        existing = current.get(entry.name) if entry else None
        merge = merge_cues(existing.cues if existing else [], request.wanted)
        notes = list(request.notes)
        if not merge.added and existing is None:
            plans.append(
                DiscPlan(
                    Status.NOTHING,
                    request.disc_id,
                    entry.name if entry else None,
                    None,
                    [],
                    0,
                    merge.already_there,
                    merge.overflow,
                    notes,
                )
            )
            continue
        if entry is None:
            entry = work.add(request.disc_id)
            status = Status.NEW_DISC
        elif existing is None:
            status = Status.NEW_FILE
        else:
            status = Status.UPDATE if merge.added else Status.IN_SYNC
        base = existing or cc.TrackFile.new(request.disc_id)
        file = replace(base, cues=merge.cues, hot_cues=list(base.hot_cues))
        current[entry.name] = file  # a second request for the same disc sees this one
        plans.append(
            DiscPlan(
                status,
                request.disc_id,
                entry.name,
                file,
                merge.added,
                len(existing.cues) if existing else 0,
                merge.already_there,
                merge.overflow,
                notes,
            )
        )
    return CardPlan(work, plans)
