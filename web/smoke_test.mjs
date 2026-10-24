// Drive the built web page in a real browser, as a phone: start it, generate a
// floor, read the map and the write-up, open a loot box, then go offline and do
// it again from the cache. Along the way, check it as a screen reader and a
// keyboard user meet it: axe-core finds nothing wrong, the maps say where they
// are told in words, and focus goes where the eye is sent.
//
//   python web/build_site.py _site
//   npm install playwright axe-core && npx playwright install chromium
//   node web/smoke_test.mjs _site [screenshot-dir]
//
// BROWSER_CHANNEL=msedge (or chrome) uses an installed browser instead of
// Playwright's own Chromium. Exits non-zero if anything fails.

import { createServer } from "node:http";
import { readFile, mkdir, mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { createRequire } from "node:module";
import { extname, join, normalize, resolve } from "node:path";

const axePath = createRequire(import.meta.url).resolve("axe-core/axe.min.js");

let chromium;
try {
  ({ chromium } = await import("playwright"));
} catch {
  ({ chromium } = await import("playwright-core"));
}

const site = resolve(process.argv[2] || "_site");
const shots = process.argv[3] ? resolve(process.argv[3]) : null;
const TYPES = {
  ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
  ".css": "text/css", ".json": "application/json", ".py": "text/x-python",
  ".png": "image/png", ".svg": "image/svg+xml", ".webmanifest": "application/manifest+json",
};

// A static server for the folder, as GitHub Pages serves it.
const server = createServer(async (req, res) => {
  let path = decodeURIComponent(new URL(req.url, "http://x").pathname);
  if (path.endsWith("/")) path += "index.html";
  const file = normalize(join(site, path));
  if (!file.startsWith(site)) { res.writeHead(403).end(); return; }
  try {
    const body = await readFile(file);
    res.writeHead(200, { "Content-Type": TYPES[extname(file)] || "application/octet-stream" });
    res.end(body);
  } catch {
    res.writeHead(404).end();
  }
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const url = `http://127.0.0.1:${server.address().port}/`;

let failed = false;
function check(ok, what) {
  console.log(`${ok ? "  ok  " : "  FAIL"} ${what}`);
  if (!ok) failed = true;
}

const browser = await chromium.launch(
  process.env.BROWSER_CHANNEL ? { channel: process.env.BROWSER_CHANNEL } : {});
const context = await browser.newContext({
  viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
});
const page = await context.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
const shot = async (name) => {
  if (!shots) return;
  await mkdir(shots, { recursive: true });
  await page.screenshot({ path: join(shots, `${name}.png`) });
};

// Everything axe-core can find wrong with the page as it stands: a control
// with no name, text too faint to read, ARIA that says the wrong thing.
async function axeCheck(what) {
  if (!(await page.evaluate(() => "axe" in window))) await page.addScriptTag({ path: axePath });
  const found = await page.evaluate(async () =>
    (await window.axe.run(document, { resultTypes: ["violations"] })).violations.map((v) =>
      `${v.id} (${v.impact}, ${v.nodes.length}x, e.g. ${v.nodes[0].target.join(" ")})`));
  check(found.length === 0, `${what}: nothing axe-core flags${found.length ? " -- " + found.join("; ") : ""}`);
}
const said = () => page.$eval("#status", (s) => s.textContent);
const saying = (start) => page.waitForFunction(
  (w) => document.getElementById("status").textContent.startsWith(w), start, { timeout: 60_000 });
const saying2 = (id, start) => page.waitForFunction(
  ([i, w]) => document.getElementById(i).textContent.startsWith(w), [id, start], { timeout: 60_000 });
const focused = () => page.evaluate(() => {
  const el = document.activeElement;
  return { id: el.id, tag: el.tagName, text: el.textContent.trim() };
});
const generated = (words) => page.waitForFunction(
  (w) => document.getElementById("status").textContent.startsWith(w), words, { timeout: 60_000 });

try {
  await page.goto(url);
  await page.waitForSelector("#generate:not([disabled])", { timeout: 180_000 });
  check(true, "Python loaded and the page is ready");
  await shot("1-ready");
  await axeCheck("the page as it starts");

  await page.selectOption("#floor", "4");
  await page.selectOption("#size", "medium");
  await page.fill("#seed", "4242");
  await page.click("#generate");
  await generated("Floor 4, medium, seed 4242:");
  check(true, `generated Floor 4, medium, seed 4242, and said so in words ("${await said()}")`);
  const img = await page.$eval("#map-img", (i) => ({ hidden: i.hidden, w: i.naturalWidth, src: i.src, alt: i.alt }));
  check(!img.hidden && img.w > 0 && img.src.startsWith("blob:"), `the map is on show (${img.w}px wide)`);
  check(img.alt.includes('"Colored Lines"'), `the map says which section tells it in words ("${img.alt}")`);
  const inView = await page.$eval("#viewer", (v) => v.getBoundingClientRect().top < innerHeight);
  check(inView, "the page has scrolled the map into view");
  await page.waitForTimeout(800);            // let the smooth scroll finish
  await shot("2-map");
  await axeCheck("a floor with its map on show");

  await page.selectOption("#floor", "1");
  await page.click("#generate");
  await generated("Floor 1, medium, seed 4242:");
  const maps = await page.$$eval("#map-pick option", (o) => o.length);
  check(maps > 1 && await page.$eval("#map-pick", (p) => !p.hidden), `Floor 1 offers its ${maps} maps to pick from`);
  await page.selectOption("#map-pick", "1");
  await page.click("#zoom-in");
  await shot("2b-rooms");
  // A room map is told in words in its neighborhood's section; the button goes
  // there, focus and all.
  await page.click("#map-words");
  let at = await focused();
  check(at.tag === "H3" && at.text.startsWith("A. ")
        && await page.$eval("#tab-text", (t) => t.getAttribute("aria-selected") === "true"),
        `"Read this map as text" opens the write-up at neighborhood A ("${at.text}")`);
  const layout = await page.$eval("#writeup", (w) => {
    const h = [...w.querySelectorAll("h2")].find((x) => x.textContent === "Layout");
    const part = [];
    for (let el = h.nextElementSibling; el && el.tagName !== "H2"; el = el.nextElementSibling) part.push(el);
    const table = part.map((el) => el.querySelector("table")).find(Boolean);
    return { pre: part.some((el) => el.matches("pre") || el.querySelector("pre")),
             rows: table ? table.querySelectorAll("tbody th[scope=row]").length : 0,
             cols: table ? table.querySelectorAll("thead th[scope=col]").length : 0 };
  });
  check(!layout.pre && layout.rows === 2 && layout.cols === 3,
        `the layout is a table with its rows and columns named, not a drawing in characters (${JSON.stringify(layout)})`);

  // The tabs answer the keys a screen reader user expects of tabs.
  await page.focus("#tab-text");
  const keyed = [];
  for (const key of ["ArrowRight", "ArrowRight", "Home", "End", "ArrowLeft"]) {
    await page.keyboard.press(key);
    keyed.push((await focused()).id);
  }
  check(keyed.join() === "tab-loot,tab-edit,tab-map,tab-edit,tab-loot",
        `arrow keys, Home and End move along the tabs (${keyed.join(", ")})`);

  await page.selectOption("#floor", "4");
  await page.click("#generate");
  await generated("Floor 4, medium, seed 4242:");

  await page.click("#tab-text");
  const writeup = await page.$eval("#writeup", (w) => ({
    h1: w.querySelector("h1")?.textContent || "", h2: w.querySelectorAll("h2").length,
    tables: w.querySelectorAll(".table-wrap table").length,
  }));
  check(writeup.h1.startsWith("Floor 4"), `the write-up is rendered ("${writeup.h1}")`);
  check(writeup.h2 > 10 && writeup.tables > 0, `${writeup.h2} sections, ${writeup.tables} tables, tables wrapped to scroll`);
  const jumps = await page.$$eval("#jump-list a", (a) => a.map((x) => x.textContent));
  check(jumps.length === writeup.h2, "every section is in the jump list");
  await page.click("#jump summary");
  await page.click("#jump-list a >> nth=3");
  at = await focused();
  check(at.tag === "H2" && at.text === jumps[3] && !(await page.$eval("#jump", (d) => d.open)),
        `a section picked from the list gets the focus ("${at.text}")`);
  await shot("3-writeup");
  await axeCheck("the write-up");

  await page.click("#tab-loot");
  const suggestions = await page.$$eval("#suggestions button", (b) => b.length);
  check(suggestions > 0, `${suggestions} boxes suggested by the floor`);
  await page.click("#suggestions button >> nth=0");
  at = await focused();
  check(at.id === "open" && (await said()).endsWith("Open is next."),
        `picking a box moves focus to Open and says what was picked ("${await said()}")`);
  await page.click("#open");
  const opened = await page.$$eval("#opened .box", (b) => b.length);
  check(opened === 1, "a box opens");
  await shot("4-loot");
  await axeCheck("the loot boxes, one opened");
  await page.emulateMedia({ colorScheme: "dark" });
  await axeCheck("the loot boxes in the dark theme");
  await page.click("#tab-text");
  await axeCheck("the write-up in the dark theme");
  await page.emulateMedia({ colorScheme: "light" });

  // Floor files: Save floor file downloads one, and Open draws it again.
  const keep = await mkdtemp(join(tmpdir(), "dccmapgen-smoke-"));
  await page.selectOption("#floor", "1");
  await page.fill("#seed", "4242");
  await page.click("#generate");
  await generated("Floor 1, medium, seed 4242:");
  const wrote = await page.$eval("#writeup", (w) => w.textContent);
  const [saved] = await Promise.all([page.waitForEvent("download"), page.click("#save-floor")]);
  const floorFile = join(keep, saved.suggestedFilename());
  await saved.saveAs(floorFile);
  check(saved.suggestedFilename() === "floor1_medium_seed4242.floor.json",
        `Save floor file downloads ${saved.suggestedFilename()}`);
  await page.selectOption("#floor", "3");
  await page.click("#generate");
  await generated("Floor 3, ");
  await page.setInputFiles("#floor-file", floorFile);
  await saying("Opened ");
  check(await page.$eval("#writeup", (w) => w.textContent) === wrote
        && await page.$eval("#floor", (f) => f.value) === "1",
        `Open a floor file draws it again ("${await said()}")`);

  // Re-roll rooms: from the write-up saved as Markdown, one neighborhood's rooms.
  await page.click("#tab-text");
  const [md] = await Promise.all([page.waitForEvent("download"), page.click("#save-md")]);
  const mdFile = join(keep, md.suggestedFilename());
  await md.saveAs(mdFile);
  await page.click("#reroll summary");
  await page.setInputFiles("#reroll-file", mdFile);
  await page.fill("#reroll-label", "b");
  await page.fill("#reroll-seed", "9");
  await page.click("#reroll-go");
  await saying("Rooms re-rolled for B.");
  const rolled = await page.evaluate(() => ({
    maps: [...document.querySelectorAll("#map-pick option")].map((o) => o.textContent),
    head: document.querySelector("#writeup h3")?.textContent || "",
    ways: document.getElementById("writeup").textContent.includes("crossing west into A1"),
    save: document.getElementById("save-floor").disabled,
  }));
  check(rolled.maps.join() === "Rooms - neighborhood B" && rolled.head.endsWith("Room Detail") && rolled.ways
        && rolled.save, `rooms re-roll with their crossings kept ("${await said()}")`);
  await axeCheck("re-rolled rooms, with the re-roll controls open");

  // A reload brings back what was on show: the rooms, then a floor.
  await page.reload();
  await page.waitForSelector("#generate:not([disabled])", { timeout: 180_000 });
  await saying("Back where you left off: rooms re-rolled for B.");
  check(await page.$$eval("#map-pick option", (o) => o.length) === 1
        && await page.$eval("#map-img", (i) => !i.hidden), "a reload brings back re-rolled rooms");
  await page.setInputFiles("#floor-file", floorFile);
  await saying("Opened ");
  await page.reload();
  await page.waitForSelector("#generate:not([disabled])", { timeout: 180_000 });
  await saying("Back where you left off: Floor 1, medium, seed 4242.");
  check(await page.$eval("#writeup", (w) => w.textContent) === wrote
        && !(await page.$eval("#save-floor", (b) => b.disabled)), "a reload brings back a floor");

  // The Edit tab: step down to a part, change it, rename it everywhere, undo.
  await page.selectOption("#floor", "5");
  await page.selectOption("#size", "medium");
  await page.fill("#seed", "4242");
  await page.click("#generate");
  await generated("Floor 5, medium, seed 4242:");
  await page.click("#tab-edit");
  const top = await page.$eval("#edit-title", (h) => h.textContent);
  check(top.startsWith("Floor 5 — Bubble") && await page.$eval("#edit-empty", (p) => p.hidden),
        `the Edit tab opens on the floor ("${top}")`);
  await page.click("#edit-parts button >> text=/^Quadrants/");
  await page.click("#edit-parts button >> text=/^Quadrant — Air/");
  await page.click("#edit-parts button >> text=/^Castle —/");
  const trail = await page.$$eval("#edit-trail li", (l) => l.map((x) => x.textContent));
  at = await focused();
  check(trail.length === 4 && at.id === "edit-title" && at.text.startsWith("Castle —"),
        `stepping into a part moves focus to its name (${trail.join(" › ")})`);
  const castleName = at.text.replace("Castle — ", "");
  const asked = [];
  page.once("dialog", (d) => { asked.push(d.message()); d.accept(); });
  await page.fill("#ef0", "Castle Mumbles");
  await page.press("#ef0", "Enter");              // Enter applies, as Apply does
  await saying2("edit-status", "Changed Name, and 1 other mention.");
  const edited = await page.$eval("#writeup", (w) => w.textContent);
  check(asked.length === 1 && asked[0].includes("Change it to \"Castle Mumbles\" there too?")
        && edited.includes("Castle Mumbles") && !edited.includes(castleName)
        && (await page.$eval("#edit-title", (h) => h.textContent)) === "Castle — Castle Mumbles",
        "a rename asks to change the name everywhere, and the write-up and the editor show it");
  await page.click("#edit-parts button >> text=/^Boss —/");
  await page.fill("#ef2", "lots");
  await page.click("#edit-apply");
  await saying2("edit-status", "Level should be a whole number");
  check(await page.$eval("#edit-status", (s) => s.classList.contains("error")), "a value of the wrong kind is refused");
  await axeCheck("the Edit tab, a boss's values on show");
  await page.click("#edit-undo");
  await saying2("edit-status", "Undone.");
  check((await page.$eval("#writeup", (w) => w.textContent)).includes(castleName)
        && await page.$eval("#edit-undo", (b) => b.disabled), "Undo puts the old name back");
  // An edit is kept for a reload.
  await page.click("#edit-trail button >> text=/^Castle —/");
  await page.fill("#ef0", "Keep of the Reload");
  await page.click("#edit-apply");
  await saying2("edit-status", "Changed Name");
  await page.reload();
  await page.waitForSelector("#generate:not([disabled])", { timeout: 180_000 });
  await saying("Back where you left off: Floor 5, medium, seed 4242.");
  check((await page.$eval("#writeup", (w) => w.textContent)).includes("Keep of the Reload"),
        "a reload brings back the floor as edited");

  // A bad seed is reported, not crashed on.
  await page.fill("#seed", "abc");
  await page.click("#generate");
  await page.waitForSelector("#status.error", { timeout: 10_000 });
  check(true, "a bad seed is reported");

  // Installed and offline: the service worker controls a reload, and the page
  // still starts and generates with the network gone.
  await page.reload();
  await page.waitForFunction(() => navigator.serviceWorker && navigator.serviceWorker.controller,
    null, { timeout: 30_000 });
  await page.waitForSelector("#generate:not([disabled])", { timeout: 180_000 });
  await context.setOffline(true);
  await page.reload();
  await page.waitForSelector("#generate:not([disabled])", { timeout: 60_000 });
  await page.selectOption("#floor", "9");
  await page.fill("#seed", "11");
  await page.click("#generate");
  await generated("Floor 9, medium, seed 11:");
  check(true, "offline, it still starts and generates");
  await shot("5-offline");

  check(errors.length === 0, `no errors in the page${errors.length ? ": " + errors.join(" | ") : ""}`);
} catch (e) {
  check(false, e.message.split("\n")[0]);
  await shot("failure");
} finally {
  await browser.close();
  server.close();
}
process.exit(failed ? 1 : 0);
