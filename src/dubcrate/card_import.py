"""What changed on the CDJ-1000MK3 itself, found on its SD card and offered back to Dubcrate (Qt-free).

The reverse of `card_sync`. A disc's file on the card holds every cue the player has for it: the ones
`card_sync` wrote (every rekordbox cue, hot cues and loops included, as memory cues) and whatever the user did on
the player. `changes_on_card` compares the file with what Dubcrate has for each track (`known`: rekordbox's cues
with any memory cues and loops edited in Dubcrate laid over them) and, if there is one, with the **baseline**: the
cues the last sync left on the card (`Library.card_baseline`, saved from the sync's result). It finds, per track:

- **New memory cues** (`add`) and **new loops** (`loop`): on the card, and none of Dubcrate's cues (or the baseline)
  is within `DUPLICATE_FRAMES` of them. A cue that is in the baseline but that Dubcrate no longer has was deleted or
  moved *in Dubcrate* after the sync: it is not offered back.
- **Hot cues set on the player** (`hot`): the three slots as memory cues, **not ticked by default**.
- **Cues deleted on the player** (`remove`, `remove_loop`): in the baseline, gone from the card, and one Dubcrate
  can delete: a plain memory cue, or a memory loop (rekordbox's or saved in Dubcrate: `PreviewController` deletes it
  in Dubcrate and notes it for rekordbox). A baseline cue that is a hot cue, or the loop of a hot cue, is only
  counted in `kept` (hot cues are never changed that way).
- **Cues moved on the player** (`move`, `move_loop`): when a track has as many deletions as new cues (or loops),
  they are paired in time order and shown as moves. This is only how they are shown: a move is a deletion and an
  addition, and each is applied the same way.

Without a baseline (no sync recorded for the disc) only additions can be found. Nothing is ever removed from
rekordbox by this module: what the user ticks is applied by `PreviewController.import_memory`, which changes
Dubcrate's own memory cues and loops, and `RekordboxEditor` writes them on if that is allowed.
A track keeps at most `MAX_MEMORY_CUES` memory cues and loops together: the earliest new ones fit, the rest are
counted in `over_limit`.

`survey_new_cues` is the library half: it maps a card's cues on to the Dubcrate discs the card holds.
`dev_docs/card-sync.md`, "Importing cues from the card". **Not tried with cues set on a real player.**

**MP3 discs** (`changes_on_mp3_card`, `survey_new_mp3_cues`) are the same review with two differences. The player
names a track by (folder, file) and not by a position on the disc, so the tracks are numbered as the burned image
numbers them (`card_mp3.mp3_slots`); and its time counts the Info frame, so a card time is turned into audio time by
taking off the file's lead (`cue_timing`). **No baseline is kept for an MP3 disc yet**, so only additions can be
found: cues and loops set on the player and its hot cues (not ticked); a deletion or a move is not noticed.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from dubcrate import cdj_card as cc
from dubcrate import cdj_card_mp3 as m3
from dubcrate.analysis import Cue
from dubcrate.card_discs import DiscSurvey, RekordboxCues
from dubcrate.card_mp3 import (
    CHANGED,
    NOT_RECORDED,
    Mp3DiscRow,
    Mp3Slot,
    Mp3SyncError,
    card_file_for,
    mp3_slots,
    player_frame,
)
from dubcrate.card_sync import DUPLICATE_FRAMES, WantedCue
from dubcrate.library import Library
from dubcrate.library.cue_edits import MAX_MEMORY_CUES, memory_count
from dubcrate.library.models import Track

# the kinds of row a review lists
ADD = "add"  # a memory cue set on the player
LOOP = "loop"  # a loop set on the player
HOT = "hot"  # one of the player's hot cues, as a memory cue (not ticked by default)
REMOVE = "remove"  # a memory cue deleted on the player
REMOVE_LOOP = "remove_loop"  # a loop saved in Dubcrate, deleted on the player
MOVE = "move"  # a memory cue moved on the player (a deletion and an addition)
MOVE_LOOP = "move_loop"


@dataclass(frozen=True)
class Row:
    """One thing the review can bring in. Times are milliseconds into the track.

    `ms` / `end_ms` are the new place (an addition or the new end of a move) or, for a removal, the place of the
    cue Dubcrate has; `old_ms` / `old_end_ms` are the place a move leaves."""

    kind: str
    ms: int
    end_ms: int = 0  # a loop's end
    old_ms: int = 0
    old_end_ms: int = 0
    slot: int = 0  # a hot cue's number, 1 = A
    ticked: bool = True  # ticked when the review opens

    @property
    def adds(self) -> bool:
        return self.kind in (ADD, LOOP, HOT, MOVE, MOVE_LOOP)

    @property
    def removes(self) -> bool:
        return self.kind in (REMOVE, REMOVE_LOOP, MOVE, MOVE_LOOP)

    @property
    def is_loop(self) -> bool:
        return self.kind in (LOOP, REMOVE_LOOP, MOVE_LOOP)


@dataclass
class CardCues:
    """What `changes_on_card` found in one disc's file."""

    by_track: dict[int, list[Row]] = field(
        default_factory=dict
    )  # 1-based track -> rows, in time order
    kept: int = 0  # deleted on the player, but Dubcrate can't delete them (hot cues, rekordbox's loops)
    outside: int = 0  # cues that are not inside any track of the disc as planned

    @property
    def total(self) -> int:
        return sum(len(rows) for rows in self.by_track.values())


def _track_of(position: int, claimed: int, starts: Sequence[int], lead_out: int) -> int:
    """The 1-based track a cue at `position` is in: the one it names if it fits, else whichever holds it; 0: none."""

    def holds(n: int) -> bool:
        stop = starts[n] if n < len(starts) else lead_out
        return starts[n - 1] <= position < stop

    if 1 <= claimed <= len(starts) and holds(claimed):
        return claimed
    return next((n for n in range(1, len(starts) + 1) if holds(n)), 0)


def _near(a: int, b: int) -> bool:
    return abs(a - b) <= DUPLICATE_FRAMES


def _ms(position: int, start: int) -> int:
    return round((position - start) * 1000 / cc.FRAMES_PER_SECOND)


def _frame(start: int, seconds: float) -> int:
    return start + round(seconds * cc.FRAMES_PER_SECOND)


def _paired(rows: list[Row]) -> list[Row]:
    """A track's rows in time order (hot cues last). Deletions and additions of one kind (cues, or loops) are
    paired as moves, in time order, when there are as many of each; otherwise they stay as they are."""
    out: list[Row] = []
    for add_kind, remove_kind, move_kind in (
        (ADD, REMOVE, MOVE),
        (LOOP, REMOVE_LOOP, MOVE_LOOP),
    ):
        added = sorted((r for r in rows if r.kind == add_kind), key=lambda r: r.ms)
        removed = sorted((r for r in rows if r.kind == remove_kind), key=lambda r: r.ms)
        if added and len(added) == len(removed):
            out += [
                Row(move_kind, new.ms, new.end_ms, old.ms, old.end_ms)
                for new, old in zip(added, removed, strict=True)
            ]
        else:
            out += added + removed
    out.sort(key=lambda r: r.ms)
    return out + sorted((r for r in rows if r.kind == HOT), key=lambda r: r.ms)


def changes_on_card(
    file: cc.TrackFile,
    starts: Sequence[int],
    lead_out: int,
    known: Mapping[int, Sequence[Cue]],
    baseline: Sequence[tuple[int, int, int]] | Sequence[WantedCue] | None = None,
) -> CardCues:
    """What the player changed in `file`, against `known` (Dubcrate's cues by 1-based track) and `baseline`.

    `starts` and `lead_out` are the disc's TOC in absolute frames: a cue's time in its track is its position
    minus the track's start (`card_sync.map_cues` backwards). `baseline` is `Library.card_baseline`'s
    (track, position, end) triples (or `WantedCue`s), or None when no sync is recorded for the disc."""
    found = CardCues()
    base = (
        None
        if baseline is None
        else [
            (b.track, b.position, b.end)
            if isinstance(b, WantedCue)
            else (b[0], b[1], b[2])
            for b in baseline
        ]
    )
    # Dubcrate's cues as card positions, per track: (cue, position, end position)
    positions: dict[int, list[tuple[Cue, int, int]]] = {}

    def of_track(track: int) -> list[tuple[Cue, int, int]]:
        if track not in positions:
            start = starts[track - 1]
            positions[track] = [
                (
                    c,
                    _frame(start, c.time),
                    _frame(start, c.end) if c.end else 0,
                )
                for c in known.get(track, ())
            ]
        return positions[track]

    base_cues = [p for _t, p, e in (base or []) if not e]
    base_loops = [(p, e) for _t, p, e in (base or []) if e]
    card_cues = [e.position for e in file.cues if not e.is_loop]
    card_loops = [
        (e.position, e.end) for e in file.cues if e.is_loop and e.end > e.position
    ]
    rows: dict[int, list[Row]] = {}
    gone: dict[int, list[Row]] = {}
    taken: dict[int, list[int]] = {}  # new cue positions already seen, per track
    taken_loops: dict[int, list[tuple[int, int]]] = {}

    for entry in file.cues:
        track = _track_of(entry.position, entry.track, starts, lead_out)
        if track == 0:
            found.outside += 1
            continue
        start = starts[track - 1]
        if entry.is_loop:
            if entry.end <= entry.position:
                continue  # (no usable end)
            span = (entry.position, entry.end)
            if (
                any(
                    _near(span[0], p) and _near(span[1], q)
                    for _c, p, q in of_track(track)
                    if q
                )
                or any(_near(span[0], p) and _near(span[1], q) for p, q in base_loops)
                or any(
                    _near(span[0], p) and _near(span[1], q)
                    for p, q in taken_loops.get(track, [])
                )
            ):
                continue
            taken_loops.setdefault(track, []).append(span)
            rows.setdefault(track, []).append(
                Row(LOOP, _ms(span[0], start), _ms(span[1], start))
            )
            continue
        if (
            any(_near(entry.position, p) for _c, p, _q in of_track(track))
            or any(_near(entry.position, p) for p in base_cues)
            or any(_near(entry.position, p) for p in taken.get(track, []))
        ):
            continue
        taken.setdefault(track, []).append(entry.position)
        rows.setdefault(track, []).append(Row(ADD, _ms(entry.position, start)))

    for hot in file.hot_cues:  # the player's hot cues, as memory cues to opt in to
        if hot is None or hot.kind != cc.KIND_CUE:
            continue
        track = _track_of(hot.position, hot.track, starts, lead_out)
        if track == 0:
            found.outside += 1
            continue
        if (
            any(_near(hot.position, p) for _c, p, _q in of_track(track))
            or any(_near(hot.position, p) for p in card_cues)
            or any(_near(hot.position, p) for p in taken.get(track, []))
        ):
            continue  # (Dubcrate has a cue there, or the player has a memory cue there already)
        taken.setdefault(track, []).append(hot.position)
        rows.setdefault(track, []).append(
            Row(HOT, _ms(hot.position, starts[track - 1]), slot=hot.slot, ticked=False)
        )

    removed_seen: set[tuple[int, int, int]] = set()
    for b_track, b_pos, b_end in base or []:
        track = _track_of(b_pos, b_track, starts, lead_out)
        if track == 0:
            continue
        start = starts[track - 1]
        if b_end:
            if any(_near(b_pos, p) and _near(b_end, q) for p, q in card_loops):
                continue  # still on the card
            match = next(
                (
                    c
                    for c, p, q in of_track(track)
                    if q and _near(b_pos, p) and _near(b_end, q)
                ),
                None,
            )
            if match is None:
                continue  # (Dubcrate has no such loop now: nothing to delete)
            if match.slot is not None:
                found.kept += 1  # (a hot cue's loop: rekordbox's, never changed)
                continue
            ident = (track, round(match.time * 1000), round(match.end * 1000))
            if ident in removed_seen:
                continue
            removed_seen.add(ident)
            gone.setdefault(track, []).append(Row(REMOVE_LOOP, ident[1], ident[2]))
        else:
            if any(_near(b_pos, p) for p in card_cues):
                continue
            near = [c for c, p, _q in of_track(track) if _near(b_pos, p)]
            match = next(
                (c for c in near if c.kind == "memory" and c.slot is None), None
            )
            if match is None:
                if near:
                    found.kept += (
                        1  # (a hot cue, or a loop's start: Dubcrate can't delete those)
                    )
                continue
            ident = (track, round(match.time * 1000), 0)
            if ident in removed_seen:
                continue
            removed_seen.add(ident)
            gone.setdefault(track, []).append(Row(REMOVE, ident[1]))

    for track in sorted({*rows, *gone}):
        found.by_track[track] = _paired(rows.get(track, []) + gone.get(track, []))
    return found


@dataclass
class TrackFinding:
    track: Track
    number: int  # 1-based, on the disc
    title: str  # as on the disc
    artist: str
    rows: list[Row]
    over_limit: int = 0  # new cues and loops left out: a track has room for ten


@dataclass
class DiscFinding:
    disc_id: int
    name: str
    path: str  # "Folder / Disc"
    tracks: list[TrackFinding] = field(default_factory=list)
    kept: int = 0  # deleted on the player, but cues Dubcrate can't delete
    outside: int = 0
    has_baseline: bool = (
        False  # a sync is recorded for the disc (so deletions and moves can be noticed)
    )

    @property
    def total(self) -> int:
        return sum(len(t.rows) for t in self.tracks)


@dataclass
class ImportChoice:
    """What the user ticked for one track: memory cues and loops to add, and to delete (milliseconds)."""

    track: Track
    add: list[int] = field(default_factory=list)
    loops: list[tuple[int, int]] = field(default_factory=list)  # (start, end) to add
    remove: list[int] = field(default_factory=list)
    remove_loops: list[tuple[int, int]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.add or self.loops or self.remove or self.remove_loops)


def choice_from(track: Track, rows: Sequence[Row]) -> ImportChoice:
    """The `ImportChoice` for ticked `rows` of one track (a move is a deletion and an addition)."""
    choice = ImportChoice(track)
    for row in rows:
        if row.kind in (ADD, HOT, MOVE):
            choice.add.append(row.ms)
        if row.kind in (LOOP, MOVE_LOOP):
            choice.loops.append((row.ms, row.end_ms))
        if row.kind == REMOVE:
            choice.remove.append(row.ms)
        if row.kind == MOVE:
            choice.remove.append(row.old_ms)
        if row.kind == REMOVE_LOOP:
            choice.remove_loops.append((row.ms, row.end_ms))
        if row.kind == MOVE_LOOP:
            choice.remove_loops.append((row.old_ms, row.old_end_ms))
    return choice


def survey_new_cues(
    lib: Library,
    card: tuple[cc.IndexFile, Mapping[str, cc.TrackFile | None]],
    cues: RekordboxCues,
    surveys: Sequence[DiscSurvey],
) -> list[DiscFinding]:
    """For each surveyed disc that is on the card with a file: what changed on the card since Dubcrate's last sync.

    A disc that can't be synced (no TOC) or isn't on the card is skipped, and so is one with nothing to offer."""
    index, files = card
    findings = []
    for survey in surveys:
        request = survey.input.request
        if request is None or not survey.input.starts:
            continue
        entry = index.find(request.disc_id)
        file = files.get(entry.name) if entry else None
        if file is None:
            continue
        entries = lib.disc_entries(survey.disc.id)
        known = {e.position + 1: cues.for_track(e.track) for e in entries}
        baseline = lib.card_baseline(request.disc_id.key)
        found = changes_on_card(
            file,
            survey.input.starts,
            request.disc_id.lead_out,
            known,
            baseline,
        )
        finding = DiscFinding(
            survey.disc.id,
            survey.disc.name,
            survey.path,
            kept=found.kept,
            outside=found.outside,
            has_baseline=baseline is not None,
        )
        for e in entries:
            rows = found.by_track.get(e.position + 1)
            if not rows:
                continue
            freed = sum(1 for r in rows if r.kind in (REMOVE, REMOVE_LOOP))
            room = max(0, MAX_MEMORY_CUES - memory_count(known[e.position + 1]) + freed)
            kept_rows, over = [], 0
            for row in rows:
                if row.kind in (ADD, LOOP):
                    if room <= 0:
                        over += 1
                        continue
                    room -= 1
                kept_rows.append(row)
            finding.tracks.append(
                TrackFinding(
                    e.track, e.position + 1, e.title, e.artist, kept_rows, over
                )
            )
        if finding.tracks:
            findings.append(finding)
    return findings


# A card entry says how long its track was (1/75 s); the library's length is only as good as its duration (whole
# seconds for rekordbox) or a measure of the decoded audio, so a track is taken as the same within this.
LENGTH_TOLERANCE_FRAMES = 113  # 1.5 s


def _audio_ms(position: int, lead_ms: int) -> int:
    """The player's position (1/75 s) as milliseconds of audio: earlier by the lead, never before 0."""
    return max(0, round(position * 1000 / cc.FRAMES_PER_SECOND) - lead_ms)


def changes_on_mp3_card(
    file: m3.Mp3TrackFile,
    slots: Mapping[tuple[int, int], Mp3Slot],
    known: Mapping[int, Sequence[Cue]],
) -> CardCues:
    """What the player added to an MP3 disc's `file`, against `known` (Dubcrate's cues in audio time, by 1-based
    track). `slots` says which (folder, file) is which track and its lead (`card_mp3.mp3_slots`).

    New memory cues, new loops and the player's hot cues (not ticked) are found, as for an audio disc. A card
    time is in the player's time: the file's lead off it gives audio time, and Dubcrate's cues are compared as
    the player's positions (within `DUPLICATE_FRAMES`). Nothing is found for a deletion or a move: **an MP3 disc
    has no baseline**, so a cue that is gone from the card is not noticed. An entry for a (folder, file) the disc
    doesn't have, or whose length is far from its track's, is counted in `outside`."""
    found = CardCues()
    # Dubcrate's cues as player positions, per track: (position, end position, 0 for a cue)
    held: dict[int, list[tuple[int, int]]] = {}

    def of_track(slot: Mp3Slot) -> list[tuple[int, int]]:
        if slot.track not in held:
            held[slot.track] = [
                (
                    player_frame(c.time, slot.lead_ms),
                    player_frame(c.end, slot.lead_ms) if c.end else 0,
                )
                for c in known.get(slot.track, ())
            ]
        return held[slot.track]

    def slot_of(entry: m3.Mp3Entry) -> Mp3Slot | None:
        slot = slots.get((entry.folder, entry.file))
        if slot is None:
            return None
        if (
            slot.length_frames
            and entry.length
            and abs(entry.length - slot.length_frames) > LENGTH_TOLERANCE_FRAMES
        ):
            return None  # (the disc has been changed since: another track is in that place)
        return slot

    rows: dict[int, list[Row]] = {}
    taken: dict[int, list[int]] = {}
    taken_loops: dict[int, list[tuple[int, int]]] = {}
    card_cues = [(e.folder, e.file, e.position) for e in file.cues if not e.is_loop]

    for entry in file.cues:
        slot = slot_of(entry)
        if slot is None:
            found.outside += 1
            continue
        mine = of_track(slot)
        if entry.is_loop:
            if entry.end <= entry.position:
                continue
            span = (entry.position, entry.end)
            if any(
                q and _near(span[0], p) and _near(span[1], q) for p, q in mine
            ) or any(
                _near(span[0], p) and _near(span[1], q)
                for p, q in taken_loops.get(slot.track, [])
            ):
                continue
            taken_loops.setdefault(slot.track, []).append(span)
            rows.setdefault(slot.track, []).append(
                Row(
                    LOOP,
                    _audio_ms(span[0], slot.lead_ms),
                    _audio_ms(span[1], slot.lead_ms),
                )
            )
            continue
        if any(_near(entry.position, p) for p, q in mine if not q) or any(
            _near(entry.position, p) for p in taken.get(slot.track, [])
        ):
            continue
        taken.setdefault(slot.track, []).append(entry.position)
        rows.setdefault(slot.track, []).append(
            Row(ADD, _audio_ms(entry.position, slot.lead_ms))
        )

    for number, hot in enumerate(file.hot, 1):
        if hot is None or hot.is_loop:
            continue
        slot = slot_of(hot)
        if slot is None:
            found.outside += 1
            continue
        if (
            any(_near(hot.position, p) for p, _q in of_track(slot))
            or any(
                (f, n) == (hot.folder, hot.file) and _near(hot.position, p)
                for f, n, p in card_cues
            )
            or any(_near(hot.position, p) for p in taken.get(slot.track, []))
        ):
            continue  # (Dubcrate has a cue there, or the player has a memory cue there already)
        taken.setdefault(slot.track, []).append(hot.position)
        rows.setdefault(slot.track, []).append(
            Row(HOT, _audio_ms(hot.position, slot.lead_ms), slot=number, ticked=False)
        )

    for track in sorted(rows):
        found.by_track[track] = _paired(rows[track])
    return found


@dataclass
class Mp3Survey:
    """`survey_new_mp3_cues`' answer: what was found, and the MP3 discs it couldn't look at (`unchecked`: no
    record of the image they were burned from, or a disc that no longer matches it)."""

    findings: list["DiscFinding"] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)


def survey_new_mp3_cues(
    lib: Library,
    card: tuple[m3.Mp3Index, Mapping[str, m3.Mp3TrackFile | None]],
    cues: RekordboxCues,
    discs: Sequence[Mp3DiscRow],
) -> Mp3Survey:
    """For each MP3 disc the card has a file for: what the player added since Dubcrate's sync, as `DiscFinding`s.

    A disc is found on the card by the image it was burned from (`Library.disc_image`: the three numbers the player's
    ID is checked by), which is why one with no record can't be looked at, and neither can one whose files no longer
    add up to the record (it was changed since: the player's file and folder numbers would not be its tracks').
    A disc the card has no file for is left out without a word: there is nothing on the card to bring in."""
    out = Mp3Survey()
    for row in discs:
        if row.problems:
            continue
        entries = lib.disc_entries(row.disc.id)
        dirs = lib.disc_dirs(row.disc.id)
        file, why = card_file_for(lib, row.disc.id, card, entries, dirs)
        if why in (NOT_RECORDED, CHANGED):
            out.unchecked.append(row.disc.name)
            continue
        if file is None:
            continue  # (the card has nothing for it: nothing to bring in)
        try:
            slots = mp3_slots(entries, dirs)
        except Mp3SyncError:  # (a folder inside a folder)
            out.unchecked.append(row.disc.name)
            continue
        known = {e.position + 1: cues.for_track(e.track) for e in entries}
        found = changes_on_mp3_card(file, slots, known)
        finding = DiscFinding(
            row.disc.id,
            row.disc.name,
            row.path,
            kept=found.kept,
            outside=found.outside,
            has_baseline=False,
        )
        for e in entries:
            rows = found.by_track.get(e.position + 1)
            if not rows:
                continue
            room = max(0, MAX_MEMORY_CUES - memory_count(known[e.position + 1]))
            kept_rows, over = [], 0
            for r in rows:
                if r.kind in (ADD, LOOP):
                    if room <= 0:
                        over += 1
                        continue
                    room -= 1
                kept_rows.append(r)
            finding.tracks.append(
                TrackFinding(
                    e.track, e.position + 1, e.title, e.artist, kept_rows, over
                )
            )
        if finding.tracks:
            out.findings.append(finding)
    return out


@dataclass
class ImportResult:
    """What importing the ticked changes did."""

    added: int = 0  # memory cues
    loops: int = 0  # loops
    removed: int = 0  # memory cues and loops deleted
    left_out: int = 0  # not added: a track has room for ten memory cues and loops

    @property
    def changed(self) -> int:
        return self.added + self.loops + self.removed

    def summary(self) -> str:
        parts = []
        if self.added:
            parts.append(f"{self.added} memory cue{'s' if self.added != 1 else ''}")
        if self.loops:
            parts.append(f"{self.loops} loop{'s' if self.loops != 1 else ''}")
        said = ""
        if parts:
            said = "Imported " + " and ".join(parts) + " from the card"
        if self.removed:
            gone = f"deleted {self.removed} as on the card"
            said = f"{said}; {gone}" if said else gone.capitalize()
        if not said:
            said = "Nothing imported"
        if self.left_out:
            said += f" ({self.left_out} left out: a track has room for ten)"
        return said
