"""Putting an MP3 disc's waveforms and cues on the CDJ-1000MK3's SD card: the plan, pure (no Qt, no library).

The card files are `cdj_card_mp3`'s; the plan is the same as an audio disc's (`card_sync`): **what is on the
card is kept**, ours is added after it. A track with a waveform record (the player's, ours, or half filled) is
never given another; a cue within `DUPLICATE_FRAMES` of one there, memory cue or hot cue, is the same cue;
hot cues are never written; at most 100 cues and loops a disc. A disc is found on the card by the three
fields of its ID that are understood (`Mp3DiscId`), so **it must already have an entry**, which the player
makes when it first plays the disc: Dubcrate can't make an ID. Those three numbers come from the image the
disc's files make (`iso.image_layout`), so they also check that the files here are the ones that were burned.

`dev_docs/card-sync.md`, "MP3 discs: the v1 plan". **No file written this way has been loaded by a player yet.**
"""

from __future__ import annotations

import copy
import os
import shutil
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from dubcrate import cdj_card as cc
from dubcrate import cdj_card_mp3 as m3
from dubcrate import wave_model
from dubcrate.card_sync import DUPLICATE_FRAMES


@dataclass
class Mp3TrackInput:
    """One track of the disc: where it is on the image, its frames, and what to put on the card for it."""

    folder: int  # 0 is the root
    file: int  # from 1 in its folder
    title: str
    table: m3.FrameTable
    wave: list[int] | None = (
        None  # the CDJ waveform (100 columns), None = not analysed yet
    )
    cues: list[tuple[int, int]] = field(
        default_factory=list
    )  # (position, end) in 1/75 s; end 0 = a memory cue


@dataclass
class Mp3DiscInput:
    name: str
    file_count: int
    last_file_start: int
    image_sectors: int
    tracks: list[Mp3TrackInput]
    notes: list[str] = field(default_factory=list)  # what could not be placed, in words


class Mp3Status(StrEnum):
    READY = "ready"  # something to add
    IN_SYNC = "in sync"  # the card has all of it
    NO_ENTRY = "no entry"  # the player has not seen this disc
    AMBIGUOUS = "ambiguous"  # two entries fit


@dataclass
class Mp3Plan:
    status: Mp3Status
    message: str = ""
    entry: m3.Mp3IndexEntry | None = None
    file: m3.Mp3TrackFile | None = (
        None  # the whole new file (what was there, then ours)
    )
    had_file: bool = False
    waves_added: int = 0
    waves_filled: int = 0  # a record the player had only part of: the columns it never played, filled in
    waves_kept: int = 0  # a record that was there and complete: not touched
    waves_pending: int = 0  # not analysed yet
    cues_added: int = 0
    cues_already: int = 0  # on the card already (or a hot cue there)
    cues_overflow: int = 0  # past the player's 100
    notes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.status is Mp3Status.READY and bool(
            self.waves_added or self.waves_filled or self.cues_added
        )


def _fill_columns(existing: bytes, ours: bytes) -> bytes:
    """`existing` (a `W001` record's 50 amplitudes) with each column it has nothing for (0) taken from `ours`."""
    return bytes(e or o for e, o in zip(existing, ours, strict=True))


def _fill_nibbles(existing: bytes, ours: bytes) -> bytes:
    """The same for a `W002` record, whose bytes hold two columns each (a nibble apiece)."""
    out = bytearray()
    for e, o in zip(existing, ours, strict=True):
        low = (e & 0x0F) or (o & 0x0F)
        high = (e >> 4) or (o >> 4)
        out.append(high << 4 | low)
    return bytes(out)


def _near(a: int, b: int) -> bool:
    return abs(a - b) <= DUPLICATE_FRAMES


def plan_mp3_disc(
    disc: Mp3DiscInput,
    index: m3.Mp3Index,
    files: dict[str, m3.Mp3TrackFile | None],
) -> Mp3Plan:
    """What a sync would write for this disc: the card's file for it with ours added, or why not."""
    found = index.find(disc.file_count, disc.last_file_start, disc.image_sectors)
    if not found:
        return Mp3Plan(
            Mp3Status.NO_ENTRY,
            "The player doesn't know this disc yet (or the files here are not the ones burned: its size and last "
            "file are not on the card). Play the disc in the player once, so it makes its entry, then sync again.",
            notes=list(disc.notes),
        )
    if len(found) > 1:
        return Mp3Plan(
            Mp3Status.AMBIGUOUS,
            f"{len(found)} discs on the card look like this one, so it can't tell which is it.",
            notes=list(disc.notes),
        )
    entry = found[0]
    existing = files.get(entry.name)
    work = (
        copy.deepcopy(existing)
        if existing is not None
        else m3.Mp3TrackFile.new(entry.disc_id)
    )
    plan = Mp3Plan(
        Mp3Status.IN_SYNC,
        entry=entry,
        file=work,
        had_file=existing is not None,
        notes=list(disc.notes),
    )

    # A record the player made by merely starting a track (0 or a few columns) is not a waveform: drawn as it is the
    # track shows nothing, and the player treats the track as already analysed. So a record that is there is
    # COMPLETED, column by column, the way the player fills one as a track plays: a column it measured stays, a
    # column it has nothing for (0) takes ours. A complete record is not touched.
    where_coarse = {w.key: i for i, w in enumerate(work.coarse)}
    where_fine = {w.key: i for i, w in enumerate(work.fine)}
    for track in disc.tracks:
        key = (track.folder, track.file)
        there = key in where_coarse or key in where_fine
        if track.wave is None:
            if there:
                plan.waves_kept += 1
            else:
                plan.waves_pending += 1
            continue
        coarse, fine = wave_model.records(1, track.wave)
        changed = False
        for records, where, made, fill in (
            (work.coarse, where_coarse, coarse.data, _fill_columns),
            (work.fine, where_fine, fine.data, _fill_nibbles),
        ):
            if key in where:
                old = records[where[key]]
                data = fill(old.data, made)
                if data != old.data:
                    records[where[key]] = m3.Mp3Wave(
                        old.folder, old.file, data, old.third
                    )
                    changed = True
            else:  # (none of this kind yet: a new record, at the end as the player appends them)
                where[key] = len(records)
                records.append(m3.Mp3Wave(track.folder, track.file, made))
                changed = True
        if not there:
            plan.waves_added += 1
        elif changed:
            plan.waves_filled += 1
        else:
            plan.waves_kept += 1

    for track in disc.tracks:
        here = (track.folder, track.file)
        for position, end in track.cues:
            is_loop = end > 0
            if position >= track.table.length_frames or (
                is_loop and end > track.table.length_frames
            ):
                plan.notes.append(
                    f"A cue of “{track.title}” is past the end of the track and was left out."
                )
                continue
            if _present(work, here, position, end):
                plan.cues_already += 1
                continue
            if len(work.cues) >= cc.MAX_CUES:
                plan.cues_overflow += 1
                continue
            entry_ = (
                m3.Mp3Entry.loop(track.table, track.folder, track.file, position, end)
                if is_loop
                else m3.Mp3Entry.cue(track.table, track.folder, track.file, position)
            )
            work.cue_indexes.append(work.next_cue_index())
            work.cues.append(entry_)
            plan.cues_added += 1

    # (not `plan.changed`: that asks the status, which is what is being decided here)
    plan.status = (
        Mp3Status.READY
        if (plan.waves_added or plan.waves_filled or plan.cues_added)
        else Mp3Status.IN_SYNC
    )
    return plan


def _present(
    file: m3.Mp3TrackFile, key: tuple[int, int], position: int, end: int
) -> bool:
    """Whether the card already has this cue: a memory cue or loop of this track at the same place, or (for a
    cue) a hot cue there."""
    for entry in file.cues:
        if (entry.folder, entry.file) != key or entry.is_loop != (end > 0):
            continue
        if _near(entry.position, position) and (end == 0 or _near(entry.end, end)):
            return True
    if end == 0:
        return any(
            h is not None and (h.folder, h.file) == key and _near(h.position, position)
            for h in file.hot
        )
    return False


def describe_plan(plan: Mp3Plan, name: str = "") -> list[str]:
    """The plan in words, for the command line and the window."""
    head = f"“{name}”: " if name else ""
    if plan.status in (Mp3Status.NO_ENTRY, Mp3Status.AMBIGUOUS):
        return [head + plan.message, *plan.notes]
    where = plan.entry.file_name if plan.entry else ""
    bits = []
    if plan.waves_added:
        bits.append(
            f"{plan.waves_added} waveform{'s' if plan.waves_added != 1 else ''}"
        )
    if plan.waves_filled:
        bits.append(
            f"{plan.waves_filled} waveform{'s' if plan.waves_filled != 1 else ''} the player only started "
            "completed (what it measured stays)"
        )
    if plan.cues_added:
        bits.append(
            f"{plan.cues_added} cue{'s' if plan.cues_added != 1 else ''} and loops"
        )
    if plan.status is Mp3Status.IN_SYNC:
        lines = [head + "the card already has everything (nothing to add)."]
    else:
        lines = [head + "to add to " + where + ": " + ", ".join(bits) + "."]
    kept = []
    if plan.waves_kept:
        kept.append(
            f"{plan.waves_kept} complete waveform{'s' if plan.waves_kept != 1 else ''} stay as they are"
        )
    if plan.cues_already:
        kept.append(
            f"{plan.cues_already} cue{'s' if plan.cues_already != 1 else ''} already there"
        )
    if kept:
        lines.append("  kept: " + "; ".join(kept) + ".")
    if plan.waves_pending:
        lines.append(
            f"  {plan.waves_pending} track{'s' if plan.waves_pending != 1 else ''} not analysed yet: no waveform for them."
        )
    if plan.cues_overflow:
        lines.append(
            f"  {plan.cues_overflow} cues left out: a disc holds 100 cues and loops."
        )
    lines += [f"  {n}" for n in plan.notes]
    return lines


# -- writing ----------------------------------------------------------------------------------------


@dataclass
class Mp3Outcome:
    wrote: bool
    backup: Path | None = None
    waveforms_written: int = 0
    cues_written: int = 0
    name: str = ""


def backup_mp3_card(path: Path, into: Path) -> Path:
    """Copy a card's `PIONEER/MPJ` files (a few kilobytes) to a new `card-DATE-TIME-mpj` folder in `into`."""
    folder = m3.mpj_dir(path)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    destination = into / f"card-{stamp}-mpj"
    n = 1
    while destination.exists():
        n += 1
        destination = into / f"card-{stamp}-mpj-{n}"
    destination.mkdir(parents=True)
    for file in sorted(folder.glob("*.PDJ")):
        shutil.copy2(file, destination / file.name)
    return destination


def apply_mp3_plan(card: Path, plan: Mp3Plan, backup_dir: Path) -> Mp3Outcome:
    """Back up the card's MP3 files, write the disc's file (a temporary name, then a rename, read back and
    compared), and flush. Only that one file is touched: the index already has the entry, and no other disc is
    changed. **The card must be ejected through the system before it is pulled.**"""
    if not plan.changed or plan.entry is None or plan.file is None:
        return Mp3Outcome(False, name=plan.entry.name if plan.entry else "")
    folder = m3.mpj_dir(card)
    backup = backup_mp3_card(card, backup_dir)
    data = plan.file.to_bytes()
    target = folder / plan.entry.file_name
    cc.replace_file(target, data)
    again = m3.Mp3TrackFile.parse(
        target.read_bytes()
    )  # (it reads back as a file the player can read)
    if again.to_bytes() != data:
        raise m3.Mp3CardError(f"{target.name} did not read back as it was written.")
    if hasattr(os, "sync"):
        os.sync()
    return Mp3Outcome(
        True,
        backup,
        plan.waves_added + plan.waves_filled,
        plan.cues_added,
        plan.entry.name,
    )
