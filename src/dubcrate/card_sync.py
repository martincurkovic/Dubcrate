"""Plan what to write to the CDJ-1000MK3's card for some discs (Qt-free, no files touched).

The owner's rules (`dev_docs/card-sync.md`): **every rekordbox cue becomes a memory cue** (a loop
a memory loop), the card's three hot cue slots are never written or touched, and cues already on
the card are **kept**: ours are added around them, skipping what is already there.

Three steps, each a pure function:

* `map_cues`: rekordbox's cues for a disc's tracks -> `WantedCue`s at card positions (the
  track's start in the disc's TOC plus the time in the track, in 1/75 s frames).
* `merge_cues`: those into the cues a disc's file already holds.
* `merge_waveforms`: a disc's waveforms (`wave_model`, one 100-value record per track) into the records
  its file already holds: **a track that already has a record keeps it** (the player's own or ours; the
  player itself never changes a column that has a value), and only a track with none gets ours.
* `plan_card`: a whole plan for a card (which discs get a new entry, a new file, new cues or new
  waveforms), with what would be written, ready for `cdj_card.write_card`.

Whether the player accepts a file written this way is **not yet shown** (Phase 0 of the plan).
"""

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum

from dubcrate import cdj_card as cc
from dubcrate import wave_model
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


def _frame(seconds: float) -> int:
    """The frame a time falls in: rounded down, so a cue is never later than where it was set (a late cue
    cuts the start of the sound off; an early one only adds a sliver of what comes before it). The small
    addition keeps a time that is a whole number of frames, held as a float a hair short, in its frame."""
    return math.floor(seconds * cc.FRAMES_PER_SECOND + 1e-6)


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
            position = start + _frame(cue.time)
            if position >= stop:
                result.notes.append(f"The cue in {where} is past the end of the track.")
                continue
            end = 0
            if cue.kind == "loop":
                end = start + _frame(cue.end) if cue.end is not None else 0
                if cue.end is not None and cue.end > cue.time:
                    end = max(
                        end, position + 1
                    )  # (a very short loop keeps a frame, whichever way it rounds)
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
    # cues of ours from an earlier sync, moved to where they are wanted now: (as it was, as it is)
    moved: list[tuple[cc.CueEntry, cc.CueEntry]] = field(default_factory=list)


# A cue we wrote in an earlier sync (`DiscRequest.earlier`) and have not seen changed on the player is moved to
# a wanted cue up to this far from it: far enough for the correction of an MP3's start (`cue_timing`, up to
# four frames) and a cue nudged in Dubcrate, near enough that two separate cues are not taken for one.
MOVE_FRAMES = 8


def merge_cues(
    existing: Sequence[cc.CueEntry],
    wanted: Sequence[WantedCue],
    earlier: Collection[tuple[int, int, int]] = (),
) -> Merge:
    """Add `wanted` after `existing`, which is only changed in one way: a cue **exactly as an earlier sync left
    it** (`earlier`: (track, position, end), the disc's baseline) is moved to the nearest wanted cue within
    `MOVE_FRAMES`, keeping its place in the list. Anything else on the card, set or moved on the player, is
    never changed, reordered or removed.

    A wanted cue within `DUPLICATE_FRAMES` of one already there (same track and kind) is skipped.
    Past `MAX_CUES` the rest is reported in `overflow`, not dropped silently. New entries are
    numbered on from the highest index.
    """
    cues = list(existing)
    result = Merge(cues, [], 0, [])
    ours = set(earlier)

    def present(c: cc.CueEntry, want: WantedCue) -> bool:
        """Already where `want` is: within `DUPLICATE_FRAMES`, unless it is one we wrote that is not exactly there
        (then it is moved: it may be a frame or two later than a cue is meant to be)."""
        return _same(c.track, c.is_loop, c.position, want) and (
            c.position == want.position or (c.track, c.position, c.end) not in ours
        )

    # a cue that is already where something is wanted is not ours to move
    pinned = {i for i, c in enumerate(cues) if any(present(c, w) for w in wanted)}
    for want in wanted:
        if any(present(c, want) for c in cues):
            result.already_there += 1
            continue
        near = [
            (abs(c.position - want.position), i)
            for i, c in enumerate(cues)
            if i not in pinned
            and (c.track, c.position, c.end) in ours
            and c.track == want.track
            and c.is_loop == want.is_loop
            and abs(c.position - want.position) <= MOVE_FRAMES
            and (not want.is_loop or abs(c.end - want.end) <= MOVE_FRAMES)
        ]
        if near:
            i = min(near)[1]
            old = cues[i]
            cues[i] = replace(old, position=want.position, end=want.end)
            pinned.add(i)
            result.moved.append((old, cues[i]))
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


def _has_record(
    coarse: Sequence[cc.WaveRecord], fine: Sequence[cc.WaveRecord], track: int
) -> bool:
    """Whether the file already holds a waveform (even a part-filled one) for a 1-based track number.

    A slot the player left before a track it played (track 0, all zero) is no record."""
    slot = track - 1
    return any(
        any(records[slot].data) for records in (coarse, fine) if slot < len(records)
    )


@dataclass
class WaveMerge:
    coarse: list[
        cc.WaveRecord
    ]  # the whole new W001 list: what was there, unchanged, then ours
    fine: list[cc.WaveRecord]
    added: list[int]  # the tracks given a waveform
    kept: list[int]  # wanted tracks that already had one
    pending: list[int]  # wanted tracks with no values yet


def merge_waveforms(
    coarse: Sequence[cc.WaveRecord],
    fine: Sequence[cc.WaveRecord],
    wanted: Mapping[int, Sequence[int] | None],
) -> WaveMerge:
    """Add `wanted` waveforms (by 1-based track number) to a file's records.

    A track that already has a record (any value set: the player's own, ours, or one it part-filled)
    is **never changed**. Records are slots, slot `n - 1` for track `n`, filled up to the highest
    track written; a slot between is the empty one the player leaves (`WaveRecord.unplayed`). The two
    lists stay the same length, as the player's do."""
    new_coarse, new_fine = list(coarse), list(fine)
    result = WaveMerge(new_coarse, new_fine, [], [], [])
    for track in sorted(wanted):
        values = wanted[track]
        if _has_record(coarse, fine, track):
            result.kept.append(track)
            continue
        if values is None:
            result.pending.append(track)
            continue
        slot = track - 1
        while len(new_coarse) <= slot:
            new_coarse.append(cc.WaveRecord.unplayed())
        while len(new_fine) <= slot:
            new_fine.append(cc.WaveRecord.unplayed())
        new_coarse[slot], new_fine[slot] = wave_model.records(track, list(values))
        result.added.append(track)
    length = max(len(new_coarse), len(new_fine))
    while len(new_coarse) < length:
        new_coarse.append(cc.WaveRecord.unplayed())
    while len(new_fine) < length:
        new_fine.append(cc.WaveRecord.unplayed())
    return result


class Status(Enum):
    NEW_DISC = "not on the card"  # an index entry and a file are made
    NEW_FILE = (
        "on the card, no file yet"  # the player makes a file later; we make one now
    )
    UPDATE = "cues or waveforms to add"
    IN_SYNC = "already there"
    NOTHING = "no cues to write"  # no entry or file is made


@dataclass
class DiscRequest:
    """One disc to sync: its ID, the cues wanted, and what `map_cues` left out.

    `waves` are the waveforms wanted, by 1-based track number: 100 values (1 to 15, in time order) or
    None for a track not analysed yet (counted, but nothing can be written for it)."""

    disc_id: cc.DiscId
    wanted: Sequence[WantedCue]
    notes: Sequence[str] = ()
    waves: Mapping[int, Sequence[int] | None] = field(default_factory=dict)
    # the cues the last sync left on the card for this disc (`Library.card_baseline`): (track, position, end)
    earlier: Sequence[tuple[int, int, int]] = ()


@dataclass
class DiscPlan:
    status: Status
    disc_id: cc.DiscId
    name: str | None  # the card's file name (`CDJ00001`); None when nothing is written
    file: cc.TrackFile | None  # what to write; None when nothing is
    added: list[cc.CueEntry]
    kept: int  # cues already on the card, left exactly as they are (not the ones moved)
    already_there: int
    overflow: list[WantedCue]
    notes: list[str]
    waves_added: list[int] = field(default_factory=list)  # tracks that get a waveform
    waves_kept: list[int] = field(
        default_factory=list
    )  # tracks that already have one: left alone
    waves_pending: list[int] = field(default_factory=list)  # wanted, not analysed yet
    moved: list[tuple[cc.CueEntry, cc.CueEntry]] = field(
        default_factory=list
    )  # see `Merge.moved`


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
    on the card keeps every cue, its hot cues and every waveform record it has; only new cues are
    added, and a waveform only for a track that has none (`merge_waveforms`).
    """
    work = cc.IndexFile(
        [cc.IndexEntry(e.name, e.disc_id) for e in index.entries], index.version
    )
    current = dict(files)
    plans: list[DiscPlan] = []
    for request in requests:
        entry = work.find(request.disc_id)
        existing = current.get(entry.name) if entry else None
        merge = merge_cues(
            existing.cues if existing else [], request.wanted, request.earlier
        )
        waves = merge_waveforms(
            existing.coarse if existing else [],
            existing.fine if existing else [],
            request.waves,
        )
        notes = list(request.notes)
        if not merge.added and not waves.added and existing is None:
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
                    [],
                    waves.kept,
                    waves.pending,
                )
            )
            continue
        if entry is None:
            entry = work.add(request.disc_id)
            status = Status.NEW_DISC
        elif existing is None:
            status = Status.NEW_FILE
        else:
            status = (
                Status.UPDATE
                if merge.added or merge.moved or waves.added
                else Status.IN_SYNC
            )
        base = existing or cc.TrackFile.new(request.disc_id)
        file = replace(
            base,
            cues=merge.cues,
            hot_cues=list(base.hot_cues),
            coarse=waves.coarse,
            fine=waves.fine,
        )
        current[entry.name] = file  # a second request for the same disc sees this one
        plans.append(
            DiscPlan(
                status,
                request.disc_id,
                entry.name,
                file,
                merge.added,
                len(existing.cues) - len(merge.moved) if existing else 0,
                merge.already_there,
                merge.overflow,
                notes,
                waves.added,
                waves.kept,
                waves.pending,
                merge.moved,
            )
        )
    return CardPlan(work, plans)
