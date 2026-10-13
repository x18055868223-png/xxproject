// Same-origin API client for the Astra workbench server.
const BASE = "/api/workbench";

async function json(resp) {
  const text = await resp.text();
  let body = null;
  // A proxy error page (HTML) is never shown as text; only its status is kept.
  try { body = text ? JSON.parse(text) : null; } catch { body = { code: "bad_json", message: `服务器返回了非预期的内容（HTTP ${resp.status}）` }; }
  if (!resp.ok) {
    const err = new Error((body && (body.message || body.code)) || `HTTP ${resp.status}`);
    err.status = resp.status; err.body = body; throw err;
  }
  return body;
}

export const api = {
  // The page projection: only what this page draws (the reading view stays at BASE).
  view: () => fetch(`${BASE}?view=page`, { credentials: "same-origin" }).then(json),
  session: () => fetch(`${BASE}/session`, { credentials: "same-origin" }).then(json),
  login: (token) => fetch(`${BASE}/session`, {
    method: "POST", credentials: "same-origin",
    headers: { Authorization: `Bearer ${token}` },
  }).then(json),
  logout: () => fetch(`${BASE}/session`, { method: "DELETE", credentials: "same-origin" }).then(json),
  reviewState: () => fetch(`${BASE}/review-state`, { credentials: "same-origin" }).then(json),
  lockedSeries: (id) => fetch(`${BASE}/locked-candidates/${encodeURIComponent(id)}/series`, { credentials: "same-origin" }).then(json),
  chainAudit: () => fetch(`${BASE}/chain-audit`, { credentials: "same-origin" }).then(json),
  lockedDetail: (id) => fetch(`${BASE}/locked-candidates/${encodeURIComponent(id)}`, { credentials: "same-origin" }).then(json),
  post: (path, body) => fetch(`${BASE}${path}`, {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }).then(json),
};

export function idempotencyKey(prefix) {
  const rnd = crypto.getRandomValues(new Uint32Array(2));
  return `${prefix}-${Date.now()}-${rnd[0].toString(36)}${rnd[1].toString(36)}`;
}
