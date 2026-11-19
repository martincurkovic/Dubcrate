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

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dubcrate import keys, labels
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
    bpm: str = ""  # whole number, "" when unknown (as are the rest)
    key: str = ""
    year: str = ""
    genre: str = ""
    length: str = ""  # M:SS

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
    lib: Library, disc_id: int, where: dict[int | None, str], notation: str
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
        track = entry.track
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
            )
        )
    if not tracks:
        return None
    return FinderDisc(
        disc.name, where.get(disc.folder_id, ""), disc.kind, tuple(tracks)
    )


def collect(
    lib: Library, *, disc_id: int | None = None, notation: str = keys.CAMELOT
) -> Finder:
    """One disc, or (the default) every disc, with keys in `notation`. Raises `FinderError` if no disc
    has a track."""
    where = _disc_where(lib)
    ids = [disc_id] if disc_id is not None else [d.id for d in lib.discs()]
    found = Finder()
    for i in ids:
        disc = _disc(lib, i, where, notation)
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
  [hidden] { display: none !important; }
  .bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 8px; }
  .bar button, .bar select, .frow select, .frow button { font: inherit; font-size: 16px; color: var(--bright);
         background: var(--input); border: 1px solid var(--border); border-radius: 6px;
         padding: 8px 12px; min-height: 40px; }
  .bar button[aria-pressed="true"] { background: var(--accent); border-color: var(--accent);
         color: #fff; }
  .sortrow { display: flex; gap: 8px; flex: 1 1 100%; min-width: 0; }
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
  .hit { background: var(--card); border: 1px solid var(--edge); border-left: 5px solid var(--stripe);
         border-radius: 6px; padding: 11px 14px; margin: 0 0 8px;
         display: flex; gap: 12px; align-items: center; }
  .what { flex: 1; min-width: 0; }
  .title { color: var(--bright); font-weight: 600; overflow-wrap: anywhere; }
  .artist { color: var(--muted); font-size: 15px; overflow-wrap: anywhere; }
  .info { color: var(--muted); font-size: 13px; margin-top: 3px; overflow-wrap: anywhere; }
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
  <div class="bar">
    <button id="all" type="button" aria-pressed="false">All tracks</button>
    <button id="tagged" type="button" aria-pressed="false">Tagged</button>
    <button id="filter" type="button" aria-pressed="false">Filter</button>
    <span class="sortrow" id="sortrow" hidden>
      <select id="sort" aria-label="Sort by"></select>
      <button id="dir" type="button" aria-label="Reverse the order">\u2191</button>
    </span>
  </div>
  <div class="note" id="status"></div>
</header>
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
</section>
<main id="results"></main>
<script id="data" type="application/json">__DATA__</script>
<script>
// Inside a function so no name here can clash with a browser global (window.top is one: a
// top-level `function top` stops the whole script from loading).
(() => {
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
  disc.index = i;
  for (const [number, title, artist, folder, info, bpm, key, year, genre, length] of disc.tracks) {
    const clock = /^([0-9]+):([0-9]{2})$/.exec(length);
    rows.push({ disc, number, title, artist, folder, info, genre, key, order: rows.length,
                id: JSON.stringify([disc.name, folder, number, title]),
                text: " " + norm(title + " " + artist),
                titleKey: norm(title), artistKey: norm(artist),
                bpmN: parseInt(bpm, 10) || 0, yearN: parseInt(year, 10) || 0,
                secs: clock ? +clock[1] * 60 + +clock[2] : 0, keyN: keyRank(key) });
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
  title:  { label: "Title",  value: r => r.titleKey || null,  group: r => initial(r.titleKey) },
  artist: { label: "Artist", value: r => r.artistKey || null, group: r => r.artistKey ? initial(r.artistKey) : "No artist" },
  genre:  { label: "Genre",  value: r => r.genre.toLowerCase() || null, group: r => r.genre || "No genre" },
  bpm:    { label: "BPM",    value: r => r.bpmN || null,  group: r => r.bpmN ? Math.floor(r.bpmN / 10) * 10 + "s" : "No BPM" },
  key:    { label: "Key",    value: r => r.keyN,          group: r => r.key || "No key" },
  year:   { label: "Year",   value: r => r.yearN || null, group: r => r.yearN || "No year" },
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
let browse = false, tagView = false, sortKey = "title", descending = false;

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

function toggleTag(r) {
  const i = tags.indexOf(r.id);
  if (i < 0) tags.push(r.id); else tags.splice(i, 1);
  saveTags();
  showTagCount();
  if (tagView) showStatus();
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

function search(text) {
  return matching(text).sort((a, b) => a.rank - b.rank || a.text.localeCompare(b.text));
}

// All tracks (or those matching the search) in the chosen order. A track with nothing for the
// chosen column goes last whichever way it runs; ties fall back to the title, then the disc.
function sorted(list) {
  const s = SORTS[sortKey], dir = descending ? -1 : 1;
  const tie = (a, b) => sortKey === "disc" ? a.order - b.order
    : a.titleKey.localeCompare(b.titleKey) || a.order - b.order;
  return list.sort((a, b) => {
    const x = s.value(a), y = s.value(b);
    if (x === null || y === null) return x === y ? tie(a, b) : x === null ? 1 : -1;
    const c = typeof x === "string" ? x.localeCompare(y) : x - y;
    return c ? c * dir : tie(a, b);
  });
}

function card(r) {
  const hit = el("div", "hit");
  hit.style.setProperty("--stripe", r.disc.stripe);
  const what = el("div", "what");
  what.append(el("div", "title", r.title));
  if (r.artist) what.append(el("div", "artist", r.artist));
  if (r.info.length) what.append(el("div", "info", r.info.join(" · ")));
  const where = el("div", "where");
  where.append(el("div", "disc", r.disc.name));
  where.append(el("div", "num", "Track " + r.number));
  if (r.folder) where.append(el("div", "sub", r.folder));
  if (r.disc.where) where.append(el("div", "sub", r.disc.where));
  // In the Tagged list an untagged track stays, dimmed, until the list is shown again, so a slip is undone with one tap.
  const star = el("button", "star");
  star.type = "button";
  const paint = () => {
    const on = tags.includes(r.id);
    star.textContent = on ? "★" : "☆";
    star.className = "star" + (on ? " on" : "");
    star.setAttribute("aria-label", on ? "Remove from Tagged" : "Tag this track");
    hit.className = "hit" + (tagView && !on ? " off" : "");
  };
  star.addEventListener("click", () => { toggleTag(r); paint(); });
  paint();
  hit._rid = r.id;
  if (tagView) {
    // Hold the handle and move: the card follows the finger and the others make room.
    const grip = el("div", "grip", "≡");
    grip.setAttribute("aria-label", "Drag to move");
    grip.addEventListener("pointerdown", e => startDrag(e, hit, grip));
    grip.addEventListener("pointermove", e => moveDrag(e, hit));
    grip.addEventListener("pointerup", () => endDrag(hit));
    grip.addEventListener("pointercancel", () => endDrag(hit));
    hit.append(grip, what, where, star);
  } else hit.append(what, where, star);
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

function draw() {
  if (watcher) watcher.unobserve(sentinel);
  sentinel.remove();
  const group = listing ? SORTS[sortKey].group : null;
  const end = watcher ? Math.min(pending.length, shown + CHUNK) : pending.length;
  for (; shown < end; shown++) {
    const r = pending[shown];
    if (group) {
      const g = String(group(r));
      if (g !== heading) { results.append(el("div", "group", g)); heading = g; }
    }
    results.append(card(r));
  }
  if (shown < pending.length) { results.append(sentinel); watcher.observe(sentinel); }
}

function showStatus() {
  const text = q.value.trim();
  if (tagView) {
    const note = storageOk ? "kept on this device" : "NOT kept: this app can't save them";
    status.textContent = tags.length
      ? tags.length + " tagged · " + note
      : "Nothing tagged yet: tap the star on a track · " + note;
  } else if (listing) {
    status.textContent = (text || filtering ? shownCount + " of " + rows.length : rows.length) + " tracks · by " +
      SORTS[sortKey].label + (descending ? ", reversed" : "");
  } else if (!text) {
    status.textContent = rows.length + " tracks on " + data.discs.length +
                         " discs · made " + data.made;
  } else if (!shownCount) {
    status.textContent = "Not on any disc";
  } else {
    status.textContent = shownCount > LIMIT
      ? "Showing " + LIMIT + " of " + shownCount + ": type more to narrow it down"
      : shownCount + (shownCount === 1 ? " track" : " tracks");
  }
}

// The list is "sorted" (with headings and the sort menu) in All tracks, and whenever a filter is set.
let shownCount = 0, listing = false, filtering = false;
function show() {
  const text = q.value.trim();
  const f = filterState();
  filtering = f.count > 0 && !tagView;
  listing = browse || filtering;
  filterButton.textContent = f.count ? "Filter · " + f.count : "Filter";
  sortRow.hidden = tagView || !listing;
  let found;
  if (tagView) found = tags.map(id => byId.get(id));
  else if (listing) {
    const pool = filtering ? rows.filter(r => passes(r, f)) : rows;
    found = sorted(text ? matching(text, pool) : pool.slice());
  } else found = text ? search(text) : [];
  results.replaceChildren();
  shownCount = found.length;
  pending = !listing && !tagView && found.length > LIMIT ? found.slice(0, LIMIT) : found;
  heading = null; shown = 0;
  showStatus();
  draw();
}

function scrollUp() { if (typeof window !== "undefined") window.scrollTo(0, 0); }

function setMode(mode) {  // "search", "all" or "tagged"
  browse = mode === "all";
  tagView = mode === "tagged";
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

q.addEventListener("input", () => { if (tagView) setMode("search"); else show(); });
saveTags();  // finds out whether storage works here, and drops tags for tracks no longer on a disc
showTagCount();
show();
q.focus();
})();
</script>
</body>
</html>
"""
