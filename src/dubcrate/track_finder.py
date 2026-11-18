"""A track finder for the gig: one HTML page that says which disc a track is on (Qt-free).

You have the CDs, their printed labels and a phone. Type part of a title or artist and the page lists
every disc that has it, with the track number (and the folder, on an MP3 disc). The page is one file with
the data inside it and no network use of any kind, so it works offline; on an iPhone open it in an app that
runs local HTML (Safari and the Files app won't run its script).

**Read-only on every library.** This only reads Dubcrate's own discs and writes a NEW file you choose.

The text is what is **written on the disc** (`DiscEntry.title` / `.artist`: the disc's own edit if any),
because it is the label you are looking at, unlike `playlist_export`, which sends the library's own text to
another program. Nothing is left out for a missing file: the CD is already burned. A track's number is its
place on an audio disc, and on an MP3 disc its number in its folder (as the files are numbered when burned).
"""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dubcrate.library import Library
from dubcrate.library.models import DiscDir
from dubcrate.playlist_export import ordered

log = logging.getLogger(__name__)


class FinderError(Exception):
    """An expected failure (a folder that can't be written, no disc to list): the message is for the user."""


@dataclass(frozen=True)
class FinderTrack:
    number: int  # on an audio disc; in its folder on an MP3 disc
    title: str
    artist: str
    folder: str  # the folder on an MP3 disc, "A / B"; empty for the root and for an audio disc


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

    @property
    def tracks(self) -> int:
        return sum(len(d.tracks) for d in self.discs)


@dataclass(frozen=True)
class Written:
    path: Path
    discs: int
    tracks: int


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
    lib: Library, disc_id: int, where: dict[int | None, str]
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
    for entry in entries:
        number = numbers[entry.dir_id] = numbers.get(entry.dir_id, 0) + 1
        tracks.append(
            FinderTrack(
                number,
                entry.title or Path(entry.track.path).stem,
                entry.artist,
                folder_names.get(entry.dir_id, ""),
            )
        )
    if not tracks:
        return None
    return FinderDisc(
        disc.name, where.get(disc.folder_id, ""), disc.kind, tuple(tracks)
    )


def collect(lib: Library, *, disc_id: int | None = None) -> Finder:
    """One disc, or (the default) every disc. Raises `FinderError` if no disc has a track."""
    where = _disc_where(lib)
    ids = [disc_id] if disc_id is not None else [d.id for d in lib.discs()]
    found = Finder()
    for i in ids:
        disc = _disc(lib, i, where)
        if disc is None:
            found.empty_discs.append(lib.disc(i).name)
        else:
            found.discs.append(disc)
    found.discs.sort(key=lambda d: (d.where.casefold(), d.name.casefold()))
    if not found.discs:
        raise FinderError("There is nothing to list: no disc has a track on it.")
    return found


def summary(found: Finder) -> str:
    """What was left out, for a message after an export; empty when nothing was."""
    if not found.empty_discs:
        return ""
    return "Discs left out (no tracks): " + ", ".join(found.empty_discs)


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
                "tracks": [[t.number, t.title, t.artist, t.folder] for t in d.tracks],
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
          --accent: #4a90d9; --mark: #e8922b; }
  @media (prefers-color-scheme: light) {
    :root { --bg: #f5f3fa; --panel: #ffffff; --bar: #ece7f5; --input: #ffffff; --card: #ffffff;
            --border: #d6cce8; --edge: #ddd0ee; --text: #2a2438; --bright: #191320; --muted: #7a7290;
            --accent: #8b5cf6; --mark: #d9651c; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font: 17px/1.3 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         padding: 0 max(12px, env(safe-area-inset-right)) env(safe-area-inset-bottom)
                  max(12px, env(safe-area-inset-left)); }
  header { position: sticky; top: 0; z-index: 1; background: var(--bg);
           margin: 0 calc(-1 * max(12px, env(safe-area-inset-right))) 0
                   calc(-1 * max(12px, env(safe-area-inset-left)));
           padding: 0 max(12px, env(safe-area-inset-right)) 8px
                    max(12px, env(safe-area-inset-left)); }
  .brand { display: flex; align-items: baseline; gap: 8px; background: var(--bar);
           border-bottom: 1px solid var(--edge); padding: 8px 0 8px;
           padding-top: max(8px, env(safe-area-inset-top));
           margin: 0 calc(-1 * max(12px, env(safe-area-inset-right))) 10px
                   calc(-1 * max(12px, env(safe-area-inset-left)));
           padding-left: max(12px, env(safe-area-inset-left)); }
  .brand b { color: var(--bright); letter-spacing: .02em; }
  .brand span { color: var(--muted); font-size: 14px; }
  input { width: 100%; font-size: 20px; padding: 11px 14px; border-radius: 6px;
          border: 1px solid var(--border); background: var(--input); color: var(--bright);
          outline: none; }
  input:focus { border-color: var(--accent); }
  .note { color: var(--muted); font-size: 14px; padding: 6px 2px 0; }
  main { padding-top: 4px; }
  .hit { background: var(--card); border: 1px solid var(--edge); border-left: 5px solid var(--stripe);
         border-radius: 6px; padding: 11px 14px; margin: 0 0 8px;
         display: flex; gap: 12px; align-items: center; }
  .what { flex: 1; min-width: 0; }
  .title { color: var(--bright); font-weight: 600; overflow-wrap: anywhere; }
  .artist { color: var(--muted); font-size: 15px; overflow-wrap: anywhere; }
  .where { text-align: right; max-width: 45%; }
  .disc { color: var(--bright); font-weight: 700; font-size: 18px; overflow-wrap: anywhere; }
  .num { color: var(--mark); font-size: 15px; font-weight: 600; }
  .sub { color: var(--muted); font-size: 13px; overflow-wrap: anywhere; }
</style>
</head>
<body>
<header>
  <div class="brand"><b>Dubcrate</b><span>Track Finder</span></div>
  <input id="q" type="search" placeholder="Title or artist" autocomplete="off"
         autocorrect="off" autocapitalize="off" spellcheck="false" enterkeyhint="search">
  <div class="note" id="status"></div>
</header>
<main id="results"></main>
<script id="data" type="application/json">__DATA__</script>
<script>
"use strict";
const data = JSON.parse(document.getElementById("data").textContent);
const LIMIT = 100;

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
  for (const [number, title, artist, folder] of disc.tracks)
    rows.push({ disc, number, title, artist, folder,
                key: " " + norm(title + " " + artist) });
});

const q = document.getElementById("q");
const status = document.getElementById("status");
const results = document.getElementById("results");

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text) e.textContent = text;
  return e;
}

function search(text) {
  const words = norm(text).split(" ").filter(Boolean);
  if (!words.length) return [];
  const found = [];
  for (const r of rows) {
    if (!words.every(w => r.key.includes(w))) continue;
    // Words that start a word in the title or artist rank above ones found inside a word.
    r.rank = words.every(w => r.key.includes(" " + w)) ? 0 : 1;
    found.push(r);
  }
  return found.sort((a, b) => a.rank - b.rank || a.key.localeCompare(b.key));
}

function show() {
  const found = search(q.value);
  results.replaceChildren();
  if (!q.value.trim()) {
    status.textContent = rows.length + " tracks on " + data.discs.length +
                         " discs \\u00b7 made " + data.made;
    return;
  }
  if (!found.length) { status.textContent = "Not on any disc"; return; }
  status.textContent = found.length > LIMIT
    ? "Showing " + LIMIT + " of " + found.length + ": type more to narrow it down"
    : found.length + (found.length === 1 ? " track" : " tracks");
  for (const r of found.slice(0, LIMIT)) {
    const hit = el("div", "hit");
    hit.style.setProperty("--stripe", r.disc.stripe);
    const what = el("div", "what");
    what.append(el("div", "title", r.title));
    if (r.artist) what.append(el("div", "artist", r.artist));
    const where = el("div", "where");
    where.append(el("div", "disc", r.disc.name));
    where.append(el("div", "num", "Track " + r.number));
    if (r.folder) where.append(el("div", "sub", r.folder));
    if (r.disc.where) where.append(el("div", "sub", r.disc.where));
    hit.append(what, where);
    results.append(hit);
  }
}

q.addEventListener("input", show);
show();
q.focus();
</script>
</body>
</html>
"""
