"""Dubcrate discs as card sync requests: a disc's TOC from its tracks, and its rekordbox cues.

The library-facing half of `card_sync` (Qt-free; the library belongs to the GUI thread, so a GUI
caller does this on that thread and plans on a worker). Only audio discs are synced.

A disc's ID is its TOC, which is only known exactly when every track's CD length is measured
(`Track.cd_sectors`, written by a burn or by `measure_tracks`): a conservative estimate would
give an ID the disc never had. A disc edited after it was burned has a TOC that no longer
matches the physical disc, and there is no record of what was burned, so this says when a disc
has not been marked as burned. **A disc with gaps (`pregap_sectors`) is a guess**: how a burn
with gaps lays the TOC out has not been checked on a drive.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from dubcrate import cdj_card as cc
from dubcrate.analysis import Cue, read_analysis, share_root
from dubcrate.card_sync import DiscRequest, map_cues
from dubcrate.library import Library
from dubcrate.library.models import DiscEntry, Track


@dataclass
class DiscInput:
    """What `prepare_disc` found: a request ready for `plan_card`, or why there is none."""

    disc_id: int
    name: str
    request: DiscRequest | None = None
    problems: list[str] = field(default_factory=list)  # why this disc can't be synced
    warnings: list[str] = field(default_factory=list)  # synced, but be careful
    cue_tracks: int = 0  # tracks that had rekordbox cues


def disc_toc(
    entries: Sequence[DiscEntry], pregap_sectors: int = 0
) -> tuple[list[int], int]:
    """Track starts and the lead-out, in absolute frames, for a disc's tracks as burned."""
    return cc.toc_from_lengths(
        [e.track.length_sectors for e in entries], pregap_sectors
    )


def rekordbox_cues(lib: Library, track: Track) -> Sequence[Cue]:
    """The cues rekordbox holds for a track, read from its analysis files (never written).

    A file that is also in rekordbox (same path) uses rekordbox's, as the preview does. A track
    with no analysis gives none.
    """
    analysed = lib.rekordbox_twin(track) or track
    source = next((s for s in lib.sources() if s.id == analysed.source_id), None)
    if source is None or source.kind != "rekordbox" or not analysed.analysis_path:
        return ()
    analysis = read_analysis(
        share_root(Path(source.path)), analysed.analysis_path, track.duration
    )
    return analysis.cues if analysis else ()


def prepare_disc(lib: Library, disc_id: int) -> DiscInput:
    """Everything `plan_card` needs for one disc, or the reasons it can't be synced."""
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
    if unmeasured := [e for e in entries if not e.track.length_exact]:
        result.problems.append(
            f"{len(unmeasured)} of {len(entries)} tracks have no measured length yet "
            "(a burn, or the length check, measures them)."
        )
        return result

    starts, lead_out = disc_toc(entries, summary.pregap_sectors)
    cues_by_track: dict[int, Sequence[Cue]] = {}
    for entry in entries:
        cues = rekordbox_cues(lib, entry.track)
        if cues:
            cues_by_track[entry.position + 1] = cues
    result.cue_tracks = len(cues_by_track)
    mapped = map_cues(cues_by_track, starts, lead_out)
    result.request = DiscRequest(
        cc.DiscId.from_toc(starts, lead_out), mapped.cues, mapped.notes
    )
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
