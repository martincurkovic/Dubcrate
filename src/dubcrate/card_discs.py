"""Dubcrate discs as card sync requests: a disc's TOC from its tracks, and its rekordbox cues.

The library-facing half of `card_sync` (Qt-free; the library belongs to the GUI thread, so a GUI
caller does this on that thread and plans on a worker). Only audio discs are synced.

A disc's ID is its TOC, which is only known exactly when every track's CD length is measured
(`Track.cd_sectors`, written by a burn or by `measure_tracks`): a conservative estimate would
give an ID the disc never had. A disc edited after it was burned has a TOC that no longer
matches the physical disc, and there is no record of what was burned, so this says when a disc
has not been marked as burned. The exact answer is the TOC of the disc in the drive
(`drive_toc.read_drive_toc`, passed to `prepare_disc` as `toc`): it needs no measured lengths, and a
disc edited after its burn is told apart by `toc_differences`. **A disc with gaps (`pregap_sectors`) is a guess**: how a burn
with gaps lays the TOC out has not been checked on a drive.
"""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from dubcrate import cdj_card as cc
from dubcrate.analysis import Cue
from dubcrate.card_report import describe_plan, msf
from dubcrate.card_sync import DiscPlan, DiscRequest, Status, map_cues, plan_card
from dubcrate.cue_timing import lead_ms, to_audio
from dubcrate.library import Library
from dubcrate.library.cue_edits import with_edits
from dubcrate.library.models import DiscEntry, DiscSummary, Track
from dubcrate.library.rekordbox import RekordboxError, read_cues

Toc = tuple[
    list[int], int
]  # a disc's track starts and its lead-out, in absolute frames


@dataclass
class DiscInput:
    """What `prepare_disc` found: a request ready for `plan_card`, or why there is none."""

    disc_id: int
    name: str
    request: DiscRequest | None = None
    problems: list[str] = field(default_factory=list)  # why this disc can't be synced
    warnings: list[str] = field(default_factory=list)  # synced, but be careful
    cue_tracks: int = 0  # tracks that had rekordbox cues
    from_drive: bool = False  # the TOC is the disc in the drive's, not computed
    to_analyse: list[Track] = field(default_factory=list)  # tracks with no waveform yet
    starts: list[int] = field(
        default_factory=list
    )  # where each track starts on the disc, absolute frames
    alternates: list[DiscRequest] = field(
        default_factory=list
    )  # the same cues for the disc's other known TOCs (other physical copies of it)

    def all_requests(self) -> list[DiscRequest]:
        """What a sync writes for this disc: the request, then one for each other TOC it is known to have."""
        return [self.request, *self.alternates] if self.request else []


def disc_toc(
    entries: Sequence[DiscEntry], pregap_sectors: int = 0
) -> tuple[list[int], int]:
    """Track starts and the lead-out, in absolute frames, for a disc's tracks as burned."""
    return cc.toc_from_lengths(
        [e.track.length_sectors for e in entries], pregap_sectors
    )


def known_tocs(
    lib: Library, disc_id: int, entries: Sequence[DiscEntry], pregap_sectors: int = 0
) -> list[Toc]:
    """The TOCs recorded for physical copies of this disc (read back after a burn, or in the TOC Manager), newest
    first, leaving out any switched off in the TOC Manager and any made for a different plan (a disc edited since is no
    longer the disc they were read from).

    A drive can lay a disc out a frame differently from the plan, and the card knows a disc by its TOC, so these are
    what Sync writes to; the plan's own TOC is used only when there are none."""
    planned = disc_toc(entries, pregap_sectors)
    return [
        k.actual for k in lib.disc_tocs(disc_id) if k.enabled and k.planned == planned
    ]


def toc_differences(
    planned: tuple[Sequence[int], int], actual: tuple[Sequence[int], int]
) -> list[str]:
    """How a computed TOC differs from a real one, in words; empty when they are the same."""
    (planned_starts, planned_end), (starts, end) = planned, actual
    if len(planned_starts) != len(starts):
        return [
            f"the disc in the drive has {len(starts)} tracks, this disc {len(planned_starts)}"
        ]
    found = [
        f"track {n} starts at {msf(have)}, planned {msf(want)}"
        for n, (want, have) in enumerate(zip(planned_starts, starts, strict=True), 1)
        if want != have
    ]
    if planned_end != end:
        found.append(f"it ends at {msf(end)}, planned {msf(planned_end)}")
    return found


class RekordboxCues:
    """rekordbox's cues for tracks, read from its database (a snapshot copy, never written).

    The database is read once per rekordbox source, the first time a track of it is asked about.
    A file that is also in rekordbox (same path) takes rekordbox's cues, as the preview takes its
    waveform; a track with no rekordbox twin has none. **Memory cues edited in Dubcrate replace its memory cues**
    (`cue_edits.with_edits`), so the card gets the edited ones.
    """

    def __init__(
        self,
        lib: Library,
        load: Callable[[Path], Mapping[str, Sequence[Cue]]] | None = None,
    ) -> None:
        self._lib = lib
        self._load = load
        self._by_source: dict[int, Mapping[str, Sequence[Cue]]] = {}

    def for_track(self, track: Track) -> Sequence[Cue]:
        """The track's cues for the card, in audio time (`cue_timing`: rekordbox's are later than the audio on the
        disc for most MP3s): rekordbox's, with its memory cues replaced by any edited in Dubcrate."""
        base = to_audio(self._rekordbox_cues(track), lead_ms(track.path))
        return with_edits(self._lib, track, base)

    def _rekordbox_cues(self, track: Track) -> Sequence[Cue]:
        analysed = self._lib.rekordbox_twin(track) or track
        source = next(
            (s for s in self._lib.sources() if s.id == analysed.source_id), None
        )
        if source is None or source.kind != "rekordbox":
            return ()
        if source.id not in self._by_source:
            self._by_source[source.id] = (self._load or read_cues)(Path(source.path))
        return self._by_source[source.id].get(analysed.ext_id, ())


def prepare_disc(
    lib: Library,
    disc_id: int,
    cues: RekordboxCues | None = None,
    toc: tuple[Sequence[int], int] | None = None,
    waves: bool = False,
) -> DiscInput:
    """Everything `plan_card` needs for one disc, or the reasons it can't be synced.

    Give one `RekordboxCues` for all the discs of a sync so rekordbox's database is read once.
    With `toc` (the disc in the drive, from `drive_toc`) the ID and the cue positions come from the
    real disc: the tracks need no measured length, and the plan says if the disc differs from the
    one planned now (edited since it was burned, or another disc).

    With `waves`, each track's CDJ waveform (`wave_model`, cached on the track by `library.waves`) is
    wanted too: `DiscInput.to_analyse` lists the tracks that have none yet, and the request holds None
    for them until they are analysed.
    """
    cues = cues or RekordboxCues(lib)
    summary = lib.disc(disc_id)
    result = DiscInput(disc_id, summary.name)
    if summary.kind != "audio":
        result.problems.append(
            "MP3 discs aren't supported: the card format for them isn't known."
        )
        return result
    entries = lib.disc_entries(disc_id)
    if len(entries) < 2:
        result.problems.append(
            "A disc of one track can't be synced: the player's ID for it isn't known."
        )
        return result
    unmeasured = [e for e in entries if not e.track.length_exact]
    if toc is not None:
        starts, lead_out = list(toc[0]), toc[1]
        result.from_drive = True
        if len(starts) != len(entries):
            result.problems.append(
                f"The disc in the drive has {len(starts)} tracks and this one {len(entries)}: "
                "it isn't this disc, or this disc was edited after it was burned."
            )
            return result
        if not unmeasured:  # (an estimate would only look like a difference)
            planned = disc_toc(entries, summary.pregap_sectors)
            known = known_tocs(lib, disc_id, entries, summary.pregap_sectors)
            drive = (list(starts), lead_out)
            if drive in [(list(k[0]), k[1]) for k in (planned, *known)]:
                differences = []
            else:
                differences = toc_differences(known[0] if known else planned, drive)
            if differences:
                result.warnings.append(
                    "The disc in the drive isn't the disc as planned now ("
                    + "; ".join(differences[:3])
                    + (", ..." if len(differences) > 3 else "")
                    + "): it was edited after its burn, or it is another disc. "
                    "The card entry follows the disc in the drive."
                )
    elif unmeasured:
        result.problems.append(
            f"{len(unmeasured)} of {len(entries)} tracks have no measured length yet "
            "(a burn, or the length check, measures them)."
        )
        return result
    else:
        known = known_tocs(lib, disc_id, entries, summary.pregap_sectors)
        starts, lead_out = known[0] if known else disc_toc(entries, summary.pregap_sectors)  # fmt: skip
    result.starts = list(starts)
    cues_by_track: dict[int, Sequence[Cue]] = {}
    for entry in entries:
        if found := cues.for_track(entry.track):
            cues_by_track[entry.position + 1] = found
    result.cue_tracks = len(cues_by_track)
    mapped = map_cues(cues_by_track, starts, lead_out)
    wanted_waves: dict[int, list[int] | None] = {}
    if waves:
        for entry in entries:
            track = entry.track
            if track.waveform is not None:
                wanted_waves[entry.position + 1] = list(track.waveform)
            elif track.missing:
                mapped.notes.append(
                    f"Track {entry.position + 1} has no waveform: its file is missing."
                )
            else:
                wanted_waves[entry.position + 1] = None
                result.to_analyse.append(track)
    this_id = cc.DiscId.from_toc(starts, lead_out)
    result.request = DiscRequest(
        this_id,
        mapped.cues,
        mapped.notes,
        wanted_waves,
        tuple(lib.card_baseline(this_id.key) or ()),
    )
    if toc is not None:
        return result
    seen = {result.request.disc_id.key}
    for other_starts, other_end in known[
        1:
    ]:  # (cue positions are absolute: map them again for each TOC)
        other_id = cc.DiscId.from_toc(other_starts, other_end)
        if other_id.key in seen:
            continue
        seen.add(other_id.key)
        again = map_cues(cues_by_track, other_starts, other_end)
        result.alternates.append(
            DiscRequest(
                other_id,
                again.cues,
                [],
                wanted_waves,
                tuple(lib.card_baseline(other_id.key) or ()),
            )
        )
    if known:
        return result
    if summary.pregap_sectors:
        result.warnings.append(
            "This disc has gaps between tracks: the card entry assumes a TOC that has not been "
            "checked against a burned disc."
        )
    if summary.last_burned_at is None:
        result.warnings.append(
            "This disc isn't marked as burned. The card entry matches the disc as planned now."
        )
    return result


@dataclass
class DiscSurvey:
    """One audio disc looked at against a card: what could be synced, and what that would do.

    `plan` is for this disc alone, on the card as it is (a sync of several discs plans them one
    after the other); None when the disc can't be synced or there is no card to compare with.
    """

    disc: DiscSummary
    path: str  # "Folder / Disc", as the label window names it
    input: DiscInput
    plan: DiscPlan | None
    extra_plans: list[DiscPlan] = field(
        default_factory=list
    )  # the plans for the disc's other known TOCs (`DiscInput.alternates`)

    @property
    def to_add(self) -> int:
        return len(self.plan.added) + len(self.plan.moved) if self.plan else 0

    @property
    def waves_to_add(self) -> int:
        """Waveforms a sync would write, counting the tracks it would analyse first."""
        if not self.plan:
            return 0
        return len(self.plan.waves_added) + len(self.plan.waves_pending)

    @property
    def writable(self) -> bool:
        """Whether a sync would write something for this disc."""
        return self.plan is not None and any(
            p.status in (Status.NEW_DISC, Status.NEW_FILE, Status.UPDATE)
            or bool(p.waves_pending)
            for p in (self.plan, *self.extra_plans)
        )

    @property
    def status(self) -> str:
        """A short line for a list; `detail` has all of it."""
        if self.input.problems:
            return "Can't sync"
        if self.plan is None:
            return "Choose a card"
        parts = []
        if self.to_add or not self.waves_to_add:
            parts.append(f"{self.to_add} cue{'s' if self.to_add != 1 else ''}")
        if self.waves_to_add:
            parts.append(
                f"{self.waves_to_add} waveform{'s' if self.waves_to_add != 1 else ''}"
            )
        n = " and ".join(parts) + " to add"
        if self.plan.status is Status.NOTHING and self.plan.waves_pending:
            return n
        match self.plan.status:
            case Status.NEW_DISC:
                return f"Not on the card: {n}"
            case Status.NEW_FILE:
                return f"On the card, no file yet: {n}"
            case Status.UPDATE:
                return n
            case Status.IN_SYNC:
                return "In sync" if not self.writable else "Other TOC versions to add"
            case _:
                return "No rekordbox cues"

    def detail(self, tracks: int | None = None) -> list[str]:
        """The dry run in words: why it can't be synced, warnings, and what would be added."""
        count = tracks if tracks is not None else self.disc.track_count
        out = [self.path]
        out += [f"  can't sync: {p}" for p in self.input.problems]
        out += [f"  warning: {w}" for w in self.input.warnings]
        if not self.input.problems:
            out.append(
                f"  rekordbox cues found on {self.input.cue_tracks} of {count} tracks"
            )
        if self.plan is not None:
            out += describe_plan(self.plan)
        for n, extra in enumerate(self.extra_plans, 2):
            out.append(f"  for the disc's TOC no. {n} (another copy of it):")
            out += describe_plan(extra)
        return out


# What the Sync tab's waveform preview shows for a track (the strips are painted by `ui/wave_strip.py`)
WAVE_WRITE, WAVE_CARD, WAVE_PENDING, WAVE_NONE = "write", "card", "pending", "none"


@dataclass(frozen=True)
class WaveRow:
    """One track of a disc in the waveform preview: its place, text, what is drawn and what to say."""

    number: int  # 1-based, as on the disc
    title: str
    state: str  # WAVE_WRITE | WAVE_CARD | WAVE_PENDING | WAVE_NONE
    values: tuple[int, ...] = ()  # the 100 columns to draw (empty: nothing to draw)
    note: str = ""
    # for the track browser: the track's own parts, its length on the disc and what a sync does with its cues
    name: str = ""
    artist: str = ""
    seconds: float = 0.0
    cues: str = ""
    marks: tuple[
        float, ...
    ] = ()  # where the memory cues fall in the track (0 to 1), for the strip's red dashes


def _marks(
    cues: Sequence, number: int, starts: Sequence[int], length: int
) -> tuple[float, ...]:
    """Where track `number`'s cues fall in it, as 0 to 1 (a loop at its start): positions are absolute frames."""
    if not 0 < number <= len(starts) or length <= 0:
        return ()
    start = starts[number - 1]
    found = {
        round(min(1.0, max(0.0, (c.position - start) / length)), 4)
        for c in cues
        if c.track == number
    }
    return tuple(sorted(found))


def wave_rows(
    survey: DiscSurvey, entries: Sequence[DiscEntry], enabled: bool = True
) -> list[WaveRow]:
    """For each track of the disc: the waveform a sync would write, the one the card already has
    (the player draws that one), or why there is none. `enabled` is the Write waveforms tick.

    With no card to compare with (`survey.plan` is None) a track shows its own analysed waveform."""
    plan = survey.plan
    rows = []
    request = survey.input.request
    wanted = Counter(c.track for c in request.wanted) if request else Counter()
    added = Counter(c.track for c in plan.added) if plan else Counter()
    # the cues the track will have on the card (what is there already and what is added), else those wanted
    placed = plan.file.cues if plan is not None and plan.file is not None else (request.wanted if request else ())  # fmt: skip
    starts = survey.input.starts
    for entry in entries:
        number = entry.position + 1
        title = " – ".join(p for p in (entry.artist, entry.title) if p) or "Untitled"
        own = entry.track.waveform
        if not enabled:
            row = WaveRow(number, title, WAVE_NONE, note="Write waveforms is off")
        elif plan is None:
            row = (
                WaveRow(
                    number, title, WAVE_WRITE, tuple(own), "analysed; choose a card"
                )
                if own is not None
                else WaveRow(number, title, WAVE_NONE, note="not analysed yet")
                if entry.track.missing
                else WaveRow(number, title, WAVE_PENDING, note="not analysed yet")
            )
        elif number in plan.waves_added and plan.file is not None:
            values = tuple(plan.file.fine[number - 1].time_values())
            row = WaveRow(
                number, title, WAVE_WRITE, values
            )  # (the usual case: nothing to say)
        elif number in plan.waves_kept and plan.file is not None:
            values = tuple(plan.file.fine[number - 1].time_values())
            row = WaveRow(number, title, WAVE_CARD, values, "already on the card: kept")
        elif number in plan.waves_pending:
            row = WaveRow(
                number, title, WAVE_PENDING, note="analysed first, when you sync"
            )
        else:
            why = "its file is missing" if entry.track.missing else "nothing to write"
            row = WaveRow(number, title, WAVE_NONE, note=why)
        if survey.input.problems or request is None:
            cues = ""
        elif plan is None:
            cues = f"{wanted[number]}" if wanted[number] else "–"
        elif added[number]:
            cues = f"{added[number]} to add"
        else:
            cues = "on the card" if wanted[number] else "–"
        rows.append(
            replace(
                row,
                name=entry.title,
                artist=entry.artist,
                seconds=entry.track.length_sectors / cc.FRAMES_PER_SECOND,
                cues=cues,
                marks=_marks(placed, number, starts, entry.track.length_sectors),
            )
        )
    return rows


def survey_discs(
    lib: Library,
    card: tuple[cc.IndexFile, Mapping[str, cc.TrackFile | None]] | None,
    cues: RekordboxCues,
    paths: Mapping[int, str],
    waves: bool = False,
) -> list[DiscSurvey]:
    """Every audio disc with a track, looked at against `card` (as `cdj_card.read_card` gives it;
    None: no card yet, so no plan). `paths` names each disc with its folders. `waves`: waveforms
    are wanted too (`prepare_disc`)."""
    surveys = []
    for disc in lib.discs():
        if disc.kind != "audio" or disc.track_count == 0:
            continue  # (only Dubcrate's own audio discs are synced)
        try:
            found = prepare_disc(lib, disc.id, cues, waves=waves)
        except RekordboxError as exc:
            found = DiscInput(
                disc.id, disc.name, problems=[f"Couldn't read rekordbox's cues: {exc}"]
            )
        plans: list[DiscPlan] = []
        if card is not None and found.request is not None:
            plans = plan_card(card[0], card[1], found.all_requests()).discs
        surveys.append(
            DiscSurvey(
                disc,
                paths.get(disc.id, disc.name),
                found,
                plans[0] if plans else None,
                plans[1:],
            )
        )
    return surveys
