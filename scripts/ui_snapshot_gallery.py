"""Builds the HTML gallery for the UI snapshot harness (scripts/ui_snapshots.py): one self-contained
index.html next to the images folder. Open it in any browser; no server needed.

For each screen it shows every variant that was rendered (theme, window size), the message boxes and
menus the screen raised (as pictures of their own), the layout problems the checks found (outlined on the
image on request), the screen sizes the window is too big for, every piece of text on it (tooltips included,
searchable) and a place for notes. Notes and marks are kept in the browser's localStorage keyed by
screen, so they survive re-rendering the images after a fix; "Export notes" turns them into Markdown to
paste to whoever is fixing things.

Keyboard: j / k next and previous screen, g / n / q mark the screen under review as looks fine / needs work /
question, c jumps to its notes box, o toggles the outlines, / searches the text, Esc leaves a box.
"""

import json
import os

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Dubcrate UI review</title>
<style>
  :root { --thumb: 420px; --bg: #e8eaee; --card: #fff; --ink: #1d2330; --muted: #5d6677;
          --line: #cfd4dc; --accent: #2a62d9; --bad: #d4351c; --warn: #b26a00; --good: #1b7f43; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; color: var(--ink);
         background: var(--bg); }
  header.top { position: sticky; top: 0; z-index: 5; background: #1d2330; color: #fff; padding: 8px 16px;
               display: flex; flex-wrap: wrap; gap: 8px 18px; align-items: center; }
  header.top h1 { font-size: 16px; margin: 0; font-weight: 600; }
  header.top label { font-size: 12px; color: #c4cada; display: flex; gap: 6px; align-items: center; }
  header.top select, header.top button, header.top input[type=search] { font: inherit; font-size: 12px; }
  header.top input[type=search] { width: 170px; }
  header.top .sp { flex: 1; }
  #layout { display: flex; align-items: flex-start; }
  nav { position: sticky; top: 46px; width: 250px; flex: none; height: calc(100vh - 46px); overflow: auto;
        padding: 12px; font-size: 13px; border-right: 1px solid var(--line); background: #f3f4f7; }
  nav h3 { margin: 12px 0 4px; font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); }
  nav a { display: flex; gap: 6px; align-items: center; padding: 2px 4px; color: var(--ink); text-decoration: none;
          border-radius: 4px; }
  nav a:hover, nav a.cur { background: #dbe2f0; }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: #c3c9d4; flex: none; }
  .dot.good { background: var(--good); } .dot.bad { background: var(--bad); } .dot.question { background: var(--warn); }
  nav .n { margin-left: auto; font-size: 11px; color: var(--bad); }
  main { flex: 1; min-width: 0; padding: 16px; }
  #summary { margin-bottom: 14px; color: var(--muted); }
  section.scene { background: var(--card); border: 1px solid var(--line); border-radius: 8px; margin-bottom: 18px;
                  padding: 14px 16px; scroll-margin-top: 58px; }
  section.scene.cur { border-color: var(--accent); box-shadow: 0 0 0 2px #2a62d933; }
  section.scene > h2 { margin: 0; font-size: 17px; display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }
  .sid { font: 12px ui-monospace, Menlo, Consolas, monospace; color: var(--muted); font-weight: 400; }
  .badge { font-size: 11px; padding: 1px 7px; border-radius: 10px; background: #e6e9f0; color: var(--ink); font-weight: 500; }
  .badge.bad { background: #fde4df; color: var(--bad); } .badge.warn { background: #fdefd5; color: var(--warn); }
  .row { display: flex; gap: 16px; flex-wrap: wrap; margin-top: 8px; align-items: flex-start; }
  .info { flex: 1 1 340px; min-width: 280px; font-size: 13px; }
  .info p { margin: 4px 0; }
  .info details { margin: 4px 0; }
  .info summary { cursor: pointer; }
  .info ul { margin: 4px 0 4px 18px; padding: 0; }
  .info code { font: 12px ui-monospace, Menlo, Consolas, monospace; background: #f0f2f6; padding: 0 3px; border-radius: 3px; }
  .about { color: var(--muted); }
  table.texts { border-collapse: collapse; font-size: 12px; margin-top: 4px; width: 100%; }
  table.texts td { border-top: 1px solid #edf0f4; padding: 2px 8px 2px 0; vertical-align: top; }
  table.texts td.k { color: var(--muted); white-space: nowrap; width: 1%; }
  table.texts td.tip { color: #5a4a00; }
  table.texts mark { background: #ffe58a; }
  .notes { flex: 1 1 320px; min-width: 260px; }
  .notes select, .notes textarea { width: 100%; font: inherit; font-size: 13px; }
  .notes textarea { height: 76px; margin-top: 4px; padding: 6px; resize: vertical; }
  .err { background: #fde4df; border: 1px solid #f1b3a8; padding: 8px; font: 12px ui-monospace, Menlo, monospace;
         white-space: pre-wrap; border-radius: 4px; margin-top: 8px; }
  .grid { display: flex; flex-wrap: wrap; gap: 14px; margin-top: 12px; }
  figure { margin: 0; width: min(var(--thumb), 100%); }
  figure.extra { width: min(calc(var(--thumb) * .8), 100%); }
  .frame { position: relative; border: 1px solid var(--line); border-radius: 4px; overflow: hidden; cursor: zoom-in;
           background: #888; line-height: 0; }
  .frame img { width: 100%; height: auto; display: block; }
  .box { position: absolute; border: 2px solid #ff2d55; background: rgba(255, 45, 85, .12); display: none; pointer-events: none; }
  body.outlines .box { display: block; }
  figcaption { font-size: 12px; color: var(--muted); margin-top: 3px; line-height: 1.35; }
  figcaption .bad { color: var(--bad); font-weight: 600; } figcaption .warn { color: var(--warn); }
  figcaption .x { color: var(--accent); font-weight: 600; }
  #lightbox { position: fixed; inset: 0; background: rgba(10, 12, 18, .92); z-index: 20; display: none; flex-direction: column; }
  #lightbox.on { display: flex; }
  #lightbox .bar { color: #e6e9f2; padding: 8px 14px; font-size: 13px; display: flex; gap: 14px; align-items: center; }
  #lightbox .bar button { font: inherit; }
  #lightbox .scroll { flex: 1; overflow: auto; padding: 0 14px 14px; }
  #lightbox .frame { display: inline-block; cursor: default; }
  #lightbox .frame img { width: auto; max-width: none; }
  #lightbox .issues { color: #ffd0d8; font-size: 12px; padding: 0 14px 8px; max-height: 18vh; overflow: auto; }
  .fit td, .fit th { padding: 1px 10px 1px 0; text-align: left; font-weight: 400; font-size: 12px; }
  .fit .no { color: var(--bad); }
  kbd { font: 11px ui-monospace, Menlo, monospace; background: #eef0f4; border: 1px solid #cfd4dc; border-radius: 3px; padding: 0 4px; }
</style>
</head>
<body>
<header class="top">
  <h1>Dubcrate UI review <span id="round" style="font-weight:400;font-size:12px;color:#c4cada"></span></h1>
  <label>Theme <select id="f-theme"></select></label>
  <label>Show <select id="f-show">
    <option value="all">all screens</option><option value="flags">only with layout flags</option>
    <option value="marked">only marked in my notes</option><option value="bad">only needs work</option>
    <option value="open">not yet reviewed</option></select></label>
  <label><input type="search" id="f-search" placeholder="search the text (/)"></label>
  <label><input type="checkbox" id="f-outline"> outline flagged widgets</label>
  <label>Image size <input type="range" id="f-thumb" min="160" max="1000" value="420"></label>
  <span class="sp"></span>
  <button id="b-export">Export notes (Markdown)</button>
  <button id="b-clear">Clear notes</button>
</header>
<div id="layout"><nav id="nav"></nav><main><div id="summary"></div><div id="scenes"></div></main></div>
<div id="lightbox"><div class="bar"><button id="lb-prev">&larr;</button><button id="lb-next">&rarr;</button>
  <span id="lb-title"></span><span style="flex:1"></span><button id="lb-close">Close (Esc)</button></div>
  <div class="issues" id="lb-issues"></div><div class="scroll"><div class="frame" id="lb-frame"></div></div></div>
<script id="data" type="application/json">/*DATA*/</script>
<script>
const DATA = JSON.parse(document.getElementById("data").textContent);
// round 1 keeps the original key; every later round has its own, so a second pass starts clean
// and the first is left untouched
const ROUND = String(DATA.round || "1");
const STORE = "dubcrate-ui-notes:v1" + (ROUND === "1" ? "" : ":round-" + ROUND);
let notes = {};
try { notes = JSON.parse(localStorage.getItem(STORE) || "{}"); } catch (e) { notes = {}; }
const save = () => localStorage.setItem(STORE, JSON.stringify(notes));
const STATUS = [["", "not reviewed"], ["good", "looks fine"], ["bad", "needs work"], ["question", "question / unsure"]];

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") el.className = v; else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v); else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid);
  return el;
}
const el = id => document.getElementById(id);
const fmt = a => a[0] + "×" + a[1];

function uniqueIssues(scene) {
  const map = new Map();
  for (const v of scene.variants) for (const i of v.issues) {
    const key = i.kind + "|" + i.path;
    if (!map.has(key)) map.set(key, {...i, count: 0});
    map.get(key).count++;
  }
  return [...map.values()];
}

function fitRows(scene) {
  if (!scene.min_size) return null;
  const [w, h2] = scene.min_size;
  return DATA.screens.map(s => ({ label: s.label, ok: w <= s.w && h2 <= s.usable_h, w, h: h2, usable: s.usable_h }));
}

const activeFilters = () => ({ theme: el("f-theme").value, show: el("f-show").value, q: el("f-search").value.trim().toLowerCase() });
const variantVisible = (v, f) => f.theme === "all" || v.theme === f.theme;

function textMatches(scene, q) {
  return scene.texts.some(t => (t.text + " " + t.tip).toLowerCase().includes(q)) || scene.title.toLowerCase().includes(q);
}

function sceneVisible(scene, f) {
  const n = notes[scene.id] || {};
  if (f.show === "flags" && !(scene.error || scene.variants.some(v => variantVisible(v, f) && v.issues.length))) return false;
  if (f.show === "marked" && !(n.status || n.note)) return false;
  if (f.show === "bad" && n.status !== "bad") return false;
  if (f.show === "open" && n.status) return false;
  if (f.q && !textMatches(scene, f.q)) return false;
  return true;
}

function highlight(text, q) {
  if (!q) return text;
  const i = text.toLowerCase().indexOf(q);
  if (i < 0) return text;
  const m = h("mark", { text: text.slice(i, i + q.length) });
  return [text.slice(0, i), m, text.slice(i + q.length)];
}

function textsTable(scene, q) {
  return h("table", { class: "texts" }, scene.texts.map(t => h("tr", {},
    h("td", { class: "k", text: t.kind }),
    h("td", {}, highlight(t.text, q)),
    h("td", { class: "tip" }, t.tip ? ["tooltip: ", highlight(t.tip, q)] : ""))));
}

let visibleIds = [];
function render() {
  const f = activeFilters();
  const root = el("scenes"); root.replaceChildren();
  const nav = el("nav"); nav.replaceChildren();
  visibleIds = [];
  let shown = 0, images = 0, flagged = 0, tooBig = 0, reviewed = 0, bad = 0;
  let area = null;
  for (const scene of DATA.scenes) {
    const n0 = notes[scene.id] || {};
    if (n0.status) reviewed++; if (n0.status === "bad") bad++;
    if (!sceneVisible(scene, f)) continue;
    shown++; visibleIds.push(scene.id);
    const n = notes[scene.id] || {};
    const vs = scene.variants.filter(v => variantVisible(v, f));
    images += vs.filter(v => v.file).length; flagged += vs.filter(v => v.issues.length).length;
    const uniq = uniqueIssues(scene);
    if (scene.area !== area) { area = scene.area; nav.append(h("h3", { text: area })); }
    nav.append(h("a", { href: "#s-" + scene.id, "data-id": scene.id }, h("span", { class: "dot " + (n.status || "") }), scene.title,
      uniq.length ? h("span", { class: "n", text: uniq.length }) : null));

    const fit = fitRows(scene);
    const misfit = fit ? fit.filter(r => !r.ok) : [];
    if (misfit.length) tooBig++;
    const sec = h("section", { class: "scene", id: "s-" + scene.id });
    sec.append(h("h2", {}, scene.title, h("span", { class: "sid", text: scene.id + " · " + scene.kind }),
      uniq.length ? h("span", { class: "badge bad", text: uniq.length + " layout flag" + (uniq.length > 1 ? "s" : "") }) : null,
      misfit.length ? h("span", { class: "badge warn", text: "too big for " + misfit.length + " screen size" + (misfit.length > 1 ? "s" : "") }) : null,
      scene.error ? h("span", { class: "badge bad", text: "failed to render" }) : null));
    if (scene.about) sec.append(h("div", { class: "about", text: scene.about }));

    const info = h("div", { class: "info" });
    if (scene.min_size) {
      info.append(h("p", { text: (scene.fixed ? "Fixed size " : "Smallest size ") + fmt(scene.min_size) + "." }));
      if (fit) info.append(h("details", {}, h("summary", { text: misfit.length ? "Screen fit: does not fit " + misfit.map(r => r.label).join(", ") : "Screen fit: fits all tested screens" }),
        h("table", { class: "fit" }, fit.map(r => h("tr", {}, h("th", { text: r.label }),
          h("td", { class: r.ok ? "" : "no", text: r.ok ? "fits" : "needs " + r.w + "×" + r.h + ", screen gives about " + r.usable + " tall" }))))));
    }
    if (uniq.length) {
      info.append(h("details", { open: "" }, h("summary", { text: "Layout flags (" + uniq.length + ")" }),
        h("ul", {}, uniq.map(i => h("li", {}, h("b", { text: i.kind }), " ", h("code", { text: i.path }), " — " + i.detail,
          " (" + i.count + " of " + scene.variants.length + " renders)")))));
    }
    if (scene.texts.length) {
      const d = h("details", f.q ? { open: "" } : {}, h("summary", { text: "All text on this screen (" + scene.texts.length + "), tooltips included" }), textsTable(scene, f.q));
      info.append(d);
    }
    if (scene.skipped.length) {
      info.append(h("details", {}, h("summary", { text: "Not shown as a picture (" + scene.skipped.length + ")" }),
        h("ul", {}, scene.skipped.map(p => h("li", { text: p })))));
    }
    sec.append(h("div", { class: "row" }, info, notesBox(scene, n)));
    if (scene.error) sec.append(h("div", { class: "err", text: scene.error }));

    const grid = h("div", { class: "grid" });
    for (const v of vs) {
      if (v.file) grid.append(figure(scene, v));
      for (const x of v.extras || []) grid.append(extraFigure(scene, v, x));
    }
    sec.append(grid);
    root.append(sec);
  }
  el("summary").textContent = shown + " screens shown, " + images + " main images, " + flagged + " with layout flags, " + tooBig +
    " screens too big for some tested screen size. You have reviewed " + reviewed + " of " + DATA.scenes.length + " (" + bad + " need work). Rendered " + DATA.generated +
    " (offscreen: a layout check, not real-hardware ground truth).";
  markCurrent();
}

function notesBox(scene, n) {
  const sel = h("select", { id: "st-" + scene.id, onchange: e => { (notes[scene.id] ||= {}).status = e.target.value; save(); refreshNavDots(); } },
    STATUS.map(([v, label]) => h("option", { value: v, text: label })));
  sel.value = n.status || "";
  const ta = h("textarea", { id: "ta-" + scene.id, placeholder: "Notes on this screen (layout, wording, anything)…", oninput: e => { (notes[scene.id] ||= {}).note = e.target.value; save(); } });
  ta.value = n.note || "";
  return h("div", { class: "notes" }, sel, ta);
}

function refreshNavDots() {
  el("nav").querySelectorAll("a").forEach(a => {
    a.querySelector(".dot").className = "dot " + ((notes[a.dataset.id] || {}).status || "");
  });
}

function boxes(v) {
  return v.issues.filter(i => i.rect.length).map(i => {
    const [x, y, w, h2] = i.rect, [W, H] = v.actual;
    const b = h("div", { class: "box", title: i.kind + ": " + i.detail });
    b.style.cssText = `left:${x / W * 100}%;top:${y / H * 100}%;width:${w / W * 100}%;height:${h2 / H * 100}%`;
    return b;
  });
}

function figure(scene, v) {
  const frame = h("div", { class: "frame", onclick: () => openLightbox(scene, v.file, [v.theme, v.size, fmt(v.actual)].join(" · "), v) },
    h("img", { src: v.file, loading: "lazy", alt: scene.title }), boxes(v));
  const cap = h("figcaption", {}, [v.theme, v.size, fmt(v.actual)].join(" · "));
  if (v.grew) cap.append(h("div", { class: "warn", text: "asked for " + fmt(v.requested) + " but the window needs " + fmt(v.actual) }));
  if (v.issues.length) cap.append(h("div", { class: "bad", text: v.issues.length + " layout flag" + (v.issues.length > 1 ? "s" : "") }));
  return h("figure", {}, frame, cap);
}

function extraFigure(scene, v, x) {
  const frame = h("div", { class: "frame", onclick: () => openLightbox(scene, x.file, x.label + " · " + v.theme, null) },
    h("img", { src: x.file, loading: "lazy", alt: x.label }));
  return h("figure", { class: "extra" }, frame, h("figcaption", {}, h("span", { class: "x", text: x.label }), " · " + v.theme));
}

let lb = { scene: null, list: [], i: 0 };
function openLightbox(scene, file, caption, v) {
  const f = activeFilters();
  lb.scene = scene;
  lb.list = [];
  for (const x of scene.variants.filter(y => variantVisible(y, f))) {
    if (x.file) lb.list.push({ file: x.file, caption: [x.theme, x.size, fmt(x.actual)].join(" · "), v: x });
    for (const e of x.extras || []) lb.list.push({ file: e.file, caption: e.label + " · " + x.theme, v: null });
  }
  lb.i = Math.max(0, lb.list.findIndex(e => e.file === file));
  showLightbox(); el("lightbox").classList.add("on");
}
function showLightbox() {
  const e = lb.list[lb.i];
  el("lb-title").textContent = lb.scene.title + " — " + e.caption + "  (" + (lb.i + 1) + "/" + lb.list.length + ")";
  el("lb-frame").replaceChildren(h("img", { src: e.file }), e.v ? boxes(e.v) : []);
  el("lb-issues").replaceChildren(...(e.v ? e.v.issues : []).map(i => h("div", { text: i.kind + ": " + i.path + " — " + i.detail })));
}
function step(d) { lb.i = (lb.i + d + lb.list.length) % lb.list.length; showLightbox(); }
el("lb-prev").onclick = () => step(-1); el("lb-next").onclick = () => step(1);
el("lb-close").onclick = () => el("lightbox").classList.remove("on");

// keyboard review: j/k scenes, g/n/q mark, c notes, o outlines, / search
let current = null;
function markCurrent() {
  if (!visibleIds.includes(current)) current = visibleIds[0] || null;
  document.querySelectorAll("section.scene").forEach(s => s.classList.toggle("cur", s.id === "s-" + current));
  document.querySelectorAll("nav a").forEach(a => a.classList.toggle("cur", a.dataset.id === current));
}
function go(d) {
  const i = visibleIds.indexOf(current);
  current = visibleIds[Math.max(0, Math.min(visibleIds.length - 1, i + d))];
  markCurrent();
  const s = el("s-" + current); if (s) s.scrollIntoView({ block: "start" });
}
function mark(status) {
  if (!current) return;
  (notes[current] ||= {}).status = status; save();
  const sel = el("st-" + current); if (sel) sel.value = status;
  refreshNavDots();
}
document.addEventListener("keydown", e => {
  const typing = ["TEXTAREA", "INPUT", "SELECT"].includes(document.activeElement.tagName);
  if (e.key === "Escape") { el("lightbox").classList.remove("on"); if (typing) document.activeElement.blur(); return; }
  if (el("lightbox").classList.contains("on")) {
    if (e.key === "ArrowLeft") step(-1); if (e.key === "ArrowRight") step(1); return;
  }
  if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "j") go(1); else if (e.key === "k") go(-1);
  else if (e.key === "g") mark("good"); else if (e.key === "n") mark("bad"); else if (e.key === "q") mark("question");
  else if (e.key === "o") { const b = el("f-outline"); b.checked = !b.checked; b.onchange({ target: b }); }
  else if (e.key === "c") { const t = el("ta-" + current); if (t) { e.preventDefault(); t.focus(); } }
  else if (e.key === "/") { e.preventDefault(); el("f-search").focus(); }
});

function exportNotes() {
  const lines = ["# Dubcrate UI review notes (round " + ROUND + ")", "", "Rendered " + DATA.generated, ""];
  for (const scene of DATA.scenes) {
    const n = notes[scene.id] || {};
    const flags = uniqueIssues(scene);
    if (!n.status && !n.note) continue;
    lines.push("## " + scene.title + " (`" + scene.id + "`)");
    lines.push("Status: " + (STATUS.find(s => s[0] === (n.status || ""))[1]));
    if (n.note) lines.push("", n.note);
    if (flags.length) lines.push("", "Layout flags the checker found:", ...flags.map(i => "- " + i.kind + ": " + i.path + " — " + i.detail));
    lines.push("");
  }
  return lines.join("\n");
}
el("b-export").onclick = () => {
  const text = exportNotes();
  const a = h("a", { href: URL.createObjectURL(new Blob([text], { type: "text/markdown" })), download: "ui-review-notes.md" });
  document.body.append(a); a.click(); a.remove();
  if (navigator.clipboard) navigator.clipboard.writeText(text).catch(() => {});
};
el("b-clear").onclick = () => { if (confirm("Delete all your notes and marks in this browser?")) { notes = {}; save(); render(); } };

el("round").textContent = "· review round " + ROUND;
const themeSelect = el("f-theme");
themeSelect.append(h("option", { value: "all", text: "all themes" }));
for (const t of DATA.themes) themeSelect.append(h("option", { value: t, text: t }));
themeSelect.value = DATA.themes[0];  // start on one theme, so the page is not a wall of near-identical images
for (const id of ["f-theme", "f-show"]) el(id).onchange = render;
el("f-search").oninput = render;
el("f-outline").onchange = e => document.body.classList.toggle("outlines", e.target.checked);
el("f-thumb").oninput = e => document.documentElement.style.setProperty("--thumb", e.target.value + "px");
render();
</script>
</body>
</html>
"""


def write_gallery(manifest, out_dir):
    data = json.dumps(manifest).replace("</", "<\\/")
    path = os.path.join(out_dir, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(PAGE.replace("/*DATA*/", data))
    return path
