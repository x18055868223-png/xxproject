import { btc, h, isNum, usd } from "./format.js";

const states = {
  AWAITING_RESPONSE: "等待正式回复", NO_RESPONSE: "回复窗口已结束", EXPECTED: "待发牌",
  DATA_BLOCKED: "资料阻塞", MISSED: "错过采样窗口", PARSE_FAILED: "回复未通过解析",
  PROVENANCE_MISSING: "回复缺模型或作答时钟",
  REQUEST_FAILED: "请求失败", TIMEOUT: "模型超时", LATE: "回复超时到达",
  EVIDENCE_UNAVAILABLE: "证据暂不可读", TRACKING: "已关联锁定追踪",
  ARCHIVED: "归档证据可读（实时追踪已过保留期）", TRACKER_UNAVAILABLE: "追踪暂不可读",
  LOCK_BLOCKED: "锁定受阻", LINK_UNVERIFIED: "锁定回执待核对", SHADOW_LOCK_REQUESTED: "已提交锁定申请",
  OBSERVATION: "锁定追踪采样同步", CONDITION_UNKNOWN: "网格检查：资料待补齐",
  CONDITION_TRIGGERED: "网格检查：已触发破坏条件", CONDITION_NOT_TRIGGERED: "网格检查：未触发破坏条件",
  REVIEW_REQUIRED: "ARD 变化需复核", AI_HOLD: "AI 续判：继续持有观察",
  AI_REVOKE: "AI 续判：撤销原承担条件", SHADOW_ACTION: "影子管理事件",
  active: "观察中", stopped: "已停止观察", expired: "已到期", error: "追踪异常", paused: "追踪暂停",
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

const label = (status) => states[status] || status;
const sideName = (side) => side === "put" ? "Put" : side === "call" ? "Call" : "";
export const playerRef = (card) => `${card.dossier_id}/${card.hand_id}`;
export const utc = (t) => isNum(t) ? new Date(t).toISOString().slice(0, 19).replace("T", " ") + " UTC" : "—";
const utcShort = (t) => isNum(t) ? new Date(t).toISOString().slice(0, 16).replace("T", " ") + " UTC" : "—";
const para = (text, cls) => h("p", { text, class: cls || null });
const pre = (text) => h("pre", { text });

function verdictText(c) {
  return [label(c.state), sideName(c.side)].filter(Boolean).join(" / ");
}

function verdictTone(state) {
  if (state === "Accept") return "ok";
  if (state === "Reject") return "warn";
  if (state === "PARSE_FAILED" || state === "LOCK_BLOCKED" || state === "DATA_BLOCKED") return "bad";
  return "";
}

function modelTag(model, effort) {
  if (!model) return null;
  return `AI · ${model}${effort ? ` · ${effort}` : ""}`;
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

export function renderPlayerAudit(box, player, { onSelect, onTracker, onOriginals, onLogin, onRefresh, authenticated }) {
  if (!authenticated) {
    box.append(h("div", { class: "pad" }, para("登录后按期查阅每日交割卷宗、冻结牌面与 AI 牌手推理审计。", "muted"),
      h("button", { class: "btn secondary", type: "button", onclick: onLogin }, "登录")));
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

  const metaRows = [
    ["交割到期", `${utc(d.expiry_ms)}（${samples[d.sample] || d.sample}）`],
    ["发牌与作答", `发牌 ${utc(d.frozen_at_ms)} · 作答 ${utc(d.decision?.decided_at_ms)}`],
    ["牌手模型", d.model ? `${d.model}${d.reasoning_effort ? `（推理强度 ${d.reasoning_effort}）` : ""}` : "未记录"],
    ["ARD 依赖", judgment ? (judgment.depends_on_ard ? "原回复声明依赖锚相对动态（ARD）" : "仅按一阶责任路径成立，不依赖 ARD") : "—"],
  ];
  left.append(h("dl", { class: "kv pa-meta" },
    ...metaRows.flatMap(([k, v]) => [h("dt", { text: k }), h("dd", { text: v })])));

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

  // ── Left Lower: 事中持续追踪与到期判卷 ──
  left.append(h("h4", { class: "pa-sec-title", text: "事中持续追踪与到期判卷" }));

  const trackCard = h("div", { class: "ev pa-track-card" },
    h("header", {},
      h("b", { text: "同合同锁定追踪基线" }),
      d.tracking_state ? h("span", { class: `chip ${d.tracking_state === "TRACKING" ? "ok" : ""}`,
        text: [label(d.tracking_state), label(d.tracker_status)].filter(Boolean).join(" · ") }) : null));
  if (receipt) {
    const credit = receipt.economy?.net_credit_after_entry_fee_btc;
    const t0Rows = [
      ["锁定与盘口时钟", `锁定 ${utc(receipt.locked_at_ms)} · 盘口读数 ${utc(receipt.tracking_asof_ms)}`],
      ["锁定合约", `${receipt.contract?.short_instrument || "—"} / ${receipt.contract?.long_instrument || "—"} · ${receipt.contract?.quantity_btc ?? 1} BTC`],
      ["入场当时指数", isNum(receipt.market?.index_price_usd) ? `${usd(receipt.market.index_price_usd, 2)} USD` : "—"],
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
    const ruleType = condition.invalidation_rule?.type;
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
    if (d.events?.length) {
      const tbl = h("table", { class: "t compact pa-events-table" },
        h("thead", {}, h("tr", {}, h("th", { text: "事件 / 网格检查结果" }), h("th", { class: "n", text: "观测 / 记录时刻" }))),
        h("tbody", {}, ...d.events.map((ev) => h("tr", {},
          h("td", { text: label(ev.kind) }),
          h("td", { class: "n num", text: utc(ev.observed_at_ms || ev.recorded_at_ms) })))));
      condCard.append(tbl);
    }
    left.append(condCard);
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
            attempts: d.attempts, receipt, archive: d.archive,
          }, null, 2))),
      ], null, "pa-provenance"));
  } else {
    right.append(para("本局未冻结可读牌面。", "muted"));
  }
}
