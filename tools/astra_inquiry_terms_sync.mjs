// Writes tools/astra_inquiry_terms_v1.json from the page's dictionaries (docs/opus/18 §4), so
// the inquirer's reading view and the page read codes the same way. `--check` exits 1 when stale.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.join(here, "..", "deploy", "astra_workbench_v3");
const { TERM_CN, FIELD_CN } = await import(pathToFileURL(path.join(web, "glossary.js")).href);
const { IQ_TERMS } = await import(pathToFileURL(path.join(web, "inquiry_terms.js")).href);
const sorted = (o) => Object.fromEntries(Object.entries(o).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));
const body = JSON.stringify({ source: "deploy/astra_workbench_v3/{inquiry_terms,glossary}.js",
  iq: sorted(IQ_TERMS), term: sorted(TERM_CN), field: sorted(FIELD_CN) }, null, 1) + "\n";
const out = path.join(here, "astra_inquiry_terms_v1.json");
if (process.argv.includes("--check")) {
  const now = fs.existsSync(out) ? fs.readFileSync(out, "utf8") : "";
  if (now !== body) { console.error("astra_inquiry_terms_v1.json is stale; run node tools/astra_inquiry_terms_sync.mjs"); process.exit(1); }
} else fs.writeFileSync(out, body);
