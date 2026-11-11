"""Read rekordbox's analysis of a track: waveforms, beat grid, cues and phrases.

Every rekordbox track has up to three analysis files (.DAT, .EXT, .2EX) under
`<rekordbox folder>/share`. They are only ever read, never written.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from pyrekordbox.anlz import AnlzFile

from .waveform import COLUMNS_PER_SECOND, PREVIEW_COLUMNS, Waveform, from_bytes

log = logging.getLogger(__name__)

# What a phrase kind means depends on the track's "mood". "High" is checked against a real
# track and the rbxport display (kinds 1, 5, 3, 2 read INTRO, CHORUS, DOWN, UP); "mid" and
# "low" follow Deep Symmetry's notes on the format and are NOT checked against real files.
_PHRASES = {
    1: {1: "INTRO", 2: "UP", 3: "DOWN", 5: "CHORUS", 6: "OUTRO"},
    2: {1: "INTRO", 2: "VERSE 1", 3: "VERSE 2", 4: "VERSE 3", 5: "VERSE 4", 6: "VERSE 5", 7: "VERSE 6",
        8: "BRIDGE", 9: "CHORUS", 10: "OUTRO"},
    3: {1: "INTRO", 2: "VERSE 1", 3: "VERSE 1", 4: "VERSE 1", 5: "VERSE 2", 6: "VERSE 2", 7: "VERSE 2",
        8: "BRIDGE", 9: "CHORUS", 10: "OUTRO"},
}  # fmt: skip


@dataclass(frozen=True)
class Cue:
    kind: str  # "memory" | "hot" | "loop"
    time: float  # seconds
    end: float | None = None  # a loop's end
    slot: int | None = None  # a hot cue's number, 1 = A
    label: str = ""


@dataclass(frozen=True)
class Phrase:
    start: float
    end: float
    label: str


@dataclass
class Analysis:
    waveform: Waveform | None = None  # detail, 150 columns a second
    preview: Waveform | None = None  # the whole track in 1200 columns
    beat_times: np.ndarray = field(default_factory=lambda: np.zeros(0))
    beat_numbers: np.ndarray = field(
        default_factory=lambda: np.zeros(0, dtype=np.int8)
    )  # 1..4 within the bar
    bpm: float | None = None
    cues: tuple[Cue, ...] = ()
    phrases: tuple[Phrase, ...] = ()


def share_root(master_db: str | Path) -> Path:
    """Where the analysis files live, given the path of rekordbox's master.db."""
    return Path(master_db).parent / "share"


def analysis_file(root: Path, analysis_path: str, suffix: str) -> Path | None:
    """The `suffix` (".DAT", ".EXT" or ".2EX") analysis file for a track, if it exists."""
    candidate = (root / analysis_path.lstrip("/")).with_suffix(suffix)
    return candidate if candidate.is_file() else None


def _tag(parsed: AnlzFile, kind: str):
    return next((t for t in parsed.tags if t.type == kind), None)


def _parse(root: Path, analysis_path: str, suffix: str) -> AnlzFile | None:
    path = analysis_file(root, analysis_path, suffix)
    if path is None:
        return None
    try:
        return AnlzFile.parse_file(path)
    except Exception:
        log.warning("couldn't read analysis file %s", path, exc_info=True)
        return None


def read_preview(root: Path, analysis_path: str) -> Waveform | None:
    """A whole-track waveform of PREVIEW_COLUMNS columns: small and quick, for the track list.

    Built from the detail waveform when there is one (it looks like the overview; rekordbox's own
    1200-column preview is dominated by the highs), else from that preview.
    """
    parsed = _parse(root, analysis_path, ".2EX")
    if parsed is None:
        return None
    for kind in ("PWV7", "PWV6"):
        tag = _tag(parsed, kind)
        if tag is not None:
            wave = from_bytes(bytes(tag.get().entries), 0.0).resampled(
                PREVIEW_COLUMNS, how="mean"
            )
            return Waveform(
                wave.bands, 0.0
            )  # whole-track, so a rate in columns per second means nothing
    return None


def phrase_label(mood: int, kind: int) -> str:
    return _PHRASES.get(mood, {}).get(kind, f"PART {kind}")


def build_phrases(
    mood: int,
    entries: list[tuple[int, int]],
    end_beat: int,
    beat_times: np.ndarray,
    track_end: float,
) -> tuple[Phrase, ...]:
    """Phrases with times, from (beat, kind) entries and the beat grid. Beats are 1-based."""
    if len(beat_times) == 0:
        return ()

    def at(beat: int) -> float:
        return float(beat_times[min(max(beat, 1), len(beat_times)) - 1])

    phrases = []
    for i, (beat, kind) in enumerate(entries):
        following = entries[i + 1][0] if i + 1 < len(entries) else end_beat
        start = at(beat)
        end = track_end if following > len(beat_times) else at(following)
        if end > start:
            phrases.append(Phrase(start, end, phrase_label(mood, kind)))
    return tuple(phrases)


def _cue_from_entry(entry, loop_seen: bool = False) -> Cue | None:
    """A Cue from a PCO2/PCOB entry; None for anything that doesn't look like one."""
    time = getattr(entry, "time", None)
    if time is None:
        return None
    slot = int(getattr(entry, "hot_cue", 0) or 0)
    loop_time = getattr(entry, "loop_time", None)
    kind_name = str(getattr(entry, "type", "")).lower()
    is_loop = "loop" in kind_name or (
        loop_time not in (None, 0, 0xFFFFFFFF) and loop_time > time
    )
    comment = getattr(entry, "comment", "") or ""
    return Cue(
        kind="loop" if is_loop else ("hot" if slot else "memory"),
        time=time / 1000.0,
        end=loop_time / 1000.0 if is_loop else None,
        slot=slot or None,
        label=str(comment),
    )


def read_analysis(root: Path, analysis_path: str, track_end: float) -> Analysis | None:
    """Everything rekordbox knows about a track, or None if it has no analysis files."""
    dat = _parse(root, analysis_path, ".DAT")
    ext = _parse(root, analysis_path, ".EXT")
    two = _parse(root, analysis_path, ".2EX")
    if not (dat or ext or two):
        return None
    result = Analysis()

    pwv7 = _tag(two, "PWV7") if two else None
    if pwv7 is not None:
        result.waveform = from_bytes(bytes(pwv7.get().entries), COLUMNS_PER_SECOND)
    pwv6 = _tag(two, "PWV6") if two else None
    if pwv6 is not None:
        preview = from_bytes(bytes(pwv6.get().entries), 0)
        result.preview = Waveform(
            preview.bands, preview.columns / track_end if track_end else 0.0
        )

    pqtz = _tag(dat, "PQTZ") if dat else None
    if pqtz is not None:
        numbers, tempos, times = pqtz.get()
        result.beat_numbers = np.asarray(numbers, dtype=np.int8)
        result.beat_times = np.asarray(times, dtype=np.float64)
        result.bpm = float(tempos[0]) if len(tempos) else None

    cues: list[Cue] = []
    for source, kinds in ((ext, ("PCO2",)), (dat, ("PCOB",))):
        if source is None or cues:
            continue
        for tag in source.tags:
            if tag.type in kinds:
                cues += [
                    c
                    for e in tag.get().entries
                    if (c := _cue_from_entry(e)) is not None
                ]
    result.cues = tuple(sorted(cues, key=lambda c: c.time))

    pssi = _tag(ext, "PSSI") if ext else None
    if pssi is not None:
        data = pssi.get()
        entries = [(int(e.beat), int(e.kind)) for e in data.entries]
        result.phrases = build_phrases(
            int(data.mood), entries, int(data.end_beat), result.beat_times, track_end
        )
    return result
