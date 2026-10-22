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

function setStatus(text, error = false) {
  const s = $("status");
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
  setStatus("Ready. Pick a floor and generate.");
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
    result = res;
    $("seed").value = res.seed;
    remember("floor", String(res.floor));
    remember("size", $("size").value);
    showMaps();
    showWriteup();
    showSuggestions();
    const n = res.maps.length;
    // Read aloud, so in words: "floor4_medium_seed4242" is spelled out.
    setStatus(`Floor ${res.floor}, ${res.size}, seed ${res.seed}: ${n} map${n === 1 ? "" : "s"} `
              + "and a write-up. Same seed, same floor.");
    // On a phone the controls fill the first screen; bring the result up.
    document.querySelector(".tabs").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    setStatus(`Something went wrong: ${e.message}`, true);
    console.error(e);
  } finally {
    btn.disabled = false;
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
  const box = markdown(result.markdown);
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

// -- tabs and wiring ----------------------------------------------------------------

function selectTab(tab) {
  for (const t of document.querySelectorAll("[role=tab]")) {
    const on = t === tab;
    t.setAttribute("aria-selected", String(on));
    t.tabIndex = on ? 0 : -1;            // Tab reaches the chosen tab; arrows the rest
    $(t.getAttribute("aria-controls")).hidden = !on;
  }
  if (tab.id === "tab-map") fit();
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
