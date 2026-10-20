#!/usr/bin/env python3
"""Assemble the web page into a folder GitHub Pages can serve as it stands.

    python web/build_site.py [OUT]          # OUT defaults to _site

The page is the files in web/ plus the generator itself, dcc_mapgen.py and its
data/ banks, which the browser runs through Pyodide. Writes files.json (what to
load into Python, and the version to show) and fills in the service worker's list
of files and its build hash, so a release that changes anything is picked up.

To try it locally:  python -m http.server --directory _site 8000
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys

WEB = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(WEB)
# web/ files the page serves as they are. The rest of web/ is tooling.
SHELL = ["index.html", "app.js", "style.css", "web_api.py", "manifest.webmanifest",
         "icon-180.png", "icon-192.png", "icon-512.png", "icon-maskable-512.png"]


def version() -> str:
    """The release tag when GitHub builds a tag, else what git describes."""
    ref = os.environ.get("GITHUB_REF_NAME", "")
    if ref.startswith("v"):
        return ref
    try:
        out = subprocess.run(["git", "describe", "--tags", "--always"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.strip()
        return out or "dev"
    except (OSError, subprocess.CalledProcessError):
        return "dev"


def build(out: str) -> None:
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(os.path.join(out, "data"))
    for name in SHELL:
        shutil.copy2(os.path.join(WEB, name), os.path.join(out, name))
    shutil.copy2(os.path.join(ROOT, "dcc_mapgen.py"), os.path.join(out, "dcc_mapgen.py"))
    data = sorted(n for n in os.listdir(os.path.join(ROOT, "data")) if n.endswith(".json"))
    for name in data:
        shutil.copy2(os.path.join(ROOT, "data", name), os.path.join(out, "data", name))

    with open(os.path.join(WEB, "versions.json"), encoding="utf-8") as f:
        pins = json.load(f)
    site = {"version": version(), "pyodide": pins["pyodide"],
            "python": ["dcc_mapgen.py", "web_api.py"],
            "data": [f"data/{n}" for n in data]}
    with open(os.path.join(out, "files.json"), "w", encoding="utf-8") as f:
        json.dump(site, f, indent=1)

    # Every file the site serves, for the service worker to cache; and a hash of
    # them all, so any change at all makes it a new build.
    served = ["./", "files.json"] + SHELL + site["python"][:1] + site["data"]
    digest = hashlib.sha256()
    for rel in served[1:]:
        with open(os.path.join(out, rel), "rb") as f:
            digest.update(rel.encode() + b"\0" + f.read())
    with open(os.path.join(WEB, "sw.js"), encoding="utf-8") as f:
        sw = f.read()
    sw = (sw.replace('"__BUILD__"', json.dumps(digest.hexdigest()[:12]))
            .replace("__SHELL__", json.dumps(served)))
    with open(os.path.join(out, "sw.js"), "w", encoding="utf-8", newline="\n") as f:
        f.write(sw)
    # Pages runs Jekyll over a site unless told otherwise; this one needs none.
    open(os.path.join(out, ".nojekyll"), "w").close()
    print(f"built {out}: version {site['version']}, build {digest.hexdigest()[:12]}, "
          f"{len(served)} files")


if __name__ == "__main__":
    build(os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "_site")))
