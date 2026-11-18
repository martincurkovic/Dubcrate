"""Discs as playlists for other programs: a rekordbox XML file or M3U files (Qt-free).

**Read-only on every library.** This only reads Dubcrate's own discs and writes NEW files you choose;
nothing here opens a rekordbox database (`rekordbox_write.py` is the one place that does, and it knows
nothing about playlists). In rekordbox you import the XML yourself (Preferences > Advanced > Database >
rekordbox xml, then its "rekordbox xml" tree in the browser) and drag the playlists where you want them.

What a playlist holds is the **library track's own** title, artist and tags, never a disc's edited CD text
(`DiscEntry.title`): those edits belong to the CD and must not reach rekordbox. Tracks are named by the
file's absolute path, which is how rekordbox matches one it already has. A track whose file was missing or
is stale at the last import is left out and reported; so is a disc left with nothing in it.

An audio disc is one playlist in its running order. An MP3 disc is one flat playlist in the order a player
lists it: the root's files, then each folder depth first, siblings by name. The discs' own folders
(`disc_folder`) become folders in the XML, and folders on disk for M3U (which has neither).
"""

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dubcrate import mp3
from dubcrate.library import Library
from dubcrate.library.models import DiscDir, DiscEntry, Track

log = logging.getLogger(__name__)

FOLDER_NAME = "Dubcrate Playlists"  # the folder an M3U export makes


class PlaylistError(Exception):
    """An expected failure (a folder that can't be written, nothing to export): the message is for the user."""


@dataclass
class Playlist:
    name: str
    tracks: list[Track]


@dataclass
class PlaylistFolder:
    name: str
    folders: list["PlaylistFolder"] = field(default_factory=list)
    playlists: list[Playlist] = field(default_factory=list)

    def all_playlists(self) -> list[Playlist]:
        found = list(self.playlists)
        for folder in self.folders:
            found.extend(folder.all_playlists())
        return found

    @property
    def empty(self) -> bool:
        return not self.playlists and not self.folders


@dataclass
class Collected:
    """What an export would hold: a root (it has no name of its own), and what was left out."""

    root: PlaylistFolder
    skipped: list[str] = field(
        default_factory=list
    )  # "Disc: Artist - Title", files not usable
    empty_discs: list[str] = field(
        default_factory=list
    )  # discs with nothing left to list

    @property
    def playlists(self) -> int:
        return len(self.root.all_playlists())

    @property
    def tracks(self) -> int:
        return sum(len(p.tracks) for p in self.root.all_playlists())


@dataclass(frozen=True)
class Written:
    path: Path  # the XML file, an M3U file, or the folder the M3U files are in
    playlists: int
    tracks: int


def ordered(entries: list[DiscEntry], dirs: list[DiscDir]) -> list[DiscEntry]:
    """An MP3 disc's entries as a player lists them (the root's files, then the folders depth first,
    siblings by name; within a folder, disc order). An audio disc has no folders: its order as it is."""
    by_parent: dict[int | None, list[DiscDir]] = {}
    for d in dirs:
        by_parent.setdefault(d.parent_id, []).append(d)
    in_dir: dict[int | None, list[DiscEntry]] = {}
    for entry in entries:
        in_dir.setdefault(entry.dir_id, []).append(entry)
    result = list(in_dir.pop(None, []))

    def walk(parent: int | None) -> None:
        for d in sorted(by_parent.get(parent, []), key=lambda d: d.name.casefold()):
            result.extend(in_dir.pop(d.id, []))
            walk(d.id)

    walk(None)
    for (
        rest
    ) in in_dir.values():  # a folder the store no longer lists: never lose a track
        result.extend(rest)
    return result


def _unique(name: str, used: set[str], fallback: str) -> str:
    """`name`, or `name (2)`, `name (3)`... so that no two siblings share one (compared without case)."""
    base = " ".join(name.split()) or fallback
    candidate, n = base, 1
    while candidate.casefold() in used:
        n += 1
        candidate = f"{base} ({n})"
    used.add(candidate.casefold())
    return candidate


def _label(track: Track) -> str:
    return (
        " - ".join(p for p in (track.artist, track.title) if p) or Path(track.path).stem
    )


def _disc_playlist(
    lib: Library, disc_id: int, name: str, collected: Collected
) -> Playlist | None:
    disc = lib.disc(disc_id)
    entries = lib.disc_entries(disc_id)
    if disc.kind == "mp3":
        entries = ordered(entries, lib.disc_dirs(disc_id))
    tracks: list[Track] = []
    for entry in entries:
        track = entry.track
        if track.missing or track.stale:
            collected.skipped.append(f"{disc.name}: {_label(track)}")
        else:
            tracks.append(track)
    if not tracks:
        collected.empty_discs.append(disc.name)
        return None
    return Playlist(name, tracks)


def _fill(
    lib: Library, folder: PlaylistFolder, folder_id: int | None, collected: Collected
) -> None:
    """The discs filed in `folder_id` (None: the top level) and the folders in it, as the Discs list shows
    them: folders first A to Z, then discs A to Z."""
    used: set[str] = set()
    folders = sorted(
        (f for f in lib.disc_folders() if f.parent_id == folder_id),
        key=lambda f: f.name.casefold(),
    )
    for f in folders:
        child = PlaylistFolder(_unique(f.name, used, "Folder"))
        _fill(lib, child, f.id, collected)
        if child.empty:
            continue
        folder.folders.append(child)
    discs = sorted(
        (d for d in lib.discs() if d.folder_id == folder_id),
        key=lambda d: d.name.casefold(),
    )
    for d in discs:
        playlist = _disc_playlist(lib, d.id, _unique(d.name, used, "Disc"), collected)
        if playlist is not None:
            folder.playlists.append(playlist)


def collect(
    lib: Library, *, disc_id: int | None = None, folder_id: int | None = None
) -> Collected:
    """What to export: one disc, one disc folder (with what is inside it, kept as that folder), or, with
    neither, every disc. Raises `PlaylistError` if nothing is left to write."""
    collected = Collected(PlaylistFolder(""))
    if disc_id is not None:
        playlist = _disc_playlist(lib, disc_id, lib.disc(disc_id).name, collected)
        if playlist is not None:
            collected.root.playlists.append(playlist)
    elif folder_id is not None:
        name = next(f.name for f in lib.disc_folders() if f.id == folder_id)
        child = PlaylistFolder(name)
        _fill(lib, child, folder_id, collected)
        if not child.empty:
            collected.root.folders.append(child)
    else:
        _fill(lib, collected.root, None, collected)
    if not collected.playlists:
        raise PlaylistError(
            "There is nothing to export: no disc has a track whose file is there."
        )
    return collected


def summary(collected: Collected) -> str:
    """What was left out, for a message after an export; empty when nothing was."""
    lines = []
    if collected.skipped:
        shown = collected.skipped[:8]
        more = len(collected.skipped) - len(shown)
        lines.append(
            f"{len(collected.skipped)} track(s) left out because their file is missing:"
        )
        lines.extend(f"  {s}" for s in shown)
        if more:
            lines.append(f"  and {more} more")
    if collected.empty_discs:
        lines.append(
            "Discs left out (nothing to list): " + ", ".join(collected.empty_discs)
        )
    return "\n".join(lines)


# -- rekordbox XML -------------------------------------------------------------------


def _location(path: str) -> str:
    """The path for pyrekordbox's `Location`, which puts `file://localhost/` in front: rekordbox writes
    `file://localhost/Users/...`, so a leading slash is dropped (a Windows `C:\\` path has none)."""
    return path.replace("\\", "/").removeprefix("/")


def _attributes(track: Track) -> dict[str, str]:
    """What rekordbox's `TRACK` takes from the library's own tags (empty and unknown ones left out)."""
    values: dict[str, object] = {
        "Name": track.title,
        "Artist": track.artist,
        "Album": track.album,
        "Genre": track.genre,
        "Size": track.file_size,
        "TotalTime": round(track.duration) if track.duration else None,
        "Year": track.year,
        "AverageBpm": f"{track.bpm:.2f}" if track.bpm else None,
        "Tonality": track.key,
        "BitRate": track.bitrate,
        "SampleRate": track.sample_rate,
    }
    return {k: str(v) for k, v in values.items() if v not in (None, "", 0)}


def write_xml(collected: Collected, path: Path) -> Written:
    """Write the playlists as a rekordbox XML file at `path` (an existing file is replaced: the caller
    asked for that name). Each file is one `TRACK` however many playlists use it."""
    from pyrekordbox.rbxml import RekordboxXml

    from dubcrate.ui.version import app_version

    xml = RekordboxXml(name="Dubcrate", version=app_version(), company="Dubcrate")
    ids: dict[str, int] = {}

    def track_id(track: Track) -> int:
        key = os.path.normpath(track.path)
        if key not in ids:
            added = xml.add_track(_location(track.path), **_attributes(track))
            ids[key] = int(added["TrackID"])
        return ids[key]

    def add(folder: PlaylistFolder, node) -> None:
        for f in folder.folders:
            add(f, node.add_playlist_folder(f.name))
        for playlist in folder.playlists:
            target = node.add_playlist(playlist.name)
            for track in playlist.tracks:
                target.add_track(track_id(track))

    add(collected.root, xml.root_playlist_folder)
    part = path.with_name(path.name + ".part")
    try:
        xml.save(part, indent="  ")
        os.replace(part, path)
    except OSError as exc:
        part.unlink(missing_ok=True)
        raise PlaylistError(f"Could not write {path}: {exc.strerror or exc}") from exc
    log.info(
        "exported %d playlist(s), %d track(s) to %s",
        collected.playlists,
        len(ids),
        path,
    )
    return Written(path, collected.playlists, collected.tracks)


# -- M3U -----------------------------------------------------------------------------


def m3u_text(playlist: Playlist) -> str:
    """An extended M3U (read as UTF-8): `#EXTINF` with the length and the name, then the absolute path."""
    lines = ["#EXTM3U"]
    for track in playlist.tracks:
        label = " ".join(_label(track).split())
        seconds = round(track.duration) if track.duration else -1
        lines.append(f"#EXTINF:{seconds},{label}")
        lines.append(track.path)
    return "\n".join(lines) + "\n"


def _write_m3u(playlist: Playlist, path: Path) -> None:
    path.write_text(m3u_text(playlist), encoding="utf-8", newline="\n")


def write_m3u(collected: Collected, path: Path) -> Written:
    """Write a single playlist (the export holds exactly one) as an M3U file at `path`."""
    playlists = collected.root.all_playlists()
    if len(playlists) != 1:
        raise PlaylistError(
            "A single M3U file holds one disc; export a folder instead."
        )
    try:
        _write_m3u(playlists[0], path)
    except OSError as exc:
        raise PlaylistError(f"Could not write {path}: {exc.strerror or exc}") from exc
    return Written(path, 1, len(playlists[0].tracks))


def new_folder(parent: Path) -> Path:
    """`parent`/`Dubcrate Playlists`, or `... 2`, `... 3`: a folder that does not exist yet (never reused)."""
    n, folder = 1, parent / FOLDER_NAME
    while folder.exists():
        n += 1
        folder = parent / f"{FOLDER_NAME} {n}"
    return folder


def write_m3u_folder(collected: Collected, parent: Path) -> Written:
    """Write every playlist as `<name>.m3u8` in a NEW folder inside `parent`, the discs' folders as folders
    (their names as a file system allows). Removes its own half-made folder if anything fails."""
    root = new_folder(parent)
    try:
        root.mkdir(parents=False)
    except OSError as exc:
        raise PlaylistError(
            f"Could not make a folder in {parent}: {exc.strerror or exc}"
        ) from exc

    def write(folder: PlaylistFolder, where: Path) -> None:
        used: set[str] = set()
        for f in folder.folders:
            sub = where / _unique(mp3.dir_name(f.name), used, "Folder")
            sub.mkdir()
            write(f, sub)
        for playlist in folder.playlists:
            name = _unique(mp3.dir_name(playlist.name), used, "Disc")
            _write_m3u(playlist, where / f"{name}.m3u8")

    try:
        write(collected.root, root)
    except OSError as exc:
        shutil.rmtree(root, ignore_errors=True)
        raise PlaylistError(
            f"Could not write the playlists: {exc.strerror or exc}"
        ) from exc
    log.info("exported %d playlist(s) as M3U to %s", collected.playlists, root)
    return Written(root, collected.playlists, collected.tracks)
