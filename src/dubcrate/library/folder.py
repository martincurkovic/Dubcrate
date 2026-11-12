"""Scan a folder of audio files into track and browse-node records."""

import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from dubcrate.library.models import NodeRecord, TrackRecord
from dubcrate.render import RenderError, SourceInfo, probe

AUDIO_EXTENSIONS = frozenset(
    {".mp3", ".flac", ".wav", ".aif", ".aiff", ".m4a", ".aac", ".ogg", ".opus", ".wma"}
)

Progress = Callable[[int, int], None]  # (done, total)


@dataclass
class FolderScan:
    root: Path
    tracks: list[TrackRecord] = field(default_factory=list)
    nodes: list[NodeRecord] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)  # (path, reason)


def _is_audio(name: str) -> bool:
    # "._foo.mp3" files are macOS resource-fork litter, not audio.
    return not name.startswith(".") and Path(name).suffix.lower() in AUDIO_EXTENSIONS


def find_audio_files(root: Path) -> dict[Path, list[Path]]:
    """Map each directory under `root` to its direct audio files, name-sorted.

    Hidden directories are skipped and symlinked directories aren't followed.
    Directories without any audio file are omitted.
    """
    found: dict[Path, list[Path]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        files = sorted(
            (Path(dirpath) / f for f in filenames if _is_audio(f)),
            key=lambda p: p.name.casefold(),
        )
        if files:
            found[Path(dirpath)] = files
    return found


def _record(path: Path, info: SourceInfo) -> TrackRecord:
    try:
        size = path.stat().st_size
    except OSError:
        size = None
    return TrackRecord(
        ext_id=str(path),
        path=str(path),
        title=info.title,
        artist=info.artist,
        album=info.album,
        genre=info.genre,
        year=info.year,
        comment=info.comment,
        bpm=info.bpm,
        key=info.key,
        duration=info.duration,
        bitrate=info.bitrate,
        sample_rate=info.sample_rate,
        bit_depth=info.bits,
        file_size=size,
    )


def _build_nodes(root: Path, by_dir: dict[Path, list[Path]]) -> list[NodeRecord]:
    """One node per directory that holds audio, plus the ancestors linking it to root."""
    kept: set[Path] = set()
    for directory in by_dir:
        current = directory
        while True:
            kept.add(current)
            if current == root or current.parent == current:
                break
            current = current.parent
    kept.add(root)

    children: dict[Path, list[Path]] = {}
    for directory in kept:
        if directory != root:
            children.setdefault(directory.parent, []).append(directory)

    nodes: list[NodeRecord] = []

    def walk(directory: Path, parent: Path | None) -> None:
        subdirs = sorted(children.get(directory, []), key=lambda p: p.name.casefold())
        nodes.append(
            NodeRecord(
                ext_id=str(directory),
                parent_ext_id=str(parent) if parent else None,
                name=directory.name or str(directory),
                is_folder=bool(subdirs),
                track_ext_ids=tuple(str(f) for f in by_dir.get(directory, [])),
            )
        )
        for sub in subdirs:
            walk(sub, directory)

    walk(root, None)
    return nodes


def scan_folder(
    root: Path, progress: Progress | None = None, workers: int = 8
) -> FolderScan:
    """Probe every audio file under `root`; unreadable files land in `errors`."""
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    by_dir = find_audio_files(root)
    files = [f for dir_files in by_dir.values() for f in dir_files]
    scan = FolderScan(root=root)

    def run(path: Path) -> tuple[Path, SourceInfo | str]:
        try:
            return path, probe(path)
        except (RenderError, OSError, ValueError) as exc:
            return path, str(exc)

    readable: set[Path] = set()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for done, (path, result) in enumerate(pool.map(run, files), start=1):
            if isinstance(result, str):
                scan.errors.append((str(path), result))
            else:
                scan.tracks.append(_record(path, result))
                readable.add(path)
            if progress:
                progress(done, len(files))

    # Keep nodes consistent with what could actually be read.
    by_dir = {d: [f for f in fs if f in readable] for d, fs in by_dir.items()}
    scan.nodes = _build_nodes(root, {d: fs for d, fs in by_dir.items() if fs})
    return scan


@dataclass
class DirectoryScan:
    directory: Path
    tracks: list[TrackRecord] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)  # (path, reason)


def scan_directory(
    directory: Path,
    known: dict[str, TrackRecord] | None = None,
    workers: int = 8,
) -> DirectoryScan:
    """Probe the audio files directly inside `directory` (not its subfolders), name-sorted.

    `known` is what was read from this folder before, by path: a file whose size is the same
    keeps what was read (the tags are not read again), so opening a folder a second time is
    quick. Unreadable files land in `errors`. Raises `NotADirectoryError` / `PermissionError`
    for a folder that is gone or not allowed.
    """
    known = known or {}
    files = sorted(
        (
            Path(e.path)
            for e in os.scandir(directory)
            if _is_audio(e.name) and e.is_file()
        ),
        key=lambda p: p.name.casefold(),
    )
    scan = DirectoryScan(directory)

    def read(path: Path) -> tuple[Path, TrackRecord | str]:
        try:
            size = path.stat().st_size
        except OSError:
            size = None
        old = known.get(str(path))
        if old is not None and size is not None and old.file_size == size:
            return path, old
        try:
            return path, _record(path, probe(path))
        except (RenderError, OSError, ValueError) as exc:
            return path, str(exc)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for path, result in pool.map(read, files):
            if isinstance(result, str):
                scan.errors.append((str(path), result))
            else:
                scan.tracks.append(result)
    return scan
