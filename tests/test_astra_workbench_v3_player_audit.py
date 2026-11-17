"""v3 player-audit UI: model-input copy and download controls on frozen hands and valid hold reviews."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
API_JS = (ROOT / "deploy" / "astra_workbench_v3" / "api.js").as_uri()
PLAYER_AUDIT_JS = (ROOT / "deploy" / "astra_workbench_v3" / "player-audit.js").as_uri()

SHIM = r"""
class T { constructor(t) { this.text = String(t); } get textContent() { return this.text; } }
class CList {
  constructor(n) { this.n = n; }
  _parts() { return String(this.n.className || "").split(/\s+/).filter(Boolean); }
  add(...cs) { const s = new Set(this._parts()); cs.forEach((c) => s.add(c)); this.n.className = [...s].join(" "); }
  remove(...cs) { const r = new Set(cs); this.n.className = this._parts().filter((c) => !r.has(c)).join(" "); }
  contains(c) { return this._parts().includes(c); }
}
class N {
  constructor(tag) {
    this.tagName = tag; this.childNodes = []; this.attrs = {}; this.className = "";
    this.dataset = {}; this.style = {}; this.listeners = {}; this.classList = new CList(this);
    this.isConnected = true; this.value = "";
  }
  append(...cs) { for (const c of cs) if (c != null) this.childNodes.push(c); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(ev, fn) { (this.listeners[ev] ||= []).push(fn); }
  remove() {}
  focus() {}
  select() {}
  setSelectionRange() {}
  querySelectorAll() { return []; }
  set textContent(v) { this.childNodes = [new T(v)]; }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(""); }
}
globalThis.Node = N;
globalThis.document = {
  body: new N("body"),
  createElement: (t) => new N(t),
  createElementNS: (_, t) => new N(t),
  createTextNode: (t) => new T(t),
  execCommand: () => false,
};
"""


def _run_node(body: str, payload: dict) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    script = (
        SHIM
        + 'import fs from "node:fs";\n'
        + 'const p = JSON.parse(fs.readFileSync(0, "utf8"));\n'
        + f'const {{ api }} = await import("{API_JS}");\n'
        + f'const {{ renderPlayerAudit }} = await import("{PLAYER_AUDIT_JS}");\n'
        + f"const out = await (async () => {{ {body} }})();\n"
        + "console.log(JSON.stringify(out));\n"
    )
    res = subprocess.run(
        [node, "--input-type=module", "-e", script],
        input=json.dumps(payload),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        cwd=ROOT,
    )
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_player_audit_model_input_urls_and_copy_download_controls():
    body = r"""
    let copied = null;
    Object.defineProperty(globalThis, "navigator", {
      value: { clipboard: { writeText: async (txt) => { copied = txt; } } },
      configurable: true,
    });
    const calls = [];
    const box = document.createElement("div");
    const walk = (n, out = []) => {
      if (n && n.attrs) {
        out.push(n);
        for (const c of n.childNodes) walk(c, out);
      }
      return out;
    };
    renderPlayerAudit(box, {
      cards: [p.detail],
      ref: `${p.detail.dossier_id}/${p.detail.hand_id}`,
      detail: p.detail,
      expanded: new Set(),
    }, {
      authenticated: true,
      available: true,
      onSelect: () => {},
      onTracker: () => {},
      onOriginals: () => {},
      onLogin: () => {},
      onRefresh: () => {},
      modelInputUrl: api.playerModelInputUrl,
      onModelInput: async (d, h, r) => {
        calls.push({ d, h, r });
        return {
          single_box_text: `BOX:${d}:${h}:${r || "initial"}`,
          fidelity: "RECONSTRUCTED",
          fidelity_reason: "historical",
        };
      },
    });
    const nodes = walk(box);
    const links = nodes.filter((n) => n.tagName === "a" && (n.className || "").includes("pa-export-link"))
      .map((n) => ({ text: n.textContent, href: n.attrs.href, download: n.attrs.download }));
    const btns = nodes.filter((n) => n.tagName === "button" && (n.className || "").includes("pa-export-btn"));
    for (const fn of btns[0].listeners.click || []) await fn();
    const firstCopy = { label: btns[0].textContent, copied };
    for (const fn of btns[1].listeners.click || []) await fn();
    const secondCopy = { label: btns[1].textContent, copied };

    const emptyBox = document.createElement("div");
    renderPlayerAudit(emptyBox, {
      cards: [p.expectedOnly],
      ref: `${p.expectedOnly.dossier_id}/${p.expectedOnly.hand_id}`,
      detail: p.expectedOnly,
      expanded: new Set(),
    }, {
      authenticated: true,
      available: true,
      onSelect: () => {},
      onTracker: () => {},
      onOriginals: () => {},
      onLogin: () => {},
      onRefresh: () => {},
      modelInputUrl: api.playerModelInputUrl,
      onModelInput: async () => ({ single_box_text: "none" }),
    });
    const emptyLinks = walk(emptyBox).filter((n) => (n.className || "").includes("pa-export-bar"));
    return { links, btnCount: btns.length, calls, firstCopy, secondCopy, emptyExportBars: emptyLinks.length };
    """
    payload = {
        "detail": {
            "dossier_id": "Deribit_BTC_20261117T080000Z",
            "hand_id": "hand_4e0efb992e4e459cca46d497",
            "sample": "AD_HOC",
            "state": "Accept",
            "side": "call",
            "expiry_ms": 1794902400000,
            "frozen_at_ms": 1794850171699,
            "model": "deepseek-v4-pro",
            "board": "1. **责任。** 示例牌面",
            "decision": {"decided_at_ms": 1794850442999, "judgment": {
                "action": "Accept", "side": "call", "depends_on_ard": False,
                "comparison": "cmp", "put": "put", "put_counter": "pc", "call": "call", "call_counter": "cc",
            }},
            "hold_reviews": [
                {"review_id": "rev_1e61af69aea5679ea554232e", "status": "VALID", "verdict": "HOLD", "decided_at_ms": 1794867328657, "public_reply": "ok"},
                {"review_id": "rev_000000000000000000000001", "status": "FAILED", "error": "TIMEOUT", "decided_at_ms": 1794867000000},
            ],
        },
        "expectedOnly": {
            "dossier_id": "Deribit_BTC_20261117T080000Z",
            "hand_id": "hand_111111111111111111111111",
            "sample": "SCHEDULED",
            "state": "EXPECTED",
            "expiry_ms": 1794902400000,
        },
    }
    out = _run_node(body, payload)
    assert out["btnCount"] == 2
    assert out["emptyExportBars"] == 0
    assert out["calls"] == [
        {"d": "Deribit_BTC_20261117T080000Z", "h": "hand_4e0efb992e4e459cca46d497", "r": None},
        {"d": "Deribit_BTC_20261117T080000Z", "h": "hand_4e0efb992e4e459cca46d497", "r": "rev_1e61af69aea5679ea554232e"},
    ]
    assert out["firstCopy"] == {
        "label": "已复制（重建）✓",
        "copied": "BOX:Deribit_BTC_20261117T080000Z:hand_4e0efb992e4e459cca46d497:initial",
    }
    assert out["secondCopy"] == {
        "label": "已复制（重建）✓",
        "copied": "BOX:Deribit_BTC_20261117T080000Z:hand_4e0efb992e4e459cca46d497:rev_1e61af69aea5679ea554232e",
    }
    assert [x["href"] for x in out["links"]] == [
        "/api/workbench/player-audit/Deribit_BTC_20261117T080000Z/hand_4e0efb992e4e459cca46d497/model-input.md",
        "/api/workbench/player-audit/Deribit_BTC_20261117T080000Z/hand_4e0efb992e4e459cca46d497/request.json",
        "/api/workbench/player-audit/Deribit_BTC_20261117T080000Z/hand_4e0efb992e4e459cca46d497/model-input.md?review_id=rev_1e61af69aea5679ea554232e",
        "/api/workbench/player-audit/Deribit_BTC_20261117T080000Z/hand_4e0efb992e4e459cca46d497/request.json?review_id=rev_1e61af69aea5679ea554232e",
    ]
