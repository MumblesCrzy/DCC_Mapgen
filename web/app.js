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
  // Wide tables scroll on their own rather than pushing the page sideways.
  for (const t of box.querySelectorAll("table")) {
    const wrap = Object.assign(document.createElement("div"), { className: "table-wrap" });
    t.replaceWith(wrap);
    wrap.append(t);
  }
  return box;
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
    setStatus(`${res.basename}: ${n} map${n === 1 ? "" : "s"}. Same seed, same floor.`);
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
  img.alt = m.title;
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
  const jump = $("jump");
  jump.replaceChildren(new Option("Jump to a section", ""));
  box.querySelectorAll("h2").forEach((h, i) => {
    h.id = `s${i}`;
    jump.add(new Option(h.textContent, h.id));
  });
  jump.disabled = jump.options.length < 2;
  $("writeup").replaceChildren(...box.childNodes);
  $("save-md").disabled = false;
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
      } else {
        $("tier").value = s.tier;
        $("box-type").value = s.type;
      }
      $("open").scrollIntoView({ behavior: "smooth", block: "center" });
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
  setStatus(`Opened ${res.count} × ${res.label} for Floor ${res.floor}.`);
}

// -- tabs and wiring ----------------------------------------------------------------

function selectTab(tab) {
  for (const t of document.querySelectorAll("[role=tab]")) {
    const on = t === tab;
    t.setAttribute("aria-selected", String(on));
    $(t.getAttribute("aria-controls")).hidden = !on;
  }
  if (tab.id === "tab-map") fit();
}

function wire() {
  $("floor").addEventListener("change", syncFloor);
  $("generate").addEventListener("click", generate);
  $("map-pick").addEventListener("change", (e) => showMap(+e.target.value));
  $("zoom-in").addEventListener("click", () => panzoom && panzoom.zoomIn());
  $("zoom-out").addEventListener("click", () => panzoom && panzoom.zoomOut());
  $("zoom-fit").addEventListener("click", fit);
  window.addEventListener("resize", () => { if (!$("panel-map").hidden) fit(); });
  $("save-svg").addEventListener("click", () => {
    const m = result.maps[+$("map-pick").value || 0];
    download(`${result.basename}${m.suffix}.svg`, m.svg, "image/svg+xml");
  });
  $("save-md").addEventListener("click",
    () => download(`${result.basename}.md`, result.markdown, "text/markdown"));
  $("jump").addEventListener("change", (e) => {
    if (e.target.value) $(e.target.value).scrollIntoView({ behavior: "smooth" });
    e.target.value = "";
  });
  for (const t of document.querySelectorAll("[role=tab]")) {
    t.addEventListener("click", () => selectTab(t));
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
