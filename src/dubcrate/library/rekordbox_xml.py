"""Read a rekordbox XML file (File > Export Collection in xml format) into track, playlist and cue records.

**Read-only**: the file is only parsed, never written (it is a source of its own kind, `rekordbox_xml`, which nothing
that writes to rekordbox ever acts on). It needs no database key and no `pyrekordbox`: rekordbox 5, 6 and 7, and other
programs that write this format (Dubcrate's own `playlist_export` does), all make the same `DJ_PLAYLISTS` file.
Parsed with the standard library (pyrekordbox's reader drops a path's leading slash and hides the cue marks).

What it holds: `COLLECTION` > `TRACK` (title, artist, album, genre, year, BPM, key, rating, ... and a `Location` URL),
each with `POSITION_MARK`s (memory cues, hot cues and loops) and `TEMPO` points, and `PLAYLISTS` > `NODE`s (folders and
playlists of track ids). **Not read: the `TEMPO` points (a beat grid) and anything like a waveform**: an XML file has no
analysis files, so a track from it has no rekordbox waveform, phrases or beat grid (the player draws the waveform from
the audio; Q needs a beat grid, so it is off). `dev_docs/rekordbox-xml.md`.
"""

import logging
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from dubcrate.analysis import Cue
from dubcrate.cancel import CancelToken
from dubcrate.library.models import NodeRecord, TrackRecord
from dubcrate.library.rekordbox import Progress, RekordboxError, RekordboxLibrary

log = logging.getLogger(__name__)

ROOT_TAG = "DJ_PLAYLISTS"
_FOLDER, _PLAYLIST = "0", "1"  # a NODE's Type
_MARK_CUE, _MARK_LOOP = (
    "0",
    "4",
)  # a POSITION_MARK's Type (1 and 2 are fades, 3 a "load" point: not cues)
_STARS = 51  # Rating is 0 to 255 in steps of 51: one star each


def problem(path: Path) -> str:
    """Why `path` isn't a rekordbox XML file, in plain words; "" if it looks like one (only its first tag is read)."""
    path = Path(path).expanduser()
    if not path.is_file():
        return f"“{path}” isn't a file."
    try:
        for _event, element in ET.iterparse(path, events=("start",)):
            if element.tag == ROOT_TAG:
                return ""
            return f"“{path.name}” isn't a rekordbox XML file (it doesn't start with {ROOT_TAG})."
    except (ET.ParseError, OSError, UnicodeError) as exc:
        return f"“{path.name}” isn't a rekordbox XML file ({_why(exc)})."
    return f"“{path.name}” is empty."


def _why(exc: Exception) -> str:
    return getattr(exc, "strerror", None) or str(exc) or type(exc).__name__


def _parse(path: Path) -> ET.Element:
    path = Path(path).expanduser()
    why = problem(path)
    if why:
        raise RekordboxError(why)
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError, UnicodeError) as exc:
        raise RekordboxError(f"could not read {path.name}: {_why(exc)}") from exc
    return root


def location_path(location: str) -> str:
    """A file path from a `Location` (`file://localhost/Users/me/a%20b.mp3`; on Windows `file://localhost/C:/Music/a.mp3`);
    a value that is not a URL is taken as the path it is. '' if there is none."""
    if not location:
        return ""
    if not location.lower().startswith("file:"):
        return unquote(location)
    path = unquote(urlparse(location).path)
    if re.match(r"^/[A-Za-z]:[/\\]", path):  # (a drive letter: no slash in front of it)
        path = path[1:]
    return path


def _number(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _int(value: str | None) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number > 0 else None


def track_from_element(element: ET.Element) -> TrackRecord | None:
    """A `TrackRecord` from a `COLLECTION` > `TRACK`; None if it has no file (no `Location`, or one that isn't a path)."""
    a: Any = element.attrib
    path = location_path(a.get("Location", ""))
    if not path or not a.get("TrackID"):
        return None
    bpm = _number(a.get("AverageBpm"))
    rating = _int(a.get("Rating"))
    return TrackRecord(
        ext_id=str(a["TrackID"]),
        path=path,
        title=a.get("Name") or Path(path).stem,
        artist=a.get("Artist", ""),
        album=a.get("Album", ""),
        genre=a.get("Genre", ""),
        year=_int(a.get("Year")),
        comment=a.get("Comments", ""),
        bpm=bpm if bpm and bpm > 0 else None,
        key=a.get("Tonality", ""),
        duration=float(_number(a.get("TotalTime")) or 0),
        bitrate=_int(a.get("BitRate")),
        sample_rate=_int(a.get("SampleRate")),
        rating=round(rating / _STARS) if rating else None,
        file_size=_int(a.get("Size")),
        label=a.get("Label", ""),
        remixer=a.get("Remixer", ""),
        composer=a.get("Composer", ""),
        play_count=_int(a.get("PlayCount")),
        date_added=a.get("DateAdded", ""),
    )


def cue_from_mark(mark: ET.Element) -> Cue | None:
    """A `Cue` from a `POSITION_MARK`: `Num` -1 is a memory cue and 0 up a hot cue (0 = A); type 4 is a loop (a hot
    cue saved as a loop keeps its slot). Fades and "load" points are not cues, and a mark with no start (or a loop with no
    usable end) is dropped.
    Times are rekordbox's, in seconds (later than the audio for most MP3s: `cue_timing` takes care of that)."""
    kind = mark.get("Type", "")
    start = _number(mark.get("Start"))
    if kind not in (_MARK_CUE, _MARK_LOOP) or start is None or start < 0:
        return None
    num = _number(mark.get("Num"))
    slot = int(num) + 1 if num is not None and num >= 0 else None
    end = _number(mark.get("End"))
    if kind == _MARK_LOOP and (end is None or end <= start):
        return None  # (a loop with no usable end is not a cue)
    is_loop = kind == _MARK_LOOP
    return Cue(
        kind="loop" if is_loop else ("hot" if slot else "memory"),
        time=start,
        end=end if is_loop else None,
        slot=slot,
        label=mark.get("Name", ""),
    )


def _collection(root: ET.Element) -> Iterator[ET.Element]:
    collection = root.find("COLLECTION")
    return iter(collection.findall("TRACK")) if collection is not None else iter(())


def build_nodes(
    root: ET.Element, ids: set[str], by_path: dict[str, str]
) -> list[NodeRecord]:
    """The `PLAYLISTS` tree as NodeRecords: folders (`Type` 0) and playlists (1) in file order, with their tracks by id
    (`KeyType` 1 names them by location instead). The top `ROOT` node is not a folder of its own."""
    top = root.find("PLAYLISTS")
    base = top.find("NODE") if top is not None else None
    nodes: list[NodeRecord] = []

    def keys(node: ET.Element) -> tuple[str, ...]:
        by_location = node.get("KeyType") == "1"
        found = []
        for track in node.findall("TRACK"):
            key = track.get("Key", "")
            ident = by_path.get(location_path(key)) if by_location else key
            if ident in ids:
                found.append(ident)
        return tuple(found)

    def walk(node: ET.Element, parent: str | None, prefix: str) -> None:
        for index, child in enumerate(node.findall("NODE")):
            kind = child.get("Type")
            if kind not in (_FOLDER, _PLAYLIST):
                continue
            ident = f"{prefix}/{index}"
            nodes.append(
                NodeRecord(
                    ext_id=ident,
                    parent_ext_id=parent,
                    name=child.get("Name") or "(untitled)",
                    is_folder=kind == _FOLDER,
                    track_ext_ids=() if kind == _FOLDER else keys(child),
                )
            )
            if kind == _FOLDER:
                walk(child, ident, ident)

    if base is not None:
        walk(base, None, "")
    return nodes


def read_xml(
    path: Path,
    progress: Progress | None = None,
    cancel: CancelToken | None = None,
) -> RekordboxLibrary:
    """Read the rekordbox XML file at `path`. The result's `source` is the file itself. Raises `RekordboxError` in plain
    words for a file that isn't one; with `cancel` set it raises `Cancelled` every few hundred tracks."""
    path = Path(path).expanduser()
    say = progress or (lambda _msg: None)
    say("reading the XML file")
    root = _parse(path)
    library = RekordboxLibrary(source=path)
    skipped = 0
    seen: set[str] = set()
    for count, element in enumerate(_collection(root)):
        if cancel is not None and count % 200 == 0:
            cancel.check()
        track = track_from_element(element)
        if track is None or track.ext_id in seen:
            skipped += 1
            continue
        seen.add(track.ext_id)
        library.tracks.append(track)
    if skipped:
        library.notes.append(
            f"skipped {skipped} track(s) with no file path (or listed twice)"
        )
    if cancel is not None:
        cancel.check()
    say("reading playlists")
    library.nodes = build_nodes(root, seen, {t.path: t.ext_id for t in library.tracks})
    return library


def read_xml_cues(path: Path) -> dict[str, list[Cue]]:
    """Every cue in the XML file, by track (its `TrackID`, a track's `ext_id`), each in time order."""
    root = _parse(path)
    grouped: dict[str, list[Cue]] = defaultdict(list)
    for element in _collection(root):
        ident = element.get("TrackID")
        if not ident:
            continue
        for mark in element.findall("POSITION_MARK"):
            if (cue := cue_from_mark(mark)) is not None:
                grouped[ident].append(cue)
    return {k: sorted(v, key=lambda c: c.time) for k, v in grouped.items()}
