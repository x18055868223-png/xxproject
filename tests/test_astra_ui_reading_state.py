import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from test_astra_wpem_frontend import _candidate, _workbench  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "deploy" / "signal_audit" / "frontend" / "app.js"
DISCLOSURE_STORAGE_KEY = "astra-v261-disclosures"

WORKBENCH_IDS = [
    "appStatus",
    "previewBanner",
    "scenarioControl",
    "currentConclusion",
    "marketContext",
    "gateArea",
    "btcMap",
    "candidateArea",
    "manualReviewState",
    "dataHealth",
    "dailySnapshots",
    "transitionArea",
    "gapArea",
    "historyList",
    "researchLibrary",
    "auditLibrary",
]


def _dual_candidate_workbench():
    workbench = _workbench([_candidate("put"), _candidate("call")])
    workbench["preview"] = {"kind": "ui-reading-state-regression"}
    for candidate, suffix in zip(workbench["candidates"], ("p", "c")):
        candidate["snapshot"]["snapshot_id"] += "-" + suffix * 192
    return workbench


def _with_second_snapshot_tail_changed(workbench):
    changed = copy.deepcopy(workbench)
    # Deliberately change only the final character; the candidate identity is unchanged.
    original = changed["candidates"][1]["snapshot"]["snapshot_id"]
    changed["candidates"][1]["snapshot"]["snapshot_id"] = original[:-1] + "x"
    return changed


def _with_missing_snapshot_identity(workbench):
    missing = copy.deepcopy(workbench)
    missing["candidates"][1]["snapshot"].pop("snapshot_id", None)
    return missing


def _with_missing_candidate_identity(workbench):
    missing = copy.deepcopy(workbench)
    missing["candidates"][0].pop("candidate_id", None)
    missing["candidates"][0]["snapshot"].pop("candidate_id", None)
    missing["candidates"][0]["snapshot"].pop("id", None)
    return missing


def _workbench_without_candidate_data_clock():
    candidate = _candidate("put", with_wpem=False)
    candidate.pop("review_display", None)
    candidate.pop("asof_ms", None)
    candidate.pop("updated_at_ms", None)
    snapshot = candidate["snapshot"]
    snapshot.pop("decision_asof_ms", None)
    snapshot.pop("asof_ms", None)
    snapshot.pop("valuation_ts_ms", None)
    snapshot["market"].pop("decision_asof_ms", None)
    snapshot["market"].pop("valuation_ts_ms", None)
    workbench = _workbench([candidate])
    workbench["preview"] = {"kind": "ui-reading-state-clock-regression"}
    # A workbench clock must not be borrowed into the candidate data/explanation clock.
    workbench["updated_at_ms"] = 1790323200000
    return workbench


def _run_node(script, payload):
    node = shutil.which("node")
    assert node, "node is required for frontend behavior regression tests"
    env = os.environ.copy()
    env["ASTRA_WORKBENCH_IDS"] = ",".join(WORKBENCH_IDS)
    completed = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(completed.stdout) from exc


def _node_harness(test_body):
    return r'''
const fs = require("fs");
const vm = require("vm");
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
const appSource = fs.readFileSync("deploy/signal_audit/frontend/app.js", "utf8");
const STORAGE_KEY = "astra-v261-disclosures";
const ids = (process.env.ASTRA_WORKBENCH_IDS || "").split(",").filter(Boolean);

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function decodeAttr(value) {
  return String(value || "")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">");
}

function makeDetail(attrs) {
  const handlers = new Map();
  const dataset = {};
  const disclosure = /data-disclosure-key="([^"]*)"/.exec(attrs);
  const mapDisclosure = /data-map-disclosure-key="([^"]*)"/.exec(attrs);
  const id = /\sid="([^"]*)"/.exec(attrs);
  if (disclosure) dataset.disclosureKey = decodeAttr(disclosure[1]);
  if (mapDisclosure) dataset.mapDisclosureKey = decodeAttr(mapDisclosure[1]);
  const detail = {
    tagName: "DETAILS",
    dataset,
    open: /(?:^|\s)open(?:\s|=|$)/.test(attrs),
    id: id ? decodeAttr(id[1]) : "",
    parentElement: null,
    classList: { add() {}, remove() {}, contains() { return false; } },
    addEventListener(type, handler) {
      if (!handlers.has(type)) handlers.set(type, []);
      handlers.get(type).push(handler);
    },
    dispatchToggle() {
      (handlers.get("toggle") || []).forEach((handler) => handler({ target: detail }));
    },
    scrollIntoView() {},
  };
  return detail;
}

function makeElement(id) {
  let html = "";
  let details = [];
  const handlers = new Map();
  const element = {
    id,
    dataset: {},
    style: {},
    tagName: id === "researchLibrary" || id === "auditLibrary" ? "DETAILS" : "DIV",
    open: false,
    classList: { add() {}, remove() {}, contains() { return false; } },
    addEventListener(type, handler) {
      if (!handlers.has(type)) handlers.set(type, []);
      handlers.get(type).push(handler);
    },
    dispatchToggle() {
      (handlers.get("toggle") || []).forEach((handler) => handler({ target: element }));
    },
    querySelectorAll(selector) {
      if (selector === "details[data-disclosure-key]") return details.filter((item) => item.dataset.disclosureKey);
      if (selector === "details[open][data-disclosure-key]") return details.filter((item) => item.open && item.dataset.disclosureKey);
      if (selector === "details[data-map-disclosure-key]") return details.filter((item) => item.dataset.mapDisclosureKey);
      if (selector === "details[open][data-map-disclosure-key]") return details.filter((item) => item.open && item.dataset.mapDisclosureKey);
      if (selector === "details") return details.slice();
      return [];
    },
    querySelector(selector) {
      return this.querySelectorAll(selector)[0] || null;
    },
    scrollIntoView() {},
  };
  Object.defineProperty(element, "innerHTML", {
    get() { return html; },
    set(value) {
      html = String(value || "");
      details = [];
      const re = /<details\b([^>]*)>/gi;
      let match;
      while ((match = re.exec(html))) details.push(makeDetail(match[1] || ""));
    },
  });
  return element;
}

const elements = Object.fromEntries(ids.map((id) => [id, makeElement(id)]));
elements.researchLibrary.dataset.disclosureKey = "global-research";
elements.auditLibrary.dataset.disclosureKey = "global-audit";

const storageMap = new Map(Object.entries(payload.__storage || {}));
const writes = [];
const sessionStorage = {
  getItem(key) { return storageMap.has(key) ? storageMap.get(key) : null; },
  setItem(key, value) {
    const text = String(value);
    writes.push({ key, value: text });
    storageMap.set(key, text);
  },
  removeItem(key) { storageMap.delete(key); },
  clear() { storageMap.clear(); },
};

const window = {
  document: null,
  location: { protocol: "http:", hostname: "127.0.0.1", hash: "#candidateArea" },
  sessionStorage,
  scrollY: 0,
  scrollToCalls: [],
  scrollTo(x, y) { this.scrollToCalls.push([x, y]); this.scrollY = y; },
  setTimeout(callback) { callback(); return 0; },
  clearTimeout() {},
  setInterval() { return 0; },
  clearInterval() {},
  addEventListener() {},
  CSS: { escape(value) { return String(value).replace(/"/g, "\\\""); } },
  crypto: { randomUUID() { return "00000000-0000-4000-8000-000000000000"; } },
  prompt() { throw new Error("prompt must not be used in this harness"); },
};
const document = {
  readyState: "loading",
  currentScript: { src: "http://127.0.0.1/app.js?v=test" },
  addEventListener() {},
  getElementById(id) { return elements[id] || null; },
  querySelector(selector) {
    if (selector.startsWith("#")) return elements[selector.slice(1)] || null;
    const name = /^\[name="([^"]+)"\]$/.exec(selector);
    return name ? (elements[name[1]] || null) : null;
  },
  querySelectorAll() { return []; },
};
window.document = document;

const sandbox = {
  window,
  document,
  console,
  setTimeout: window.setTimeout,
  clearTimeout: window.clearTimeout,
  setInterval: window.setInterval,
  clearInterval: window.clearInterval,
  fetch() { throw new Error("network must not be used in this harness"); },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(appSource, sandbox, { filename: "app.js" });
const api = sandbox.window.__astraWorkbenchTest;

function clone(value) { return JSON.parse(JSON.stringify(value)); }
function render(workbench) {
  api.state.workbench = clone(workbench);
  api.state.apiOnline = true;
  api.state.lastLoadedAt = workbench.updated_at_ms || 0;
  api.state.pendingHashOpen = false;
  api.state.pendingHashScroll = false;
  api.renderAll();
}
function stored() {
  const raw = sessionStorage.getItem(STORAGE_KEY);
  return raw ? JSON.parse(raw) : null;
}
function candidateDetails() {
  return elements.candidateArea.querySelectorAll("details[data-disclosure-key]");
}
function findDetail(key) {
  return candidateDetails().find((item) => item.dataset.disclosureKey === key) || null;
}
function pickSafeLongKey() {
  const forbidden = /(?:api[_-]?key|authorization|auth|token|password|secret|credentials?|private[_-]?key|source[_-]?code|source-code|_source|source_bindings|source_records)/i;
  const key = candidateDetails()
    .map((item) => item.dataset.disclosureKey)
    .filter((item) => item && item.length > 160 && !forbidden.test(item))
    .sort((a, b) => b.length - a.length)[0];
  assert(key, "expected a long safe disclosure key from rendered app details");
  return key;
}
function identityRows(scope) {
  const parsed = JSON.parse(scope);
  assert(Array.isArray(parsed) && Array.isArray(parsed[1]), "scope must include candidate identities");
  return parsed[1];
}
''' + test_body


def test_disclosure_scope_restore_and_storage_fail_closed():
    base = _dual_candidate_workbench()
    payload = {
        "base": base,
        "changed": _with_second_snapshot_tail_changed(base),
        "missingSnapshot": _with_missing_snapshot_identity(base),
        "missingCandidate": _with_missing_candidate_identity(base),
    }
    result = _run_node(
        _node_harness(
            r'''
render(payload.base);
const longKey = pickSafeLongKey();
const initialDetail = findDetail(longKey);
assert(initialDetail, "long key detail should exist in base render");
window.scrollY = 123;
window.location.hash = "#candidateArea";
initialDetail.open = true;
initialDetail.dispatchToggle();
const initial = stored();
assert(initial && initial.scope, "opening a real detail should persist disclosure state");
const baseScope = initial.scope;
const baseIds = identityRows(baseScope);
assert(baseIds.length === 2, "dual candidate scope should include both candidate identities");
for (const candidate of payload.base.candidates) {
  assert(baseIds.some(([candidateId, snapshotId]) => candidateId === candidate.candidate_id && snapshotId === candidate.snapshot.snapshot_id), "complete candidate/snapshot identity missing from scope");
}
assert(baseScope.length > 160, "regression must exercise the former scope truncation boundary");

sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
  scope: baseScope,
  keys: [longKey, "Authorization: Bearer SECRET", "private_token_should_not_return"],
  scrollY: 88,
  hash: "#candidateArea"
}));
writes.length = 0;
api.state.disclosureScope = "";
window.scrollY = 0;
window.location.hash = "#candidateArea";
render(payload.base);
const restoredDetail = findDetail(longKey);
assert(restoredDetail && restoredDetail.open === true, "same identity should restore the full long disclosure key");
window.scrollY = 345;
restoredDetail.dispatchToggle();
const sanitized = stored();
assert(JSON.stringify(sanitized.keys).includes(longKey), "sanitized storage should retain the full long key");
assert(Object.keys(sanitized).sort().join("|") === "hash|keys|scope|scrollY", "storage must only contain scope, keys, scrollY and hash");
assert(typeof sanitized.scrollY === "number" && sanitized.scrollY === 345, "scrollY should be stored as a number");
assert(sanitized.hash === "#candidateArea", "safe hash should be retained");
assert(!/Authorization|Bearer|SECRET|token/i.test(JSON.stringify(sanitized)), "storage must not retain authorization or token text");

sessionStorage.setItem(STORAGE_KEY, JSON.stringify(sanitized));
api.state.disclosureScope = baseScope;
render(payload.changed);
const changedRestoredDetail = findDetail(longKey);
assert(!changedRestoredDetail || changedRestoredDetail.open === false, "changed second snapshot must not restore old scoped keys");
const changedKey = pickSafeLongKey();
const changedDetail = findDetail(changedKey);
changedDetail.open = true;
changedDetail.dispatchToggle();
const changedSaved = stored();
assert(baseScope !== changedSaved.scope, "changing only the tail of the second snapshot id must change storage scope");
const changedIds = identityRows(changedSaved.scope);
const baseCall = baseIds.find(([candidateId]) => candidateId === "candidate-call");
const changedCall = changedIds.find(([candidateId]) => candidateId === "candidate-call");
assert(baseCall && changedCall, "call identity should exist before and after tail change");
assert(baseCall[0] === changedCall[0] && baseCall[1] !== changedCall[1] && changedCall[1] === payload.changed.candidates[1].snapshot.snapshot_id, "only the call snapshot identity should differ");

for (const name of ["missingSnapshot", "missingCandidate"]) {
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(sanitized));
  api.state.disclosureScope = "";
  writes.length = 0;
  render(payload[name]);
  const openCount = candidateDetails().filter((item) => item.open).length;
  assert(openCount === 0, `${name} should not restore any persisted disclosure keys`);
  const first = candidateDetails()[0];
  if (first) {
    first.open = true;
    first.dispatchToggle();
  }
  assert(writes.length === 0, `${name} should fail closed and skip persistence writes`);
}

console.log(JSON.stringify({ ok: true, longKeyLength: longKey.length }));
'''
        ),
        payload,
    )
    assert result["ok"] is True
    assert result["longKeyLength"] > 160


def test_candidate_missing_data_clock_does_not_borrow_workbench_updated_at():
    payload = {"workbench": _workbench_without_candidate_data_clock()}
    result = _run_node(
        _node_harness(
            r'''
render(payload.workbench);
const html = elements.candidateArea.innerHTML;
const lead = html.split('<details class="candidate-details"', 1)[0];
assert(lead.includes("Put 候选"), "candidate card should render");
const timeMatch = lead.match(/<p class="candidate-time muted">([^<]*)<\/p>/);
const timeText = timeMatch ? timeMatch[1] : "";
assert(!timeText.includes("资料与解释依据时点"), "candidate card must not invent a shared data/explanation clock");
assert(!timeText.includes("2026/9/25 16:00:00"), "workbench updated_at_ms must not appear as candidate data time");
if (timeText) assert(timeText.includes("资料时点：未提供"), "missing candidate data clock may only render as missing, not as borrowed data time");
console.log(JSON.stringify({ ok: true, leadLength: lead.length, timeText }));
'''
        ),
        payload,
    )
    assert result["ok"] is True
