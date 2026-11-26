"""The real-library mode of `scripts/make_screenshots.py` (`--real`): the same pictures, of the owner's own music.

Instead of the made-up collection the playlists and discs are built from tracks of the rekordbox library on this
computer, **only the ones a picks file names** (`local/screenshot-tracks.json`, which is gitignored: nothing in it, and
no track not in it, can reach a picture). Real tracks have rekordbox's analysis, so the pictures show real waveforms,
beat grids, phrases, cues and (for the tracks the picks file allows) cover art, none of which the made-up files have.

Everything is read-only: the library is read from a snapshot copy (`library.rekordbox.read_master_db`), audio files are
decoded and never written, the Explorer's pretend folders hold **symbolic links** to the files (never copies, never moves),
and the library, settings and logs go to the run's scratch folder like any other picture run.

Without the picks file or a rekordbox library on the computer, `status()` says why and `make_screenshots.py` falls back to
the made-up collection. A picks file that is there but wrong is an error, not a fallback.

The picks file (JSON):

    {
      "library": null,                       # a master.db path, or null for rekordbox's own
      "source": "Demo Crate",                # the name of the imported library (a tooltip, the Explorer's folder)
      "show_art": ["Go Silly (Extended Mix)"],   # titles whose cover picture may be shown (the Info tab)
      "hero_tab": "info",                    # the side panel's page in the main window pictures: "memory" or "info"
      "playlists": {                         # these four roles are needed; names are shown as they are written
        "warm_up":    {"name": "Warm Up",    "tracks": ["Ocean (Extended Mix)", ...]},
        "house":      {"name": "House",      "tracks": [...]},
        "garage":     {"name": "UK Garage",  "tracks": [...]},
        "throwbacks": {"name": "Peak Time",  "tracks": [...]}
      },
      "loose": ["Tulum (Extended Mix)", ...],      # the Explorer's loose folder (when "explorer" is not given)
      "explorer": {                                # optional: the Explorer picture shows a real folder instead
        "folder": "/Users/me/Music/CDJ CDs",       #   a folder on this computer (linked into the pretend home)
        "pinned": "Speed Garage/Speed Garage 003"  #   the folder under it that is pinned and shown
      }
    }

With "explorer" the Explorer pictures show the files in that folder, whoever made them, so that folder's names and tags
are what the picture shows: it is the owner's choice of folder, not a pick from the library.

A track is its title (matched without regard to case) or {"title": ..., "artist": ...} when two tracks share one.
The third track of "house" is the one loaded in the player.
"""

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PICKS = ROOT / "local" / "screenshot-tracks.json"
ROLES = ("warm_up", "house", "garage", "throwbacks")
HERO_TABS = ("memory", "info")


class PicksError(RuntimeError):
    """The picks file is wrong, or names a track the library doesn't have: said in plain words."""


@dataclass(frozen=True)
class Pick:
    title: str
    artist: str = ""

    def __str__(self) -> str:
        return f"{self.artist} - {self.title}" if self.artist else self.title


@dataclass(frozen=True)
class Playlist:
    name: str
    tracks: tuple[Pick, ...]


@dataclass(frozen=True)
class ExplorerPick:
    """A real folder for the Explorer pictures: `folder` is linked into the pretend home and `pinned` (a path under it)
    is the folder that is pinned and shown."""

    folder: Path
    pinned: str


@dataclass(frozen=True)
class Picks:
    library: Path | None
    source: str
    playlists: dict[str, Playlist]
    loose: tuple[Pick, ...] = ()
    show_art: frozenset[str] = frozenset()
    hero_tab: str = "memory"
    explorer: ExplorerPick | None = None


def _norm(text: str) -> str:
    return " ".join((text or "").casefold().split())


def _pick(entry, where: str) -> Pick:
    if isinstance(entry, str) and entry.strip():
        return Pick(entry.strip())
    if isinstance(entry, dict) and str(entry.get("title", "")).strip():
        return Pick(str(entry["title"]).strip(), str(entry.get("artist", "")).strip())
    raise PicksError(
        f"{where}: a track is a title, or {{'title': ..., 'artist': ...}}."
    )


def parse_picks(data: object) -> Picks:
    """The picks from the file's JSON, checked. Raises `PicksError`."""
    if not isinstance(data, dict):
        raise PicksError("The picks file must hold a JSON object.")
    playlists = data.get("playlists")
    if not isinstance(playlists, dict):
        raise PicksError('The picks file needs a "playlists" object.')
    missing = [role for role in ROLES if role not in playlists]
    if missing:
        raise PicksError(f"\"playlists\" needs {', '.join(ROLES)}; missing: {', '.join(missing)}.")  # fmt: skip
    chosen = {}
    for role in ROLES:
        entry = playlists[role]
        if not isinstance(entry, dict) or not str(entry.get("name", "")).strip():
            raise PicksError(f'"{role}" needs a "name" and a list of "tracks".')
        tracks = entry.get("tracks")
        if not isinstance(tracks, list) or not tracks:
            raise PicksError(f'"{role}" needs at least one track.')
        chosen[role] = Playlist(
            str(entry["name"]).strip(),
            tuple(_pick(t, f'"{role}"') for t in tracks),
        )
    house = chosen["house"]
    if len(house.tracks) < 3:
        raise PicksError('"house" needs at least three tracks (the third is loaded in the player).')  # fmt: skip
    hero = str(data.get("hero_tab", "memory"))
    if hero not in HERO_TABS:
        raise PicksError(f'"hero_tab" is {" or ".join(HERO_TABS)}.')
    library = data.get("library")
    explorer = data.get("explorer")
    if explorer is not None:
        if (
            not isinstance(explorer, dict)
            or not str(explorer.get("folder", "")).strip()
            or not str(explorer.get("pinned", "")).strip()
        ):
            raise PicksError('"explorer" needs a "folder" and a "pinned" folder under it.')  # fmt: skip
        explorer = ExplorerPick(
            Path(str(explorer["folder"])).expanduser(),
            str(explorer["pinned"]).strip("/"),
        )
    return Picks(
        library=Path(library).expanduser() if library else None,
        source=str(data.get("source") or "Demo Crate"),
        playlists=chosen,
        loose=tuple(_pick(t, '"loose"') for t in data.get("loose", [])),
        show_art=frozenset(_norm(t) for t in data.get("show_art", [])),
        hero_tab=hero,
        explorer=explorer,
    )


def load_picks(path: Path) -> Picks:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PicksError(f"{path} is not valid JSON: {exc}") from exc
    return parse_picks(data)


def status(path: Path = DEFAULT_PICKS) -> str | None:
    """Why real mode can't run on this computer (no picks file, no rekordbox library), or None when it can."""
    if not Path(path).is_file():
        return f"there is no picks file at {path}"
    from dubcrate.library.rekordbox import default_master_db

    picks = load_picks(path)
    library = picks.library or default_master_db()
    if library is None or not Path(library).is_file():
        return "there is no rekordbox library on this computer"
    if picks.explorer is not None and not (picks.explorer.folder / picks.explorer.pinned).is_dir():  # fmt: skip
        return f"there is no folder {picks.explorer.folder / picks.explorer.pinned} on this computer"  # fmt: skip
    return None


def _find(rb_tracks, pick: Pick):
    wanted = _norm(pick.title)
    found = [t for t in rb_tracks if _norm(t.title) == wanted]
    if pick.artist:
        found = [t for t in found if _norm(pick.artist) in _norm(t.artist)]
    if not found:
        raise PicksError(f"“{pick}” is not in the rekordbox library.")
    if len(found) > 1:
        who = "; ".join(sorted({t.artist or "?" for t in found}))
        raise PicksError(f"“{pick}” matches {len(found)} tracks ({who}): add an artist to the pick.")  # fmt: skip
    return found[0]


def select(rb, picks: Picks):
    """(the track records to import, the playlists as `NodeRecord`s, the loose tracks' records), from the library `rb`
    (a `RekordboxLibrary`): only what the picks name, in the picks' order. Raises one `PicksError` that lists every
    pick that is missing or ambiguous, so a file is fixed in one go."""
    from dubcrate.library.models import NodeRecord

    problems: list[str] = []

    def find(pick: Pick):
        try:
            return _find(rb.tracks, pick)
        except PicksError as exc:
            if str(exc) not in problems:
                problems.append(str(exc))
            return None

    records: dict[str, object] = {}
    nodes = []
    for role in ROLES:
        playlist = picks.playlists[role]
        ids = []
        for pick in playlist.tracks:
            track = find(pick)
            if track is not None:
                records.setdefault(track.ext_id, track)
                ids.append(track.ext_id)
        nodes.append(NodeRecord(f"screenshots-{role}", None, playlist.name, False, tuple(ids)))  # fmt: skip
    loose = []
    for pick in picks.loose:
        track = find(pick)
        if track is not None:
            records.setdefault(track.ext_id, track)
            loose.append(track)
    if problems:
        raise PicksError("\n".join(problems))
    return list(records.values()), nodes, loose


def link_files(folder: Path, records) -> None:
    """Symbolic links to the tracks' real files in `folder` (made if needed): the Explorer shows them as files. Never a
    copy and never a move; a link that can't be made (a filesystem without them) is a copy in the scratch folder."""
    folder.mkdir(parents=True, exist_ok=True)
    for track in records:
        link = folder / Path(track.path).name
        if link.exists() or link.is_symlink():
            continue
        try:
            os.symlink(track.path, link)
        except OSError:
            shutil.copy2(track.path, link)


AUDIO = {".mp3", ".wav", ".flac", ".aif", ".aiff", ".m4a", ".ogg", ".opus"}


def link_folder(link: Path, real: Path) -> None:
    """A symbolic link at `link` to the real folder (the Explorer reads through it). Never a copy and never a move: a
    link is removed as a link when the scratch folder is cleared, whatever is behind it stays."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if not (link.exists() or link.is_symlink()):
        os.symlink(real, link, target_is_directory=True)


def audio_previews(folder: Path) -> dict[str, object]:
    """{real path: whole-track Waveform} of the audio files directly in `folder`, worked out from the audio (these files
    are not in the rekordbox library, so there is no analysis to read). Files that can't be read are left out."""
    from dubcrate.audio.decode import DecodeError, decode_file
    from dubcrate.waveform import compute_waveform

    found: dict[str, object] = {}
    for file in sorted(folder.iterdir()):
        if file.suffix.lower() not in AUDIO or not file.is_file():
            continue
        try:
            found[os.path.realpath(file)] = compute_waveform(decode_file(str(file)).mono())  # fmt: skip
        except (DecodeError, OSError, ValueError) as exc:
            print(f"  (no preview for {file.name}: {exc})")
    return found


def make_real_demo(workdir: Path, picks: Picks):
    """The real collection, as a `screenshot_recipes.Demo`."""
    from screenshot_recipes import MISC, Demo, Names

    class RealDemo(Demo):
        real = True
        show_art = picks.show_art
        hero_tab = picks.hero_tab

        def _names(self) -> Names:
            p = picks.playlists
            return Names(
                picks.source,
                p["warm_up"].name,
                p["house"].name,
                p["garage"].name,
                p["throwbacks"].name,
            )

        def _collect(self) -> None:
            from dubcrate.analysis import read_preview
            from dubcrate.library.rekordbox import default_master_db, read_master_db
            from dubcrate.library.rekordbox_sources import analysis_root

            path = picks.library or default_master_db()
            print("reading a snapshot of the rekordbox library (read-only)...")
            rb = read_master_db(path)
            records, nodes, loose = select(rb, picks)
            self.lib.replace_source("rekordbox", str(rb.source), picks.source, records, nodes)  # fmt: skip
            # the Explorer's pretend folders: links to the real files, one folder per playlist and one loose
            root = analysis_root("rekordbox", str(rb.source))
            by_id = {t.ext_id: t for t in records}
            if (
                picks.explorer is not None
            ):  # a real folder, linked in: the Explorer shows what is in it
                real = picks.explorer.folder
                if not (real / picks.explorer.pinned).is_dir():
                    raise PicksError(f"the Explorer folder {real / picks.explorer.pinned} is not there.")  # fmt: skip
                link = self.music / real.name
                link_folder(link, real)
                self.explorer_folder = link / picks.explorer.pinned
                self.explorer_open = self.explorer_folder.parent
                print("working out previews for the Explorer's folder...")
                self.previews.update(audio_previews(self.explorer_folder))
            else:
                crate = self.music / picks.source
                for role in ROLES:
                    playlist = picks.playlists[role]
                    link_files(crate / playlist.name, [by_id[i] for i in next(n.track_ext_ids for n in nodes if n.name == playlist.name)])  # fmt: skip
                link_files(self.music / MISC, loose)
            # the Explorer's files have no rekordbox twin, so their Preview strips are rekordbox's own, by file
            for track in records:
                wave = read_preview(root, track.analysis_path) if track.analysis_path else None  # fmt: skip
                if wave is not None:
                    self.previews[os.path.realpath(track.path)] = wave

    return RealDemo(workdir)
