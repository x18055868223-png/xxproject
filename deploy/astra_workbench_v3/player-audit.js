import { btc, h, isNum, usd } from "./format.js";

import { LABELS, stageText } from "./current-hand.js";

const SKIP_REASONS = {
  COOLDOWN: "冷却顺延",
  BELOW_MATERIALITY: "低于重大性阈值",
  DUPLICATE_EVENT: "同签名事件去重",
  BUDGET_DEFERRED: "软预算限制顺延",
  DATA_UNVERIFIED: "数据未核验暂缓",
  NO_NEW_EVIDENCE: "无实质新增证据",
  BACKOFF: "失败退避期顺延",
  INFLIGHT: "已有复核进行中",
  REVOKED: "已建议撤销",
};
const TRIGGER_CATEGORIES = {
  A: "A · 责任边界变化",
  B: "B · 责任余量恶化",
  C: "C · ARD/结构实质变化",
  D: "D · 量价反转",
};
const samples = {
  SCHEDULED: "正式局", STARTUP_AD_HOC: "启动观察", AD_HOC: "追加观察",
  HISTORICAL_REPLAY: "历史回放", FIXTURE: "测试样本",
};
const ruleTypes = {
  entry_not_returned: "进入短腿责任区后未在检查钟前回到区外",
  new_entry: "入场后首次进入短腿责任区",
};
const outcomeClass = {
  OFFICIAL: "官方交割判卷", FIXTURE: "测试样本判卷", UNVERIFIED: "待核验判卷",
};

const label = (status) => LABELS[status] || status;
const sideName = (side) => side === "put" ? "Put" : side === "call" ? "Call" : "";
export const playerRef = (card) => `${card.dossier_id}/${card.hand_id}`;
export const utc = (t) => isNum(t) ? new Date(t).toISOString().slice(0, 19).replace("T", " ") + " UTC" : "—";
const utcShort = (t) => isNum(t) ? new Date(t).toISOString().slice(0, 16).replace("T", " ") + " UTC" : "—";
const para = (text, cls) => h("p", { text, class: cls || null });
const pre = (text) => h("pre", { text });
const signedBtc = (v, digits = 6) => isNum(v) ? `${v > 0 ? "+" : v < 0 ? "−" : ""}${btc(Math.abs(v), digits)} BTC` : "—";
const isValidReviewId = (id) => typeof id === "string" && /^rev_[A-Za-z0-9_-]+$/.test(id);

function legacyCopyText(text) {
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-9999px";
    ta.style.left = "-9999px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus({ preventScroll: true });
    ta.select();
    ta.setSelectionRange(0, text.length);
    const ok = typeof document.execCommand === "function" && document.execCommand("copy");
    ta.remove();
    return Boolean(ok);
  } catch {
    return false;
  }
}

export function renderModelInputActions({
  dossierId,
  handId,
  reviewId = null,
  compact = false,
  onModelInput,
  modelInputUrl,
}) {
  if (!dossierId || !handId || typeof modelInputUrl !== "function") return null;
  const defaultLabel = compact ? "复制复核输入" : "复制完整输入";
  const filePrefix = `${handId}${reviewId ? `-${reviewId}` : ""}`;
  const statusNote = h("span", { class: "pa-export-note muted small" });
  const copyBtn = typeof onModelInput === "function"
    ? h("button", { class: "chip pa-export-btn", type: "button", text: defaultLabel })
    : null;
  const bar = h("div", { class: `pa-export-bar${compact ? " compact" : ""}` },
    compact ? null : h("span", { class: "pa-export-label", text: "完整模型输入" }),
    copyBtn,
    h("a", {
      class: "chip pa-export-link",
      href: modelInputUrl(dossierId, handId, "model-input.md", reviewId),
      download: `${filePrefix}-model-input.md`,
      text: "下载完整输入",
    }),
    h("a", {
      class: "chip muted-chip pa-export-link",
      href: modelInputUrl(dossierId, handId, "request.json", reviewId),
      download: `${filePrefix}-request.json`,
      text: "request.json",
    }),
    statusNote);

  if (!copyBtn) return bar;

  let busy = false;
  let resetTimer = null;
  const clearFeedback = () => {
    bar.querySelectorAll(".pa-copy-fallback").forEach((n) => n.remove());
    statusNote.textContent = "";
    statusNote.removeAttribute("title");
    statusNote.className = "pa-export-note muted small";
  };
  const markCopied = (res) => {
    if (resetTimer) clearTimeout(resetTimer);
    const reconstructed = res?.fidelity === "RECONSTRUCTED";
    copyBtn.textContent = reconstructed ? "已复制（重建）✓" : "已复制 ✓";
    copyBtn.classList.add("ok");
    if (reconstructed) {
      statusNote.textContent = "按冻结卷宗重建";
      if (res?.fidelity_reason) statusNote.title = res.fidelity_reason;
    }
    resetTimer = setTimeout(() => {
      copyBtn.textContent = defaultLabel;
      copyBtn.classList.remove("ok");
      resetTimer = null;
    }, 2000);
  };
  const showManualFallback = (text) => {
    statusNote.textContent = "浏览器限制了自动剪贴板写入";
    statusNote.className = "pa-export-note warn-text small";
    const ta = h("textarea", { class: "pa-copy-fallback-ta", readonly: true, rows: "4" });
    ta.value = text;
    let fallbackBox;
    const closeBtn = h("button", {
      class: "chip muted-chip",
      type: "button",
      text: "收起",
      onclick: () => { fallbackBox.remove(); clearFeedback(); },
    });
    fallbackBox = h("div", { class: "pa-copy-fallback" },
      h("div", { class: "pa-copy-fallback-head" },
        h("span", { class: "small muted", text: "请按 Ctrl/Cmd+C 复制下方完整模型输入：" }),
        closeBtn),
      ta);
    bar.append(fallbackBox);
    ta.focus({ preventScroll: true });
    ta.select();
    ta.setSelectionRange(0, text.length);
  };
  const writeTextOrFallback = async (text, res) => {
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        markCopied(res);
        return true;
      } catch {
        // Fall through to execCommand / manual textarea
      }
    }
    if (legacyCopyText(text)) {
      markCopied(res);
      return true;
    }
    showManualFallback(text);
    return false;
  };

  copyBtn.addEventListener("click", async () => {
    if (busy) return;
    if (resetTimer) { clearTimeout(resetTimer); resetTimer = null; }
    copyBtn.classList.remove("ok");
    clearFeedback();
    let resultOrPromise;
    try {
      resultOrPromise = onModelInput(dossierId, handId, reviewId);
    } catch (err) {
      statusNote.textContent = `读取失败：${err?.message || "请重试"}`;
      statusNote.className = "pa-export-note warn-text small";
      return;
    }
    if (resultOrPromise && typeof resultOrPromise.then !== "function") {
      const text = String(resultOrPromise?.single_box_text || "");
      if (!text) {
        statusNote.textContent = "模型输入内容为空";
        statusNote.className = "pa-export-note warn-text small";
        return;
      }
      await writeTextOrFallback(text, resultOrPromise);
      return;
    }
    busy = true;
    copyBtn.disabled = true;
    copyBtn.textContent = "读取中…";
    let fetched = null;
    const dataPromise = Promise.resolve(resultOrPromise).then((res) => {
      fetched = res;
      return res;
    });
    try {
      if (navigator.clipboard?.write && typeof ClipboardItem !== "undefined") {
        try {
          const item = new ClipboardItem({
            "text/plain": dataPromise.then((x) => {
              const txt = String(x?.single_box_text || "");
              if (!txt) throw new Error("EMPTY_MODEL_INPUT");
              return new Blob([txt], { type: "text/plain" });
            }),
          });
          await navigator.clipboard.write([item]);
          markCopied(fetched);
          return;
        } catch {
          // Fall through to await dataPromise and try writeText / execCommand / manual fallback
        }
      }
      const res = fetched || await dataPromise;
      const text = String(res?.single_box_text || "");
      if (!text) {
        statusNote.textContent = "模型输入内容为空";
        statusNote.className = "pa-export-note warn-text small";
        return;
      }
      await writeTextOrFallback(text, res);
    } catch (err) {
      statusNote.textContent = `读取失败：${err?.message || "请重试"}`;
      statusNote.className = "pa-export-note warn-text small";
    } finally {
      busy = false;
      copyBtn.disabled = false;
      if (copyBtn.textContent === "读取中…") copyBtn.textContent = defaultLabel;
    }
  });

  return bar;
}

function holdVerdictMeta(verdict) {
  const v = String(verdict || "").toUpperCase();
  if (v === "HOLD" || v === "AI_HOLD") return { text: LABELS.AI_HOLD, tone: "ok" };
  if (v === "REVOKE" || v === "AI_REVOKE") return { text: LABELS.AI_REVOKE, tone: "bad" };
  if (v === "HOLD_REVIEW_FAILED" || v === "FAILED" || v === "ERROR" || v === "TIMEOUT") {
    return { text: LABELS.HOLD_REVIEW_FAILED, tone: "warn" };
  }
  return { text: LABELS.AI_HOLD_UNKNOWN, tone: "warn" };
}

function formatAffectedConditions(val) {
  if (!val) return null;
  if (Array.isArray(val)) {
    const items = val.map((x) => typeof x === "string" ? x : x?.label || x?.condition || x?.id || "").filter(Boolean);
    return items.length ? items.join("；") : null;
  }
  return typeof val === "string" && val.trim() ? val.trim() : null;
}

function verdictText(c) {
  return [label(c.state), sideName(c.side)].filter(Boolean).join(" / ");
}

function verdictTone(state) {
  if (state === "Accept") return "ok";
  if (state === "Reject" || state === "DAILY_BUDGET_EXHAUSTED" || state === "LLM_NOT_CONFIGURED"
    || state === "TIMEOUT" || state === "POLICY_BLOCKED") return "warn";
  if (state === "PARSE_FAILED" || state === "LOCK_BLOCKED" || state === "DATA_BLOCKED"
    || state === "REQUEST_FAILED" || state === "PROVENANCE_MISSING") return "bad";
  return "";
}

const SHADOW_ACTION_CN = {
  HOLD_SHADOW_CONTINUE: "影子动作：继续持有",
  REVOKE_ALIGNED: "影子动作：依规撤销",
  REVOKE_DISCRETIONARY: "影子动作：主动撤销",
  FROZEN_RULE_TRIGGERED_CONFLICT: "影子动作：冲突待复核",
  HOLD_UNKNOWN_OBSERVE: "影子动作：待判继续观察",
  NONE: "影子动作：无",
};

function protocolStatusChip(status) {
  if (status === "CONFLICT_WITH_FROZEN_INVALIDATION") {
    return h("span", { class: "chip bad", text: "与冻结规则冲突" });
  }
  if (status === "DISCRETIONARY_REVOKE") {
    return h("span", { class: "chip warn", text: "规则外主动撤销" });
  }
  if (status === "ALIGNED") {
    return h("span", { class: "chip muted-chip", text: "口径一致" });
  }
  return status ? h("span", { class: "chip warn", text: status }) : null;
}

function triggerListView(triggers) {
  const rows = Array.isArray(triggers)
    ? triggers.filter((t) => t && (typeof t === "string" || t.code || t.summary_cn || t.summary))
    : [];
  if (!rows.length) return null;
  return h("ul", { class: "pa-trig-list" }, ...rows.map((t) => {
    if (typeof t === "string") return h("li", { text: t });
    const codeText = t.code || t.category || "";
    const summaryText = t.summary_cn || t.summary || "";
    return h("li", {},
      codeText ? h("span", { text: codeText }) : null,
      summaryText ? h("span", { text: summaryText }) : null);
  }));
}

function entryBasisView(eb) {
  if (!eb || typeof eb !== "object") return null;
  const rows = [];
  if (eb.M1) {
    const ruleName = ruleTypes[eb.M1.invalidation] || eb.M1.invalidation;
    const interval = isNum(eb.M1.check_interval_ms) ? `每 ${eb.M1.check_interval_ms / 60000} 分钟` : null;
    const meaning = eb.M1.invalidation_meaning && eb.M1.invalidation_meaning !== "NOT_RECORDED"
      ? eb.M1.invalidation_meaning : null;
    const text = [ruleName, interval, meaning].filter(Boolean).join(" · ");
    if (text) rows.push(["M1 机械边界", text]);
  }
  if (eb.C && typeof eb.C === "object") {
    for (const [cid, val] of Object.entries(eb.C)) {
      const text = typeof val === "string" ? val : val?.meaning || val?.text || val?.condition || "";
      if (text && text !== "NOT_RECORDED") rows.push([`${cid} 持续条件`, text]);
    }
  }
  if (eb.S1) {
    const nodes = [];
    const choice = eb.S1.original_choice_reason;
    const counter = eb.S1.original_strongest_counterevidence;
    if (choice && choice !== "NOT_RECORDED") nodes.push(h("div", { text: choice }));
    if (counter && counter !== "NOT_RECORDED") {
      nodes.push(h("div", { text: `最强反证：${stripCounterPrefix(counter)}` }));
    }
    if (nodes.length) rows.push(["S1 入场锚点", nodes]);
  }
  if (eb.R1) {
    const rParts = [
      eb.R1.ard_responsibility_meaning && eb.R1.ard_responsibility_meaning !== "NOT_RECORDED"
        ? eb.R1.ard_responsibility_meaning : null,
      eb.R1.ard_review_on && eb.R1.ard_review_on !== "NOT_RECORDED"
        ? `复核触发：${eb.R1.ard_review_on}` : null,
    ].filter(Boolean);
    if (rParts.length) rows.push(["R1 ARD 责任", rParts.join(" · ")]);
  }
  if (!rows.length) return null;
  return h("div", { class: "pa-basis" },
    h("b", { text: "契约锚定 ID（M1 / C1 / S1）" }),
    h("dl", { class: "kv pa-cond-kv" },
      ...rows.flatMap(([k, v]) => [
        h("dt", { text: k }),
        Array.isArray(v) ? h("dd", {}, ...v) : h("dd", { text: v }),
      ])));
}

function modelTag(model, effort) {
  if (!model) return null;
  return `AI · ${model}${effort ? ` · ${effort}` : ""}`;
}

function usageTotalTokens(u) {
  if (!u || typeof u !== "object") return null;
  if (isNum(u.total_tokens)) return u.total_tokens;
  if (isNum(u.prompt_tokens) && isNum(u.completion_tokens)) return u.prompt_tokens + u.completion_tokens;
  return null;
}

function formatUsageText(u) {
  if (!u || typeof u !== "object") return "—";
  const pt = isNum(u.prompt_tokens) ? u.prompt_tokens : null;
  const ct = isNum(u.completion_tokens) ? u.completion_tokens : null;
  const tt = usageTotalTokens(u);
  if (pt === null && ct === null && tt === null) return "—";
  const parts = [];
  if (pt !== null) parts.push(`输入 ${pt.toLocaleString("en-US")}`);
  if (ct !== null) parts.push(`输出 ${ct.toLocaleString("en-US")}`);
  if (tt !== null) parts.push(`总计 ${tt.toLocaleString("en-US")}`);
  return `${parts.join(" / ")} tokens`;
}

function skipReasonLabel(reason) {
  const key = String(reason || "").trim().toUpperCase();
  return SKIP_REASONS[key] || reason || "已跳过";
}

function stripCounterPrefix(text) {
  if (!text) return "—";
  return String(text).replace(/^\s*(?:(?:Put|Call)\s*(?:侧)?\s*)?最强反证\s*[：:]\s*/i, "").trim();
}

/** Render inline **bold** segments into pure DOM nodes (no innerHTML). */
function richNodes(text) {
  const parts = String(text || "").split(/\*\*([^*]+)\*\*/g);
  const out = [];
  for (let i = 0; i < parts.length; i++) {
    if (!parts[i]) continue;
    out.push(i % 2 === 1 ? h("b", { text: parts[i] }) : parts[i]);
  }
  return out;
}

function renderStructuredBoard(boardText) {
  const wrap = h("div", { class: "pa-board" });
  const blocks = String(boardText || "").split(/\n{2,}/).map((b) => b.trim()).filter(Boolean);
  let ardCard = null;
  for (const block of blocks) {
    if (block.startsWith("ARD责任判读：")) {
      const note = h("details", { class: "pa-rule-note" },
        h("summary", { text: "ARD 责任判读规则说明" }),
        para(block.replace(/^ARD责任判读：/, "").trim(), "muted small"));
      if (ardCard) ardCard.append(note);
      else wrap.append(note);
      continue;
    }
    if (block.startsWith("路径窗为读取前24小时")) {
      wrap.append(para(block, "muted small pa-board-foot"));
      continue;
    }
    const lines = block.split("\n").map((l) => l.trim()).filter(Boolean);
    if (!lines.length) continue;
    const first = lines[0];
    let title = null;
    let restFirst = first;
    const numMatch = first.match(/^(\d+)\.\s*\*\*([^*]+)\*\*\s*(.*)$/);
    const boldMatch = !numMatch && first.match(/^\*\*([^*]+)\*\*\s*(.*)$/);
    if (numMatch) {
      title = `${numMatch[1]}. ${numMatch[2].replace(/[。.:：]+$/, "")}`;
      restFirst = numMatch[3];
    } else if (boldMatch) {
      title = boldMatch[1].replace(/[。.:：]+$/, "");
      restFirst = boldMatch[2];
    }
    const card = h("div", { class: "ev pa-board-sec" });
    if (title) card.append(h("header", {}, h("b", { text: title })));
    if (restFirst) card.append(h("p", { class: "pa-board-p" }, ...richNodes(restFirst)));
    const items = [];
    for (const line of lines.slice(1)) {
      if (line.startsWith("- ")) {
        items.push(h("li", {}, ...richNodes(line.slice(2).trim())));
      } else {
        card.append(h("p", { class: "pa-board-p" }, ...richNodes(line)));
      }
    }
    if (items.length) card.append(h("ul", { class: "pa-station-list" }, ...items));
    if (title && title.includes("锚相对动态ARD")) ardCard = card;
    wrap.append(card);
  }
  return wrap;
}

export function renderPlayerAudit(box, player, { available = true, onSelect, onTracker, onOriginals, onModelInput, modelInputUrl, onLogin, onRefresh, authenticated }) {
  if (!authenticated) {
    box.append(h("div", { class: "pad" }, para("登录后按期查阅每日交割卷宗、冻结牌面与 AI 牌手推理审计。", "muted"),
      h("button", { class: "btn secondary", type: "button", onclick: onLogin }, "登录")));
    return;
  }
  if (available === false) {
    box.append(h("div", { class: "pad" },
      para("本服务实例尚未挂载 AI 牌手卷宗目录（需在服务端配置 ASTRA_AI_SHADOW_DOSSIER_ROOT 并同步 16:00Z / 08:00Z 牌局卷宗）；旧版「模型复核 / 提问单」已退役，不再以此占位。", "muted")));
    return;
  }
  const cards = player.cards.some((c) => playerRef(c) === player.ref) || !player.detail
    ? player.cards : [player.detail, ...player.cards];
  if (cards.length) {
    const byExpiry = new Map();
    for (const c of cards) {
      const key = isNum(c.expiry_ms) ? `${utcShort(c.expiry_ms)} 到期` : (c.dossier_id || "其他期次");
      if (!byExpiry.has(key)) byExpiry.set(key, []);
      byExpiry.get(key).push(c);
    }
    const groups = [...byExpiry.entries()].map(([expLabel, list]) => h("optgroup", { label: expLabel },
      ...list.map((c) => h("option", {
        value: playerRef(c),
        text: `${expLabel} · ${samples[c.sample] || "卷宗"} · ${verdictText(c)}${c.model ? ` · AI · ${c.model}` : ""} · 发牌 ${utcShort(c.frozen_at_ms ?? c.scheduled_at_ms)}`
      }))));
    const menu = h("select", { "aria-label": "按期选择交割卷宗", onchange: (e) => onSelect(e.target.value) }, ...groups);
    menu.value = player.ref || "";
    const cur = player.detail || cards.find((c) => playerRef(c) === player.ref) || cards[0];
    const curModel = modelTag(cur?.model, cur?.reasoning_effort);
    box.append(h("div", { class: "pad player-audit player-controls" },
      h("label", { class: "pa-select-label" }, h("span", { class: "muted small", text: "交割期次与手牌" }), menu),
      cur?.sample ? h("span", { class: "chip", text: samples[cur.sample] || cur.sample }) : null,
      curModel ? h("span", { class: "chip", text: curModel }) : null,
      h("button", { class: "chip", type: "button", onclick: onRefresh }, "刷新卷宗")));
  }
  if (player.error || !player.detail) {
    box.append(para(player.error || (player.indexError ? "卷宗列表暂不可读。" :
      player.ref ? "正在读取本局卷宗…" : "当前暂无可读的 AI 牌手卷宗。"), "empty pad"));
    return;
  }
  const d = player.detail, judgment = d.decision?.judgment, receipt = d.receipt, condition = d.condition;
  const fold = (key, title, children, onOpen, extraCls = "") => {
    const region = h("details", { class: `src-group${extraCls ? ` ${extraCls}` : ""}`, ontoggle: (e) => {
      const node = e.currentTarget;
      if (!node.isConnected) return;
      if (node.open) { player.expanded.add(key); onOpen?.(); }
      else player.expanded.delete(key);
    } }, h("summary", { text: title }), ...children);
    region.open = player.expanded.has(key);
    return region;
  };

  const left = h("div", { class: "points" }), right = h("div", { class: "ledger" });
  box.append(h("div", { class: "audit-main player-audit" }, left, right));

  // ── Left Upper: 当局裁决与推理依据 ──
  const actorBadge = modelTag(d.model, d.reasoning_effort);
  left.append(h("div", { class: "deck-head pa-head" },
    h("h3", { text: "当局裁决与推理依据" }),
    h("span", { class: `chip ${verdictTone(d.state)}`, text: verdictText(d) }),
    actorBadge ? h("span", { class: "chip", text: actorBadge }) : null));

  if (d.frozen_at_ms || d.board) {
    const exportBar = renderModelInputActions({
      dossierId: d.dossier_id,
      handId: d.hand_id,
      onModelInput,
      modelInputUrl,
    });
    if (exportBar) left.append(exportBar);
  }

  const metaRows = [
    ["运行阶段", `${stageText(d)} · 更新 ${utc(d.last_update_ms)}`],
    ["交割到期", `${utc(d.expiry_ms)}（${samples[d.sample] || d.sample}）`],
    ["发牌与作答", `发牌 ${utc(d.frozen_at_ms)} · 作答 ${utc(d.decision?.decided_at_ms)}`],
    ["牌手模型", d.model ? `${d.model}${d.reasoning_effort ? `（推理强度 ${d.reasoning_effort}）` : ""}` : "未记录"],
    ["本手牌 Token 消耗", formatUsageText(d.usage)],
    ["ARD 依赖", judgment ? (judgment.depends_on_ard ? "原回复声明依赖锚相对动态（ARD）" : "仅按一阶责任路径成立，不依赖 ARD") : "—"],
  ];
  left.append(h("dl", { class: "kv pa-meta" },
    ...metaRows.flatMap(([k, v]) => [h("dt", { text: k }), h("dd", { text: v })])));
  if (d.sample === "AD_HOC" || d.sample === "STARTUP_AD_HOC") {
    left.append(para(`独立研究/追加手牌（${samples[d.sample] || d.sample}），不替代正式日局。`, "muted small pa-sample-note"));
  }

  if (judgment) {
    left.append(h("div", { class: "ev pa-comparison" },
      h("header", {}, h("b", { text: "双侧比较与择优结论" })),
      para(judgment.comparison, "pa-rationale")));

    for (const side of ["put", "call"]) {
      const c = d.contracts?.find((x) => x.side === side)?.contract;
      const chosen = d.state === "Accept" && d.side === side;
      left.append(h("div", { class: `ev pa-side-card${chosen ? " hl" : ""}` },
        h("header", {},
          h("b", { text: `${sideName(side)} 侧候选 · 卖 ${usd(c?.short_strike_usd)} / 买 ${usd(c?.long_strike_usd)} USD` }),
          h("span", { class: `chip ${chosen ? "ok" : "muted-chip"}`, text: chosen ? "本局承保侧" : "对照侧" })),
        para(judgment[side], "pa-rationale"),
        h("div", { class: "pa-counter" },
          h("b", { text: "最强反证：" }),
          h("span", { text: stripCounterPrefix(judgment[side + "_counter"]) }))));
    }
  } else if (d.notes?.length) {
    left.append(h("div", { class: "ev" },
      h("header", {}, h("b", { text: "本局状态说明" })),
      para(d.notes.at(-1).reason, "muted small")));
  }
  const attempts = Array.isArray(d.attempts) ? d.attempts : [];
  const latestAttempt = attempts.at(-1);
  const latestAttemptFailed = Boolean(
    latestAttempt && latestAttempt.status && !["VALID", "RECEIVED"].includes(latestAttempt.status),
  );
  if ((!judgment && attempts.length) || latestAttemptFailed) {
    const recentAttempts = attempts.slice(-4).reverse();
    const countLabel = attempts.length > recentAttempts.length
      ? `最近 ${recentAttempts.length} / 共 ${attempts.length} 次`
      : `共 ${attempts.length} 次`;
    left.append(h("div", { class: "ev" },
      h("header", {},
        h("b", { text: "作答尝试与失败状态" }),
        h("span", { class: "muted small num", text: countLabel })),
      h("ul", { class: "pa-attempt-list" }, ...recentAttempts.map((a) => {
        const st = a.status || "UNKNOWN";
        const tone = verdictTone(st) || (st === "NO_RESPONSE" || st === "LATE" ? "warn" : "");
        const mTag = modelTag(a.model, a.reasoning_effort);
        const ts = a.decided_at_ms ?? a.recorded_at_ms ?? a.requested_at_ms;
        return h("li", {},
          h("div", {},
            h("span", { class: `chip ${tone}`, text: LABELS[st] ? `${LABELS[st]}（${st}）` : st }),
            mTag ? h("span", { class: "chip muted-chip", text: mTag }) : null,
            ts ? h("span", { class: "muted small num pa-hold-ts", text: utc(ts) }) : null),
          a.error ? para(String(a.error), "small warn-text") : null);
      }))));
  }

  // ── Left Lower: 事中持续追踪与到期判卷 ──
  left.append(h("h4", { class: "pa-sec-title", text: "事中持续追踪与到期判卷" }));

  const trackCard = h("div", { class: "ev pa-track-card" },
    h("header", {},
      h("b", { text: "同合同锁定追踪基线" }),
      d.tracking_state ? h("span", { class: `chip ${d.tracking_state === "TRACKING" ? "ok" : ""}`,
        text: [label(d.tracking_state), label(d.tracker_status)].filter(Boolean).join(" · ") }) : null));
  if (receipt) {
    const credit = receipt.economy?.net_credit_after_entry_fee_btc;
    const t0Contract = receipt.contract?.short_instrument ? receipt.contract : d.contracts?.find((c) => c.side === d.side)?.contract;
    const t0Index = receipt.market?.index_price_usd ?? receipt.index_usd;
    const t0Rows = [
      ["锁定与盘口时钟", `锁定 ${utc(receipt.locked_at_ms)} · 盘口读数 ${utc(receipt.tracking_asof_ms)}`],
      ["锁定合约", `${t0Contract?.short_instrument || "—"} / ${t0Contract?.long_instrument || "—"} · ${t0Contract?.quantity_btc ?? 1} BTC`],
      ["入场当时指数", isNum(t0Index) ? `${usd(t0Index, 2)} USD` : "—"],
      ["费后净信用估计", isNum(credit) ? `${btc(credit, 6)} BTC（影子估值，非实盘成交）` : "—"],
    ];
    trackCard.append(h("dl", { class: "kv pa-meta" },
      ...t0Rows.flatMap(([k, v]) => [h("dt", { text: k }), h("dd", { text: v })])));
  } else if (d.lock_reason) {
    trackCard.append(para(`未建立锁定追踪：${d.lock_reason}`, "muted small"));
  }
  if (d.tracker_episode_id) {
    trackCard.append(h("div", { class: "pa-jump-row" },
      h("a", { href: `#lock-${d.tracker_episode_id}`, class: "dp-jump",
        onclick: (e) => { e.preventDefault(); onTracker(d.tracker_episode_id); } }, "查看同一责任的锁定追踪详情 →")));
  }
  if (d.archive) {
    trackCard.append(h("div", { class: "pa-jump-row" },
      h("a", {
        href: `/api/workbench/player-audit/${encodeURIComponent(d.dossier_id)}/${encodeURIComponent(d.hand_id)}/trajectory`,
        class: "dp-jump", target: "_blank", rel: "noopener", text: "查看归档完整轨迹、缺口与结算 →"
      })));
  }
  left.append(trackCard);

  if (condition) {
    const ruleType = condition.invalidation_rule?.type ?? condition.invalidation;
    const condCard = h("div", { class: "ev pa-cond-card" },
      h("header", {},
        h("b", { text: "冻结承担条件与事中检查" }),
        ruleType ? h("span", { class: "chip", text: ruleTypes[ruleType] || ruleType }) : null),
      h("dl", { class: "kv pa-cond-kv" },
        h("dt", { text: "入场前历史事实" }), h("dd", { text: condition.historical_facts || "—" }),
        h("dt", { text: "持续承担条件" }), h("dd", { text: condition.accepted_conditions || "—" }),
        h("dt", { text: "有效破坏标准" }), h("dd", { text: condition.invalidation_meaning || "—" }),
        h("dt", { text: "固定检查时钟" }), h("dd", { class: "num",
          text: `首检 ${utc(condition.check_clock?.first_at_ms)} · 每 ${(condition.check_clock?.interval_ms || 300000) / 60000} 分钟固定网格复核` })));
    if (d.entry_basis) {
      condCard.append(entryBasisView(d.entry_basis));
    }
    if (d.grid_check_summary || d.events?.length) {
      const gs = d.grid_check_summary;
      if (gs && gs.total) {
        condCard.append(para(
          `固定网格检查共 ${gs.total} 次（未触发 ${gs.not_triggered ?? 0} · 已触发 ${gs.triggered ?? 0}${gs.unknown ? ` · 待补齐 ${gs.unknown}` : ""}）· 最近 ${utc(gs.latest_at_ms)}`,
          "muted small num pa-grid-sum"));
      } else if (d.events?.length) {
        const byTs = new Map();
        for (const ev of d.events) {
          if (ev.kind !== "CONDITION_TRIGGERED" && ev.kind !== "CONDITION_NOT_TRIGGERED" && ev.kind !== "CONDITION_UNKNOWN") continue;
          const tsKey = ev.observed_at_ms ?? ev.recorded_at_ms ?? ev.event_id;
          const prev = byTs.get(tsKey);
          if (!prev || (prev.kind === "CONDITION_UNKNOWN" && ev.kind !== "CONDITION_UNKNOWN")) byTs.set(tsKey, ev);
        }
        const gridChecks = [...byTs.values()];
        if (gridChecks.length) {
          const triggered = gridChecks.filter((ev) => ev.kind === "CONDITION_TRIGGERED").length;
          const untriggered = gridChecks.filter((ev) => ev.kind === "CONDITION_NOT_TRIGGERED").length;
          const unknown = gridChecks.length - triggered - untriggered;
          const latestCheckTs = Math.max(...gridChecks.map((ev) => ev.observed_at_ms ?? ev.recorded_at_ms ?? 0));
          condCard.append(para(
            `固定网格检查共 ${gridChecks.length} 次（未触发 ${untriggered} · 已触发 ${triggered}${unknown ? ` · 待补齐 ${unknown}` : ""}）· 最近 ${utc(latestCheckTs > 0 ? latestCheckTs : null)}`,
            "muted small num pa-grid-sum"));
        }
      }
    }
    if (d.events?.length) {
      const lastNotTrigIdx = d.events.findLastIndex((ev) => ev.kind === "CONDITION_NOT_TRIGGERED");
      const lastObsIdx = d.events.findLastIndex((ev) => ev.kind === "OBSERVATION");
      const shownEvents = d.events.filter((ev, idx) =>
        (ev.kind !== "CONDITION_NOT_TRIGGERED" && ev.kind !== "OBSERVATION") || idx === lastNotTrigIdx || idx === lastObsIdx);
      const tbl = h("table", { class: "t compact pa-events-table" },
        h("thead", {}, h("tr", {}, h("th", { text: "事件 / 网格检查结果" }), h("th", { class: "n", text: "观测 / 记录时刻" }))),
        h("tbody", {}, ...shownEvents.map((ev) => {
          const note = ev.kind === "HOLD_REVIEW_SKIPPED"
            ? (ev.summary && ev.summary !== ev.reason ? `${skipReasonLabel(ev.reason)}：${ev.summary}` : skipReasonLabel(ev.reason))
            : (ev.summary || ev.verdict);
          const shortNote = note ? String(note).trim().slice(0, 72) : "";
          return h("tr", {},
            h("td", {},
              h("span", { text: label(ev.kind) }),
              shortNote ? h("span", { class: "muted small", text: ` · ${shortNote}` }) : null),
            h("td", { class: "n num", text: utc(ev.observed_at_ms ?? ev.recorded_at_ms ?? ev.created_at_ms) }));
        })));
      condCard.append(tbl);
    }
    left.append(condCard);
  }

  if (d.hold_reviews?.length || d.shadow_management || d.management_telemetry || d.hold_skip_summary?.total_skips) {
    const holdCard = h("div", { class: "ev pa-hold-card" },
      h("header", {}, h("b", { text: "持仓复核与影子管理轨迹" })));
    const tel = d.management_telemetry || {};
    const skipSum = d.hold_skip_summary || {};
    const validReviews = (d.hold_reviews || []).filter((r) => !r.status || r.status === "VALID");
    const gridChecks = isNum(tel.grid_check_count) ? tel.grid_check_count : (d.grid_check_summary?.total ?? 0);
    const skipDebounce = isNum(tel.skip_and_debounce_count)
      ? tel.skip_and_debounce_count
      : ((skipSum.total_skips ?? 0) + (tel.coalesced_debounce_count ?? 0));
    const aiCount = isNum(tel.ai_review_count) ? tel.ai_review_count : validReviews.length;
    let totalTokens = usageTotalTokens(tel.usage);
    if (totalTokens === null && validReviews.length) {
      let sum = 0;
      let anyTok = false;
      for (const r of validReviews) {
        const t = usageTotalTokens(r.usage);
        if (t !== null) { sum += t; anyTok = true; }
      }
      if (anyTok) totalTokens = sum;
    }
    holdCard.append(para(
      `网格检查 ${gridChecks} 次 · 跳过/去重 ${skipDebounce} 次 · 实际 AI 复核 ${aiCount} 次 · 合计 Token ${isNum(totalTokens) ? totalTokens.toLocaleString("en-US") : "0"}`,
      "small num pa-tel-line"));
    const skipEvents = (d.events || []).filter((ev) => ev.kind === "HOLD_REVIEW_SKIPPED");
    const lastSkip = skipSum.last_skip || (skipEvents.length ? skipEvents[skipEvents.length - 1] : null);
    if (lastSkip && String(lastSkip.reason || "").toUpperCase() !== "NO_TRIGGER") {
      const skipTs = lastSkip.at_ms ?? lastSkip.observed_at_ms ?? lastSkip.recorded_at_ms;
      const reasonTxt = skipReasonLabel(lastSkip.reason);
      const detailTxt = lastSkip.summary_cn || (lastSkip.summary && lastSkip.summary !== lastSkip.reason ? lastSkip.summary : "");
      holdCard.append(para(
        `最近跳过：${reasonTxt}${detailTxt ? `（${detailTxt}）` : ""}${isNum(skipTs) ? ` · ${utc(skipTs)}` : ""}`,
        "muted small num pa-skip-line"));
      const reasonEntries = Object.entries(skipSum.by_reason || {}).filter(([k, v]) => k !== "NO_TRIGGER" && isNum(v) && v > 0);
      if (reasonEntries.length > 1 || (skipSum.total_skips ?? 0) > 1) {
        holdCard.append(h("details", { class: "pa-skip-more" },
          h("summary", { text: `跳过原因分布（共 ${skipSum.total_skips ?? reasonEntries.reduce((a, [, v]) => a + v, 0)} 次）` }),
          h("ul", { class: "pa-trig-list" }, ...reasonEntries.map(([k, v]) =>
            h("li", {}, h("span", { text: skipReasonLabel(k) }), h("span", { class: "num", text: `${v} 次` }))))));
      }
    }
    const sm = d.shadow_management;
    if (sm) {
      const trigChip = sm.mechanical_condition_state === "TRIGGERED" || isNum(sm.condition_triggered_at_ms)
        ? h("span", {
            class: "chip bad",
            text: isNum(sm.condition_triggered_at_ms) ? `原规则已触发 · ${utc(sm.condition_triggered_at_ms)}` : "原规则已触发",
          })
        : sm.mechanical_condition_state === "DATA_GAP"
          ? h("span", { class: "chip warn", text: "网格检查存在数据缺口" })
          : h("span", { class: "chip ok", text: "原冻结规则未触发" });
      const revChip = isNum(sm.ai_revoked_at_ms)
        ? h("span", { class: "chip bad", text: `AI 建议撤销 · ${utc(sm.ai_revoked_at_ms)}` })
        : h("span", { class: "chip muted-chip", text: "AI 未建议撤销" });
      const protoChip = protocolStatusChip(sm.protocol_status);
      const actionChip = sm.shadow_action && sm.shadow_action !== "NONE"
        ? h("span", {
            class: `chip ${sm.shadow_action === "FROZEN_RULE_TRIGGERED_CONFLICT" ? "bad" : sm.shadow_action.startsWith("REVOKE") ? "warn" : "muted-chip"}`,
            text: SHADOW_ACTION_CN[sm.shadow_action] || sm.shadow_action,
          })
        : null;
      let exitChip;
      let exitNote = null;
      if (sm.exit_status === "SIMULATED_BUY_BACK_SHORT") {
        exitChip = h("span", { class: "chip warn", text: "影子退出：模拟买回短腿" });
        const cost = sm.buyback_cost_btc ?? sm.short_buyback_cost_btc ?? sm.simulated_order?.cost_btc;
        const fee = sm.buyback_fee_btc ?? sm.simulated_order?.fee_btc ?? sm.fees_btc?.buyback;
        const quoteTs = sm.buyback_quote_at_ms ?? sm.quote_at_ms ?? sm.quoted_at_ms ?? sm.simulated_order?.quoted_at_ms ?? sm.simulated_order?.recorded_at_ms;
        exitNote = `买回短腿成本 ${isNum(cost) ? `${btc(cost, 6)} BTC` : "—"}${isNum(fee) ? ` · 手续费 ${btc(fee, 6)} BTC` : ""}${isNum(quoteTs) ? ` · 报价 ${utc(quoteTs)}` : ""} · 保留保护腿至到期（影子模拟，非实盘成交）`;
      } else if (sm.exit_status === "REVOKE_NO_QUOTE") {
        exitChip = h("span", { class: "chip warn", text: "影子退出：撤销无报价" });
        exitNote = "REVOKE 时无可靠卖腿报价，仅记录撤销事实，未虚构止损收益；保留保护腿至到期。";
      } else {
        exitChip = h("span", { class: "chip muted-chip", text: "影子退出：无（继续观察）" });
      }
      holdCard.append(h("div", { class: "pa-trigger-row pa-shadow-state" }, trigChip, revChip, exitChip, protoChip, actionChip));
      if (exitNote) holdCard.append(para(exitNote, "small muted num pa-shadow-note"));
    }
    if (d.hold_reviews?.length) {
      const reviews = d.hold_reviews.slice().sort((a, b) => {
        const ta = a.reviewed_at_ms ?? a.decided_at_ms ?? a.recorded_at_ms ?? a.observed_at_ms ?? 0;
        const tb = b.reviewed_at_ms ?? b.decided_at_ms ?? b.recorded_at_ms ?? b.observed_at_ms ?? 0;
        return tb - ta;
      });
      for (const r of reviews) {
        const rawCats = Array.isArray(r.trigger_categories) ? r.trigger_categories
          : Array.isArray(r.triggers) ? r.triggers.map((t) => (typeof t === "string" ? t : t?.category)).filter(Boolean)
          : r.trigger_category ? [r.trigger_category] : [];
        const cats = [...new Set(rawCats)];
        const vm = holdVerdictMeta(r.verdict || r.status || r.action);
        const revProtoChip = r.protocol_status && r.protocol_status !== "ALIGNED"
          ? protocolStatusChip(r.protocol_status) : null;
        const unmappedChip = r.condition_id_mapping_status === "UNMAPPED_CONDITION_ID"
          ? h("span", {
              class: "chip warn",
              text: "条件 ID 未映射",
              title: Array.isArray(r.unmapped_condition_ids) && r.unmapped_condition_ids.length
                ? `未映射 ID：${r.unmapped_condition_ids.join("、")}` : null,
            })
          : null;
        const mTag = modelTag(r.model, r.reasoning_effort ?? r.configuration?.reasoning_effort);
        const revTokens = usageTotalTokens(r.usage);
        const ts = r.reviewed_at_ms ?? r.decided_at_ms ?? r.recorded_at_ms ?? r.observed_at_ms;
        const aff = formatAffectedConditions(r.affected_conditions);
        const trigView = triggerListView(r.triggers);
        const rawInput = r.input_md ?? r.prompt_md;
        const rawReply = r.raw_reply ?? r.raw ?? r.response;
        const item = h("div", { class: "pa-hold-item" },
          h("div", { class: "pa-hold-head" },
            ...cats.map((c) => h("span", { class: "chip", text: TRIGGER_CATEGORIES[c] || c })),
            h("span", { class: `chip ${vm.tone}`, text: vm.text }),
            revProtoChip,
            unmappedChip,
            mTag ? h("span", { class: "chip muted-chip", text: mTag }) : null,
            revTokens !== null ? h("span", { class: "chip muted-chip num", text: `${revTokens.toLocaleString("en-US")} tok` }) : null,
            h("span", { class: "muted small num pa-hold-ts", text: utc(ts) })),
          aff ? para(`涉及原条件：${aff}`, "small muted pa-hold-affected") : null,
          trigView,
          r.public_reply ? para(r.public_reply, "pa-hold-reply") : (r.error ? para(String(r.error), "small warn-text pa-hold-reply") : null));
        if (isValidReviewId(r.review_id) && r.status !== "FAILED") {
          const holdExportBar = renderModelInputActions({
            dossierId: d.dossier_id,
            handId: d.hand_id,
            reviewId: r.review_id,
            compact: true,
            onModelInput,
            modelInputUrl,
          });
          if (holdExportBar) item.append(holdExportBar);
        }
        if (rawInput || rawReply) {
          const rawBlocks = [];
          if (rawInput) rawBlocks.push(para("增量复核输入（input_md）", "small muted"), pre(String(rawInput)));
          if (rawReply) rawBlocks.push(para("AI 原始复核回复", "small muted"), pre(String(rawReply)));
          item.append(h("details", { class: "raw" },
            h("summary", { text: "展开增量牌面与 AI 原始复核回复" }),
            ...rawBlocks));
        }
        holdCard.append(item);
      }
    }
    left.append(holdCard);
  }

  const outCard = h("div", { class: "ev pa-outcome-card" });
  if (d.outcome) {
    const o = d.outcome;
    outCard.append(h("header", {},
      h("b", { text: "到期机械判卷" }),
      h("span", { class: `chip ${o.classification === "OFFICIAL" ? "ok" : ""}`, text: outcomeClass[o.classification] || o.classification })));
    const outRows = [
      ["官方交割指数", isNum(o.delivery) ? `${usd(o.delivery, 2)} USD` : "待发布"],
      ["Put 侧原生到期赔付", isNum(o.liability_btc?.put) ? `${btc(o.liability_btc.put, 6)} BTC` : "—"],
      ["Call 侧原生到期赔付", isNum(o.liability_btc?.call) ? `${btc(o.liability_btc.call, 6)} BTC` : "—"],
    ];
    if (isNum(o.t0_net_credit_after_entry_fee_btc)) {
      outRows.push(["锁定时费后净信用", `${btc(o.t0_net_credit_after_entry_fee_btc, 6)} BTC`]);
    }
    if (isNum(o.hold_to_expiry_shadow_net_btc)) {
      outRows.push(["持有到期影子净结果", `${o.hold_to_expiry_shadow_net_btc > 0 ? "+" : ""}${btc(o.hold_to_expiry_shadow_net_btc, 6)} BTC`]);
    }
    if (o.managed_cashflow) {
      const mc = o.managed_cashflow;
      if (mc.mode === "UNPRICED_REVOKE") {
        const unpricedCopy = mc.unpriced_reason === "settlement_price_missing"
          ? "官方交割价尚未取得，影子买回止损净结果暂不可算"
          : "REVOKE 时无可靠卖腿报价，仅记录撤销事实，未虚构止损收益";
        outRows.push(["提前买回短腿影子净结果（保留保护腿）", unpricedCopy]);
      } else if (isNum(mc.managed_net_btc)) {
        const delta = isNum(mc.delta_btc) ? mc.delta_btc
          : (isNum(o.hold_to_expiry_shadow_net_btc) ? mc.managed_net_btc - o.hold_to_expiry_shadow_net_btc : null);
        outRows.push([
          "提前买回短腿影子净结果（保留保护腿）",
          `${signedBtc(mc.managed_net_btc, 6)}${isNum(delta) ? `（相对持有到期 ${signedBtc(delta, 6)}）` : ""}`,
        ]);
      }
    }
    outCard.append(h("dl", { class: "kv pa-meta" },
      ...outRows.flatMap(([k, v]) => [h("dt", { text: k }), h("dd", { text: v })])));
  } else {
    outCard.append(h("header", {}, h("b", { text: "到期机械判卷" }), h("span", { class: "chip muted-chip", text: "待交割" })),
      para(`本局将于 ${utc(d.expiry_ms)} 到期；交割后读取 Deribit 官方交割价自动并列两侧到期赔付及持有到期影子净结果。`, "muted small"));
  }
  left.append(outCard);

  // ── Right Column: 当时冻结牌面与证据 ──
  right.append(h("div", { class: "deck-head pa-head" }, h("h3", { text: "当时冻结牌面与盘口证据" })));
  if (d.contracts?.length) {
    const cGrid = h("div", { class: "pa-contracts" });
    for (const row of d.contracts) {
      const q = row.quote || {};
      cGrid.append(h("div", { class: "ev pa-contract-card" },
        h("header", {},
          h("b", { text: `${sideName(row.side)} 候选快照` }),
          h("span", { class: "muted small num", text: `指数 ${usd(row.index_usd, 1)} USD` })),
        para(`${row.contract.short_instrument} / ${row.contract.long_instrument} · ${row.contract.quantity_btc} BTC`, "num small"),
        para(`读取时刻 ${utc(row.read_at_ms)}`, "muted small num"),
        isNum(q.net_credit_after_fee_btc) || isNum(q.visible_credit_btc)
          ? para(`可见信用 ${isNum(q.visible_credit_btc) ? btc(q.visible_credit_btc, 6) : "—"} BTC · 费后净信用 ${isNum(q.net_credit_after_fee_btc) ? btc(q.net_credit_after_fee_btc, 6) : "—"} BTC`, "muted small num")
          : null));
    }
    right.append(cGrid);
  }

  if (d.board) {
    right.append(renderStructuredBoard(d.board));
  } else if (d.ard_line) {
    right.append(renderStructuredBoard(d.ard_line));
  }

  if (d.identity) {
    const readout = h("div", { class: "reader-original" });
    right.append(
      fold("originals", "完整原始牌面与 AI 原始答复（原文对照）", [readout], () => onOriginals(readout)),
      fold("source", "技术溯源与完整性校验（SHA-256 与原始记录）", [
        h("dl", { class: "kv pa-tech-kv" },
          h("dt", { text: "卷宗与手牌标识" }), h("dd", { text: `${d.dossier_id} / ${d.hand_id}` }),
          h("dt", { text: "数据来源口径" }), h("dd", { text: `${d.source?.name || "—"}（指数点 ${d.source?.points ?? 0}，Pin 快照 ${d.source?.pin_records ?? 0}）` }),
          h("dt", { text: "牌面输入 SHA-256" }), h("dd", { text: d.identity.input_sha256 || "—" }),
          h("dt", { text: "源包数据 SHA-256" }), h("dd", { text: d.identity.source_sha256 || "—" }),
          h("dt", { text: "规则文本 SHA-256" }), h("dd", { text: d.identity.rules_sha256 || "—" }),
          h("dt", { text: "正式回复 SHA-256" }), h("dd", { text: d.decision?.raw_sha256 || "—" })),
        h("details", { class: "raw" },
          h("summary", { text: "展开底层审计 JSON" }),
          pre(JSON.stringify({
            hand_id: d.hand_id, dossier_id: d.dossier_id, schedule: d.scheduled_at_ms,
            requested_at_ms: d.requested_at_ms, lock_requested_at_ms: d.lock_requested_at_ms,
            contracts: d.contracts, source: d.source, input: d.identity, decision: d.decision,
            entry_basis: d.entry_basis, shadow_management: d.shadow_management,
            attempts: d.attempts, receipt, archive: d.archive,
          }, null, 2))),
      ], null, "pa-provenance"));
  } else {
    right.append(para("本局未冻结可读牌面。", "muted"));
  }
}
