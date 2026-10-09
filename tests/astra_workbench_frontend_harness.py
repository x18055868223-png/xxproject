import json
import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "deploy" / "signal_audit" / "frontend" / "app.js"


WORKBENCH_IDS = [
    "apiStatus",
    "previewBanner",
    "scenarioControl",
    "currentConclusion",
    "marketContext",
    "gateBadges",
    "spaceGate",
    "btcMap",
    "candidateArea",
    "manualReviewState",
    "dataHealth",
    "dailySnapshots",
    "transitions",
    "gapArea",
    "historyArea",
]


def render_workbench(payload: dict, *, capture: bool = False) -> dict:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node is not available")
    script = r"""
const fs = require("fs");
const vm = require("vm");
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
const app = fs.readFileSync("deploy/signal_audit/frontend/app.js", "utf8");
const ids = JSON.parse(process.env.ASTRA_WORKBENCH_IDS);

function makeElement(id) {
  return {
    id,
    hidden: false,
    className: "",
    innerHTML: "",
    textContent: "",
    value: "",
    dataset: {},
    style: {},
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {},
    setAttribute(name, value) { this[name] = value; },
    getAttribute(name) { return this[name] || null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    focus() {},
    close() {},
    showModal() {},
  };
}

const elements = {};
for (const id of ids) elements[id] = makeElement(id);
for (const id of ["refreshButton", "reloadHistoryButton", "historySearch", "forgetTokenButton",
  "cancelTokenButton", "tokenForm", "tokenInput", "tokenDialog"]) {
  elements[id] = makeElement(id);
}

const document = {
  readyState: "loading",
  querySelector(selector) {
    if (selector.startsWith("#")) return elements[selector.slice(1)] || null;
    return null;
  },
  querySelectorAll() { return []; },
  addEventListener() {},
};

const sandbox = {
  console,
  document,
  window: {
    document,
    location: { hostname: "127.0.0.1", protocol: "http:" },
    innerWidth: 1440,
    setInterval() { throw new Error("unexpected interval in explicit workbench render test"); },
    clearInterval() {},
    crypto: { randomUUID() { return "test-review-id"; } },
  },
  setTimeout() { throw new Error("unexpected timeout in explicit workbench render test"); },
  clearTimeout() {},
  fetch() { throw new Error("fetch disabled in workbench harness"); },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(app, sandbox, { filename: "app.js" });

const api = sandbox.window.__astraWorkbenchTest;
if (!api || typeof api.renderAll !== "function") throw new Error("workbench test API missing");
const extraState = payload.__test_state && typeof payload.__test_state === "object" ? payload.__test_state : {};
delete payload.__test_state;
api.state.workbench = payload;
Object.assign(api.state, extraState);
api.state.apiOnline = true;
api.state.lastLoadedAt = payload.updated_at_ms || 0;
api.renderAll();

const areas = {};
for (const id of ids) areas[id] = elements[id].innerHTML;
const captures = process.env.ASTRA_OBSERVATION_CAPTURE === "1"
  ? (payload.candidates || []).map(candidate => api.observationCapture(candidate, "test-rendered-primary")) : undefined;
console.log(JSON.stringify({ areas, captures }));
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        env={**os.environ, "ASTRA_WORKBENCH_IDS": json.dumps(WORKBENCH_IDS), "ASTRA_OBSERVATION_CAPTURE": "1" if capture else "0"},
    )
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    return json.loads(result.stdout)
