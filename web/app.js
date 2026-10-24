// DCC Mapgen on the web.
//
// The generator is the same dcc_mapgen.py the desktop app runs, executed in the
// browser by Pyodide; web_api.py is the thin layer this page talks to. Nothing is
// generated on a server, so a seed gives the same floor here as in the app.

import { marked } from "https://cdn.jsdelivr.net/npm/marked@18.1.1/lib/marked.esm.js";
import DOMPurify from "https://cdn.jsdelivr.net/npm/dompurify@3.4.17/dist/purify.es.mjs";
import Panzoom from "https://cdn.jsdelivr.net/npm/@panzoom/panzoom@4.6.2/dist/panzoom.es.js";

const $ = (id) => document.getElementById(id);
let api = null;          // web_api, once Python is up
let meta = null;         // floors, sizes and loot banks, from web_api.meta()
let result = null;       // the last floor generated
let mapUrl = null;       // object URL of the map on show
let panzoom = null;

// -- small helpers ------------------------------------------------------------

// The page's status line, or the editor's own, beside what it is about.
function setStatus(text, error = false, where = "status") {
  const s = $(where);
  s.textContent = text;
  s.classList.toggle("error", error);
}

function call(fn, payload) {
  return JSON.parse(api[fn](typeof payload === "string" ? payload : JSON.stringify(payload)));
}

function remember(key, value) {
  try { localStorage.setItem(`dccmapgen.${key}`, value); } catch { /* private mode */ }
}

function recall(key) {
  try { return localStorage.getItem(`dccmapgen.${key}`); } catch { return null; }
}

function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function markdown(md) {
  const html = DOMPurify.sanitize(marked.parse(md));
  const box = document.createElement("div");
  box.innerHTML = html;
  for (const t of box.querySelectorAll("table")) {
    // Every table's first column says what its row is about (a mob, a stat, a
    // row of the floor's grid), so it is the row's header: a screen reader
    // moving down a column names the row as well as the column.
    for (const th of t.querySelectorAll("thead th")) th.scope = "col";
    for (const tr of t.querySelectorAll("tbody tr")) {
      const td = tr.firstElementChild;
      if (!td || td.tagName !== "TD") continue;
      const th = document.createElement("th");
      for (const a of td.attributes) th.setAttribute(a.name, a.value);   // alignment
      th.scope = "row";
      th.append(...td.childNodes);
      td.replaceWith(th);
    }
    // Wide tables scroll on their own rather than pushing the page sideways.
    const wrap = Object.assign(document.createElement("div"), { className: "table-wrap" });
    t.replaceWith(wrap);
    wrap.append(t);
  }
  return box;
}

// A table too wide for the screen scrolls in a box of its own, and a keyboard
// can scroll the box only if it takes focus: those boxes do, named for the
// section they are in, and the rest stay out of the way of the Tab key.
function markScrollers() {
  for (const wrap of document.querySelectorAll(".table-wrap")) {
    if (!wrap.offsetParent) continue;            // in a hidden tab: done when shown
    if (wrap.scrollWidth > wrap.clientWidth + 1) {
      let head = wrap.previousElementSibling;
      while (head && !/^H[1-4]$/.test(head.tagName)) head = head.previousElementSibling;
      wrap.tabIndex = 0;
      wrap.setAttribute("role", "region");
      wrap.setAttribute("aria-label", `${head ? head.textContent : "Table"}, wide table`);
    } else {
      for (const a of ["tabindex", "role", "aria-label"]) wrap.removeAttribute(a);
    }
  }
}

// Move a screen reader (and the keyboard) to a heading, as well as the eye.
function goTo(el) {
  if (!el) return;
  el.tabIndex = -1;
  el.scrollIntoView({ behavior: "smooth", block: "start" });
  el.focus({ preventScroll: true });
}

// -- starting up ----------------------------------------------------------------

async function boot() {
  const site = await (await fetch("files.json")).json();
  $("version").textContent = site.version;
  const base = `https://cdn.jsdelivr.net/pyodide/v${site.pyodide}/full/`;
  const { loadPyodide } = await import(`${base}pyodide.mjs`);
  const py = await loadPyodide({ indexURL: base });

  // The repo's own layout, so the generator finds data/ beside itself.
  py.FS.mkdirTree("/app/data");
  const files = [...site.python, ...site.data];
  await Promise.all(files.map(async (rel) => {
    const resp = await fetch(rel);
    if (!resp.ok) throw new Error(`could not fetch ${rel} (${resp.status})`);
    py.FS.writeFile(`/app/${rel}`, new Uint8Array(await resp.arrayBuffer()));
  }));
  py.runPython("import sys; sys.path.insert(0, '/app')");
  api = py.pyimport("web_api");
  meta = JSON.parse(api.meta());
  fillControls();
  $("generate").disabled = false;
  $("generate").textContent = "Generate";
  $("open").disabled = false;
  $("open-floor").disabled = false;
  $("reroll-go").disabled = !$("reroll-file").files.length;
  if (!restoreShown()) setStatus("Ready. Pick a floor and generate.");
}

function fillControls() {
  const floor = $("floor");
  for (const f of meta.floors) floor.add(new Option(f.label, f.number));
  const saved = recall("floor");
  if (saved && meta.floors.some((f) => String(f.number) === saved)) floor.value = saved;
  for (const r of meta.regions) $("region").add(new Option(r, r));
  for (const t of meta.tiers) $("tier").add(new Option(t, t));
  $("tier").value = "Silver";
  for (const t of meta.types) $("box-types").append(new Option(t));
  for (const t of meta.threats) $("threat").add(new Option(t, t));
  $("roller-seed").textContent = `Box session seed ${meta.roller_seed}. Newest boxes at the top.`;
  syncFloor();
  const size = recall("size");
  if (size && [...$("size").options].some((o) => o.value === size)) $("size").value = size;
  showOdds();
}

function currentFloor() {
  return meta.floors.find((f) => String(f.number) === $("floor").value);
}

function syncFloor() {
  const f = currentFloor();
  const size = $("size");
  const keep = size.value;
  size.replaceChildren(...f.sizes.map((s) => new Option(s, s)));
  size.value = f.sizes.includes(keep) ? keep : "medium";
  let shown = 0;
  for (const el of document.querySelectorAll("[data-option]")) {
    const on = f.options.includes(el.dataset.option);
    el.hidden = !on;
    shown += on;
  }
  $("no-options").hidden = shown > 0;
}

// -- generating -------------------------------------------------------------------

async function generate() {
  const btn = $("generate");
  btn.disabled = true;
  setStatus("Generating…");
  // Let the status paint before Python takes the main thread.
  await new Promise((r) => setTimeout(r, 30));
  try {
    const f = currentFloor();
    const res = call("generate", {
      floor: f.number, size: $("size").value, seed: $("seed").value,
      days: f.options.includes("days") ? $("days").value : "",
      scaling: $("scaling").value, region: $("region").value,
    });
    if (res.error) {
      setStatus(res.error, true);
      return;
    }
    remember("floor", String(res.floor));
    remember("size", $("size").value);
    const n = res.maps.length;
    // Read aloud, so in words: "floor4_medium_seed4242" is spelled out.
    show(res, `Floor ${res.floor}, ${res.size}, seed ${res.seed}: ${n} map${n === 1 ? "" : "s"} `
              + "and a write-up. Same seed, same floor.");
  } catch (e) {
    setStatus(`Something went wrong: ${e.message}`, true);
    console.error(e);
  } finally {
    btn.disabled = false;
  }
}

// -- what is on show ------------------------------------------------------------------

// A floor (generated, or opened from a floor file) or a neighborhood's
// re-rolled rooms: shown, and kept on this device so a reload brings it back.
// An edit (editAt, the part being edited) redraws the same floor: the map on
// show stays on show, and the editor stays where it was.
function show(res, words, { scroll = true, keep = true, where = "status", editAt = null } = {}) {
  const mapAt = +$("map-pick").value || 0;
  result = res;
  if (res.kind === "floor") {
    $("floor").value = String(res.floor);
    syncFloor();
    $("size").value = res.size;
    $("seed").value = res.seed;
  }
  $("save-floor").disabled = res.kind !== "floor";
  showMaps();
  if (editAt && mapAt > 0 && mapAt < res.maps.length) {
    $("map-pick").value = String(mapAt);
    showMap(mapAt);
  }
  showWriteup();
  showSuggestions();
  setStatus(words + (keep ? keepForReload(res) : ""), false, where);
  editing = editAt || [];
  if (!$("panel-edit").hidden) editPart(editing, false);
  // On a phone the controls fill the first screen; bring the result up.
  if (scroll) document.querySelector(".tabs").scrollIntoView({ behavior: "smooth", block: "start" });
}

// What a reload brings back: a floor as its floor file, which is all it is
// drawn from; re-rolled rooms as they are. Kept in this browser alone.
const KEPT = "dccmapgen.shown";

function keepForReload(res) {
  let kept = { kind: "rooms", result: res };
  if (res.kind === "floor") {
    const file = call("save_floor", "");
    if (file.error) return "";
    kept = { kind: "floor", text: file.text };
  }
  try {
    localStorage.setItem(KEPT, JSON.stringify(kept));
    return "";
  } catch (e) {
    forgetShown();
    // A browser in private mode keeps nothing; one that ran out of room says so.
    return e && e.name === "QuotaExceededError"
      ? " It's too big to keep here for a reload; Save floor file keeps it." : "";
  }
}

function forgetShown() {
  try { localStorage.removeItem(KEPT); } catch { /* private mode */ }
}

function restoreShown() {
  let kept = null;
  try { kept = JSON.parse(localStorage.getItem(KEPT) || "null"); } catch { kept = null; }
  if (kept && kept.kind === "floor" && typeof kept.text === "string") {
    const res = call("open_floor", kept.text);
    if (!res.error) {
      show(res, `Back where you left off: Floor ${res.floor}, ${res.size}, seed ${res.seed}.`,
           { scroll: false, keep: false });
      return true;
    }
  } else if (kept && kept.kind === "rooms" && kept.result && Array.isArray(kept.result.maps)) {
    show(kept.result, `Back where you left off: rooms re-rolled for ${kept.result.label}. `
         + `${kept.result.name}.`, { scroll: false, keep: false });
    return true;
  }
  forgetShown();
  return false;
}

// -- floor files and re-rolled rooms ----------------------------------------------

function saveFloor() {
  const file = call("save_floor", "");
  if (file.error) {
    setStatus(file.error, true);
    return;
  }
  download(file.name, file.text, "application/json");
  setStatus(`Saved ${file.name}. Edit it in any text editor, and open it here or in the `
            + "desktop app to draw it again.");
}

async function openFloor(file) {
  setStatus(`Opening ${file.name}…`);
  await new Promise((r) => setTimeout(r, 30));
  try {
    const res = call("open_floor", await file.text());
    if (res.error) {
      setStatus(res.error, true);
      return;
    }
    const n = res.maps.length;
    show(res, `Opened ${file.name}: Floor ${res.floor}, ${res.size}, seed ${res.seed}, `
              + `${n} map${n === 1 ? "" : "s"} and a write-up.`);
  } catch (e) {
    setStatus(`Something went wrong: ${e.message}`, true);
    console.error(e);
  }
}

async function rerollRooms() {
  const file = $("reroll-file").files[0];
  if (!file) {
    setStatus("Pick a saved write-up to re-roll rooms from.", true);
    return;
  }
  const btn = $("reroll-go");
  btn.disabled = true;
  setStatus("Re-rolling rooms…");
  await new Promise((r) => setTimeout(r, 30));
  try {
    const res = call("reroll", { text: await file.text(), label: $("reroll-label").value,
                                 seed: $("reroll-seed").value });
    if (res.error) {
      setStatus(res.error, true);
      return;
    }
    $("reroll-seed").value = res.seed;
    const kept = !res.ways ? "No crossings or chamber ways in that write-up to keep."
      : res.moved ? `Its ways out are kept; ${res.moved} had to move, listed in the write-up.`
        : "Its ways out are kept, each in the room it was in.";
    show(res, `Rooms re-rolled for ${res.label}. ${res.name} (seed ${res.seed}). ${kept}`);
  } catch (e) {
    setStatus(`Something went wrong: ${e.message}`, true);
    console.error(e);
  } finally {
    btn.disabled = !$("reroll-file").files.length;
  }
}

// -- maps ---------------------------------------------------------------------------

function svgSize(svg) {
  const w = /<svg[^>]*?\bwidth="([\d.]+)/.exec(svg);
  const h = /<svg[^>]*?\bheight="([\d.]+)/.exec(svg);
  return [w ? +w[1] : 800, h ? +h[1] : 600];
}

function showMaps() {
  const pick = $("map-pick");
  pick.replaceChildren(...result.maps.map((m, i) => new Option(m.title, i)));
  pick.hidden = result.maps.length < 2;
  $("save-svg").disabled = false;
  showMap(0);
}

function showMap(i) {
  const m = result.maps[i];
  const img = $("map-img");
  const [w, h] = svgSize(m.svg);
  if (mapUrl) URL.revokeObjectURL(mapUrl);
  mapUrl = URL.createObjectURL(new Blob([m.svg], { type: "image/svg+xml" }));
  img.width = w;
  img.height = h;
  // The write-up has what the map shows in words; say where.
  img.alt = m.section
    ? `${m.title} of Floor ${result.floor}. The write-up's section "${m.section}" describes it in words.`
    : `${m.title} of Floor ${result.floor}.`;
  $("map-words").hidden = !m.section;
  img.hidden = false;
  $("map-empty").hidden = true;
  img.onload = fit;
  img.src = mapUrl;
  if (!panzoom) {
    panzoom = Panzoom(img, { maxScale: 8, minScale: 0.05, origin: "0 0", step: 0.25 });
    $("viewer").addEventListener("wheel", panzoom.zoomWithWheel, { passive: false });
  }
}

function fit() {
  if (!panzoom) return;
  const img = $("map-img");
  const view = $("viewer");
  const scale = Math.min(view.clientWidth / img.width, view.clientHeight / img.height);
  panzoom.zoom(scale, { animate: false, force: true });
  // With the origin at the top left, centre the map across and keep it at the
  // top: a wide map fitted to a phone is a strip, and a strip in the middle of
  // the box reads as nothing there.
  setTimeout(() => panzoom.pan(
    (view.clientWidth - img.width * scale) / 2 / scale, 0,
    { animate: false, force: true }), 0);
}

// -- the write-up -------------------------------------------------------------------

function showWriteup() {
  // Re-rolled rooms are written to go under a floor's own headings, starting at
  // ###; on their own here they get the level above, so none is skipped.
  const box = markdown(result.kind === "rooms"
    ? `## Rooms re-rolled for ${result.label}. ${result.name}\n\n${result.markdown}` : result.markdown);
  // Links, not a dropdown: a dropdown that jumps when it changes jumps on the
  // first arrow key, before a keyboard user has heard the rest.
  const links = [];
  box.querySelectorAll("h2, h3").forEach((h, i) => {
    h.id = `s${i}`;
    if (h.tagName !== "H2") return;
    const a = Object.assign(document.createElement("a"), { href: `#${h.id}`, textContent: h.textContent });
    a.addEventListener("click", (e) => {
      e.preventDefault();
      $("jump").open = false;
      goTo($(h.id));
    });
    const li = document.createElement("li");
    li.append(a);
    links.push(li);
  });
  $("jump-list").replaceChildren(...links);
  $("jump").hidden = links.length < 2;
  $("jump").open = false;
  $("writeup").replaceChildren(...box.childNodes);
  $("save-md").disabled = false;
  markScrollers();
}

// The map on show, in words: its section of the write-up.
function readMap() {
  const m = result.maps[+$("map-pick").value || 0];
  const head = [...$("writeup").querySelectorAll("h2, h3")].find((h) => h.textContent === m.section);
  selectTab($("tab-text"));
  goTo(head);
}

// -- loot boxes ---------------------------------------------------------------------

function showSuggestions() {
  const list = $("suggestions");
  list.replaceChildren();
  $("loot-floor").value = result.floor;
  $("sugg-empty").textContent = result.suggestions.length
    ? "Tap one to start from it."
    : "This floor names no boxes. Pick a tier and type below.";
  for (const s of result.suggestions) {
    const b = document.createElement("button");
    b.append(s.text, Object.assign(document.createElement("small"), { textContent: s.note }));
    b.addEventListener("click", () => {
      // A threat leaves the tier to the DM: the sponsor only knows what is coming.
      if (s.kind === "threat") {
        $("box-type").value = "Benefactor";
        $("threat").value = s.threat;
        setStatus(`Benefactor box, prepared for ${s.threat}. Open is next.`);
      } else {
        $("tier").value = s.tier;
        $("box-type").value = s.type;
        setStatus(`${s.tier} ${s.type} box. Open is next.`);
      }
      // Not just scrolled to: focus goes there, so a screen reader follows.
      $("open").scrollIntoView({ behavior: "smooth", block: "center" });
      $("open").focus({ preventScroll: true });
    });
    const li = document.createElement("li");
    li.append(b);
    list.append(li);
  }
}

function showOdds() {
  $("liked-out").textContent = $("liked").value;
  if (!api) return;
  const o = call("fan_odds", $("liked").value);
  $("odds").textContent = o.error ? o.error
    : `Odds: good ${Math.round(o.good * 100)}% / neutral ${Math.round(o.neutral * 100)}% / bad ${Math.round(o.bad * 100)}%`;
}

function openBoxes() {
  const res = call("open_boxes", {
    tier: $("tier").value, type: $("box-type").value, floor: $("loot-floor").value,
    count: $("count").value, threat: $("threat").value, liked: $("liked").value,
  });
  if (res.error) {
    setStatus(res.error, true);
    return;
  }
  const card = Object.assign(document.createElement("div"), { className: "box" });
  card.append(...markdown(res.markdown).childNodes);
  $("opened").prepend(card);
  markScrollers();
  setStatus(`Opened ${res.count} × ${res.label} for Floor ${res.floor}.`);
}

// -- the editor ---------------------------------------------------------------------
// The desktop app's edit panel, for a phone and a screen reader alike: a trail
// back to the floor, the parts of the part on show as buttons to step into,
// and its values as a form. Apply draws the floor again with the change.

let editing = [];          // the part on show in the editor, as a path from the floor
let editFields = [];       // its values, as edit_part gave them

function el(tag, props = {}, ...kids) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...kids);
  return node;
}

function editPart(path, focus = true) {
  const floor = result && result.kind === "floor";
  $("edit-empty").hidden = floor;
  $("edit-empty").textContent = !result ? "Generate or open a floor to edit it."
    : "Re-rolled rooms aren't a whole floor. Generate or open a floor to edit it.";
  for (const id of ["edit-trail", "edit-parts"]) $(id).replaceChildren();
  $("edit-parts-head").hidden = true;
  $("edit-form").hidden = true;
  $("edit-undo").disabled = true;
  if (!floor) {
    $("edit-title").textContent = "Edit this floor";
    return;
  }
  const part = call("edit_part", path);
  if (part.error) {
    setStatus(part.error, true, "edit-status");
    return;
  }
  editing = part.path;
  const last = part.trail.length - 1;
  $("edit-trail").replaceChildren(...part.trail.map((t, i) => {
    if (i !== last) {
      return el("li", {}, el("button", { type: "button", textContent: t.label, onclick: () => editPart(t.path) }));
    }
    const here = el("li", { textContent: t.label });
    here.setAttribute("aria-current", "location");
    return here;
  }));
  $("edit-title").textContent = part.trail[last].label;
  $("edit-parts").replaceChildren(...part.children.map((c) =>
    el("li", {}, el("button", { type: "button", textContent: c.label, onclick: () => editPart(c.path) }))));
  $("edit-parts-head").hidden = !part.children.length;
  editFields = part.fields;
  $("edit-fields").replaceChildren(...part.fields.map(fieldRow));
  $("edit-form").hidden = !part.fields.length;
  const open = part.fields.some((f) => !f.locked);
  $("edit-apply").disabled = !open;
  $("edit-revert").disabled = !open;
  $("edit-undo").disabled = !part.undo;
  // A screen reader hears where it has stepped to.
  if (focus) $("edit-title").focus();
}

// One value: a box to type in, a tick box, or, locked, just the value.
function fieldRow(f, i) {
  const id = `ef${i}`;
  const shown = f.value === null ? ""
    : Array.isArray(f.value) ? f.value.join(f.kind === "lines" ? "\n" : ", ") : String(f.value);
  if (f.locked) {
    return el("div", { className: "field" },
      el("span", { className: "label", textContent: `${f.label} (locked)` }),
      el("p", { className: "locked", textContent: shown || "(none)" }));
  }
  if (f.kind === "bool") {
    return el("div", { className: "field" },
      el("label", { className: "check" }, el("input", { type: "checkbox", id, checked: Boolean(f.value) }),
        ` ${f.label}`));
  }
  const multi = f.kind === "long" || f.kind === "lines";
  // A line of text too long for a phone's width wraps in a box of two lines,
  // where Enter still applies rather than starting a new line.
  const wide = f.kind === "text" && shown.length > 28;
  const input = multi
    ? el("textarea", { id, rows: Math.min(8, Math.max(3, shown.split("\n").length + (f.kind === "lines" ? 1 : 0))) })
    : wide ? el("textarea", { id, rows: 2, className: "one-line" })
      : el("input", { id, autocomplete: "off" });
  if (wide) {
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        $("edit-form").requestSubmit();
      }
    });
  }
  input.value = shown;
  if (f.kind === "int") input.inputMode = "numeric";
  if (f.kind === "float") input.inputMode = "decimal";
  if (f.optional) input.placeholder = "none";
  const box = el("div", { className: "field" }, el("label", { htmlFor: id, className: "label", textContent: f.label }), input);
  const how = { lines: "One to a line.", list: "Separated by commas." }[f.kind];
  if (how) {
    box.append(el("small", { className: "hint", id: `${id}-how`, textContent: how }));
    input.setAttribute("aria-describedby", `${id}-how`);
  }
  return box;
}

function editValues() {
  return editFields.map((f, i) => [f, $(`ef${i}`)]).filter(([f, box]) => !f.locked && box)
    .map(([f, box]) => [f.key, f.kind === "bool" ? box.checked : box.value]);
}

function renameQuestion(a) {
  const more = a.count > a.where.length ? `\n…and ${a.count - a.where.length} more` : "";
  return `"${a.old}" is also written in ${a.count} other place${a.count === 1 ? "" : "s"} on this floor:\n\n`
    + a.where.map((w) => `• ${w}`).join("\n") + more
    + `\n\nChange it to "${a.new}" there too? (OK: everywhere. Cancel: just here.)`;
}

async function applyEdit(e) {
  if (e) e.preventDefault();
  const values = editValues();
  setStatus("Applying…", false, "edit-status");
  await new Promise((r) => setTimeout(r, 30));
  try {
    let res = call("edit_apply", { path: editing, values, rename: null });
    if (res.ask) {
      // The old name is written elsewhere: ask, for each, whether to change it there too.
      const rename = res.ask.map((a) => window.confirm(renameQuestion(a)));
      res = call("edit_apply", { path: editing, values, rename });
    }
    if (res.error) {
      setStatus(res.error, true, "edit-status");
      return;
    }
    if (res.nothing) {
      setStatus("Nothing changed.", false, "edit-status");
      return;
    }
    show(res, `${res.status} Save floor file keeps it, and this browser keeps it for a reload.`,
         { scroll: false, where: "edit-status", editAt: editing });
  } catch (err) {
    setStatus(`Something went wrong: ${err.message}`, true, "edit-status");
    console.error(err);
  }
}

function undoEdit() {
  const res = call("edit_undo", "");
  if (res.error) {
    setStatus(res.error, true, "edit-status");
    return;
  }
  show(res, res.undo ? `Undone. ${res.undo} more to undo.` : "Undone.",
       { scroll: false, where: "edit-status", editAt: editing });
}

// -- tabs and wiring ----------------------------------------------------------------

function selectTab(tab) {
  for (const t of document.querySelectorAll("[role=tab]")) {
    const on = t === tab;
    t.setAttribute("aria-selected", String(on));
    t.tabIndex = on ? 0 : -1;            // Tab reaches the chosen tab; arrows the rest
    $(t.getAttribute("aria-controls")).hidden = !on;
  }
  if (tab.id === "tab-map") fit();
  if (tab.id === "tab-edit") editPart(editing, false);
  markScrollers();
}

// The tab keys a screen reader user expects: left and right move along the
// tabs (round the ends), Home and End go to the first and last.
function tabKeys(e) {
  const tabs = [...document.querySelectorAll("[role=tab]")];
  const at = tabs.indexOf(e.currentTarget);
  const to = { ArrowRight: at + 1, ArrowLeft: at - 1, Home: 0, End: tabs.length - 1 }[e.key];
  if (to === undefined) return;
  e.preventDefault();
  const tab = tabs[(to + tabs.length) % tabs.length];
  selectTab(tab);
  tab.focus();
}

function wire() {
  $("floor").addEventListener("change", syncFloor);
  $("generate").addEventListener("click", generate);
  $("map-pick").addEventListener("change", (e) => showMap(+e.target.value));
  $("zoom-in").addEventListener("click", () => panzoom && panzoom.zoomIn());
  $("zoom-out").addEventListener("click", () => panzoom && panzoom.zoomOut());
  $("zoom-fit").addEventListener("click", fit);
  window.addEventListener("resize", () => {
    if (!$("panel-map").hidden) fit();
    markScrollers();
  });
  $("save-svg").addEventListener("click", () => {
    const m = result.maps[+$("map-pick").value || 0];
    download(`${result.basename}${m.suffix}.svg`, m.svg, "image/svg+xml");
  });
  $("save-md").addEventListener("click",
    () => download(`${result.basename}.md`, result.markdown, "text/markdown"));
  $("map-words").addEventListener("click", readMap);
  $("open-floor").addEventListener("click", () => $("floor-file").click());
  $("floor-file").addEventListener("change", (e) => {
    const file = e.target.files[0];
    e.target.value = "";                   // the same file can be opened again
    if (file) openFloor(file);
  });
  $("save-floor").addEventListener("click", saveFloor);
  $("reroll-file").addEventListener("change", () => {
    $("reroll-go").disabled = !api || !$("reroll-file").files.length;
  });
  $("reroll-go").addEventListener("click", rerollRooms);
  $("edit-form").addEventListener("submit", applyEdit);
  $("edit-undo").addEventListener("click", undoEdit);
  $("edit-revert").addEventListener("click", () => {
    editPart(editing, false);
    setStatus("Back to the values it has.", false, "edit-status");
  });
  $("jump").addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || !$("jump").open) return;
    $("jump").open = false;
    $("jump").querySelector("summary").focus();
  });
  for (const t of document.querySelectorAll("[role=tab]")) {
    t.addEventListener("click", () => selectTab(t));
    t.addEventListener("keydown", tabKeys);
  }
  $("liked").addEventListener("input", showOdds);
  $("open").addEventListener("click", openBoxes);
}

// Offline and add-to-home-screen. A new release installs a new service worker;
// when it takes over, reload once so the page and its files match.
if ("serviceWorker" in navigator) {
  const updating = Boolean(navigator.serviceWorker.controller);
  navigator.serviceWorker.register("sw.js").catch(() => {});
  let reloaded = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (updating && !reloaded) {
      reloaded = true;
      location.reload();
    }
  });
}

wire();
boot().catch((e) => {
  setStatus(`Couldn't start: ${e.message}. Check the connection and reload.`, true);
  $("generate").textContent = "Unavailable";
  console.error(e);
});
