"""The track finder: which disc is this track on (read-only, one HTML file)."""

import json
import os
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from dubcrate import track_finder as tf
from dubcrate.library import Library, TrackRecord
from dubcrate.track_finder import FinderError

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
        [1, "Ünïcode ✓", "Bjørk", "", [], "", "", "", "", ""]
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
  return { tag, className: "", textContent: "", children: [], handlers: {},
    style: { setProperty() {} },
    append(...c) { this.children.push(...c); },
    replaceChildren(...c) { this.children = c; },
    addEventListener(name, fn) { this.handlers[name] = fn; },
    focus() {}, remove() {}, setAttribute() {} };
}
const els = { data: { textContent: data }, q: node("input"), status: node("div"),
              results: node("main"), all: node("button"), tagged: node("button"), sortrow: node("span"),
              sort: node("select"), dir: node("button") };
els.q.value = "";
const document = { getElementById: id => els[id], createElement: node };
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
sandbox.window = { scrollTo() { sandbox.scrolled++; } };
for (const name of ["top", "parent", "self", "location", "name", "status", "length"])
  Object.defineProperty(sandbox, name, { value: undefined, writable: false, configurable: false });
vm.runInNewContext(code, sandbox);
const out = {};
for (const query of JSON.parse(process.argv[3])) {
  els.q.value = query;
  els.q.handlers.input();
  out[query] = { status: els.status.textContent,
    hits: els.results.children.map(hit => {
      const [what, where] = hit.children;
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
els.sortrow.hidden = true;  // as the page's HTML starts it
function list() {
  return { status: els.status.textContent, sortHidden: els.sortrow.hidden,
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

    assert got[0]["rows"] == [] and got[0]["sortHidden"] is True
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


# The same stand-in again: tagging tracks to play later.
TAG_TAIL = r"""
const mode = process.argv[3];
const cards = () => els.results.children.filter(c => c.className.startsWith("hit"));
const titleOf = c => c.children[0].children[0].textContent;
const starOf = t => cards().find(c => titleOf(c) === t).children[2];
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
    # A viewer that cannot keep storage says so, rather than pretending.
    storage.unlink()
    blocked = run("tag", kept=False)
    assert blocked["titles"] == ["Nothing", "Alpha"]
    assert "NOT kept" in blocked["status"]


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
