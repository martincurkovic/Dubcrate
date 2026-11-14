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

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from dubcrate import cdj_card as cc
from dubcrate.analysis import Cue
from dubcrate.card_report import describe_plan, msf
from dubcrate.card_sync import DiscPlan, DiscRequest, Status, map_cues, plan_card
from dubcrate.library import Library
from dubcrate.library.models import DiscEntry, DiscSummary, Track
from dubcrate.library.rekordbox import RekordboxError, read_cues


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


def disc_toc(
    entries: Sequence[DiscEntry], pregap_sectors: int = 0
) -> tuple[list[int], int]:
    """Track starts and the lead-out, in absolute frames, for a disc's tracks as burned."""
    return cc.toc_from_lengths(
        [e.track.length_sectors for e in entries], pregap_sectors
    )


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
    waveform; a track with no rekordbox twin has none.
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
) -> DiscInput:
    """Everything `plan_card` needs for one disc, or the reasons it can't be synced.

    Give one `RekordboxCues` for all the discs of a sync so rekordbox's database is read once.
    With `toc` (the disc in the drive, from `drive_toc`) the ID and the cue positions come from the
    real disc: the tracks need no measured length, and the plan says if the disc differs from the
    one planned now (edited since it was burned, or another disc).
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
            if differences := toc_differences(planned, (starts, lead_out)):
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
        starts, lead_out = disc_toc(entries, summary.pregap_sectors)
    cues_by_track: dict[int, Sequence[Cue]] = {}
    for entry in entries:
        if found := cues.for_track(entry.track):
            cues_by_track[entry.position + 1] = found
    result.cue_tracks = len(cues_by_track)
    mapped = map_cues(cues_by_track, starts, lead_out)
    result.request = DiscRequest(
        cc.DiscId.from_toc(starts, lead_out), mapped.cues, mapped.notes
    )
    if toc is not None:
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

    @property
    def to_add(self) -> int:
        return len(self.plan.added) if self.plan else 0

    @property
    def writable(self) -> bool:
        """Whether a sync would write something for this disc."""
        return self.plan is not None and self.plan.status in (
            Status.NEW_DISC,
            Status.NEW_FILE,
            Status.UPDATE,
        )

    @property
    def status(self) -> str:
        """A short line for a list; `detail` has all of it."""
        if self.input.problems:
            return "Can't sync"
        if self.plan is None:
            return "Choose a card"
        n = f"{self.to_add} cue{'s' if self.to_add != 1 else ''} to add"
        match self.plan.status:
            case Status.NEW_DISC:
                return f"Not on the card: {n}"
            case Status.NEW_FILE:
                return f"On the card, no file yet: {n}"
            case Status.UPDATE:
                return n
            case Status.IN_SYNC:
                return "In sync"
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
        return out


def survey_discs(
    lib: Library,
    card: tuple[cc.IndexFile, Mapping[str, cc.TrackFile | None]] | None,
    cues: RekordboxCues,
    paths: Mapping[int, str],
) -> list[DiscSurvey]:
    """Every audio disc with a track, looked at against `card` (as `cdj_card.read_card` gives it;
    None: no card yet, so no plan). `paths` names each disc with its folders."""
    surveys = []
    for disc in lib.discs():
        if disc.kind != "audio" or disc.track_count == 0:
            continue  # (only Dubcrate's own audio discs are synced)
        try:
            found = prepare_disc(lib, disc.id, cues)
        except RekordboxError as exc:
            found = DiscInput(
                disc.id, disc.name, problems=[f"Couldn't read rekordbox's cues: {exc}"]
            )
        plan = None
        if card is not None and found.request is not None:
            plan = plan_card(card[0], card[1], [found.request]).discs[0]
        surveys.append(DiscSurvey(disc, paths.get(disc.id, disc.name), found, plan))
    return surveys
