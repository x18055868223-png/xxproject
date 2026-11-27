// Same-origin API client for the Astra workbench server.
const BASE = "/api/workbench";

async function json(resp) {
  const text = await resp.text();
  let body = null;
  // A proxy error page (HTML) is never shown as text; only its status is kept.
  try { body = text ? JSON.parse(text) : null; } catch { body = { code: "bad_json", message: `服务器返回了非预期的内容（HTTP ${resp.status}）` }; }
  if (!resp.ok) {
    const err = new Error((body && (body.message || body.code)) || `HTTP ${resp.status}`);
    err.status = resp.status; err.body = body;
    err.retryAfter = Number(resp.headers.get("Retry-After")) || null;
    throw err;
  }
  return body;
}

function read(url) {
  return fetch(url, { credentials: "same-origin", signal: AbortSignal.timeout(15000) }).then(json);
}

export const api = {
  currentHand: () => read(`${BASE}/player-audit/current`),
  // The page projection: only what this page draws (the reading view stays at BASE).
  view: () => read(`${BASE}?view=page`),
  session: () => read(`${BASE}/session`),
  login: (token) => fetch(`${BASE}/session`, {
    method: "POST", credentials: "same-origin",
    headers: { Authorization: `Bearer ${token}` },
  }).then(json),
  logout: () => fetch(`${BASE}/session`, { method: "DELETE", credentials: "same-origin" }).then(json),
  reviewState: () => read(`${BASE}/review-state`),
  lockedSeries: (id) => read(`${BASE}/locked-candidates/${encodeURIComponent(id)}/series`),
  chainAudit: () => read(`${BASE}/chain-audit`),
  lockedDetail: (id) => read(`${BASE}/locked-candidates/${encodeURIComponent(id)}`),
  playerHands: () => read(`${BASE}/player-audit`),
  playerHand: (dossier, hand, originals = false) => fetch(
    `${BASE}/player-audit/${encodeURIComponent(dossier)}/${encodeURIComponent(hand)}${originals ? "/originals" : ""}`,
    { credentials: "same-origin", signal: AbortSignal.timeout(15000) }).then(json),
  playerModelInputUrl: (dossier, hand, kind = "model-input", reviewId = null) => {
    const safeKind = ["model-input", "model-input.md", "request.json"].includes(kind) ? kind : "model-input";
    const url = `${BASE}/player-audit/${encodeURIComponent(dossier)}/${encodeURIComponent(hand)}/${safeKind}`;
    if (!reviewId) return url;
    return `${url}?${new URLSearchParams({ review_id: reviewId })}`;
  },
  playerModelInput: (dossier, hand, reviewId = null) => fetch(
    api.playerModelInputUrl(dossier, hand, "model-input", reviewId),
    { credentials: "same-origin", signal: AbortSignal.timeout(15000) }).then(json),
  // The inquirer's question sheets of one contract (docs/opus/14); summary=1 for the audit step.
  inquiry: (contract, { file = null, summary = false } = {}) => {
    const q = new URLSearchParams({ short: contract.short_instrument, long: contract.long_instrument });
    if (file) q.set("file", file);
    if (summary) q.set("summary", "1");
    return read(`${BASE}/inquiry?${q}`);
  },
  verification: (id) => read(`${BASE}/decision-path/${encodeURIComponent(id)}`),
  // Live execution is read-only on this page: one GET, no session, control or write route.
  execLive: () => read(`${BASE}/execution/live`),
  post: (path, body) => fetch(`${BASE}${path}`, {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }).then(json),
};

export function idempotencyKey(prefix) {
  const rnd = crypto.getRandomValues(new Uint32Array(2));
  return `${prefix}-${Date.now()}-${rnd[0].toString(36)}${rnd[1].toString(36)}`;
}
