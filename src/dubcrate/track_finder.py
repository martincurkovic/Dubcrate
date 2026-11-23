"""A track finder for the gig: one HTML page that says which disc a track is on (Qt-free).

You have the CDs, their printed labels and a phone. Type part of a title or artist and the page lists
every disc that has it, with the track number (and the folder, on an MP3 disc). The page is one file with
the data inside it and no network use of any kind, so it works offline; on an iPhone open it in an app that
runs local HTML (Safari and the Files app won't run its script).

**Read-only on every library.** This only reads Dubcrate's own discs and writes a NEW file you choose.

Each result also shows the track's BPM, key, year, genre and length, which are the **library track's own** (a disc
only edits the title and artist); the key is in the notation asked for (Camelot unless told otherwise).

The text is what is **written on the disc** (`DiscEntry.title` / `.artist`: the disc's own edit if any),
because it is the label you are looking at, unlike `playlist_export`, which sends the library's own text to
another program. Nothing is left out for a missing file: the CD is already burned. A track's number is its
place on an audio disc, and on an MP3 disc its number in its folder (as the files are numbered when burned).
"""

import base64
import json
import logging
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np

from dubcrate import card_sync, cdj_card, keys, labels
from dubcrate.analysis import Cue, read_beat_grid, read_preview, share_root
from dubcrate.audio.decode import DecodeError, decode_file
from dubcrate.barcount import nearest_beat
from dubcrate.cancel import Cancelled, CancelToken, cancellable_map
from dubcrate.card_discs import RekordboxCues
from dubcrate.library import Library
from dubcrate.library.cue_edits import is_memory
from dubcrate.library.models import DiscDir, Track
from dubcrate.library.rekordbox import read_cues
from dubcrate.playlist_export import ordered
from dubcrate.waveform import PREVIEW_COLUMNS, Waveform, compute_waveform

log = logging.getLogger(__name__)


class FinderError(Exception):
    """An expected failure (a folder that can't be written, no disc to list): the message is for the user."""


@dataclass(frozen=True)
class FinderTrack:
    number: int  # on an audio disc; in its folder on an MP3 disc
    title: str
    artist: str
    folder: str  # the folder on an MP3 disc, "A / B"; empty for the root and for an audio disc
    bpm: str = ""  # whole number, "" when unknown (as are the rest)
    key: str = ""
    year: str = ""
    genre: str = ""
    length: str = ""  # M:SS
    seconds: float = (
        0.0  # the length the waveform and the cues are placed against, 0 = not known
    )
    wave: str = ""  # the waveform, see `encode_wave`; "" when there is none
    cues: tuple[
        tuple[float, str, str], ...
    ] = ()  # (seconds, label, hot cue letter or ""), in time order
    # each cue's nearest beat: (bar, beat, milliseconds after it, negative if before); () = no beat grid
    beats: tuple[tuple[int, int, int], ...] = ()
    # what "Related" compares, whatever notation the page shows: the BPM to one decimal (0 = unknown) and
    # the key as Camelot ("" = unknown or not a key)
    bpm_exact: float = 0.0
    camelot: str = ""

    @property
    def info(self) -> list[str]:
        """What is known besides the name, as the page shows it: "128 BPM", "8A", "2020", "House", "6:12"."""
        return [
            text
            for text in (
                f"{self.bpm} BPM" if self.bpm else "",
                self.key,
                self.year,
                self.genre,
                self.length,
            )
            if text
        ]


@dataclass(frozen=True)
class FinderDisc:
    name: str
    where: (
        str  # the folders the disc is filed in, "Sets / Friday"; empty at the top level
    )
    kind: str  # "audio" or "mp3"
    tracks: tuple[FinderTrack, ...]


@dataclass
class Finder:
    discs: list[FinderDisc] = field(default_factory=list)
    empty_discs: list[str] = field(
        default_factory=list
    )  # discs with no tracks, left out
    without_wave: int = 0  # tracks listed with no waveform at all
    coarse_wave: int = 0  # tracks that only have the CDJ's 100-column waveform
    cue_problem: str = ""  # why memory cues could not be read, "" when they could

    @property
    def tracks(self) -> int:
        return sum(len(d.tracks) for d in self.discs)


@dataclass(frozen=True)
class Written:
    path: Path
    discs: int
    tracks: int


WAVE_COLUMNS = 400  # a whole track across a phone: about 1.2 KB a track as bytes
CueTables = Mapping[
    str, Mapping[str, Sequence[Cue]]
]  # rekordbox database path -> its cues by track


def encode_wave(wave: Waveform) -> str:
    """A whole-track waveform for the page: "3:" and base64 of `WAVE_COLUMNS` columns of (low, mid, high)
    bytes. Scaled like the player's strip, so the loud parts fill the height (the 98th percentile of the
    loudest band reaches the top; a stray peak may clip)."""
    bands = wave.resampled(WAVE_COLUMNS, how="mean").bands
    if not len(bands):
        return ""
    loudest = float(np.percentile(bands.max(axis=1), 98))
    if loudest > 0.05:
        bands = np.clip(bands / loudest, 0.0, 1.0)
    raw = np.clip(np.rint(bands * 255.0), 0, 255).astype(np.uint8).tobytes()
    return "3:" + base64.b64encode(raw).decode("ascii")


def encode_cdj_wave(values: bytes) -> str:
    """The CDJ's own waveform (100 values from 1 to 15): "1:" and base64, one byte a column. It is much
    coarser and only follows the bass, so it is what a track shows only when nothing better is stored."""
    return "1:" + base64.b64encode(bytes(values)).decode("ascii") if values else ""


class _Waves:
    """Where a track's whole-track waveform comes from, best first: rekordbox's own analysis (the track's,
    or its twin's when the same file is in rekordbox), the preview Dubcrate kept for the file (from the
    player, or from an earlier export), and last the CDJ's 100-column record written when the track went
    on an audio disc. Reads only."""

    def __init__(self, lib: Library) -> None:
        self._lib = lib
        self._roots = {
            s.id: share_root(s.path) for s in lib.sources() if s.kind == "rekordbox"
        }

    def detailed(self, track: Track, kept: dict[int, Waveform]) -> Waveform | None:
        analysed = track if track.analysis_path else self._lib.rekordbox_twin(track)
        if analysed is not None and analysed.analysis_path:
            root = self._roots.get(analysed.source_id)
            wave = read_preview(root, analysed.analysis_path) if root else None
            if wave is not None:
                return wave
        return kept.get(track.id)

    def encoded(self, track: Track, kept: dict[int, Waveform]) -> tuple[str, bool]:
        """(the waveform as the page takes it, whether it is only the coarse CDJ one); ("", False) if none."""
        wave = self.detailed(track, kept)
        if wave is not None:
            return encode_wave(wave), False
        if track.waveform:
            return encode_cdj_wave(track.waveform), True
        return "", False


def _disc_tracks(lib: Library, disc_id: int | None) -> list[Track]:
    ids = [disc_id] if disc_id is not None else [d.id for d in lib.discs()]
    seen: dict[int, Track] = {}
    for i in ids:
        for entry in lib.disc_entries(i):
            seen.setdefault(entry.track.id, entry.track)
    return list(seen.values())


def needs_analysis(lib: Library, *, disc_id: int | None = None) -> list[Track]:
    """The tracks on the discs that have no detailed waveform yet (no rekordbox analysis, nothing kept) and
    whose file can be read: `analyse_waves` makes theirs. Library thread only."""
    tracks = _disc_tracks(lib, disc_id)
    waves = _Waves(lib)
    kept = lib.previews_for(tracks)
    return [
        t
        for t in tracks
        if not t.missing and Path(t.path).is_file() and waves.detailed(t, kept) is None
    ]


def analyse_waves(
    tracks: Sequence[Track],
    progress: Callable[[int, int], None] | None = None,
    cancel: CancelToken | None = None,
    workers: int = 2,
) -> tuple[dict[int, Waveform], list[str]]:
    """Each track's whole-track waveform worked out from its audio, a couple at a time: ({track id: waveform},
    problems in plain words). Touches no database, so it is safe on a worker thread (`store_waves` keeps
    the result on the library's thread). If `cancel` is set it raises `Cancelled` whose `partial` is what
    was done so far (still good)."""

    def one(track: Track) -> tuple[Track, Waveform | str]:
        try:
            wave = compute_waveform(decode_file(track.path).mono())
            return track, wave.resampled(PREVIEW_COLUMNS, how="mean")
        except (DecodeError, OSError, ValueError) as exc:
            return track, f"{track.title or track.path}: {exc}"

    found: dict[int, Waveform] = {}
    problems: list[str] = []
    try:
        for done, (track, result) in enumerate(
            cancellable_map(one, list(tracks), workers, cancel), start=1
        ):
            if isinstance(result, str):
                problems.append(result)
            else:
                found[track.id] = result
            if progress:
                progress(done, len(tracks))
    except Cancelled:
        raise Cancelled((found, problems)) from None
    return found, problems


def store_waves(
    lib: Library, tracks: Sequence[Track], found: dict[int, Waveform]
) -> None:
    """Keep what `analyse_waves` found, as the player keeps a folder track's strip, so the next export (and
    the track list) has it at once."""
    for track in tracks:
        if track.id in found:
            lib.set_preview(track.path, track.file_size, found[track.id])


def rekordbox_databases(lib: Library) -> list[str]:
    """The paths of the imported rekordbox databases (their memory cues are read from a snapshot)."""
    return [s.path for s in lib.sources() if s.kind == "rekordbox"]


def read_cue_tables(paths: Sequence[str]) -> tuple[CueTables, str]:
    """rekordbox's cues for each database in `paths`: (tables, "" or why one could not be read). Reads a
    snapshot, never the live file, and touches no library, so it is safe on a worker thread."""
    tables: dict[str, Mapping[str, Sequence[Cue]]] = {}
    for path in paths:
        try:
            tables[str(Path(path))] = read_cues(Path(path))
        except Exception as exc:  # a database we can't read must not stop the export
            log.warning(
                "track finder: could not read cues from %s", path, exc_info=True
            )
            return tables, str(exc) or type(exc).__name__
    return tables, ""


DUPLICATE_SECONDS = card_sync.DUPLICATE_FRAMES / cdj_card.FRAMES_PER_SECOND


def card_cues(cues: Sequence[Cue]) -> tuple[tuple[float, str, str], ...]:
    """The cues a track's memory cues and hot cues become on the CDJ's card, as (seconds, label, hot cue
    letter): every hot cue (any slot) is kept as a plain memory cue there, and a hot cue and a memory cue at
    the same place (within `card_sync.DUPLICATE_FRAMES`) are one cue, as `card_sync.map_cues` makes them. The
    label and the letter are only for this page (the card has neither). Loops, and a hot cue saved as a
    loop, are not shown: they stay loops on the card."""
    found: list[list] = []
    for cue in sorted(cues, key=lambda c: c.time):
        if cue.time < 0:
            continue
        if is_memory(cue):
            hot = ""
        elif cue.kind == "hot" and cue.slot is not None and 1 <= cue.slot <= 26:
            hot = chr(ord("A") + cue.slot - 1)
        else:
            continue
        if found and cue.time - found[-1][0] <= DUPLICATE_SECONDS:
            found[-1][1] = found[-1][1] or cue.label
            found[-1][2] = found[-1][2] or hot
            continue
        found.append([round(cue.time, 2), cue.label, hot])
    return tuple((t, label, hot) for t, label, hot in found)


class _Cues:
    """A track's cues as they will be on the card (`card_cues`): rekordbox's, with the memory cues edited in
    Dubcrate laid over, as the player shows them. A failure to read the database is reported once and the
    tracks go without cues, rather than the export failing."""

    def __init__(
        self, lib: Library, tables: CueTables | None = None, problem: str = ""
    ) -> None:
        load = None if tables is None else (lambda path: tables.get(str(path), {}))
        self._provider = RekordboxCues(lib, load).for_track
        self.problem = problem

    def on_card(self, track: Track) -> tuple[tuple[float, str, str], ...]:
        if self.problem:
            return ()
        try:
            return card_cues(self._provider(track))
        except Exception as exc:  # (as above)
            log.warning("track finder: could not read cues", exc_info=True)
            self.problem = str(exc) or type(exc).__name__
            return ()


BeatGrid = tuple[np.ndarray, np.ndarray]  # (beat times in seconds, beat numbers 1 to 4)


def beat_sources(
    lib: Library, *, disc_id: int | None = None
) -> dict[int, tuple[Path, str]]:
    """Where each listed track's rekordbox beat grid is: {track id: (analysis folder, analysis path)}, through
    the rekordbox twin for a file that is also a folder track. A track with no rekordbox analysis is not in
    it, and has no bars and beats (a tempo alone can't say where the first beat is). Library thread only."""
    roots = {s.id: share_root(s.path) for s in lib.sources() if s.kind == "rekordbox"}
    found: dict[int, tuple[Path, str]] = {}
    for track in _disc_tracks(lib, disc_id):
        analysed = track if track.analysis_path else lib.rekordbox_twin(track)
        if (
            analysed is not None
            and analysed.analysis_path
            and analysed.source_id in roots
        ):
            found[track.id] = (roots[analysed.source_id], analysed.analysis_path)
    return found


def read_beat_grids(sources: Mapping[int, tuple[Path, str]]) -> dict[int, BeatGrid]:
    """The beat grid of each track in `sources` that has one (about 4 ms a track). Reads files only, so it is
    safe on a worker thread."""
    grids: dict[int, BeatGrid] = {}
    for track_id, (root, analysis_path) in sources.items():
        grid = read_beat_grid(root, analysis_path)
        if grid is not None:
            grids[track_id] = grid
    return grids


def cue_beats(
    cues: Sequence[tuple[float, str, str]], grid: BeatGrid | None
) -> tuple[tuple[int, int, int], ...]:
    """Each cue's nearest beat as (bar, beat, milliseconds after that beat, negative if before); () when there
    is no grid. Bars are counted as the player counts them (`barcount`)."""
    if grid is None:
        return ()
    found = []
    for time, _label, _hot in cues:
        nearest = nearest_beat(grid[0], grid[1], time)
        found.append(
            (0, 0, 0)
            if nearest is None
            else (nearest[0], nearest[1], round(nearest[2] * 1000))
        )
    return tuple(found)


_BAR_LABEL = re.compile(r"\s*\d+(\.\d+)?\s*bars?\s*", re.IGNORECASE)


def without_bar_labels(
    cues: Sequence[tuple[float, str, str]],
    beats: Sequence[tuple[int, int, int]],
) -> tuple[tuple[float, str, str], ...]:
    """`cues` with a label that is only a bar position ("1.1Bars", which rekordbox puts on some cues) blanked
    where the page shows its own bar.beat for that cue, so every cue reads alike. Without a beat grid the
    label is all there is, so it stays."""
    return tuple(
        (t, "" if beats and _BAR_LABEL.fullmatch(label) else label, hot)
        for t, label, hot in cues
    )


def _disc_where(lib: Library) -> dict[int | None, str]:
    """Each disc folder's id and its name with the folders above it; None (the top level) is empty."""
    folders = {f.id: f for f in lib.disc_folders()}
    where: dict[int | None, str] = {None: ""}

    def path(folder_id: int) -> str:
        if folder_id not in where:
            f = folders[folder_id]
            above = path(f.parent_id) if f.parent_id is not None else ""
            where[folder_id] = f"{above} / {f.name}" if above else f.name
        return where[folder_id]

    for folder_id in folders:
        path(folder_id)
    return where


def _dir_paths(dirs: list[DiscDir]) -> dict[int | None, str]:
    """Each folder on an MP3 disc and its name with the folders above it; None (the root) is empty."""
    by_id = {d.id: d for d in dirs}
    paths: dict[int | None, str] = {None: ""}

    def path(dir_id: int) -> str:
        if dir_id not in paths:
            d = by_id[dir_id]
            above = path(d.parent_id) if d.parent_id is not None else ""
            paths[dir_id] = f"{above} / {d.name}" if above else d.name
        return paths[dir_id]

    for dir_id in by_id:
        path(dir_id)
    return paths


def _disc(
    lib: Library,
    disc_id: int,
    where: dict[int | None, str],
    notation: str,
    found: "Finder",
    waves: _Waves | None,
    cues: _Cues | None,
    grids: Mapping[int, BeatGrid],
) -> FinderDisc | None:
    disc = lib.disc(disc_id)
    entries = lib.disc_entries(disc_id)
    folder_names: dict[int | None, str] = {None: ""}
    if disc.kind == "mp3":
        dirs = lib.disc_dirs(disc_id)
        entries = ordered(entries, dirs)
        folder_names = _dir_paths(dirs)
    numbers: dict[int | None, int] = {}
    tracks = []
    kept = lib.previews_for([e.track for e in entries]) if waves else {}
    for entry in entries:
        number = numbers[entry.dir_id] = numbers.get(entry.dir_id, 0) + 1
        track = entry.track
        wave, coarse = waves.encoded(track, kept) if waves else ("", False)
        if waves and not wave:
            found.without_wave += 1
        found.coarse_wave += coarse
        on_card = cues.on_card(track) if cues else ()
        beats = cue_beats(on_card, grids.get(track.id))
        on_card = without_bar_labels(on_card, beats)
        tracks.append(
            FinderTrack(
                number,
                entry.title or Path(track.path).stem,
                entry.artist,
                folder_names.get(entry.dir_id, ""),
                bpm=labels.bpm_text(track.bpm),
                key=keys.display(track.key, notation),
                year=labels.year_text(track.year),
                genre=" ".join(track.genre.split()),
                length=labels.clock_text(track.duration),
                seconds=track.cd_sectors / 75 if track.cd_sectors else track.duration,
                wave=wave,
                cues=on_card,
                beats=beats,
                bpm_exact=round(track.bpm, 1) if track.bpm and track.bpm > 0 else 0.0,
                camelot=_camelot_of(track.key),
            )
        )
    if not tracks:
        return None
    return FinderDisc(
        disc.name, where.get(disc.folder_id, ""), disc.kind, tuple(tracks)
    )


def _camelot_of(key: str | None) -> str:
    parsed = keys.parse_key(key) if key else None
    return keys.camelot(*parsed) if parsed else ""


def collect(
    lib: Library,
    *,
    disc_id: int | None = None,
    notation: str = keys.CAMELOT,
    waveforms: bool = True,
    memory_cues: bool = True,
    cue_tables: CueTables | None = None,
    cue_problem: str = "",
    grids: Mapping[int, BeatGrid] | None = None,
) -> Finder:
    """One disc, or (the default) every disc, with keys in `notation`. Raises `FinderError` if no disc
    has a track.

    Each track also gets its waveform (`waveforms`: see `_Waves`; `needs_analysis` and `analyse_waves`
    make the missing ones first) and its cues as they will be on the card (`memory_cues`: memory cues and hot cues). The cues come from rekordbox's
    database read once here, or from `cue_tables` that a worker already read (`read_cue_tables`, with
    `cue_problem` if that failed). Each cue is also placed on its nearest beat from rekordbox's beat grids
    (`grids`, which a worker can read first with `read_beat_grids`; read here if not given). Both are only read: nothing is written to rekordbox."""
    where = _disc_where(lib)
    ids = [disc_id] if disc_id is not None else [d.id for d in lib.discs()]
    found = Finder()
    wave_source = _Waves(lib) if waveforms else None
    cue_source = _Cues(lib, cue_tables, cue_problem) if memory_cues else None
    if grids is None:
        grids = (
            read_beat_grids(beat_sources(lib, disc_id=disc_id)) if memory_cues else {}
        )
    for i in ids:
        disc = _disc(lib, i, where, notation, found, wave_source, cue_source, grids)
        if disc is None:
            found.empty_discs.append(lib.disc(i).name)
        else:
            found.discs.append(disc)
    found.discs.sort(key=lambda d: (d.where.casefold(), d.name.casefold()))
    if cue_source is not None:
        found.cue_problem = cue_source.problem
    if not found.discs:
        raise FinderError("There is nothing to list: no disc has a track on it.")
    return found


def summary(found: Finder) -> str:
    """What was left out or is missing, for a message after an export; empty when nothing is."""
    lines = []
    if found.empty_discs:
        lines.append("Discs left out (no tracks): " + ", ".join(found.empty_discs))
    if found.without_wave:
        lines.append(f"{found.without_wave} track(s) have no waveform.")
    if found.coarse_wave:
        lines.append(
            f"{found.coarse_wave} track(s) only have the CDJ's coarse waveform (their audio could not be read)."
        )
    if found.cue_problem:
        lines.append(f"Cues could not be read: {found.cue_problem}")
    return "\n".join(lines)


# -- the page ------------------------------------------------------------------------


def page_data(found: Finder, made: date) -> str:
    """The data the page holds, as JSON that is safe inside a `<script>` element."""
    data = {
        "made": made.isoformat(),
        "discs": [
            {
                "name": d.name,
                "where": d.where,
                "kind": d.kind,
                "tracks": [
                    # info is the line shown; the rest is what "All tracks" sorts on.
                    [
                        t.number,
                        t.title,
                        t.artist,
                        t.folder,
                        t.info,
                        t.bpm,
                        t.key,
                        t.year,
                        t.genre,
                        t.length,
                        round(t.seconds, 2),
                        t.wave,
                        # each cue: seconds, label, hot cue letter, then its nearest beat (bar, beat, ms after)
                        [
                            [*cue, *(t.beats[i] if t.beats else (0, 0, 0))]
                            for i, cue in enumerate(t.cues)
                        ],
                        t.bpm_exact,
                        t.camelot,
                    ]
                    for t in d.tracks
                ],
            }
            for d in found.discs
        ],
    }
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # A name containing `</script>` or `<!--` must not end or confuse the element.
    return (
        text.replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def html_text(found: Finder, made: date | None = None) -> str:
    return PAGE.replace("__DATA__", page_data(found, made or date.today()))


def write_html(found: Finder, path: Path, made: date | None = None) -> Written:
    """Write the page at `path` (an existing file is replaced: the caller asked for that name)."""
    text = html_text(found, made)
    part = path.with_name(path.name + ".part")
    try:
        part.write_text(text, encoding="utf-8", newline="\n")
        os.replace(part, path)
    except OSError as exc:
        part.unlink(missing_ok=True)
        raise FinderError(f"Could not write {path}: {exc.strerror or exc}") from exc
    log.info("exported track finder: %d disc(s), %d track(s) to %s", len(found.discs), found.tracks, path)  # fmt: skip
    return Written(path, len(found.discs), found.tracks)


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#17171c" media="(prefers-color-scheme: dark)">
<meta name="theme-color" content="#ece7f5" media="(prefers-color-scheme: light)">
<title>Dubcrate Track Finder</title>
<style>
  /* The colours are the app's own (ui/theme.py): its dark palette, and its light one when the phone
     asks for light. The disc stripes are the capacity bar's eight track colours. */
  :root { color-scheme: dark light;
          --bg: #1e1e24; --panel: #202028; --bar: #17171c; --input: #2a2a35; --card: #2a2a35;
          --border: #40404c; --edge: #33333e; --text: #e4e4ea; --bright: #f0f0f4; --muted: #8e8e9c;
          --accent: #4a90d9; --mark: #e8922b;
          --wave-bg: #0b0b0f; --w-low: #2f62ff; --w-mid: #e8922b; --w-high: #f4f4f4; --cue: #ff4d4d; }
  @media (prefers-color-scheme: light) {
    :root { --bg: #f5f3fa; --panel: #ffffff; --bar: #ece7f5; --input: #ffffff; --card: #ffffff;
            --border: #d6cce8; --edge: #ddd0ee; --text: #2a2438; --bright: #191320; --muted: #7a7290;
            --accent: #8b5cf6; --mark: #d9651c;
            --wave-bg: #ffffff; --w-high: #4b4b5e; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font: 17px/1.3 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         padding: 0 max(12px, env(safe-area-inset-right)) env(safe-area-inset-bottom)
                  max(12px, env(safe-area-inset-left)); }
  /* The brand bar scrolls away; the search and button rows stay, and slide out of the way while you
     scroll down (back on scroll up). On a phone with a notch the strip under the status bar is
     painted so the list never shows through it. */
  body::before { content: ""; position: fixed; top: 0; left: 0; right: 0; z-index: 5;
                 height: env(safe-area-inset-top, 0px); background: var(--bar); }
  header { position: sticky; top: env(safe-area-inset-top, 0px); z-index: 1; background: var(--bg);
           margin: 0 calc(-1 * max(12px, env(safe-area-inset-right))) 0
                   calc(-1 * max(12px, env(safe-area-inset-left)));
           padding: 8px max(12px, env(safe-area-inset-right)) 6px
                    max(12px, env(safe-area-inset-left));
           transition: transform .2s ease; }
  .brand { display: flex; align-items: baseline; gap: 8px; background: var(--bar);
           border-bottom: 1px solid var(--edge); padding: 6px 0 6px;
           padding-top: max(6px, env(safe-area-inset-top));
           margin: 0 calc(-1 * max(12px, env(safe-area-inset-right))) 0
                   calc(-1 * max(12px, env(safe-area-inset-left)));
           padding-left: max(12px, env(safe-area-inset-left)); }
  .brand b { color: var(--bright); letter-spacing: .02em; }
  .brand span { color: var(--muted); font-size: 14px; }
  .bar button.danger { background: #c0392b; border-color: #c0392b; color: #fff; }
  .searchrow { display: flex; gap: 8px; align-items: center; }
  input { width: 100%; font-size: 18px; padding: 9px 12px; border-radius: 6px;
          border: 1px solid var(--border); background: var(--input); color: var(--bright);
          outline: none; }
  input:focus { border-color: var(--accent); }
  .note { color: var(--muted); font-size: 14px; padding: 8px 2px 0; }
  [hidden] { display: none !important; }
  .bar { display: flex; gap: 8px; align-items: center; margin-top: 8px; }
  .bar button, .bar select, .frow select, .frow button, .searchrow button { font: inherit; font-size: 15px;
         color: var(--bright); background: var(--input); border: 1px solid var(--border);
         border-radius: 6px; padding: 6px 10px; min-height: 38px; white-space: nowrap; }
  .searchrow button[aria-pressed="true"], .frow button[aria-pressed="true"], .bar button[aria-pressed="true"] { background: var(--accent); border-color: var(--accent);
         color: #fff; }
  .sortrow { display: flex; gap: 8px; flex: 1; min-width: 0; }
  .sortrow select { flex: 1; min-width: 0; }
  #filters { padding: 10px 0 2px; }
  .frow { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
  .frow .l { width: 50px; color: var(--muted); font-size: 15px; }
  .frow input { font-size: 17px; padding: 8px 10px; flex: 1; min-width: 0; }
  .frow select { flex: 1; min-width: 0; }
  .grip { flex: none; align-self: stretch; display: flex; align-items: center; margin: -11px 0 -11px -8px;
          padding: 0 8px; font-size: 26px; color: var(--muted); cursor: grab; user-select: none;
          -webkit-user-select: none; -webkit-touch-callout: none; touch-action: none; }
  .hit.lift { position: relative; z-index: 3; box-shadow: 0 6px 18px rgba(0,0,0,.45);
              border-color: var(--accent); }
  .star { background: none; border: 0; padding: 2px 4px; margin: -2px -6px -2px 0;
          font-size: 30px; line-height: 1; color: var(--muted); }
  .star.on { color: var(--mark); }
  .hit.off { opacity: .45; }
  .group { position: sticky; top: var(--head, 0px); z-index: 0; background: var(--bg);
           color: var(--accent); font-weight: 700; font-size: 14px; letter-spacing: .04em;
           padding: 10px 2px 4px; }
  main { padding-top: 4px; }
  /* The letter strip: a column down the right edge you drag a finger along to jump through a long list. */
  #index { position: fixed; right: env(safe-area-inset-right, 0px); top: 50%; transform: translateY(-50%);
           width: 26px; box-sizing: border-box; z-index: 5; display: flex; flex-direction: column;
           border-radius: 13px 0 0 13px; background: var(--bar); opacity: .92; color: var(--accent);
           font-size: 12px; font-weight: 700; touch-action: none; user-select: none;
           -webkit-user-select: none; -webkit-touch-callout: none; }
  #index span { height: 20px; line-height: 20px; text-align: center; pointer-events: none; }
  #bubble { position: fixed; right: 44px; top: 50%; transform: translateY(-50%); z-index: 6;
            min-width: 64px; height: 64px; padding: 0 14px; box-sizing: border-box; border-radius: 14px;
            display: flex; align-items: center; justify-content: center; pointer-events: none;
            background: var(--accent); color: #fff; font-size: 34px; font-weight: 700; }
  #index[hidden], #bubble[hidden] { display: none; }
  .hit { background: var(--card); border: 1px solid var(--edge); border-left: 5px solid var(--stripe);
         border-radius: 6px; padding: 9px 12px; margin: 0 0 8px;
         display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: center; }
  .what { flex: 1; min-width: 0; }
  /* A long title shows two lines and the artist and info one each; tap the card for the rest. */
  .title { color: var(--bright); font-weight: 600; font-size: 17px; overflow-wrap: anywhere;
           display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; }
  .artist { color: var(--muted); font-size: 15px; white-space: nowrap; overflow: hidden;
            text-overflow: ellipsis; }
  .info { color: var(--muted); font-size: 13px; margin-top: 2px; white-space: nowrap; overflow: hidden;
          text-overflow: ellipsis; }
  .hit.open .title { display: block; }
  .hit.open .artist, .hit.open .info { white-space: normal; overflow-wrap: anywhere; }
  /* Which disc, and where on it: one line at the bottom of the card. */
  .where { display: flex; flex-wrap: wrap; align-items: baseline; margin-top: 4px; }
  .where > * { margin-right: 6px; }
  .where > *:not(:last-child)::after { content: " ·"; margin-left: 6px; color: var(--muted); font-weight: 400; }
  .disc { color: var(--bright); font-weight: 700; font-size: 16px; overflow-wrap: anywhere; }
  .num { color: var(--mark); font-size: 15px; font-weight: 600; }
  .sub { color: var(--muted); font-size: 13px; overflow-wrap: anywhere; }
  /* The waveform shows when a card is open: the whole track, a faint line and a label at each minute, a
     numbered flag at each memory cue; tap it for the time at that spot. */
  .wavebox { flex: 0 0 100%; display: none; }
  .hit.open .wavebox { display: block; }
  .wave { display: block; width: 100%; height: 68px; border-radius: 4px; border: 1px solid var(--edge); }
  .readout { color: var(--muted); font-size: 13px; padding: 5px 2px 0; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; padding-top: 6px; }
  .chip { font: inherit; font-size: 14px; color: var(--bright); background: var(--input);
          border: 1px solid var(--border); border-radius: 14px; padding: 2px 10px 2px 3px;
          display: flex; align-items: center; gap: 6px; }
  .chip i { font-style: normal; font-size: 12px; font-weight: 700; color: #fff; background: var(--cue);
            border-radius: 50%; width: 20px; height: 20px; display: inline-flex;
            align-items: center; justify-content: center; }
  .chip .bb { font-size: 13px; color: var(--accent); font-weight: 600; }
  .chip b { font-size: 12px; font-weight: 700; color: var(--mark); border: 1px solid var(--mark);
            border-radius: 4px; padding: 0 4px; }
  .chip small { color: var(--muted); font-size: 13px; }
  .none { color: var(--muted); font-size: 13px; padding: 2px; }
  /* Compact cards: "Title – Artist" run together, no info line (tap the card to see it). */
  .compact .hit { padding: 6px 10px; margin-bottom: 5px; }
  .compact .title, .compact .artist { display: inline; white-space: normal; font-size: 15px; }
  .compact .title { font-size: 16px; }
  .compact .artist::before { content: " – "; }
  .compact .info { display: none; }
  .compact .hit.open .info { display: block; }
  .compact .hit.open .title, .compact .hit.open .artist { display: inline; }
  .compact .where { margin-top: 2px; }
  .compact .disc { font-size: 15px; }
  /* Related tracks: a button in an open card, and a bar above the list that says what it is related to. */
  .relbar { flex: 0 0 100%; display: none; }
  .hit.open .relbar { display: block; }
  .relbar button { font: inherit; font-size: 15px; color: var(--bright); background: var(--input);
         border: 1px solid var(--border); border-radius: 6px; padding: 6px 12px; min-height: 38px; }
  .relnote { color: var(--accent); font-size: 13px; margin-top: 2px; }
  #related { padding: 10px 0 2px; }
  .relhead { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
  .relhead button, #related .frow select, #related .frow button { font: inherit; font-size: 15px;
         color: var(--bright); background: var(--input); border: 1px solid var(--border);
         border-radius: 6px; padding: 6px 10px; min-height: 38px; white-space: nowrap; }
  #related .frow button { flex: 1; min-width: 0; }
  #related .frow button[aria-pressed="true"] { background: var(--accent); border-color: var(--accent); color: #fff; }
  #reltitle { flex: 1; min-width: 0; color: var(--bright); font-weight: 600; overflow-wrap: anywhere; }
  #reltitle small { display: block; color: var(--muted); font-weight: 400; font-size: 13px; }
</style>
</head>
<body>
<div class="brand"><b>Dubcrate</b><span>Track Finder</span></div>
<header>
  <div class="searchrow">
    <input id="q" type="search" placeholder="Title or artist" autocomplete="off"
           autocorrect="off" autocapitalize="off" spellcheck="false" enterkeyhint="search">
    <button id="filter" type="button" aria-pressed="false">Filter</button>
  </div>
  <div class="bar">
    <button id="all" type="button" aria-pressed="false">All tracks</button>
    <button id="tagged" type="button" aria-pressed="false">Tagged</button>
    <button id="clear" type="button" hidden>Clear</button>
    <button id="rellast" type="button" hidden>Related to last</button>
    <span class="sortrow" id="sortrow">
      <select id="sort" aria-label="Sort by"></select>
      <button id="dir" type="button" aria-label="Reverse the order">\u2191</button>
    </span>
  </div>
</header>
<div class="note" id="status"></div>
<section id="related" hidden>
  <div class="relhead"><button id="relback" type="button">‹ Back</button><div id="reltitle"></div></div>
  <div class="frow"><span class="l">BPM</span>
    <select id="reltol" aria-label="How far the BPM may differ">
      <option value="3">± 3%</option><option value="6" selected>± 6%</option>
      <option value="10">± 10%</option><option value="16">± 16%</option></select>
    <select id="relsort" aria-label="Order within each heading">
      <option value="bpm" selected>Nearest BPM</option><option value="title">Title</option>
      <option value="disc">Disc order</option></select></div>
  <div class="frow"><span class="l">Also</span>
    <button id="relhalf" type="button" aria-pressed="false">Half / double</button>
    <button id="relenergy" type="button" aria-pressed="false">Energy moves</button></div>
  <div class="frow"><span class="l">Genre</span>
    <button id="relgenre" type="button" aria-pressed="false">Same genre</button></div>
</section>
<section id="filters" hidden>
  <div class="frow"><span class="l">BPM</span>
    <input id="bpmMin" type="number" inputmode="numeric" placeholder="from" aria-label="BPM from">
    <input id="bpmMax" type="number" inputmode="numeric" placeholder="to" aria-label="BPM to"></div>
  <div class="frow"><span class="l">Year</span>
    <input id="yearMin" type="number" inputmode="numeric" placeholder="from" aria-label="Year from">
    <input id="yearMax" type="number" inputmode="numeric" placeholder="to" aria-label="Year to"></div>
  <div class="frow"><span class="l">Key</span><select id="fkey" aria-label="Key"></select></div>
  <div class="frow"><span class="l">Genre</span><select id="fgenre" aria-label="Genre"></select>
    <button id="fclear" type="button">Clear</button></div>
  <div class="frow"><span class="l">Cards</span>
    <button id="compact" type="button" aria-pressed="false">Compact</button></div>
  <div class="frow"><span class="l">Jump</span>
    <button id="indexbtn" type="button" aria-pressed="true">Letter strip</button></div>
</section>
<main id="results"></main>
<nav id="index" hidden aria-label="Jump through the list"></nav>
<div id="bubble" hidden></div>
<script id="data" type="application/json">__DATA__</script>
<script>
// Inside a function so no name here can clash with a browser global (window.top is one: a
// top-level `function top` stops the whole script from loading).
(() => {
"use strict";
const data = JSON.parse(document.getElementById("data").textContent);

// Case, accents and punctuation don't matter: "Bjork" finds "Bj\\u00f6rk", "dont" finds "Don't" (an apostrophe is dropped, not a word break).
function norm(s) {
  return s.normalize("NFD").replace(/[\\u0300-\\u036f]/g, "").toLowerCase()
          .replace(/['\\u2019`]/g, "")
          .replace(/[^\\p{L}\\p{N}]+/gu, " ").trim();
}

const STRIPES = ["#3987e5", "#d95926", "#199e70", "#c98500",
                 "#d55181", "#008300", "#9085e9", "#e66767"];
const rows = [];
data.discs.forEach((disc, i) => {
  disc.stripe = STRIPES[i % STRIPES.length];
  disc.index = i;
  for (const [number, title, artist, folder, info, bpm, key, year, genre, length, seconds, wave, cues, bpmExact, camelot] of disc.tracks) {
    const clock = /^([0-9]+):([0-9]{2})$/.exec(length);
    rows.push({ disc, number, title, artist, folder, info, genre, key, order: rows.length,
                id: JSON.stringify([disc.name, folder, number, title]),
                text: " " + norm(title + " " + artist),
                titleKey: norm(title), artistKey: norm(artist),
                bpmN: parseInt(bpm, 10) || 0, yearN: parseInt(year, 10) || 0,
                secs: clock ? +clock[1] * 60 + +clock[2] : 0, keyN: keyRank(key),
                dur: seconds || (clock ? +clock[1] * 60 + +clock[2] : 0), wave, cues,
                bpmX: bpmExact || 0, camelot: camelot || "" });
  }
});

// Camelot 1A, 1B, 2A ... or musical "A min", "C# maj" (by pitch, minor first); anything else is unsorted.
function keyRank(k) {
  let m = /^([0-9]{1,2})([AB])$/.exec(k);
  if (m) return +m[1] * 2 + (m[2] === "B" ? 1 : 0);
  m = /^([A-G])(#?) (min|maj)$/.exec(k);
  if (m) return ({ C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 }[m[1]] + (m[2] ? 1 : 0)) * 2 +
                (m[3] === "maj" ? 1 : 0);
  return null;
}

// value: what is sorted on (null = not known, always last); group: the heading above a run.
const initial = k => { const c = k.charAt(0).toUpperCase(); return !c ? "#" : /[0-9]/.test(c) ? "0–9" : c; };
const SORTS = {
  // Best match: words that start a word of the title or artist first (see `matching`); with nothing typed it is the title.
  match:  { label: "Best match", value: r => r.rank, group: null },
  title:  { label: "Title",  value: r => r.titleKey || null,  group: r => initial(r.titleKey), index: true },
  artist: { label: "Artist", value: r => r.artistKey || null, group: r => r.artistKey ? initial(r.artistKey) : "No artist", index: true },
  genre:  { label: "Genre",  value: r => r.genre.toLowerCase() || null, group: r => r.genre || "No genre" },
  bpm:    { label: "BPM",    value: r => r.bpmN || null,  group: r => r.bpmN ? Math.floor(r.bpmN / 10) * 10 + "s" : "No BPM", index: true },
  key:    { label: "Key",    value: r => r.keyN,          group: r => r.key || "No key", index: true },
  year:   { label: "Year",   value: r => r.yearN || null, group: r => r.yearN || "No year", index: true },
  length: { label: "Length", value: r => r.secs || null,  group: null },
  disc:   { label: "Disc",   value: r => r.disc.index,    group: r => r.disc.name },
};

const q = document.getElementById("q");
const status = document.getElementById("status");
const results = document.getElementById("results");
const allButton = document.getElementById("all");
const sortRow = document.getElementById("sortrow");
const sortSelect = document.getElementById("sort");
const dirButton = document.getElementById("dir");
const tagButton = document.getElementById("tagged");
let browse = false, tagView = false, sortKey = "match", descending = false;
// Related tracks: the track being looked at is the top of `relStack` (tapping a related track and then
// Related again goes one deeper; Back goes up, and the last Back returns to the list it came from).
const relStack = [];
const related = () => relStack[relStack.length - 1] || null;

// Tagged: tracks to play later, in the order they were tagged. They are kept in the browser's storage
// on this device, keyed by disc, folder, track number and title, so a newer export keeps them. Some
// viewer apps can't keep storage between visits: `storageOk` says whether this one did.
const TAGS_KEY = "dubcrate-finder-tags";
const byId = new Map(rows.map(r => [r.id, r]));
let storageOk = false;
let tags = [];
try {
  const saved = JSON.parse(localStorage.getItem(TAGS_KEY) || "[]");
  if (Array.isArray(saved)) tags = saved.filter(id => byId.has(id));
} catch (e) { tags = []; }

function saveTags() {
  try {
    const text = JSON.stringify(tags);
    localStorage.setItem(TAGS_KEY, text);
    storageOk = localStorage.getItem(TAGS_KEY) === text;
  } catch (e) { storageOk = false; }
}

function showTagCount() {
  tagButton.textContent = tags.length ? "Tagged " + tags.length : "Tagged";
}

// Filters: BPM and year ranges (a track with no BPM or year is left out while that range is set),
// one key and one genre. They narrow search and All tracks together, never the Tagged list.
const filterButton = document.getElementById("filter");
const filterPanel = document.getElementById("filters");
const fields = { bpmMin: "bpmMin", bpmMax: "bpmMax", yearMin: "yearMin", yearMax: "yearMax" };
const inputs = {};
for (const k of Object.keys(fields)) inputs[k] = document.getElementById(fields[k]);
const keySelect = document.getElementById("fkey");
const genreSelect = document.getElementById("fgenre");
function fill(select, any, options) {
  const o = el("option", "", any);
  o.value = "";
  select.append(o);
  for (const [value, label] of options) {
    const e = el("option", "", label);
    e.value = value;
    select.append(e);
  }
}

function number(input) {
  const v = parseFloat(input.value);
  return Number.isNaN(v) ? null : v;
}

function filterState() {
  const f = { bpmMin: number(inputs.bpmMin), bpmMax: number(inputs.bpmMax),
              yearMin: number(inputs.yearMin), yearMax: number(inputs.yearMax),
              key: keySelect.value, genre: genreSelect.value };
  f.count = (f.bpmMin !== null || f.bpmMax !== null) + (f.yearMin !== null || f.yearMax !== null) +
            (f.key !== "") + (f.genre !== "");
  return f;
}

function passes(r, f) {
  if (f.bpmMin !== null && !(r.bpmN && r.bpmN >= f.bpmMin)) return false;
  if (f.bpmMax !== null && !(r.bpmN && r.bpmN <= f.bpmMax)) return false;
  if (f.yearMin !== null && !(r.yearN && r.yearN >= f.yearMin)) return false;
  if (f.yearMax !== null && !(r.yearN && r.yearN <= f.yearMax)) return false;
  if (f.key && r.key !== f.key) return false;
  if (f.genre && r.genre.toLowerCase() !== f.genre) return false;
  return true;
}

{
  const keysHere = [...new Set(rows.map(r => r.key).filter(Boolean))];
  keysHere.sort((a, b) => { const x = keyRank(a), y = keyRank(b);
    return x === y ? a.localeCompare(b) : x === null ? 1 : y === null ? -1 : x - y; });
  fill(keySelect, "Any key", keysHere.map(k => [k, k]));
  const genres = new Map();
  for (const r of rows) if (r.genre && !genres.has(r.genre.toLowerCase())) genres.set(r.genre.toLowerCase(), r.genre);
  fill(genreSelect, "Any genre", [...genres].sort((a, b) => a[0].localeCompare(b[0])));
}

// Clear the Tagged list: two taps, so a slip of the thumb can't wipe a set (the first one asks, and
// goes back to Clear after a few seconds; some viewer apps have no confirm box, so this makes its own).
const clearButton = document.getElementById("clear");
let clearArmed = false, clearTimer = 0;
function resetClear() {
  clearArmed = false;
  clearTimeout(clearTimer);
  clearButton.textContent = "Clear";
  clearButton.className = "";
}
clearButton.addEventListener("click", () => {
  if (!clearArmed) {
    clearArmed = true;
    clearButton.textContent = "Tap again to clear " + tags.length;
    clearButton.className = "danger";
    clearTimer = setTimeout(resetClear, 4000);
    return;
  }
  resetClear();
  tags = [];
  saveTags();
  showTagCount();
  show();
});

function toggleTag(r) {
  const i = tags.indexOf(r.id);
  if (i < 0) tags.push(r.id); else tags.splice(i, 1);
  saveTags();
  showTagCount();
  showRelLast();
  if (tagView) showStatus();
}

// "Related to last": the most recently tagged track's related tracks, from Tagged or from a related list, so
// a set goes tag, see what follows, tag. Only for a last tagged track that has a BPM and a key, and not
// while that track is the one being looked at.
const relLastButton = document.getElementById("rellast");
function showRelLast() {
  const last = tags.length ? byId.get(tags[tags.length - 1]) : null;
  const src = related();
  relLastButton.hidden = !(last && canRelate(last) && last !== src && (src || tagView));
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text) e.textContent = text;
  return e;
}

for (const [key, s] of Object.entries(SORTS)) {
  const o = el("option", "", s.label);
  o.value = key;
  sortSelect.append(o);
}

function matching(text, pool = rows) {
  const words = norm(text).split(" ").filter(Boolean);
  if (!words.length) return [];
  const found = [];
  for (const r of pool) {
    if (!words.every(w => r.text.includes(w))) continue;
    // Words that start a word in the title or artist rank above ones found inside a word.
    r.rank = words.every(w => r.text.includes(" " + w)) ? 0 : 1;
    found.push(r);
  }
  return found;
}

// The tracks being listed (all, or those matching the search and filters) in the chosen order. A track with nothing for the
// chosen column goes last whichever way it runs; ties fall back to the title, then the disc.
function sorted(list, key) {
  const s = SORTS[key], dir = descending ? -1 : 1;
  const tie = (a, b) => key === "disc" ? a.order - b.order
    : key === "match" ? a.text.localeCompare(b.text)
    : a.titleKey.localeCompare(b.titleKey) || a.order - b.order;
  return list.sort((a, b) => {
    const x = s.value(a), y = s.value(b);
    if (x === null || y === null) return x === y ? tie(a, b) : x === null ? 1 : -1;
    const c = typeof x === "string" ? x.localeCompare(y) : x - y;
    return c ? c * dir : tie(a, b);
  });
}

// -- related tracks: a BPM near this one and a key that mixes with it.
// Keys are compared as Camelot whatever notation the page shows. From a key on the wheel (number, A or B):
//   0 same key; 1 smooth: one step round the wheel, or the same number's other letter (relative major/minor);
//   2 energy up: two steps up the wheel, or one step up with the letter swapped (a diagonal);
//   3 energy down: the same going down; -1 not related.
const TIERS = ["Same key", "Smooth: one step or relative", "Energy up: +2 or diagonal", "Energy down: −2 or diagonal"];
function camelotParts(k) {
  const m = /^([0-9]{1,2})([AB])$/.exec(k);
  return m ? [+m[1], m[2]] : null;
}
function keyTier(a, b) {
  const x = camelotParts(a), y = camelotParts(b);
  if (!x || !y) return -1;
  const step = (y[0] - x[0] + 12) % 12, same = x[1] === y[1];
  if (same) return step === 0 ? 0 : (step === 1 || step === 11) ? 1 : step === 2 ? 2 : step === 10 ? 3 : -1;
  return step === 0 ? 1 : step === 1 ? 2 : step === 11 ? 3 : -1;
}
// How far `bpm` is from `from`, in percent of it: the smallest gap, also against double and half
// time when asked (then `times` says which: 2 = it is at double time).
function bpmGap(from, bpm, halfDouble) {
  let best = null;
  for (const times of halfDouble ? [1, 2, 0.5] : [1]) {
    const pct = (bpm - from * times) / (from * times) * 100;
    if (best === null || Math.abs(pct) < Math.abs(best.pct)) best = { pct, times };
  }
  return best;
}
// What to do on the pitch fader to bring the track to the BPM you are playing: a track 3% faster is
// pitched down 3%. The CDJ's ranges are 6, 10 and 16 percent, so a bigger move says which it needs.
const PITCH_RANGES = [6, 10, 16];
function gapText(g) {
  const size = Math.abs(g.pct);
  const prefix = g.times === 2 ? "double time, " : g.times === 0.5 ? "half time, " : "";
  if (size < 0.05) return prefix + "same BPM";
  const range = PITCH_RANGES.find(n => size <= n);
  return prefix + "pitch " + (g.pct > 0 ? "\u2212" : "+") + size.toFixed(1) + "%" +
         (range > PITCH_RANGES[0] ? " · needs \u00b1" + range + " range" : "");
}
const relTol = document.getElementById("reltol");
const relHalf = document.getElementById("relhalf");
const relEnergy = document.getElementById("relenergy");
const relPanel = document.getElementById("related");
const relTitle = document.getElementById("reltitle");
const relGenre = document.getElementById("relgenre");
let relSkipped = 0, relHidden = 0, relNoGenre = 0, relGenreIgnored = false;
let halfOn = false, energyOn = false, genreOn = false;
const relSort = document.getElementById("relsort");
// "Deep House" and "Tech House" are one genre here: two genres match when they share a word (capitals,
// accents and punctuation ignored; "and", "the", "of" and "music" don't count).
const GENRE_SKIP = new Set(["and", "the", "of", "music"]);
function genreWords(r) {
  if (!r.gw) r.gw = new Set(norm(r.genre).split(" ").filter(w => w && !GENRE_SKIP.has(w)));
  return r.gw;
}
const sharesGenre = (a, b) => { for (const w of genreWords(a)) if (genreWords(b).has(w)) return true; return false; };

// The window, the two switches and the genre switch are kept on this device, like Compact.
const RELATED_KEY = "dubcrate-finder-related";
function saveRelated() {
  try {
    localStorage.setItem(RELATED_KEY, JSON.stringify({ tol: relTol.value, half: halfOn, energy: energyOn, genre: genreOn, order: relSort.value }));
  } catch (e) { /* not kept */ }
}
try {
  const kept = JSON.parse(localStorage.getItem(RELATED_KEY) || "{}");
  if (["3", "6", "10", "16"].includes(kept.tol)) relTol.value = kept.tol;
  if (["bpm", "title", "disc"].includes(kept.order)) relSort.value = kept.order;
  halfOn = kept.half === true;
  energyOn = kept.energy === true;
  genreOn = kept.genre === true;
} catch (e) { /* nothing kept, or unreadable */ }
relHalf.setAttribute("aria-pressed", String(halfOn));
relEnergy.setAttribute("aria-pressed", String(energyOn));
relGenre.setAttribute("aria-pressed", String(genreOn));
const canRelate = r => r.bpmX > 0 && r.camelot !== "";

function relatedTo(src) {
  const tol = parseFloat(relTol.value) || 6;
  const halfDouble = halfOn, energy = energyOn;
  const found = [];
  relSkipped = relHidden = relNoGenre = 0;
  // A track with no genre can't be narrowed by genre: the switch is then ignored, and the status says so.
  relGenreIgnored = genreOn && genreWords(src).size === 0;
  const byGenre = genreOn && !relGenreIgnored;
  for (const r of rows) {
    // the same song on another disc is not a next track
    if (r === src || (r.titleKey === src.titleKey && r.artistKey === src.artistKey)) continue;
    if (!canRelate(r)) { relSkipped++; continue; }
    const tier = keyTier(src.camelot, r.camelot);
    if (tier < 0) continue;
    const gap = bpmGap(src.bpmX, r.bpmX, halfDouble);
    if (Math.abs(gap.pct) > tol) continue;
    if (byGenre && !sharesGenre(src, r)) { if (genreWords(r).size === 0) relNoGenre++; continue; }
    if (tier >= 2 && !energy) { relHidden++; continue; }
    r.tier = tier;
    r.gap = Math.abs(gap.pct);
    r.relNote = gapText(gap);
    found.push(r);
  }
  // Within a heading: the nearest BPM first, or by title, or in disc order; ties fall back to the title, then the disc.
  const inside = relSort.value === "title" ? (a, b) => 0 : relSort.value === "disc" ? (a, b) => a.order - b.order
    : (a, b) => a.gap - b.gap;
  return found.sort((a, b) => a.tier - b.tier || inside(a, b) || a.titleKey.localeCompare(b.titleKey) || a.order - b.order);
}

// -- the waveform: "3:" + base64 of (low, mid, high) bytes a column, or "1:" + one 0-15 byte a column
// (the CDJ's coarse one). Drawn in the app's own colours; the cues are red, as rekordbox's memory cues.
const WAVE_HEIGHT = 68;
const redraws = [];

function fmtTime(t) {
  t = Math.max(0, Math.floor(t));
  return Math.floor(t / 60) + ":" + String(t % 60).padStart(2, "0");
}

function decodeWave(text) {
  if (!text) return null;
  const bytes = Uint8Array.from(atob(text.slice(2)), c => c.charCodeAt(0));
  return { bands: text.charAt(0) === "3" ? 3 : 1, bytes };
}

function paintWave(canvas, wave, dur, cues, cursor) {
  const dpr = window.devicePixelRatio || 1;
  const w = Math.round((canvas.clientWidth || 300) * dpr), h = Math.round(WAVE_HEIGHT * dpr);
  canvas.width = w;
  canvas.height = h;
  const g = canvas.getContext("2d");
  const css = getComputedStyle(document.documentElement);
  const colour = name => css.getPropertyValue(name).trim() || "#888";
  g.fillStyle = colour("--wave-bg");
  g.fillRect(0, 0, w, h);
  const axis = 13 * dpr, mid = (h - axis) / 2, reach = mid - 3 * dpr;
  const x = t => Math.min(w - 1, Math.max(0, t / dur * w));
  // a faint line at every minute, behind the waveform
  if (dur >= 60) {
    g.fillStyle = "rgba(128,128,140,.28)";
    for (let t = 60; t < dur; t += 60) g.fillRect(Math.round(x(t)), 0, Math.max(1, Math.round(dpr * .7)), h - axis);
  }
  if (wave) {
    const n = wave.bytes.length / wave.bands, step = w / n;
    const bandColours = [colour("--w-low"), colour("--w-mid"), colour("--w-high")];
    for (let band = 0; band < wave.bands; band++) {  // low first, high last, each covering the one before
      g.fillStyle = wave.bands === 1 ? bandColours[0] : bandColours[band];
      for (let i = 0; i < n; i++) {
        const v = wave.bands === 1 ? wave.bytes[i] / 15 : wave.bytes[i * 3 + band] / 255;
        const half = Math.max(dpr, v * reach);
        g.fillRect(i * step, mid - half, step + .6, half * 2);
      }
    }
  }
  if (dur > 0) {
    // the minute scale under the waveform: a tick each minute, a label as often as there is room
    g.fillStyle = "rgba(128,128,140,.8)";
    g.font = Math.round(10 * dpr) + "px -apple-system, sans-serif";
    g.textBaseline = "bottom";
    const every = [1, 2, 5, 10, 15, 30].find(m => 60 * m / dur * w >= 40 * dpr) || 60;
    for (let t = 60, m = 1; t < dur; t += 60, m++) {
      const px = Math.round(x(t));
      g.fillRect(px, h - axis, Math.max(1, Math.round(dpr * .7)), 4 * dpr);
      if (m % every === 0) {
        const label = fmtTime(t), tw = g.measureText(label).width;
        if (px + tw / 2 < w) g.fillText(label, Math.max(0, px - tw / 2), h - 1);
      }
    }
    cues.forEach(([t], i) => {  // numbered flags at the memory cues
      const px = Math.round(x(t)), label = String(i + 1);
      g.fillStyle = colour("--cue");
      g.fillRect(px, 0, Math.max(1.5, 1.5 * dpr), h - axis);
      g.font = "bold " + Math.round(9 * dpr) + "px -apple-system, sans-serif";
      g.textBaseline = "middle";
      const fw = g.measureText(label).width + 6 * dpr, fx = px + fw > w ? px - fw : px;
      g.fillRect(fx, 0, fw, 12 * dpr);
      g.fillStyle = "#fff";
      g.fillText(label, fx + 3 * dpr, 6.5 * dpr);
    });
    if (cursor !== null) {
      g.fillStyle = colour("--bright");
      g.fillRect(Math.round(x(cursor)), 0, Math.max(1, Math.round(dpr)), h - axis);
    }
  }
}

// The waveform and the memory cues for an open card; `redraw` paints it at the card's current width.
// What a tap says about a cue: "Cue 2 · 1:45 · hot cue A · Drop" (a hot cue is a memory cue on the card).
// "17.1" is bar 17, beat 1 on the nearest beat of rekordbox's grid; "≈" when the cue is clearly off the beat
// (more than 40 ms), and the readout then says by how much. Nothing for a track with no beat grid.
const OFF_BEAT_MS = 40;
function barBeat(cue) {
  const [, , , bar, beat, off] = cue;
  return bar ? (Math.abs(off) > OFF_BEAT_MS ? "≈" : "") + bar + "." + beat : "";
}

function cueText(i, cue) {
  const [t, label, hot, bar, beat, off] = cue;
  const where = bar ? " · bar " + bar + "." + beat + (Math.abs(off) > OFF_BEAT_MS ? " (" + (off > 0 ? "+" : "-") + Math.abs(off) + " ms)" : "") : "";
  return "Cue " + (i + 1) + " · " + fmtTime(t) + (hot ? " · hot cue " + hot : "") + where + (label ? " · " + label : "");
}

function waveBox(r) {
  const wave = decodeWave(r.wave), cues = r.cues, dur = r.dur;
  if (!wave && !cues.length) {
    const none = el("div", "wavebox none", "No waveform for this track");
    none.redraw = () => {};
    return none;
  }
  const box = el("div", "wavebox");
  let cursor = null;
  const canvas = el("canvas", "wave");
  const readout = el("div", "readout");
  const show = (t, text) => {
    cursor = t;
    readout.textContent = text;
    paintWave(canvas, wave, dur, cues, cursor);
  };
  const shown = () => paintWave(canvas, wave, dur, cues, cursor);
  readout.textContent = (dur ? "Length " + fmtTime(dur) : "") + (cues.length ? " · tap a cue or the waveform for a time" : "");
  box.append(canvas, readout);
  if (dur > 0) {
    canvas.addEventListener("click", e => {
      const rect = canvas.getBoundingClientRect();
      let t = Math.min(dur, Math.max(0, (e.clientX - rect.left) / rect.width * dur));
      // close to a cue (a finger is wide): jump to that cue
      const near = cues.findIndex(([c]) => Math.abs(c - t) / dur * rect.width < 14);
      if (near >= 0) { t = cues[near][0]; show(t, cueText(near, cues[near])); }
      else show(t, fmtTime(t) + " of " + fmtTime(dur));
    });
  }
  if (cues.length) {
    const chips = el("div", "chips");
    cues.forEach((cue, i) => {
      const [t, label, hot] = cue;
      const chip = el("button", "chip");
      chip.type = "button";
      const n = el("i", "", String(i + 1));
      chip.append(n, document.createTextNode(fmtTime(t)));
      if (hot) chip.append(el("b", "", hot));
      if (barBeat(cue)) chip.append(el("span", "bb", barBeat(cue)));
      if (label) chip.append(el("small", "", label));
      chip.addEventListener("click", () => show(t, cueText(i, cue)));
      chips.append(chip);
    });
    box.append(chips);
  }
  box.redraw = shown;
  redraws.push(box);
  return box;
}
window.addEventListener("resize", () => { for (const b of redraws) if (b.isConnected) b.redraw(); });

function card(r) {
  const hit = el("div", "hit");
  hit.style.setProperty("--stripe", r.disc.stripe);
  const what = el("div", "what");
  what.append(el("div", "title", r.title));
  if (r.artist) what.append(el("div", "artist", r.artist));
  if (r.info.length) what.append(el("div", "info", r.info.join(" · ")));
  if (related() && r.relNote) what.append(el("div", "relnote", r.relNote));
  const where = el("div", "where");
  where.append(el("div", "disc", r.disc.name));
  where.append(el("div", "num", "Track " + r.number));
  if (r.folder) where.append(el("div", "sub", r.folder));
  if (r.disc.where) where.append(el("div", "sub", r.disc.where));
  what.append(where);
  // In the Tagged list an untagged track stays, dimmed, until the list is shown again, so a slip is undone with one tap.
  const star = el("button", "star");
  star.type = "button";
  let open = false;
  const paint = () => {
    const on = tags.includes(r.id);
    star.textContent = on ? "★" : "☆";
    star.className = "star" + (on ? " on" : "");
    star.setAttribute("aria-label", on ? "Remove from Tagged" : "Tag this track");
    hit.className = "hit" + (tagView && !related() && !on ? " off" : "") + (open ? " open" : "");
  };
  star.addEventListener("click", () => { toggleTag(r); paint(); });
  // A tap on the card (not on the star or the handle) shows or hides the rest of a long title.
  let box = null;
  hit.addEventListener("click", e => {
    if (e.target && e.target.closest && e.target.closest(".star, .grip, .wavebox, .relbar")) return;
    open = !open;
    paint();
    if (open) {
      if (!box) {
        box = waveBox(r);
        hit.append(box);
        if (canRelate(r)) {
          const bar = el("div", "relbar");
          const go = el("button", "", "Related tracks");
          go.type = "button";
          go.addEventListener("click", () => openRelated(r));
          bar.append(go);
          hit.append(bar);
        }
      }
      box.redraw();  // (the width is known once the card is open)
    }
  });
  paint();
  hit._rid = r.id;
  if (tagView && !related()) {
    // Hold the handle and move: the card follows the finger and the others make room.
    const grip = el("div", "grip", "≡");
    grip.setAttribute("aria-label", "Drag to move");
    grip.addEventListener("pointerdown", e => startDrag(e, hit, grip));
    grip.addEventListener("pointermove", e => moveDrag(e, hit));
    grip.addEventListener("pointerup", () => endDrag(hit));
    grip.addEventListener("pointercancel", () => endDrag(hit));
    hit.append(grip, what, star);
  } else hit.append(what, star);
  return hit;
}

// -- moving a tagged track up or down. Positions are kept in the page's own coordinates so the card
// stays under the finger while the page scrolls (it scrolls by itself near the top and bottom edge).
let drag = null;
const isCard = e => e && typeof e.className === "string" && e.className.startsWith("hit");

function startDrag(e, hit, grip) {
  e.preventDefault();
  try { grip.setPointerCapture(e.pointerId); } catch (err) { /* a synthetic event has no pointer */ }
  drag = { hit, y: e.clientY + window.scrollY, last: e.clientY, frame: 0 };
  hit.className += " lift";
  autoScroll();
}

function moveDrag(e, hit) {
  if (!drag || drag.hit !== hit) return;
  drag.last = e.clientY;
  followFinger();
}

function followFinger() {
  const d = drag, hit = d.hit;
  hit.style.transform = "translateY(" + (d.last + window.scrollY - d.y) + "px)";
  // Swap places with a neighbour once the card's middle passes the neighbour's middle.
  for (let n = 0; n < 5; n++) {
    const box = hit.getBoundingClientRect(), mid = box.top + box.height / 2;
    const prev = hit.previousElementSibling, next = hit.nextElementSibling;
    let other = null, below = false;
    if (isCard(next)) {
      const b = next.getBoundingClientRect();
      if (mid > b.top + b.height / 2) { other = next; below = true; }
    }
    if (!other && isCard(prev)) {
      const b = prev.getBoundingClientRect();
      if (mid < b.top + b.height / 2) other = prev;
    }
    if (!other) break;
    if (below) results.insertBefore(other, hit); else results.insertBefore(hit, other);
    hit.style.transform = "";
    const shift = box.top - hit.getBoundingClientRect().top;
    d.y = d.last + window.scrollY - shift;
    hit.style.transform = "translateY(" + shift + "px)";
  }
}

function autoScroll() {
  if (!drag) return;
  const edge = document.querySelector("header").getBoundingClientRect().bottom + 50;
  const low = window.innerHeight - 60, y = drag.last;
  const speed = y < edge ? -Math.min(18, (edge - y) / 4 + 2) : y > low ? Math.min(18, (y - low) / 4 + 2) : 0;
  if (speed) { window.scrollBy(0, speed); followFinger(); }
  drag.frame = requestAnimationFrame(autoScroll);
}

function endDrag(hit) {
  if (!drag || drag.hit !== hit) return;
  cancelAnimationFrame(drag.frame);
  drag = null;
  hit.style.transform = "";
  hit.className = hit.className.replace(" lift", "");
  // The new order is the cards' order on screen; a track not drawn yet keeps its place after them.
  const shownIds = [...results.children].filter(c => c._rid).map(c => c._rid);
  tags = shownIds.filter(id => tags.includes(id)).concat(tags.filter(id => !shownIds.includes(id)));
  saveTags();
}

// A long list is drawn a screenful at a time: the next lot when the end comes into view.
const CHUNK = 120;
const sentinel = el("div", "");
const watcher = typeof IntersectionObserver === "undefined" ? null :
  new IntersectionObserver(entries => {
    if (entries.some(e => e.isIntersecting)) draw();
  }, { rootMargin: "800px" });
let pending = [], heading = null, shown = 0;

// `upTo`: draw at least as far as that place in the list (a jump from the letter strip).
function draw(upTo = 0) {
  if (watcher) watcher.unobserve(sentinel);
  sentinel.remove();
  const group = related() ? (r => TIERS[r.tier]) : listing ? SORTS[activeSort].group : null;
  const end = watcher ? Math.min(pending.length, Math.max(shown + CHUNK, upTo + 1)) : pending.length;
  for (; shown < end; shown++) {
    const r = pending[shown];
    if (group) {
      const g = String(group(r));
      if (g !== heading) {
        const h = el("div", "group", g);
        h._at = shown;  // where in the list this heading is, for the letter strip
        results.append(h);
        heading = g;
      }
    }
    results.append(card(r));
  }
  if (shown < pending.length) { results.append(sentinel); watcher.observe(sentinel); }
}

function showStatus() {
  const text = q.value.trim();
  const src = related();
  if (src) {
    const tol = (relTol.value || "6") + "%";
    const parts = [shownCount + " related · within " + tol];
    if (relHidden) parts.push(relHidden + " more with energy moves hidden");
    if (relSkipped) parts.push(relSkipped + " can't be compared (no BPM or key)");
    if (relGenreIgnored) parts.push("this track has no genre, so Same genre is ignored");
    else if (relNoGenre) parts.push(relNoGenre + " left out (no genre)");
    status.textContent = parts.join(" · ");
  } else if (tagView) {
    const note = storageOk ? "kept on this device" : "NOT kept: this app can't save them";
    status.textContent = tags.length
      ? tags.length + " tagged · " + note
      : "Nothing tagged yet: tap the star on a track · " + note;
  } else if (!listing) {
    status.textContent = rows.length + " tracks on " + data.discs.length +
                         " discs · made " + data.made;
  } else if (!shownCount) {
    status.textContent = filtering ? "No track matches the filters" : "Not on any disc";
  } else {
    status.textContent = (text || filtering ? shownCount + " of " + rows.length : rows.length) +
      " tracks · by " + SORTS[activeSort].label + (descending ? ", reversed" : "");
  }
}

// Something is listed when there is a search, a filter, or All tracks; the sort menu orders it. Best
// match only means something while there is text to match: without any it is the title.
let shownCount = 0, listing = false, filtering = false, activeSort = "title";
function show() {
  const text = q.value.trim();
  const f = filterState();
  filtering = f.count > 0 && !tagView;
  listing = !tagView && (text !== "" || filtering || browse);
  const src = related();
  if (src) listing = false;  // the tiers are the headings, not a sort
  filterButton.textContent = f.count ? "Filter · " + f.count : "Filter";
  sortRow.hidden = tagView || src !== null;
  relPanel.hidden = src === null;
  if (src) relTitle.replaceChildren(
    el("span", "", src.title + (src.artist ? " – " + src.artist : "")),
    el("small", "", "Related to " + src.info.join(" · ")));
  clearButton.hidden = !(tagView && tags.length) || src !== null;
  showRelLast();
  if (clearButton.hidden) resetClear();
  activeSort = sortKey === "match" && !text ? "title" : sortKey;
  let found;
  if (src) found = relatedTo(src);
  else if (tagView) found = tags.map(id => byId.get(id));
  else if (listing) {
    const pool = filtering ? rows.filter(r => passes(r, f)) : rows;
    found = sorted(text ? matching(text, pool) : pool.slice(), activeSort);
  } else found = [];
  results.replaceChildren();
  shownCount = found.length;
  pending = found;
  heading = null; shown = 0;
  showStatus();
  buildIndex(found);
  draw();
}

function scrollUp() { if (typeof window !== "undefined") window.scrollTo(0, 0); }

function setMode(mode) {  // "search", "all" or "tagged"
  relStack.length = 0;
  browse = mode === "all";
  tagView = mode === "tagged";
  if (browse) q.value = "";  // All tracks shows everything, whatever was typed
  resetClear();
  allButton.setAttribute("aria-pressed", String(browse));
  tagButton.setAttribute("aria-pressed", String(tagView));
  scrollUp();
  show();
}
allButton.addEventListener("click", () => setMode(browse ? "search" : "all"));
tagButton.addEventListener("click", () => setMode(tagView ? "search" : "tagged"));
filterButton.addEventListener("click", () => {
  const open = filterPanel.hidden;
  filterPanel.hidden = !open;
  filterButton.setAttribute("aria-pressed", String(open));
  if (open) scrollUp();
});
for (const input of Object.values(inputs)) input.addEventListener("input", show);
keySelect.addEventListener("change", show);
genreSelect.addEventListener("change", show);
document.getElementById("fclear").addEventListener("click", () => {
  for (const input of Object.values(inputs)) input.value = "";
  keySelect.value = genreSelect.value = "";
  show();
});
sortSelect.addEventListener("change", () => { sortKey = sortSelect.value; scrollUp(); show(); });
dirButton.addEventListener("click", () => {
  descending = !descending;
  dirButton.textContent = descending ? "↓" : "↑";
  scrollUp();
  show();
});

// Typing is a search: it leaves All tracks and Tagged.
q.addEventListener("input", () => { relStack.length = 0; if (tagView || browse) setMode("search"); else show(); });
function openRelated(r) {
  relStack.push(r);
  scrollUp();
  show();
}
document.getElementById("relback").addEventListener("click", () => { relStack.pop(); scrollUp(); show(); });
relTol.addEventListener("change", () => { saveRelated(); show(); });
relSort.addEventListener("change", () => { saveRelated(); show(); });
relLastButton.addEventListener("click", () => openRelated(byId.get(tags[tags.length - 1])));
relHalf.addEventListener("click", () => {
  halfOn = !halfOn;
  relHalf.setAttribute("aria-pressed", String(halfOn));
  saveRelated();
  show();
});
relEnergy.addEventListener("click", () => {
  energyOn = !energyOn;
  relEnergy.setAttribute("aria-pressed", String(energyOn));
  saveRelated();
  show();
});
relGenre.addEventListener("click", () => {
  genreOn = !genreOn;
  relGenre.setAttribute("aria-pressed", String(genreOn));
  saveRelated();
  show();
});
// Compact cards: a switch in the filter panel, kept on this device like the tags.
const COMPACT_KEY = "dubcrate-finder-compact";
const compactButton = document.getElementById("compact");
function setCompact(on, save) {
  document.body.className = on ? "compact" : "";
  compactButton.setAttribute("aria-pressed", String(on));
  if (save) try { localStorage.setItem(COMPACT_KEY, on ? "1" : "0"); } catch (e) { /* not kept */ }
}
try { setCompact(localStorage.getItem(COMPACT_KEY) === "1", false); } catch (e) { setCompact(false, false); }
compactButton.addEventListener("click", () => setCompact(document.body.className !== "compact", true));

// The search and button rows slide away while scrolling down and come back on scrolling up (always shown
// near the top, and while typing). `--head` is how far down the sticky letter headings stick.
const header = document.querySelector("header");
let hiddenHeader = false, lastY = window.scrollY, holdUntil = 0, pressed = false;  // pressed: a finger on the letter strip
function stickTop() { return parseFloat(getComputedStyle(header).top) || 0; }
function syncHead() {
  document.documentElement.style.setProperty("--head",
    (hiddenHeader ? stickTop() : stickTop() + header.offsetHeight) + "px");
}
function hideHeader(hide) {
  if (hide === hiddenHeader) return;
  hiddenHeader = hide;
  header.style.transform = hide ? "translateY(" + -(header.offsetHeight + stickTop() + 4) + "px)" : "";
  syncHead();
}
window.addEventListener("scroll", () => {
  const y = window.scrollY;
  if (pressed || Date.now() < holdUntil) { hideHeader(y >= 80); lastY = y; return; }  // a jump from the letter strip
  if (y < 80 || document.activeElement === q) { hideHeader(false); lastY = y; return; }
  if (Math.abs(y - lastY) < 8) return;  // slow movement adds up until it counts
  hideHeader(y > lastY);
  lastY = y;
}, { passive: true });
window.addEventListener("resize", syncHead);
syncHead();

// The letter strip: one stop per heading of the current sort (a letter, a BPM decade, a key, a year),
// thinned to fit the screen's height. Dragging a finger along it jumps to the stop under it, drawing the
// list as far as that first if need be. Only for sorts with short headings, only while something is
// listed, and it can be switched off (kept on this device like Compact).
const INDEX_KEY = "dubcrate-finder-index";
const indexNav = document.getElementById("index");
const bubble = document.getElementById("bubble");
const indexButton = document.getElementById("indexbtn");
const STOP_HEIGHT = 20;
let indexOn = true, stops = [], lastStop = null;

const stopLabel = g => g === "0–9" ? "#" : g.length > 4 ? "?" : g;

function buildIndex(found) {
  stops = [];
  const sort = SORTS[activeSort];
  if (listing && sort.index) {
    let last = null;
    found.forEach((r, i) => {
      const g = String(sort.group(r));
      if (g !== last) { stops.push({ group: g, at: i }); last = g; }
    });
    const room = Math.max(5, Math.floor((window.innerHeight || 600) * 0.7 / STOP_HEIGHT));
    if (stops.length > room)
      stops = Array.from({ length: room }, (_, k) => stops[Math.round(k * (stops.length - 1) / (room - 1))]);
  }
  const on = indexOn && stops.length > 1;
  indexNav.hidden = !on;
  results.style.paddingRight = on ? "30px" : "";  // room, so the strip never covers a card
  indexNav.style.height = stops.length * STOP_HEIGHT + "px";
  indexNav.replaceChildren(...stops.map(s => el("span", "", stopLabel(s.group))));
}

function jumpTo(stop) {
  if (shown <= stop.at) draw(stop.at);
  const kids = [...results.children];
  const at = kids.findIndex(c => c._at === stop.at);
  if (at < 0) return;
  holdUntil = Date.now() + 500;  // the top rows stay out of the way while the finger is down
  hideHeader(true);
  // A heading sticks to the top, so its own position says nothing: it starts where the card after it
  // starts, less its own height.
  const top = kids[at + 1].getBoundingClientRect().top - kids[at].offsetHeight;
  window.scrollTo(0, top + window.scrollY - stickTop());
}

function touchIndex(e) {
  if (!stops.length) return;
  const r = indexNav.getBoundingClientRect();
  const k = Math.floor((e.clientY - r.top) / (r.height / stops.length));
  const stop = stops[Math.min(stops.length - 1, Math.max(0, k))];
  if (stop === lastStop) return;
  lastStop = stop;
  bubble.textContent = stop.group;
  bubble.hidden = false;
  jumpTo(stop);
}
indexNav.addEventListener("pointerdown", e => {
  pressed = true; lastStop = null;
  if (document.activeElement === q) q.blur();  // the keyboard away, so the header can slide off
  try { indexNav.setPointerCapture(e.pointerId); } catch (x) { /* a mouse or an old browser */ }
  touchIndex(e);
  if (e.preventDefault) e.preventDefault();
});
indexNav.addEventListener("pointermove", e => { if (pressed) touchIndex(e); });
const letGo = () => { pressed = false; lastStop = null; bubble.hidden = true; };
indexNav.addEventListener("pointerup", letGo);
indexNav.addEventListener("pointercancel", letGo);
window.addEventListener("resize", () => buildIndex(pending));

function setIndexOn(on, save) {
  indexOn = on;
  indexButton.setAttribute("aria-pressed", String(on));
  buildIndex(pending);
  if (save) try { localStorage.setItem(INDEX_KEY, on ? "1" : "0"); } catch (e) { /* not kept */ }
}
indexButton.addEventListener("click", () => setIndexOn(!indexOn, true));
try { indexOn = localStorage.getItem(INDEX_KEY) !== "0"; } catch (e) { indexOn = true; }
indexButton.setAttribute("aria-pressed", String(indexOn));

saveTags();  // finds out whether storage works here, and drops tags for tracks no longer on a disc
showTagCount();
show();
q.focus();
})();
</script>
</body>
</html>
"""
