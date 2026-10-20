// Service worker: what lets the page work offline and install to a home screen.
//
// build_site.py fills in BUILD (a hash of the site's files) and SHELL (every file
// the site serves). A new release is a new BUILD, so the browser installs this
// worker afresh, caches the new files, and drops the old ones.
//
// The CDN files -- Pyodide, the Markdown renderer -- have their version in the
// URL, so they keep a cache of their own across releases: a new release does not
// make anyone download Python again.

const BUILD = "__BUILD__";
const SHELL = __SHELL__;
const SITE_CACHE = `dcc-mapgen-site-${BUILD}`;
const CDN_CACHE = "dcc-mapgen-cdn";
const CDN = "https://cdn.jsdelivr.net";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SITE_CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys
        .filter((k) => k.startsWith("dcc-mapgen-site-") && k !== SITE_CACHE)
        .map((k) => caches.delete(k))))
      .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  const site = url.origin === self.location.origin;
  if (!site && url.origin !== CDN) return;
  event.respondWith((async () => {
    const cache = await caches.open(site ? SITE_CACHE : CDN_CACHE);
    const hit = await cache.match(req, { ignoreSearch: site });
    if (hit) return hit;
    const resp = await fetch(req);
    if (resp.ok) cache.put(req, resp.clone());
    return resp;
  })());
});
