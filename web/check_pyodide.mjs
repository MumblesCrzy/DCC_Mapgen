// Run the generator inside Pyodide, the Python the web page uses, and check that
// every case in tests/golden.json comes out byte for byte as it does on the
// desktop. A seed has to give the same floor on a phone as in the app.
//
//   npm install pyodide@<version in web/app.js>
//   node web/check_pyodide.mjs
//
// Exits non-zero, listing the cases, if any floor differs.

import { loadPyodide } from "pyodide";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..");
const py = await loadPyodide();

// The same layout as the repo, so the generator finds data/ next to itself and
// the corpus finds golden.json next to itself.
py.FS.mkdirTree("/app/data");
py.FS.mkdirTree("/app/tests");
const put = (rel) => py.FS.writeFile(`/app/${rel}`, readFileSync(join(repo, rel)));
put("dcc_mapgen.py");
put("tests/corpus.py");
put("tests/golden.json");
for (const name of readdirSync(join(repo, "data"))) {
  if (name.endsWith(".json")) put(`data/${name}`);
}

const result = py.runPython(`
import sys
sys.path[:0] = ["/app", "/app/tests"]
import corpus
now, gold = corpus.fingerprint_all(), corpus.load_golden()
bad = sorted(k for k in gold if now.get(k) != gold[k])
f"{len(gold)} cases, {len(bad)} differ" + "".join(f"\\n  {k}: {now.get(k)} != {gold[k]}" for k in bad)
`);
console.log(`Pyodide ${py.version}, Python ${py.runPython("import sys; sys.version.split()[0]")}`);
console.log(result);
process.exit(result.includes(" 0 differ") ? 0 : 1);
