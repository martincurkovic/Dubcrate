"""Memory cues set on the CDJ-1000MK3 itself, found on its SD card and offered back to Dubcrate (Qt-free).

The reverse of `card_sync`. A disc's file on the card holds every cue the player has for it: the ones
`card_sync` wrote (every rekordbox cue, hot cues and loops included, as memory cues) and the ones the user set on
the player. `new_cues` picks out the **memory cues that are new**: a cue on the card that none of the track's cues
in Dubcrate (rekordbox's, with any memory cues edited in Dubcrate) is within `DUPLICATE_FRAMES` of. Everything
else is left alone and counted:

- **Additions only.** A cue that was deleted or moved on the player is not noticed (there is no record of what
  was written at sync time to compare with), so a moved cue shows as a new one beside the old. Nothing is ever
  removed from Dubcrate or rekordbox by an import.
- **Loops and the player's three hot cues are not imported** (rekordbox's memory loops are never written, and the
  hot cue slots are never touched): they are counted so the review can say so.
- A track keeps at most `MAX_MEMORY_CUES` memory cues (the player shows ten): the earliest new ones fit, the rest are
  counted in `over_limit`.

`survey_new_cues` is the library half: it maps a card's cues on to the Dubcrate discs the card holds.
`dev_docs/card-sync.md`, "Importing cues from the card". **Not tried with cues set on a real player.**
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from dubcrate import cdj_card as cc
from dubcrate.analysis import Cue
from dubcrate.card_discs import DiscSurvey, RekordboxCues
from dubcrate.card_sync import DUPLICATE_FRAMES
from dubcrate.library import Library
from dubcrate.library.cue_edits import MAX_MEMORY_CUES, memory_list
from dubcrate.library.models import Track


@dataclass
class CardCues:
    """What `new_cues` found in one disc's file."""

    by_track: dict[int, list[int]] = field(
        default_factory=dict
    )  # 1-based track -> new memory cues, milliseconds into it
    loops: int = 0  # loops on the card: not imported
    hot_cues: int = 0  # hot cues set on the player: not imported
    outside: int = 0  # cues that are not inside any track of the disc as planned

    @property
    def total(self) -> int:
        return sum(len(v) for v in self.by_track.values())


def _track_of(position: int, claimed: int, starts: Sequence[int], lead_out: int) -> int:
    """The 1-based track a cue at `position` is in: the one it names if it fits, else whichever holds it; 0: none."""

    def holds(n: int) -> bool:
        stop = starts[n] if n < len(starts) else lead_out
        return starts[n - 1] <= position < stop

    if 1 <= claimed <= len(starts) and holds(claimed):
        return claimed
    return next((n for n in range(1, len(starts) + 1) if holds(n)), 0)


def new_cues(
    file: cc.TrackFile,
    starts: Sequence[int],
    lead_out: int,
    known: Mapping[int, Sequence[Cue]],
) -> CardCues:
    """The memory cues in `file` that are not in `known` (cues by 1-based track, as Dubcrate has them).

    `starts` and `lead_out` are the disc's TOC in absolute frames: a cue's time in its track is its position
    minus the track's start (`card_sync.map_cues` backwards)."""
    found = CardCues()
    found.hot_cues = sum(1 for hot in file.hot_cues if hot is not None)
    taken: dict[int, list[int]] = {}  # positions already seen or known, per track
    for entry in file.cues:
        if entry.is_loop:
            found.loops += 1
            continue
        track = _track_of(entry.position, entry.track, starts, lead_out)
        if track == 0:
            found.outside += 1
            continue
        if track not in taken:
            taken[track] = [
                starts[track - 1] + round(c.time * cc.FRAMES_PER_SECOND)
                for c in known.get(track, ())
            ]
        if any(abs(entry.position - p) <= DUPLICATE_FRAMES for p in taken[track]):
            continue
        taken[track].append(entry.position)
        ms = round((entry.position - starts[track - 1]) * 1000 / cc.FRAMES_PER_SECOND)
        found.by_track.setdefault(track, []).append(ms)
    for times in found.by_track.values():
        times.sort()
    return found


@dataclass
class TrackFinding:
    track: Track
    number: int  # 1-based, on the disc
    title: str  # as on the disc
    artist: str
    cues: list[int]  # new memory cues, milliseconds (the ones that fit)
    over_limit: int = 0  # more than the track can have: left out


@dataclass
class DiscFinding:
    disc_id: int
    name: str
    path: str  # "Folder / Disc"
    tracks: list[TrackFinding] = field(default_factory=list)
    loops: int = 0
    hot_cues: int = 0
    outside: int = 0

    @property
    def total(self) -> int:
        return sum(len(t.cues) for t in self.tracks)


def survey_new_cues(
    lib: Library,
    card: tuple[cc.IndexFile, Mapping[str, cc.TrackFile | None]],
    cues: RekordboxCues,
    surveys: Sequence[DiscSurvey],
) -> list[DiscFinding]:
    """For each surveyed disc that is on the card with a file: what the card has that Dubcrate hasn't.

    A disc that can't be synced (no TOC) or isn't on the card is skipped. A disc with nothing new, nothing
    to say about loops or hot cues, is left out."""
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
        found = new_cues(file, survey.input.starts, request.disc_id.lead_out, known)
        finding = DiscFinding(
            survey.disc.id,
            survey.disc.name,
            survey.path,
            loops=found.loops,
            hot_cues=found.hot_cues,
            outside=found.outside,
        )
        for e in entries:
            wanted = found.by_track.get(e.position + 1)
            if not wanted:
                continue
            room = max(0, MAX_MEMORY_CUES - len(memory_list(known[e.position + 1])))
            finding.tracks.append(
                TrackFinding(
                    e.track,
                    e.position + 1,
                    e.title,
                    e.artist,
                    wanted[:room],
                    max(0, len(wanted) - room),
                )
            )
        if finding.tracks:
            findings.append(finding)
    return findings
