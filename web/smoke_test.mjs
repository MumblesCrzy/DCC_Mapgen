// Drive the built web page in a real browser, as a phone: start it, generate a
// floor, read the map and the write-up, open a loot box, then go offline and do
// it again from the cache.
//
//   python web/build_site.py _site
//   npm install playwright && npx playwright install chromium
//   node web/smoke_test.mjs _site [screenshot-dir]
//
// BROWSER_CHANNEL=msedge (or chrome) uses an installed browser instead of
// Playwright's own Chromium. Exits non-zero on the first thing that fails.

import { createServer } from "node:http";
import { readFile, mkdir } from "node:fs/promises";
import { extname, join, normalize, resolve } from "node:path";

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

try {
  await page.goto(url);
  await page.waitForSelector("#generate:not([disabled])", { timeout: 180_000 });
  check(true, "Python loaded and the page is ready");
  await shot("1-ready");

  await page.selectOption("#floor", "4");
  await page.selectOption("#size", "medium");
  await page.fill("#seed", "4242");
  await page.click("#generate");
  await page.waitForFunction(() => document.getElementById("status").textContent.includes("floor4_medium_seed4242"),
    null, { timeout: 60_000 });
  check(true, "generated Floor 4, medium, seed 4242");
  const img = await page.$eval("#map-img", (i) => ({ hidden: i.hidden, w: i.naturalWidth, src: i.src }));
  check(!img.hidden && img.w > 0 && img.src.startsWith("blob:"), `the map is on show (${img.w}px wide)`);
  const inView = await page.$eval("#viewer", (v) => v.getBoundingClientRect().top < innerHeight);
  check(inView, "the page has scrolled the map into view");
  await page.waitForTimeout(800);            // let the smooth scroll finish
  await shot("2-map");
  await page.selectOption("#floor", "1");
  await page.click("#generate");
  await page.waitForFunction(() => document.getElementById("status").textContent.includes("floor1_medium_seed4242"),
    null, { timeout: 60_000 });
  const maps = await page.$$eval("#map-pick option", (o) => o.length);
  check(maps > 1 && await page.$eval("#map-pick", (p) => !p.hidden), `Floor 1 offers its ${maps} maps to pick from`);
  await page.selectOption("#map-pick", "1");
  await page.click("#zoom-in");
  await shot("2b-rooms");
  await page.selectOption("#floor", "4");
  await page.click("#generate");
  await page.waitForFunction(() => document.getElementById("status").textContent.includes("floor4_medium_seed4242"),
    null, { timeout: 60_000 });

  await page.click("#tab-text");
  const writeup = await page.$eval("#writeup", (w) => ({
    h1: w.querySelector("h1")?.textContent || "", h2: w.querySelectorAll("h2").length,
    tables: w.querySelectorAll(".table-wrap table").length,
  }));
  check(writeup.h1.startsWith("Floor 4"), `the write-up is rendered ("${writeup.h1}")`);
  check(writeup.h2 > 10 && writeup.tables > 0, `${writeup.h2} sections, ${writeup.tables} tables, tables wrapped to scroll`);
  const jumps = await page.$$eval("#jump option", (o) => o.length);
  check(jumps === writeup.h2 + 1, "every section is in the jump menu");
  await shot("3-writeup");

  await page.click("#tab-loot");
  const suggestions = await page.$$eval("#suggestions button", (b) => b.length);
  check(suggestions > 0, `${suggestions} boxes suggested by the floor`);
  await page.click("#suggestions button >> nth=0");
  await page.click("#open");
  const opened = await page.$$eval("#opened .box", (b) => b.length);
  check(opened === 1, "a box opens");
  await shot("4-loot");

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
  await page.waitForFunction(() => document.getElementById("status").textContent.includes("floor9_medium_seed11"),
    null, { timeout: 60_000 });
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
