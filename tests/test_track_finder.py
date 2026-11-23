"""The track finder: which disc is this track on (read-only, one HTML file)."""

import base64
import json
import os
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from dubcrate import track_finder as tf
from dubcrate.library import Library, TrackRecord
from dubcrate.track_finder import FinderError
from dubcrate.waveform import Waveform

MADE = date(2026, 11, 18)


@pytest.fixture
def lib():
    with Library.open(":memory:") as library:
        yield library


def seed(lib, names=("a", "b", "c", "d")):
    lib.replace_source(
        "folder",
        "/music",
        "M",
        [
            TrackRecord(
                ext_id=n,
                path=f"/music/{n}.mp3",
                title=f"Title {n.upper()}",
                artist=f"Artist {n.upper()}",
                duration=200.4,
            )
            for n in names
        ],
    )
    return {t.title[-1].lower(): t.id for t in lib.tracks(sort="path")}


def test_an_audio_disc_lists_its_tracks_in_running_order(lib):
    ids = seed(lib)
    disc = lib.create_disc("Night")
    lib.add_to_disc(disc, [ids["c"], ids["a"]])
    [found] = tf.collect(lib).discs
    assert found.name == "Night"
    assert [(t.number, t.title, t.artist) for t in found.tracks] == [
        (1, "Title C", "Artist C"),
        (2, "Title A", "Artist A"),
    ]


def test_the_text_is_what_is_written_on_the_disc(lib):
    ids = seed(lib)
    disc = lib.create_disc("Night")
    lib.add_to_disc(disc, [ids["a"]])
    lib.set_entry_text(disc, 0, "title", "Edited for the CD")
    lib.set_entry_text(disc, 0, "artist", "CD Artist")
    [track] = tf.collect(lib).discs[0].tracks
    assert (track.title, track.artist) == ("Edited for the CD", "CD Artist")
    # ...and the library's own track is untouched.
    assert lib.tracks(sort="path")[0].title == "Title A"


def test_an_mp3_disc_numbers_each_folder_from_one_in_player_order(lib):
    ids = seed(lib)
    disc = lib.create_disc("MP3", kind="mp3")
    z = lib.create_disc_dir(disc, "Zed")
    a = lib.create_disc_dir(disc, "Alpha")
    inner = lib.create_disc_dir(disc, "Inner", a)
    lib.add_to_disc(disc, [ids["a"]], dir_id=z)
    lib.add_to_disc(disc, [ids["b"]])
    lib.add_to_disc(disc, [ids["c"]], dir_id=a)
    lib.add_to_disc(disc, [ids["d"]], dir_id=inner)
    [found] = tf.collect(lib).discs
    assert [(t.title[-1], t.number, t.folder) for t in found.tracks] == [
        ("B", 1, ""),
        ("C", 1, "Alpha"),
        ("D", 1, "Alpha / Inner"),
        ("A", 1, "Zed"),
    ]


def test_a_track_on_two_discs_is_listed_for_both(lib):
    ids = seed(lib)
    for name in ("One", "Two"):
        lib.add_to_disc(lib.create_disc(name), [ids["a"]])
    assert [d.name for d in tf.collect(lib).discs] == ["One", "Two"]


def test_discs_carry_the_folders_they_are_filed_in_and_empty_ones_are_reported(lib):
    ids = seed(lib)
    sets = lib.create_disc_folder("Sets")
    friday = lib.create_disc_folder("Friday", sets)
    lib.add_to_disc(lib.create_disc("Opening", folder_id=friday), [ids["a"]])
    lib.add_to_disc(lib.create_disc("Top"), [ids["b"]])
    lib.create_disc("Blank")
    found = tf.collect(lib)
    assert [(d.name, d.where) for d in found.discs] == [
        ("Top", ""),
        ("Opening", "Sets / Friday"),
    ]
    assert found.empty_discs == ["Blank"]
    assert "Blank" in tf.summary(found)


def test_one_disc_only(lib):
    ids = seed(lib)
    one = lib.create_disc("One")
    lib.add_to_disc(one, [ids["a"]])
    lib.add_to_disc(lib.create_disc("Two"), [ids["b"]])
    assert [d.name for d in tf.collect(lib, disc_id=one).discs] == ["One"]


def test_a_file_that_is_missing_is_still_listed(lib):
    ids = seed(lib)
    disc = lib.create_disc("Night")
    lib.add_to_disc(disc, [ids["a"], ids["b"]])
    seed(lib, names=("a",))  # a re-import notices b has gone
    assert tf.collect(lib).tracks == 2


def test_nothing_to_list_is_an_expected_error(lib):
    seed(lib)
    lib.create_disc("Empty")
    with pytest.raises(FinderError, match="nothing to list"):
        tf.collect(lib)


def test_bpm_key_year_genre_and_length_come_from_the_library_track(lib):
    lib.replace_source(
        "folder",
        "/m",
        "M",
        [
            TrackRecord(
                ext_id="t",
                path="/m/t.mp3",
                title="T",
                artist="A",
                bpm=127.6,
                key="Am",
                year=2020,
                genre="  Deep   House ",
                duration=372.9,
            )
        ],
    )
    disc = lib.create_disc("D")
    lib.add_to_disc(disc, [lib.tracks(sort="path")[0].id])
    lib.set_entry_text(
        disc, 0, "title", "Edited"
    )  # the CD's own text changes only the name
    [track] = tf.collect(lib).discs[0].tracks
    assert track.title == "Edited"
    assert track.info == ["128 BPM", "8A", "2020", "Deep House", "6:12"]
    [musical] = tf.collect(lib, notation="musical").discs[0].tracks
    assert musical.info[1] == "A min"


def test_what_is_not_known_is_left_out_rather_than_shown_as_zero(lib):
    lib.replace_source(
        "folder",
        "/m",
        "M",
        [TrackRecord(ext_id="t", path="/m/t.mp3", title="T", artist="A", year=0)],
    )
    lib.add_to_disc(lib.create_disc("D"), [lib.tracks(sort="path")[0].id])
    [track] = tf.collect(lib).discs[0].tracks
    assert track.info == []


# -- the page ------------------------------------------------------------------------


def finder_with(lib, title, artist="X", disc="D"):
    lib.replace_source(
        "folder",
        "/m",
        "M",
        [TrackRecord(ext_id="t", path="/m/t.mp3", title=title, artist=artist)],
    )
    track = lib.tracks(sort="path")[0]
    lib.add_to_disc(lib.create_disc(disc), [track.id])
    return tf.collect(lib)


def embedded_data(text: str) -> dict:
    [raw] = re.findall(r'<script id="data" type="application/json">(.*?)</script>', text, re.DOTALL)  # fmt: skip
    return json.loads(raw)


def test_the_page_holds_the_data_and_no_network_reference(lib):
    page = tf.html_text(finder_with(lib, "Ünïcode ✓", "Bjørk"), MADE)
    data = embedded_data(page)
    assert data["made"] == "2026-11-18"
    assert data["discs"][0]["tracks"] == [
        [1, "Ünïcode ✓", "Bjørk", "", [], "", "", "", "", "", 0.0, "", [], 0.0, ""]
    ]
    assert "http" not in page.replace("http-equiv", "")


def test_a_name_with_markup_cannot_break_out_of_the_data(lib):
    nasty = "</script><script>alert(1)</script><!-- x"
    page = tf.html_text(finder_with(lib, nasty, disc="</script>"), MADE)
    assert page.count("<script") == 2  # the data and the code, nothing more
    assert embedded_data(page)["discs"][0]["tracks"][0][1] == nasty


def test_write_html_makes_the_file_and_leaves_no_part_file(lib, tmp_path):
    out = tmp_path / "finder.html"
    written = tf.write_html(finder_with(lib, "T"), out, MADE)
    assert (written.discs, written.tracks, written.path) == (1, 1, out)
    assert out.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")
    assert not list(tmp_path.glob("*.part"))


def test_an_unwritable_place_is_an_expected_error(lib, tmp_path):
    with pytest.raises(FinderError, match="Could not write"):
        tf.write_html(finder_with(lib, "T"), tmp_path / "nope" / "f.html", MADE)


def test_the_module_is_read_only_on_every_library():
    source = Path(tf.__file__).read_text(encoding="utf-8")
    assert "masterdb" not in source
    assert "commit(" not in source


# -- the page's search, run in node when there is one -----------------------------------

# The smallest stand-in for the browser the page's script uses.
NODE_HARNESS = r"""
const fs = require("fs");
const html = fs.readFileSync(process.argv[2], "utf8");
const data = html.match(/<script id="data"[^>]*>([\s\S]*?)<\/script>/)[1];
const code = html.match(/<script>\n([\s\S]*?)<\/script>/)[1];
function node(tag) {
  return { tag, className: "", textContent: "", value: "", children: [], isConnected: true,
    getContext() { return new Proxy({}, { get: (t, k) => k === "measureText" ? () => ({ width: 10 }) : () => {}, set: () => true }); },
    getBoundingClientRect() { return { left: 0, width: 300 }; }, handlers: {},
    style: { setProperty() {} },
    append(...c) { this.children.push(...c); },
    replaceChildren(...c) { this.children = c; },
    addEventListener(name, fn) { this.handlers[name] = fn; },
    focus() {}, remove() {}, setAttribute() {} };
}
const els = { data: { textContent: data }, q: node("input"), status: node("div"),
              results: node("main"), all: node("button"), tagged: node("button"), sortrow: node("span"),
              filter: node("button"), filters: node("section"), bpmMin: node("input"),
              bpmMax: node("input"), yearMin: node("input"), yearMax: node("input"),
              fkey: node("select"), fgenre: node("select"), fclear: node("button"),
              compact: node("button"), clear: node("button"), index: node("nav"),
              indexbtn: node("button"), bubble: node("div"),
              related: node("section"), relback: node("button"), reltitle: node("div"),
              reltol: node("select"), relhalf: node("button"), relenergy: node("button"),
              relgenre: node("button"), relsort: node("select"), rellast: node("button"),
              sort: node("select"), dir: node("button") };
els.reltol.value = "6";
els.q.value = "";
const header = Object.assign(node("header"), { offsetHeight: 90 });
const document = { getElementById: id => els[id], createElement: node, querySelector: () => header,
                   documentElement: node("html"), body: node("body"),
                   createTextNode: text => Object.assign(node("#text"), { textContent: text }) };
// A browser's global scope has protected names (window.top can't be redeclared): a script that
// declares one at the top level fails to load at all, which node's `new Function` would never show.
const vm = require("vm");
// A phone's local storage, kept between runs of the page in a file when asked.
const stored = process.env.FINDER_STORAGE && fs.existsSync(process.env.FINDER_STORAGE)
  ? JSON.parse(fs.readFileSync(process.env.FINDER_STORAGE, "utf8")) : {};
const localStorage = { getItem: k => (k in stored ? stored[k] : null), setItem: (k, v) => { stored[k] = v; } };
if (process.env.FINDER_NO_STORAGE) localStorage.setItem = () => { throw new Error("blocked"); };
process.on("exit", () => process.env.FINDER_STORAGE && fs.writeFileSync(process.env.FINDER_STORAGE, JSON.stringify(stored)));
const sandbox = { document, localStorage, scrolled: 0 };
sandbox.window = { scrollTo() { sandbox.scrolled++; }, scrollY: 0, addEventListener() {} };
sandbox.getComputedStyle = () => ({ top: "0px", getPropertyValue: () => "" });
sandbox.atob = atob;
sandbox.timers = [];
sandbox.setTimeout = fn => { sandbox.timers.push(fn); return sandbox.timers.length; };
sandbox.clearTimeout = () => {};
for (const name of ["top", "parent", "self", "location", "name", "status", "length"])
  Object.defineProperty(sandbox, name, { value: undefined, writable: false, configurable: false });
vm.runInNewContext(code, sandbox);
const out = {};
for (const query of JSON.parse(process.argv[3])) {
  els.q.value = query;
  els.q.handlers.input();
  out[query] = { status: els.status.textContent,
    hits: els.results.children.map(hit => {
      const what = hit.children[0];
      const where = what.children.find(c => c.className === "where");
      const info = what.children.find(c => c.className === "info");
      return [what.children[0].textContent, ...where.children.map(c => c.textContent),
              "info:" + (info ? info.textContent : "")];
    }) };
}
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_search_in_the_page(lib, tmp_path):
    lib.replace_source(
        "folder",
        "/m",
        "M",
        [
            TrackRecord(
                ext_id=str(i),
                path=f"/m/{i}.mp3",
                title=t,
                artist=a,
                **(
                    {"bpm": 90.0, "key": "8A", "year": 1995}
                    if t == "Hyperballad"
                    else {}
                ),
            )
            for i, (t, a) in enumerate(
                [
                    ("Hyperballad", "Björk"),
                    ("Don't Stop", "Fleetwood Mac"),
                    ("Stop Me Now", "Queen"),
                    ("Unstoppable", "Sia"),
                ]
            )
        ],
    )
    ids = [t.id for t in lib.tracks(sort="path")]
    one = lib.create_disc("Warm Up")
    lib.add_to_disc(one, ids[:2])
    folder = lib.create_disc_folder("Sets")
    two = lib.create_disc("Peak", folder_id=folder)
    lib.add_to_disc(two, ids[2:])
    page = tmp_path / "finder.html"
    tf.write_html(tf.collect(lib), page, MADE)
    queries = ["bjork", "DONT stop", "stop", "zzz", "", "me now queen"]
    harness = tmp_path / "harness.js"
    harness.write_text(NODE_HARNESS, encoding="utf-8")
    run = subprocess.run(
        ["node", str(harness), str(page), json.dumps(queries)],
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    got = json.loads(run.stdout)
    assert got["bjork"]["hits"] == [
        ["Hyperballad", "Warm Up", "Track 1", "info:90 BPM · 8A · 1995"]
    ]  # accents ignored
    assert got["DONT stop"]["hits"] == [["Don't Stop", "Warm Up", "Track 2", "info:"]]
    assert got["me now queen"]["hits"] == [
        ["Stop Me Now", "Peak", "Track 1", "Sets", "info:"]
    ]
    # Word starts rank above a match inside a word: "Unstoppable" comes last.
    assert [h[0] for h in got["stop"]["hits"]] == ["Don't Stop", "Stop Me Now", "Unstoppable"]  # fmt: skip
    assert got["zzz"]["status"] == "Not on any disc"
    assert got[""]["hits"] == [] and "4 tracks on 2 discs" in got[""]["status"]
    assert "made 2026-11-18" in got[""]["status"]


# The same stand-in, driven by clicks: the "All tracks" list and its sorting.
BROWSE_TAIL = r"""
const out = [];
function list() {
  return { status: els.status.textContent, sortHidden: els.sortrow.hidden, q: els.q.value,
    rows: els.results.children.map(c => c.className === "group" ? "# " + c.textContent
                                                                   : c.children[0].children[0].textContent) };
}
out.push(list());
els.all.handlers.click();
out.push(list());
for (const [sort, reverse] of JSON.parse(process.argv[3])) {
  els.sort.value = sort;
  els.sort.handlers.change();
  if (reverse) els.dir.handlers.click();
  out.push(list());
  if (reverse) els.dir.handlers.click();
}
els.sort.value = "title";
els.sort.handlers.change();
els.q.value = "zeta";
els.q.handlers.input();
out.push(list());
// All tracks replaces whatever was typed; typing again is a search (best match first by default).
els.q.value = "zeta";
els.q.handlers.input();
els.all.handlers.click();
out.push(list());
els.sort.value = "match";
els.sort.handlers.change();
els.q.value = "mid";
els.q.handlers.input();
out.push(list());
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_all_tracks_sorted_in_the_page(lib, tmp_path):
    def extra(**kw):
        return kw

    rows = [
        ("Zeta", "Bee", extra(bpm=130.0, key="8A", year=2001, genre="House", duration=125)),
        ("Alpha", "Cee", extra(bpm=95.0, key="1B", year=1999, genre="Dub", duration=300)),
        ("Mid", "Ay", extra(bpm=124.0, key="8B", year=2010, genre="House", duration=61)),
        ("Nothing", "", {}),
    ]  # fmt: skip
    lib.replace_source(
        "folder",
        "/m",
        "M",
        [
            TrackRecord(ext_id=str(i), path=f"/m/{i}.mp3", title=t, artist=a, **more)
            for i, (t, a, more) in enumerate(rows)
        ],
    )
    lib.add_to_disc(lib.create_disc("D"), [t.id for t in lib.tracks(sort="path")])
    page = tmp_path / "finder.html"
    tf.write_html(tf.collect(lib), page, MADE)
    harness = tmp_path / "harness.js"
    harness.write_text(NODE_HARNESS.split("const out = {};")[0] + BROWSE_TAIL, encoding="utf-8")  # fmt: skip
    steps = [["artist", False], ["bpm", False], ["bpm", True], ["key", False], ["length", False], ["year", True], ["genre", False]]  # fmt: skip
    run = subprocess.run(
        ["node", str(harness), str(page), json.dumps(steps)],
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    got = json.loads(run.stdout)

    def titles(i):
        return [r for r in got[i]["rows"] if not r.startswith("# ")]

    # The sort menu is there from the start, for a search as much as for All tracks.
    assert got[0]["rows"] == [] and got[0]["sortHidden"] is False
    assert got[1]["sortHidden"] is False and got[1]["status"].startswith("4 tracks")
    # By title, with a heading per first letter.
    assert got[1]["rows"] == ["# A", "Alpha", "# M", "Mid", "# N", "Nothing", "# Z", "Zeta"]  # fmt: skip
    # A track with nothing for the column is last, in either direction.
    assert titles(2) == ["Mid", "Zeta", "Alpha", "Nothing"]  # artist
    assert titles(3) == ["Alpha", "Mid", "Zeta", "Nothing"]  # bpm
    assert titles(4) == ["Zeta", "Mid", "Alpha", "Nothing"]  # bpm, reversed
    assert titles(5) == ["Alpha", "Zeta", "Mid", "Nothing"]  # key: 1B, 8A, 8B
    assert titles(6) == ["Mid", "Zeta", "Alpha", "Nothing"]  # length
    assert titles(7) == ["Mid", "Zeta", "Alpha", "Nothing"]  # year, newest first
    assert titles(8) == ["Alpha", "Mid", "Zeta", "Nothing"]  # genre
    assert "# House" in got[8]["rows"]
    # A search narrows the list and keeps the order.
    assert titles(9) == ["Zeta"] and got[9]["status"].startswith("1 of 4 tracks")
    # Pressing All tracks with a search typed clears it and lists everything.
    assert got[10]["q"] == "" and len(titles(10)) == 4
    assert got[10]["status"].startswith("4 tracks")
    # Typing after that searches again, ordered by best match.
    assert (
        titles(11) == ["Mid"] and "1 of 4 tracks · by Best match" in got[11]["status"]
    )


# The same stand-in again: the filters (BPM and year ranges, key, genre) with and without a search.
FILTER_TAIL = r"""
const part = (c, cls) => c.children.find(x => x.className.split(" ")[0] === cls);
const titles = () => els.results.children.filter(c => c.className.startsWith("hit"))
                                         .map(c => part(c, "what").children[0].textContent);
const out = {};
const snap = name => { out[name] = { titles: titles(), status: els.status.textContent,
                                      button: els.filter.textContent, sortHidden: els.sortrow.hidden }; };
els.sortrow.hidden = true;
const set = (el, v, ev = "input") => { el.value = v; el.handlers[ev](); };
set(els.q, "jackson");
snap("search");
set(els.sort, "bpm", "change");
snap("searchByBpm");
set(els.sort, "match", "change");
set(els.bpmMin, "130");
snap("bpm130");
set(els.bpmMax, "140");
snap("bpm130to140");
set(els.bpmMin, ""); set(els.bpmMax, "");
set(els.yearMin, "1990");
snap("year1990");
set(els.yearMin, "");
set(els.fgenre, "pop", "change");
snap("pop");
set(els.q, "");
set(els.fgenre, "", "change");
set(els.fkey, "8A", "change");
snap("key8A_nosearch");
out.keyOptions = els.fkey.children.map(o => o.value);
out.genreOptions = els.fgenre.children.map(o => o.textContent);
els.fclear.handlers.click();
snap("cleared");
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_filters_in_the_page(lib, tmp_path):
    def more(**kw):
        return kw

    rows = [
        ("Beat It", "Michael Jackson", more(bpm=139.0, key="8A", year=1982, genre="Pop")),
        ("Billie Jean", "Michael Jackson", more(bpm=117.0, key="8A", year=1982, genre="Pop")),
        ("Thriller", "Michael Jackson", more(bpm=118.0, key="3B", year=1982, genre="Disco")),
        ("Rock With You", "Michael Jackson", more(year=1979, genre="pop")),
        ("Jackson 5 Medley", "Other Jackson", more(bpm=135.0, key="9A", year=1995)),
        ("Fast One", "Someone", more(bpm=150.0, key="12B", year=2020, genre="Techno")),
    ]  # fmt: skip
    lib.replace_source(
        "folder",
        "/m",
        "M",
        [
            TrackRecord(ext_id=str(i), path=f"/m/{i}.mp3", title=t, artist=a, **extra)
            for i, (t, a, extra) in enumerate(rows)
        ],
    )
    lib.add_to_disc(lib.create_disc("D"), [t.id for t in lib.tracks(sort="path")])
    page = tmp_path / "finder.html"
    tf.write_html(tf.collect(lib), page, MADE)
    harness = tmp_path / "harness.js"
    harness.write_text(NODE_HARNESS.split("const out = {};")[0] + FILTER_TAIL, encoding="utf-8")  # fmt: skip
    run = subprocess.run(
        ["node", str(harness), str(page)],
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    got = json.loads(run.stdout)
    # No filter: the search as before (ranked, no sort menu).
    assert got["search"]["sortHidden"] is False and got["search"]["button"] == "Filter"
    assert len(got["search"]["titles"]) == 5
    # The same search ordered by BPM: a track with no BPM goes last.
    assert got["searchByBpm"]["titles"] == ["Billie Jean", "Thriller", "Jackson 5 Medley", "Beat It", "Rock With You"]  # fmt: skip
    assert "by BPM" in got["searchByBpm"]["status"]
    # BPM from 130: only those with a BPM at or above it ("Rock With You" has none).
    assert got["bpm130"]["titles"] == ["Beat It", "Jackson 5 Medley"]
    assert (
        got["bpm130"]["button"] == "Filter · 1" and got["bpm130"]["sortHidden"] is False
    )
    assert "2 of 6 tracks" in got["bpm130"]["status"]
    assert got["bpm130to140"]["titles"] == ["Beat It", "Jackson 5 Medley"]
    assert got["year1990"]["titles"] == ["Jackson 5 Medley"]
    # A genre ignores capitals ("Pop" and "pop" are one genre).
    assert got["pop"]["titles"] == ["Beat It", "Billie Jean", "Rock With You"]
    # A filter alone, no search text, lists the matching tracks.
    assert got["key8A_nosearch"]["titles"] == ["Beat It", "Billie Jean"]
    assert got["keyOptions"] == ["", "3B", "8A", "9A", "12B"]
    assert got["genreOptions"] == ["Any genre", "Disco", "Pop", "Techno"]
    assert got["cleared"]["button"] == "Filter" and got["cleared"]["titles"] == []


# The same stand-in again: opening a card shows its memory cues and a time for each.
WAVE_TAIL = r"""
const open = { target: { closest: () => null } };
const cards = () => els.results.children.filter(c => c.className.startsWith("hit"));
const part = (c, cls) => c.children.find(x => x.className.split(" ")[0] === cls);
const titleOf = c => part(c, "what").children[0].textContent;
els.all.handlers.click();
const out = {};
for (const card of cards()) {
  card.handlers.click(open);
  const box = card.children.find(x => x.className.startsWith("wavebox"));
  const chips = box.children.find(x => x.className === "chips");
  const readout = box.children.find(x => x.className === "readout");
  const canvas = box.children.find(x => x.className === "wave");
  const entry = { box: box.className, readout: readout ? readout.textContent : null,
    chips: chips ? chips.children.map(c => c.children.map(k => k.textContent).join(" ")) : [] };
  if (canvas) {
    canvas.handlers.click({ clientX: 150 });
    entry.tapMiddle = readout.textContent;
    canvas.handlers.click({ clientX: 120 });  // 5 px from the second cue: jumps to it
    entry.tapNearCue = readout.textContent;
    chips.children[0].handlers.click();
    entry.chipReadout = readout.textContent;
  }
  out[titleOf(card)] = entry;
}
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_opening_a_card_shows_its_memory_cues_and_times(lib, tmp_path):
    lib.replace_source(
        "folder",
        "/m",
        "M",
        [
            TrackRecord(ext_id="1", path="/m/1.mp3", title="With Cues", duration=275.0, file_size=10),
            TrackRecord(ext_id="2", path="/m/2.mp3", title="Plain", duration=100.0, file_size=10),
        ],
    )  # fmt: skip
    ids = {t.title: t for t in lib.tracks(sort="path")}
    lib.set_memory_cues(ids["With Cues"].path, [(32000, "Drop"), (105500, "")])
    lib.set_preview(
        ids["With Cues"].path, 10, Waveform(np.full((50, 3), 0.5, np.float32), 0.0)
    )
    lib.add_to_disc(lib.create_disc("D"), [t.id for t in ids.values()])
    page = tmp_path / "finder.html"
    tf.write_html(tf.collect(lib), page, MADE)
    harness = tmp_path / "harness.js"
    harness.write_text(NODE_HARNESS.split("const out = {};")[0] + WAVE_TAIL, encoding="utf-8")  # fmt: skip
    run = subprocess.run(
        ["node", str(harness), str(page)],
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    got = json.loads(run.stdout)
    cued = got["With Cues"]
    assert cued["chips"] == ["1 0:32 Drop", "2 1:45"]
    assert cued["readout"].startswith("Length 4:35")
    assert cued["tapMiddle"] == "2:17 of 4:35"
    assert cued["tapNearCue"] == "Cue 2 · 1:45"
    assert cued["chipReadout"] == "Cue 1 · 0:32 · Drop"
    # No waveform and no cue: it says so, and draws nothing.
    assert (
        got["Plain"]["box"].startswith("wavebox none") and got["Plain"]["chips"] == []
    )


# The same stand-in again: tagging tracks to play later.
TAG_TAIL = r"""
const mode = process.argv[3];
const cards = () => els.results.children.filter(c => c.className.startsWith("hit"));
const part = (c, cls) => c.children.find(x => x.className.split(" ")[0] === cls);
const titleOf = c => part(c, "what").children[0].textContent;
const starOf = t => part(cards().find(c => titleOf(c) === t), "star");
els.sortrow.hidden = true;
if (mode === "tag") {
  els.all.handlers.click();
  for (const t of ["Nothing", "Alpha"]) starOf(t).handlers.click();
}
els.tagged.handlers.click();
const out = { button: els.tagged.textContent, status: els.status.textContent, titles: cards().map(titleOf) };
if (mode === "untag") {
  starOf("Alpha").handlers.click();
  out.afterUntag = cards().map(c => [titleOf(c), c.className]);
  out.statusAfter = els.status.textContent;
  starOf("Alpha").handlers.click();  // a slip is undone with one tap
  out.afterRetag = cards().map(c => [titleOf(c), c.className]);
  starOf("Alpha").handlers.click();
}
if (mode === "clear") {
  const text = () => [els.clear.textContent, els.clear.className, els.clear.hidden];
  out.before = text();
  els.clear.handlers.click();          // the first tap only asks
  out.asked = text();
  out.stillTagged = els.tagged.textContent;
  sandbox.timers[0]();                  // ... and the question lapses
  out.lapsed = text();
  els.clear.handlers.click();
  els.clear.handlers.click();          // asked again, then confirmed
  out.after = text();
  out.button = els.tagged.textContent;
  out.titles = cards().map(titleOf);
  out.status = els.status.textContent;
}
if (mode === "type") {
  els.q.value = "alpha";
  els.q.handlers.input();  // typing leaves the Tagged list for the search
  out.pressed = els.tagged.textContent;
  out.titles = cards().map(titleOf);
}
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_tagging_tracks_to_play_later(lib, tmp_path):
    lib.replace_source(
        "folder",
        "/m",
        "M",
        [
            TrackRecord(ext_id=str(i), path=f"/m/{i}.mp3", title=t, artist="X")
            for i, t in enumerate(["Alpha", "Mid", "Nothing"])
        ],
    )
    lib.add_to_disc(lib.create_disc("D"), [t.id for t in lib.tracks(sort="path")])
    page = tmp_path / "finder.html"
    tf.write_html(tf.collect(lib), page, MADE)
    harness = tmp_path / "harness.js"
    harness.write_text(NODE_HARNESS.split("const out = {};")[0] + TAG_TAIL, encoding="utf-8")  # fmt: skip
    storage = tmp_path / "storage.json"

    def run(mode, *, kept=True):
        env = {**os.environ, "FINDER_STORAGE": str(storage)}
        if not kept:
            env["FINDER_NO_STORAGE"] = "1"
        done = subprocess.run(
            ["node", str(harness), str(page), mode],
            check=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        return json.loads(done.stdout)

    first = run("tag")
    assert first["titles"] == ["Nothing", "Alpha"]  # in the order tagged
    assert first["button"] == "Tagged 2" and "kept on this device" in first["status"]
    # Closing and opening the page again: still there.
    again = run("untag")
    assert again["titles"] == ["Nothing", "Alpha"] and again["button"] == "Tagged 2"
    # Untagging dims the card until the list is shown again; one more tap brings it back.
    assert again["afterUntag"] == [["Nothing", "hit"], ["Alpha", "hit off"]]
    assert "1 tagged" in again["statusAfter"]
    assert again["afterRetag"] == [["Nothing", "hit"], ["Alpha", "hit"]]
    # (the run ended by untagging it for good)
    last = run("show")
    assert last["titles"] == ["Nothing"] and last["button"] == "Tagged 1"
    assert run("type")["titles"] == ["Alpha"]
    # Clear: two taps; the first only asks, and the question lapses.
    storage.unlink()
    run("tag")  # (two tagged again: Nothing, Alpha)
    cleared = run("clear")
    assert cleared["before"][0] == "Clear" and cleared["before"][2] is False
    assert cleared["asked"][:2] == ["Tap again to clear 2", "danger"]
    assert cleared["stillTagged"] == "Tagged 2"
    assert cleared["lapsed"][:2] == ["Clear", ""]
    assert cleared["button"] == "Tagged" and cleared["titles"] == []
    assert cleared["after"][2] is True  # nothing left to clear: the button goes
    assert "Nothing tagged yet" in cleared["status"]
    assert run("show")["button"] == "Tagged"  # and it stays cleared
    # A viewer that cannot keep storage says so, rather than pretending.
    storage.unlink()
    blocked = run("tag", kept=False)
    assert blocked["titles"] == ["Nothing", "Alpha"]
    assert "NOT kept" in blocked["status"]


# -- waveforms and memory cues in the page data ----------------------------------------


def one_track_disc(lib, **track):
    lib.replace_source(
        "folder",
        "/m",
        "M",
        [TrackRecord(ext_id="t", path="/m/t.mp3", title="T", file_size=10, duration=200.0, **track)],
    )  # fmt: skip
    t = lib.tracks(sort="path")[0]
    lib.add_to_disc(lib.create_disc("D"), [t.id])
    return t


def test_a_kept_preview_becomes_a_400_column_waveform(lib):
    t = one_track_disc(lib)
    bands = np.zeros((1200, 3), np.float32)
    bands[:, 0] = (
        0.25  # steady low band: scaled up so the loudest part fills the height
    )
    lib.set_preview(t.path, 10, Waveform(bands, 0.0))
    [track] = tf.collect(lib).discs[0].tracks
    kind, raw = track.wave.split(":", 1)
    data = np.frombuffer(base64.b64decode(raw), np.uint8).reshape(-1, 3)
    assert kind == "3" and data.shape == (tf.WAVE_COLUMNS, 3)
    assert data[:, 0].min() == 255 and data[:, 1].max() == 0
    assert track.seconds == 200.0


def test_the_cdj_record_is_the_last_resort_and_is_reported(lib):
    t = one_track_disc(lib)
    lib.set_waveform(t.id, [1 + i % 15 for i in range(100)])
    found = tf.collect(lib)
    [track] = found.discs[0].tracks
    assert track.wave.startswith("1:") and found.coarse_wave == 1
    assert "coarse waveform" in tf.summary(found)


def test_no_waveform_at_all_is_counted(lib):
    one_track_disc(lib)
    found = tf.collect(lib)
    assert found.without_wave == 1 and found.discs[0].tracks[0].wave == ""
    assert "1 track(s) have no waveform" in tf.summary(found)
    assert tf.collect(lib, waveforms=False).without_wave == 0


def test_a_cd_length_is_the_exact_length(lib):
    t = one_track_disc(lib)
    lib.set_cd_sectors(t.id, 75 * 201)
    assert tf.collect(lib).discs[0].tracks[0].seconds == 201.0


def test_memory_cues_are_in_time_order_and_nothing_else(lib):
    t = one_track_disc(lib)
    lib.set_memory_cues(t.path, [(90500, "Break"), (1000, "")])
    lib.set_memory_loops(t.path, [(5000, 9000, "Loop")])
    [track] = tf.collect(lib).discs[0].tracks
    assert track.cues == (
        (1.0, "", ""),
        (90.5, "Break", ""),
    )  # the loop is not a cue here


def test_hot_cues_are_shown_as_the_memory_cues_they_become_on_the_card(lib):
    from dubcrate.analysis import Cue

    lib.replace_source(
        "rekordbox",
        "/rb/master.db",
        "RB",
        [
            TrackRecord(
                ext_id="42", path="/m/t.mp3", title="T", file_size=10, duration=300.0
            )
        ],
    )
    track = lib.tracks(sort="path")[0]
    lib.add_to_disc(lib.create_disc("D"), [track.id])
    cues = [
        Cue("memory", 60.0, label="Build"),
        Cue("hot", 32.0, slot=1, label="Drop"),  # a hot cue: a memory cue on the card
        Cue("hot", 200.0, slot=4),  # hot cue D
        Cue("memory", 32.01),  # at the same place as hot A: one cue on the card
        Cue("hot", 100.0, slot=2),
        Cue("memory", 100.0),
        Cue("loop", 150.0, end=154.0),  # a loop stays a loop: not shown here
        Cue("loop", 170.0, end=174.0, slot=3),  # a hot cue saved as a loop: neither
        Cue("memory", -1.0),  # before the track starts
    ]
    tables = {"/rb/master.db": {"42": cues}}
    [shown] = tf.collect(lib, cue_tables=tables).discs[0].tracks
    assert shown.cues == (
        (32.0, "Drop", "A"),
        (60.0, "Build", ""),
        (100.0, "", "B"),  # a hot cue and a memory cue together
        (200.0, "", "D"),
    )


def test_hot_cues_travel_in_the_page_data(lib):
    from dubcrate.analysis import Cue

    lib.replace_source("rekordbox", "/rb/master.db", "RB", [TrackRecord(ext_id="1", path="/m/t.mp3", title="T", file_size=10, duration=100.0)])  # fmt: skip
    lib.add_to_disc(lib.create_disc("D"), [lib.tracks(sort="path")[0].id])
    tables = {"/rb/master.db": {"1": [Cue("hot", 12.0, slot=3, label="Go")]}}
    page = tf.html_text(tf.collect(lib, cue_tables=tables), MADE)
    assert embedded_data(page)["discs"][0]["tracks"][0][12] == [
        [12.0, "Go", "C", 0, 0, 0]
    ]


def _grid_track(lib):
    lib.replace_source("folder", "/m", "M", [TrackRecord(ext_id="t", path="/m/t.mp3", title="T", file_size=10, duration=100.0)])  # fmt: skip
    t = lib.tracks(sort="path")[0]
    lib.add_to_disc(lib.create_disc("D"), [t.id])
    return t


def test_each_cue_is_placed_on_its_nearest_beat(lib):
    t = _grid_track(lib)
    # 120 bpm from 1.0 s: beat 1 of bar 1 at 1.0, bar 2 at 3.0, bar 3 at 5.0
    times = 1.0 + 0.5 * np.arange(40)
    numbers = (np.arange(40) % 4 + 1).astype(np.int8)
    lib.set_memory_cues(t.path, [(1000, ""), (3010, "Drop"), (5230, ""), (0, "")])
    [track] = tf.collect(lib, grids={t.id: (times, numbers)}).discs[0].tracks
    assert [c[0] for c in track.cues] == [0.0, 1.0, 3.01, 5.23]
    assert track.beats == (
        (1, 1, -1000),  # before the first beat: measured from it
        (1, 1, 0),
        (2, 1, 10),  # 10 ms after bar 2
        (3, 1, 230),  # 230 ms after bar 3 beat 1: nearer that than beat 2 (250 ms away)
    )


def test_a_track_with_no_beat_grid_has_times_only(lib):
    t = _grid_track(lib)
    lib.set_memory_cues(t.path, [(5000, "")])
    [track] = tf.collect(lib, grids={}).discs[0].tracks
    assert track.cues == ((5.0, "", ""),) and track.beats == ()
    page = embedded_data(tf.html_text(tf.collect(lib, grids={}), MADE))
    assert page["discs"][0]["tracks"][0][12] == [[5.0, "", "", 0, 0, 0]]


def test_the_page_carries_the_bar_and_beat_with_the_time(lib):
    t = _grid_track(lib)
    lib.set_memory_cues(t.path, [(3000, "Drop")])
    times, numbers = 1.0 + 0.5 * np.arange(40), (np.arange(40) % 4 + 1).astype(np.int8)
    page = embedded_data(tf.html_text(tf.collect(lib, grids={t.id: (times, numbers)}), MADE))  # fmt: skip
    assert page["discs"][0]["tracks"][0][12] == [[3.0, "Drop", "", 2, 1, 0]]


def test_beat_grids_are_read_for_the_tracks_that_have_rekordbox_analysis(
    lib, tmp_path, monkeypatch
):
    lib.replace_source(
        "rekordbox",
        str(tmp_path / "master.db"),
        "RB",
        [
            TrackRecord(ext_id="1", path="/m/a.mp3", title="A", file_size=10, analysis_path="/PIONEER/a/ANLZ0000.DAT"),
            TrackRecord(ext_id="2", path="/m/b.mp3", title="B", file_size=10),
        ],
    )  # fmt: skip
    lib.add_to_disc(lib.create_disc("D"), [t.id for t in lib.tracks(sort="path")])
    sources = tf.beat_sources(lib)
    assert len(sources) == 1  # B has no analysis, so no grid
    [(root, path)] = sources.values()
    assert root == tmp_path / "share" and path == "/PIONEER/a/ANLZ0000.DAT"
    grid = (np.array([0.0, 0.5]), np.array([1, 2], dtype=np.int8))
    monkeypatch.setattr(
        tf,
        "read_beat_grid",
        lambda r, p: grid if p.endswith("a/ANLZ0000.DAT") else None,
    )
    grids = tf.read_beat_grids(sources)
    assert list(grids.values()) == [grid]


def test_unreadable_cues_do_not_stop_the_export(lib):
    one_track_disc(lib)
    found = tf.collect(lib, cue_tables={}, cue_problem="database is locked")
    assert found.cue_problem == "database is locked"
    assert found.discs[0].tracks[0].cues == ()
    assert "Cues could not be read: database is locked" in tf.summary(found)


def test_cue_tables_read_once_are_used(lib):
    one_track_disc(lib)
    tables, problem = tf.read_cue_tables([])
    assert (tables, problem) == ({}, "")
    assert tf.collect(lib, cue_tables=tables).discs[0].tracks[0].cues == ()


def test_a_database_that_cannot_be_read_is_a_message(tmp_path):
    tables, problem = tf.read_cue_tables([str(tmp_path / "nope" / "master.db")])
    assert tables == {} and problem


class FakeAudio:
    def __init__(self, seconds):
        self._n = int(44100 * seconds)

    def mono(self):
        return (0.5 * np.sin(np.arange(self._n) * 0.05)).astype(np.float32)


def test_analysing_the_missing_waveforms_from_the_audio(lib, tmp_path, monkeypatch):
    real = tmp_path / "a.mp3"
    real.write_bytes(b"x")
    lib.replace_source(
        "folder",
        str(tmp_path),
        "M",
        [
            TrackRecord(ext_id="a", path=str(real), title="Real", file_size=1),
            TrackRecord(ext_id="b", path=str(tmp_path / "gone.mp3"), title="Gone", file_size=1),
        ],
    )  # fmt: skip
    tracks = lib.tracks(sort="path")
    lib.add_to_disc(lib.create_disc("D"), [t.id for t in tracks])
    # only the file that can be read, and has no waveform yet, needs analysing
    [todo] = tf.needs_analysis(lib)
    assert todo.title == "Real"
    monkeypatch.setattr(tf, "decode_file", lambda path: FakeAudio(3))
    seen = []
    found, problems = tf.analyse_waves(
        [todo], lambda done, total: seen.append((done, total))
    )
    assert problems == [] and seen == [(1, 1)]
    assert found[todo.id].columns == tf.PREVIEW_COLUMNS
    tf.store_waves(lib, [todo], found)
    assert tf.needs_analysis(lib) == []  # kept: the next export is quick
    wave = {t.title: t.wave for t in tf.collect(lib).discs[0].tracks}
    assert wave["Real"].startswith("3:") and wave["Gone"] == ""


def test_a_file_that_cannot_be_decoded_is_a_message_not_a_failure(
    lib, tmp_path, monkeypatch
):
    from dubcrate.audio.decode import DecodeError

    real = tmp_path / "a.mp3"
    real.write_bytes(b"x")
    lib.replace_source("folder", str(tmp_path), "M", [TrackRecord(ext_id="a", path=str(real), title="Bad", file_size=1)])  # fmt: skip
    [track] = lib.tracks(sort="path")

    def broken(path):
        raise DecodeError("not audio")

    monkeypatch.setattr(tf, "decode_file", broken)
    found, problems = tf.analyse_waves([track])
    assert found == {} and problems == ["Bad: not audio"]


def test_cancelling_the_analysis_keeps_what_was_done(lib, tmp_path, monkeypatch):
    from dubcrate.cancel import Cancelled, CancelToken

    files = []
    for n in range(3):
        f = tmp_path / f"{n}.mp3"
        f.write_bytes(b"x")
        files.append(TrackRecord(ext_id=str(n), path=str(f), title=str(n), file_size=1))
    lib.replace_source("folder", str(tmp_path), "M", files)
    tracks = lib.tracks(sort="path")
    token = CancelToken()
    monkeypatch.setattr(tf, "decode_file", lambda path: FakeAudio(1))

    def stop_after_first(done, total):
        token.cancel()

    with pytest.raises(Cancelled) as raised:
        tf.analyse_waves(tracks, stop_after_first, token, workers=1)
    found, problems = raised.value.partial
    assert 1 <= len(found) < 3 and problems == []


# -- the command line ------------------------------------------------------------------


def run_cli(tmp_path, *argv):
    from dubcrate import cli

    return cli.main(["--library", str(tmp_path / "lib.db"), *map(str, argv)])


def test_cli_export_finder(tmp_path, capsys):
    with Library.open(tmp_path / "lib.db") as library:
        ids = seed(library)
        one = library.create_disc("Night")
        library.add_to_disc(one, [ids["a"], ids["b"]])
        library.add_to_disc(library.create_disc("Day"), [ids["c"]])
    out = tmp_path / "finder.html"
    assert run_cli(tmp_path, "export-finder", out) == 0
    assert "3 track(s) on 2 disc(s)" in capsys.readouterr().out
    assert len(embedded_data(out.read_text(encoding="utf-8"))["discs"]) == 2
    assert run_cli(tmp_path, "export-finder", out) == 1  # never overwritten
    assert "already exists" in capsys.readouterr().err
    only = tmp_path / "one.html"
    assert run_cli(tmp_path, "export-finder", only, "--disc", one) == 0
    assert [d["name"] for d in embedded_data(only.read_text(encoding="utf-8"))["discs"]] == ["Night"]  # fmt: skip


def test_cli_analyses_missing_waveforms_unless_told_not_to(
    tmp_path, capsys, monkeypatch
):
    music = tmp_path / "m"
    music.mkdir()
    f = music / "a.mp3"
    f.write_bytes(b"x")
    with Library.open(tmp_path / "lib.db") as library:
        library.replace_source("folder", str(music), "M", [TrackRecord(ext_id="a", path=str(f), title="A", file_size=1)])  # fmt: skip
        library.add_to_disc(
            library.create_disc("D"), [t.id for t in library.tracks(sort="path")]
        )
    monkeypatch.setattr(tf, "decode_file", lambda path: FakeAudio(2))
    skipped = tmp_path / "skipped.html"
    assert run_cli(tmp_path, "export-finder", skipped, "--no-analyse") == 0
    assert embedded_data(skipped.read_text(encoding="utf-8"))["discs"][0]["tracks"][0][11] == ""  # fmt: skip
    assert "no waveform" in capsys.readouterr().err
    out = tmp_path / "finder.html"
    assert run_cli(tmp_path, "export-finder", out) == 0
    assert embedded_data(out.read_text(encoding="utf-8"))["discs"][0]["tracks"][0][11].startswith("3:")  # fmt: skip
    assert "analysing waveforms: 1 of 1" in capsys.readouterr().err


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_a_hot_cue_chip_names_its_letter(tmp_path):
    track = tf.FinderTrack(
        1, "T", "A", "", seconds=275.0, cues=((32.0, "Drop", "A"), (105.5, "", ""))
    )
    page = tmp_path / "finder.html"
    tf.write_html(
        tf.Finder(discs=[tf.FinderDisc("D", "", "audio", (track,))]), page, MADE
    )
    harness = tmp_path / "harness.js"
    harness.write_text(NODE_HARNESS.split("const out = {};")[0] + WAVE_TAIL, encoding="utf-8")  # fmt: skip
    run = subprocess.run(
        ["node", str(harness), str(page)],
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    got = json.loads(run.stdout)["T"]
    assert got["chips"] == ["1 0:32 A Drop", "2 1:45"]
    assert got["chipReadout"] == "Cue 1 · 0:32 · hot cue A · Drop"


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_each_cue_shows_its_time_and_its_bar_and_beat(tmp_path):
    track = tf.FinderTrack(
        1,
        "T",
        "A",
        "",
        seconds=275.0,
        cues=((32.0, "Drop", "A"), (105.5, "", ""), (150.0, "", "")),
        beats=(
            (17, 1, 0),
            (53, 3, 80),
            (76, 2, -5),
        ),  # the second is clearly off the beat
    )
    page = tmp_path / "finder.html"
    tf.write_html(
        tf.Finder(discs=[tf.FinderDisc("D", "", "audio", (track,))]), page, MADE
    )
    harness = tmp_path / "harness.js"
    harness.write_text(NODE_HARNESS.split("const out = {};")[0] + WAVE_TAIL, encoding="utf-8")  # fmt: skip
    run = subprocess.run(
        ["node", str(harness), str(page)],
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    got = json.loads(run.stdout)["T"]
    # number, time, the hot cue letter, then the bar and beat (≈ when more than 40 ms off the beat), then the name
    assert got["chips"] == ["1 0:32 A 17.1 Drop", "2 1:45 ≈53.3", "3 2:30 76.2"]
    assert got["chipReadout"] == "Cue 1 · 0:32 · hot cue A · bar 17.1 · Drop"
    assert got["tapNearCue"] == "Cue 2 · 1:45 · bar 53.3 (+80 ms)"


def test_rekordboxs_bar_labels_are_dropped_where_the_page_has_its_own_bar_beat():
    from dubcrate.track_finder import without_bar_labels

    cues = ((0.0, "1.1Bars", "A"), (29.0, "", "B"), (60.0, "Drop", ""))
    beats = ((1, 1, 0), (17, 1, 0), (33, 1, 0))
    assert without_bar_labels(cues, beats) == (
        (0.0, "", "A"),
        (29.0, "", "B"),
        (60.0, "Drop", ""),
    )
    # no beat grid: the label is all there is
    assert without_bar_labels(cues, ()) == cues


# The same stand-in again: the letter strip down the right edge. A watcher that never fires keeps the list
# drawn 120 at a time, as on a phone, so a jump has to draw ahead of itself.
STRIP_TAIL = r"""
const groups = () => els.results.children.filter(c => c.className === "group").map(c => c.textContent);
const out = {};
els.all.handlers.click();
const stops = () => els.index.children.map(c => c.textContent);
out.stops = stops();
out.shown = els.results.children.filter(c => c.className.startsWith("hit")).length;
out.visible = !els.index.hidden;
out.padding = els.results.style.paddingRight;
out.heightPx = els.index.style.height;
els.index.getBoundingClientRect = () => ({ top: 0, height: stops().length * 20 });
const scrolledBefore = sandbox.scrolled;
els.index.handlers.pointerdown({ clientY: stops().length * 20 - 5, pointerId: 1 });  // the last stop
out.bubble = els.bubble.textContent;
out.bubbleShown = !els.bubble.hidden;
out.groupsAfter = groups();
out.shownAfter = els.results.children.filter(c => c.className.startsWith("hit")).length;
out.scrolled = sandbox.scrolled - scrolledBefore;
els.index.handlers.pointermove({ clientY: 2, pointerId: 1 });  // back to the first
out.scrolledAgain = sandbox.scrolled - scrolledBefore;
els.index.handlers.pointerup({});
out.bubbleGone = els.bubble.hidden;
// a sort whose headings are long has no strip
els.sort.value = "genre"; els.sort.handlers.change();
out.genreHidden = els.index.hidden;
els.sort.value = "title"; els.sort.handlers.change();
out.backOn = !els.index.hidden;
// switched off: gone, and remembered
els.indexbtn.handlers.click();
out.offHidden = els.index.hidden;
out.offPadding = els.results.style.paddingRight;
out.pressed = els.indexbtn.attrs && els.indexbtn.attrs["aria-pressed"];
out.saved = stored["dubcrate-finder-index"];
// Tagged has no strip either
els.indexbtn.handlers.click();
els.tagged.handlers.click();
out.taggedHidden = els.index.hidden;
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_letter_strip_jumps_through_a_long_list(lib, tmp_path):
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    lib.replace_source(
        "folder",
        "/m",
        "M",
        [
            TrackRecord(
                ext_id=f"{c}{i}",
                path=f"/m/{c}{i}.mp3",
                title=f"{c}ta {i:02d}",
                artist="X",
            )
            for c in letters
            for i in range(12)
        ],
    )
    ids = [t.id for t in lib.tracks(sort="path")]
    for n in range(0, len(ids), 78):  # an audio disc holds at most 99 tracks
        lib.add_to_disc(lib.create_disc(f"D{n}"), ids[n : n + 78])
    page = tmp_path / "finder.html"
    tf.write_html(tf.collect(lib), page, MADE)
    head = NODE_HARNESS.split("const out = {};")[0]
    head = head.replace(
        "vm.runInNewContext(code, sandbox);",
        "sandbox.IntersectionObserver = class { observe() {} unobserve() {} };\n"
        "sandbox.Date = Date;\n"
        "vm.runInNewContext(code, sandbox);",
    )
    head = head.replace(
        "scrollY: 0,", "scrollY: 0, innerHeight: 800,"
    )  # a phone's height
    # setAttribute keeps what it was given, for the pressed state
    head = head.replace(
        "focus() {}, remove() {}, setAttribute() {} };",
        "focus() {}, remove() {}, attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } };",
    )
    harness = tmp_path / "harness.js"
    harness.write_text(head + STRIP_TAIL, encoding="utf-8")
    env = {**os.environ, "FINDER_STORAGE": str(tmp_path / "store.json")}
    run = subprocess.run(
        ["node", str(harness), str(page)],
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    got = json.loads(run.stdout)
    assert got["stops"] == list(letters)
    assert got["visible"] and got["padding"] == "30px" and got["heightPx"] == "520px"
    assert got["shown"] == 120  # drawn a screenful at a time
    # Pressing on the last stop draws ahead to Z, shows its letter and scrolls there.
    assert got["bubble"] == "Z" and got["bubbleShown"]
    assert got["groupsAfter"][-1] == "Z" and got["shownAfter"] >= 312 - 12
    assert got["scrolled"] == 1
    # Sliding back to the first stop scrolls again, to a heading already drawn.
    assert got["scrolledAgain"] == 2
    assert got["bubbleGone"]
    assert got["genreHidden"] and got["backOn"]
    assert got["offHidden"] and got["offPadding"] == ""
    assert got["pressed"] == "false" and got["saved"] == "0"
    assert got["taggedHidden"]


# -- related tracks ----------------------------------------------------------------------


def test_the_page_carries_the_exact_bpm_and_the_camelot_key(lib):
    lib.replace_source(
        "folder",
        "/m",
        "M",
        [
            TrackRecord(ext_id="a", path="/m/a.mp3", title="A", bpm=127.64, key="Am"),
            TrackRecord(ext_id="b", path="/m/b.mp3", title="B"),
        ],
    )
    lib.add_to_disc(lib.create_disc("D"), [t.id for t in lib.tracks(sort="path")])
    found = tf.collect(lib, notation="musical")
    a, b = found.discs[0].tracks
    assert (a.bpm_exact, a.camelot, a.key) == (
        127.6,
        "8A",
        "A min",
    )  # shown as musical, compared as Camelot
    assert (b.bpm_exact, b.camelot) == (0.0, "")
    data = embedded_data(tf.html_text(found, MADE))
    assert data["discs"][0]["tracks"][0][13:] == [127.6, "8A"]


RELATED_TAIL = r"""
const open = { target: { closest: () => null } };
const cards = () => els.results.children.filter(c => c.className.startsWith("hit"));
const part = (c, cls) => c.children.find(x => x.className.split(" ")[0] === cls);
const titleOf = c => part(c, "what").children[0].textContent;
const rowsOf = () => els.results.children.map(c => c.className === "group" ? "# " + c.textContent
  : titleOf(c) + (part(c, "what").children.find(x => x.className === "relnote") ? " (" +
      part(c, "what").children.find(x => x.className === "relnote").textContent + ")" : ""));
const out = {};
const relatedOf = title => {
  els.q.value = title;
  els.q.handlers.input();
  const card = cards()[0];
  card.handlers.click(open);
  const bar = card.children.find(x => x.className === "relbar");
  return { card, button: bar && bar.children[0] };
};
const view = () => ({ rows: rowsOf(), status: els.status.textContent, hidden: els.related.hidden,
                      title: els.reltitle.children.map(c => c.textContent) });
// A track with no BPM has no Related button.
out.noBpm = relatedOf("Nothing").button === undefined;
const { button } = relatedOf("Source");
button.handlers.click();
out.first = view();
els.relenergy.handlers.click();
out.energy = view();
els.relenergy.handlers.click();
els.reltol.value = "3";
els.reltol.handlers.change();
out.tight = view();
els.reltol.value = "6";
els.relhalf.handlers.click();
out.half = view();
els.relhalf.handlers.click();
// A related track opens and goes one deeper; Back goes up, and the last Back returns to the search.
els.reltol.value = "10";
els.reltol.handlers.change();
const deeper = cards().find(c => titleOf(c) === "Smooth Up");
deeper.handlers.click(open);
deeper.children.find(x => x.className === "relbar").children[0].handlers.click();
out.deeper = view();
els.relback.handlers.click();
out.up = view();
els.relback.handlers.click();
out.back = { hidden: els.related.hidden, status: els.status.textContent, q: els.q.value };
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_related_tracks_in_the_page(lib, tmp_path):
    def track(title, bpm, key, artist="X"):
        return (title, artist, {"bpm": bpm, "key": key} if bpm or key else {})

    rows = [
        track("Source", 128.0, "8A"),
        track("Same Key", 129.0, "8A"),
        track("Smooth Up", 124.0, "9A"),  # one step up the wheel
        track("Smooth Down", 132.0, "7A"),
        track("Relative", 127.0, "8B"),  # the same number's other letter
        track("Plus Two", 128.0, "10A"),  # an energy move up
        track("Diagonal", 128.0, "9B"),  # an energy move
        track("Minus Two", 128.0, "6A"),  # an energy move down
        track("Diagonal Down", 128.0, "7B"),  # an energy move down
        track("Clash", 128.0, "3A"),  # no key relation
        track("Far", 150.0, "8A"),  # key fits, BPM does not
        track("Double", 256.0, "8A"),  # only at double time
        track("Unknown Key", 128.0, ""),
        track("Nothing", None, None),
    ]
    lib.replace_source(
        "folder",
        "/m",
        "M",
        [
            TrackRecord(ext_id=str(i), path=f"/m/{i}.mp3", title=t, artist=a, **extra)
            for i, (t, a, extra) in enumerate(rows)
        ],
    )
    lib.add_to_disc(lib.create_disc("D"), [t.id for t in lib.tracks(sort="path")])
    page = tmp_path / "finder.html"
    tf.write_html(tf.collect(lib), page, MADE)
    harness = tmp_path / "harness.js"
    harness.write_text(NODE_HARNESS.split("const out = {};")[0] + RELATED_TAIL, encoding="utf-8")  # fmt: skip
    run = subprocess.run(
        ["node", str(harness), str(page)],
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    got = json.loads(run.stdout)
    assert got["noBpm"] is True
    # Same key first, then smooth (nearest BPM first); energy moves stay hidden until asked for.
    assert got["first"]["rows"] == [
        "# Same key",
        "Same Key (pitch −0.8%)",
        "# Smooth: one step or relative",
        "Relative (pitch +0.8%)",
        "Smooth Down (pitch −3.1%)",  # level with Smooth Up: the title decides
        "Smooth Up (pitch +3.1%)",
    ]
    assert got["first"]["hidden"] is False
    assert got["first"]["title"] == ["Source – X", "Related to 128 BPM · 8A"]
    assert "4 related" in got["first"]["status"]
    assert "4 more with energy moves hidden" in got["first"]["status"]
    # Unknown Key and Nothing have no BPM or no key to compare.
    assert "2 can't be compared" in got["first"]["status"]
    assert got["energy"]["rows"][-6:] == [
        "# Energy up: +2 or diagonal",
        "Diagonal (same BPM)",
        "Plus Two (same BPM)",
        "# Energy down: −2 or diagonal",
        "Diagonal Down (same BPM)",
        "Minus Two (same BPM)",
    ]
    assert "hidden" not in got["energy"]["status"]
    # A tighter BPM window drops the 3.1% ones.
    assert got["tight"]["rows"] == ["# Same key", "Same Key (pitch −0.8%)", "# Smooth: one step or relative", "Relative (pitch +0.8%)"]  # fmt: skip
    # Half and double time bring the 256 BPM track in.
    assert "Double (double time, same BPM)" in got["half"]["rows"]
    # Chaining: Smooth Up (124, 9A) at 10% sees 8A, 9B, 10A, 8B ...; Back climbs out, then returns to the search.
    assert got["deeper"]["title"][0] == "Smooth Up – X"
    assert "Source (pitch −3.2%)" in got["deeper"]["rows"]
    assert got["up"]["title"][0] == "Source – X"
    assert got["back"]["hidden"] is True and got["back"]["q"] == "Source"
    assert "related" not in got["back"]["status"]


RELATED_GENRE_TAIL = r"""
const open = { target: { closest: () => null } };
const cards = () => els.results.children.filter(c => c.className.startsWith("hit"));
const part = (c, cls) => c.children.find(x => x.className.split(" ")[0] === cls);
const titleOf = c => part(c, "what").children[0].textContent;
const noteOf = c => { const n = part(c, "what").children.find(x => x.className === "relnote"); return n ? n.textContent : ""; };
const view = () => ({ rows: els.results.children.filter(c => c.className.startsWith("hit")).map(c => titleOf(c) + "|" + noteOf(c)),
                      status: els.status.textContent });
const out = { start: { tol: els.reltol.value } };
els.q.value = "Source";
els.q.handlers.input();
cards()[0].handlers.click(open);
cards()[0].children.find(x => x.className === "relbar").children[0].handlers.click();
for (const step of JSON.parse(process.argv[3])) {
  if (step === "tol10") { els.reltol.value = "10"; els.reltol.handlers.change(); }
  else if (step === "genre") els.relgenre.handlers.click();
  else if (step === "half") els.relhalf.handlers.click();
  else if (step === "energy") els.relenergy.handlers.click();
  else if (step.startsWith("order:")) { els.relsort.value = step.slice(6); els.relsort.handlers.change(); }
  out[step] = view();
}
out.final = view();
console.log(JSON.stringify(out));
"""


def related_genre_page(lib, tmp_path, tail=RELATED_GENRE_TAIL):
    rows = [
        ("Source", 128.0, "8A", "Deep House"),
        ("Tech", 128.0, "8A", "tech  HOUSE"),  # shares "house"
        ("Techno", 128.0, "8A", "Techno"),
        ("No Genre", 128.0, "8A", ""),
        ("Fast", 136.5, "8A", "Deep House"),  # 6.6% faster: needs the +-10 range
        ("Music Only", 128.0, "8A", "The Music"),  # only words that don't count
    ]
    lib.replace_source(
        "folder",
        "/m",
        "M",
        [
            TrackRecord(
                ext_id=str(i), path=f"/m/{i}.mp3", title=t, bpm=b, key=k, genre=g
            )
            for i, (t, b, k, g) in enumerate(rows)
        ],
    )
    lib.add_to_disc(lib.create_disc("D"), [t.id for t in lib.tracks(sort="path")])
    page = tmp_path / "finder.html"
    tf.write_html(tf.collect(lib), page, MADE)
    harness = tmp_path / "harness.js"
    harness.write_text(NODE_HARNESS.split("const out = {};")[0] + tail, encoding="utf-8")  # fmt: skip
    return page, harness


def run_related_genre(page, harness, steps, storage=None):
    env = {**os.environ, **({"FINDER_STORAGE": str(storage)} if storage else {})}
    run = subprocess.run(
        ["node", str(harness), str(page), json.dumps(steps)],
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    return json.loads(run.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_related_says_what_to_do_with_the_pitch_and_narrows_by_genre(lib, tmp_path):
    page, harness = related_genre_page(lib, tmp_path)
    got = run_related_genre(page, harness, ["tol10", "genre", "half"])
    # At 6% the 6.6% track is out; at 10% it is in, and the note says which pitch range it needs.
    assert got["start"]["tol"] == "6"
    assert got["tol10"]["rows"] == [
        "Music Only|same BPM",  # level on BPM: the title decides
        "No Genre|same BPM",
        "Tech|same BPM",
        "Techno|same BPM",
        "Fast|pitch −6.6% · needs ±10 range",
    ]
    # Same genre: a shared word counts ("Deep House" / "tech  HOUSE"); no genre, or only filler words, is left out.
    assert got["genre"]["rows"] == [
        "Tech|same BPM",
        "Fast|pitch −6.6% · needs ±10 range",
    ]
    # "No Genre" and "Music Only" (only filler words) can't be told apart from a track with no genre.
    assert "2 left out (no genre)" in got["genre"]["status"]


def test_the_page_script_keeps_the_related_choices():
    assert "dubcrate-finder-related" in tf.PAGE


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_related_choices_are_kept_for_the_next_visit(lib, tmp_path):
    page, harness = related_genre_page(lib, tmp_path)
    store = tmp_path / "phone.json"
    run_related_genre(
        page,
        harness,
        ["tol10", "genre", "half", "energy", "order:title"],
        storage=store,
    )
    got = run_related_genre(page, harness, [], storage=store)
    assert got["start"]["tol"] == "10"
    # Same genre and the title order are still on: only tracks sharing a word, by title.
    assert [r.split("|")[0] for r in got["final"]["rows"]] == ["Fast", "Tech"]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_related_can_be_ordered_by_title_or_disc_inside_a_heading(lib, tmp_path):
    page, harness = related_genre_page(lib, tmp_path)
    got = run_related_genre(page, harness, ["tol10", "order:title", "order:disc"])
    titles = lambda step: [r.split("|")[0] for r in got[step]["rows"]]
    assert titles("tol10") == [
        "Music Only",
        "No Genre",
        "Tech",
        "Techno",
        "Fast",
    ]  # nearest BPM, then title
    assert titles("order:title") == ["Fast", "Music Only", "No Genre", "Tech", "Techno"]
    assert titles("order:disc") == ["Tech", "Techno", "No Genre", "Fast", "Music Only"]


RELATED_LAST_TAIL = r"""
const open = { target: { closest: () => null } };
const cards = () => els.results.children.filter(c => c.className.startsWith("hit"));
const part = (c, cls) => c.children.find(x => x.className.split(" ")[0] === cls);
const titleOf = c => part(c, "what").children[0].textContent;
const tag = title => { const c = cards().find(x => titleOf(x) === title); c.children[c.children.length - 1].handlers.click(); };
const find = title => { els.q.value = title; els.q.handlers.input(); };
const out = {};
find("Smooth Up");
tag("Smooth Up");
out.beforeTagged = els.rellast.hidden;           // not in a plain search
els.tagged.handlers.click();
out.inTagged = els.rellast.hidden;
els.rellast.handlers.click();
out.related = { title: els.reltitle.children[0].textContent, hidden: els.rellast.hidden, rows: cards().map(titleOf) };
tag("Same Key");                                  // tagged from the related list: it is now the last
out.nowLast = els.rellast.hidden;
els.rellast.handlers.click();
out.chained = els.reltitle.children[0].textContent;
els.relback.handlers.click();
els.relback.handlers.click();
out.backInTagged = { relHidden: els.related.hidden, rows: cards().map(titleOf), lastShown: !els.rellast.hidden };
find("Nothing");
tag("Nothing");
els.tagged.handlers.click();                      // Nothing has no BPM or key: no button
out.end = els.rellast.hidden;
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_related_to_the_last_tagged_track(lib, tmp_path):
    rows = [
        ("Source", 128.0, "8A"),
        ("Same Key", 129.0, "8A"),
        ("Smooth Up", 124.0, "9A"),
        ("Nothing", None, None),
    ]
    lib.replace_source(
        "folder",
        "/m",
        "M",
        [
            TrackRecord(
                ext_id=str(i),
                path=f"/m/{i}.mp3",
                title=t,
                **({"bpm": b, "key": k} if b else {}),
            )
            for i, (t, b, k) in enumerate(rows)
        ],
    )
    lib.add_to_disc(lib.create_disc("D"), [t.id for t in lib.tracks(sort="path")])
    page = tmp_path / "finder.html"
    tf.write_html(tf.collect(lib), page, MADE)
    harness = tmp_path / "harness.js"
    harness.write_text(NODE_HARNESS.split("const out = {};")[0] + RELATED_LAST_TAIL, encoding="utf-8")  # fmt: skip
    run = subprocess.run(["node", str(harness), str(page)], check=True, capture_output=True, encoding="utf-8", errors="replace")  # fmt: skip
    got = json.loads(run.stdout)
    assert got["beforeTagged"] is True and got["inTagged"] is False
    # Smooth Up (124, 9A) at 6%: Source (128, 8A) and Same Key (129, 8A) are each a step away.
    assert got["related"]["title"] == "Smooth Up" and got["related"]["hidden"] is True
    assert sorted(got["related"]["rows"]) == ["Same Key", "Source"]
    assert got["nowLast"] is False  # the new last track is another one
    assert got["chained"] == "Same Key"
    # Two Backs: up to Smooth Up's list, then out to Tagged, where the button is back.
    assert got["backInTagged"]["relHidden"] is True
    assert got["backInTagged"]["rows"] == ["Smooth Up", "Same Key"]
    assert got["backInTagged"]["lastShown"] is True
    assert got["end"] is True


def test_the_beat_grid_is_moved_into_audio_time_with_the_cues(monkeypatch):
    # rekordbox has beat 1 at 1.050 s on a file it times 50 ms late; the cue is already at 1.000 s in audio time
    monkeypatch.setattr(tf, "lead_ms", lambda _path: 50)
    grid = (np.array([1.05, 1.55, 2.05]), np.array([1, 2, 3], dtype=np.int8))
    moved = tf.in_audio_time(grid, "/m/a.mp3")
    assert [round(float(t), 3) for t in moved[0]] == [1.0, 1.5, 2.0]
    [(bar, beat, ms)] = tf.cue_beats([(1.0, "", "")], moved)
    assert (bar, beat, ms) == (1, 1, 0)  # on the beat, not "50 ms before" it
    assert tf.in_audio_time(None, "/m/a.mp3") is None
    monkeypatch.setattr(tf, "lead_ms", lambda _path: 0)
    assert tf.in_audio_time(grid, "/m/a.mp3") is grid
