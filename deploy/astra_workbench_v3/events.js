// Exogenous events: scheduled US macro releases (FOMC, CPI, NFP, PCE, PPI, GDP) from the
// static calendar kept in the repository. Display only: times in Beijing time, and for
// each contract the releases inside its remaining life with the time left to expiry.
import { h, isNum } from "./format.js";

const DAY = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", weekday: "short" });
const HM = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hour12: false });
const SOURCE_CN = { BLS: "美国劳工统计局", Fed: "美联储", BEA: "美国经济分析局" };

const dayText = (ms) => {
  const p = Object.fromEntries(DAY.formatToParts(new Date(ms)).map((x) => [x.type, x.value]));
  return `${p.month}/${p.day} ${p.weekday}`;
};
const hm = (ms) => HM.format(new Date(ms));
const eventName = (e) => `${e.name_cn}${e.kind === "GDP" ? ` ${e.period_cn}` : `（${e.period_cn}）`}`;
const tags = (e) => [e.sep ? "含经济预测" : null, e.tentative ? "日期暂定" : null].filter(Boolean);
const BJ_DATE = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" });
const bjDay = (ms) => Date.parse(BJ_DATE.format(new Date(ms)));  // Beijing calendar date as a UTC midnight
const until = (ms, now) => {
  const left = ms - now;
  if (left <= 0) return "已发布";
  if (left < 36e5 * 24) return `${(left / 36e5).toFixed(1)} 小时后`;
  const days = Math.round((bjDay(ms) - bjDay(now)) / 864e5);  // calendar days, so it matches the tile's date
  return days === 1 ? "明天" : `${days} 天后`;
};

function gapsText(gaps) {
  const parts = (gaps || []).map((g) => (g.undated
    ? `${g.name_cn}（${g.period_cn}）日期待${SOURCE_CN[g.source] || g.source}公布`
    : `${SOURCE_CN[g.source] || g.source}的日程只公布到 ${String(g.through).slice(5)}`));
  return parts.length ? `日历未覆盖：${parts.join("；")}。` : "";
}

/** The two-week list, grouped by Beijing date; releases inside the current contract are marked. */
export function eventBand(view, now = Date.now()) {
  const ex = view?.exogenous_events;
  const box = h("div", {});
  if (!ex || ex.error_type) {
    box.append(h("p", { class: "empty", text: "事件日历暂不可用。" }));
    return box;
  }
  const expiries = (view.candidates || []).map((c) => c?.exogenous_events?.expiry_ms).filter(isNum);
  const expiry = expiries.length ? Math.min(...expiries) : null;
  const list = (ex.events || []).filter((e) => e.at_ms > now - 36e5);
  if (isNum(expiry)) {
    const inside = list.filter((e) => e.at_ms < expiry);
    box.append(h("p", { class: "ev-sum" },
      h("span", { text: `本期合约 ${dayText(expiry).slice(0, 5)} ${hm(expiry)} 到期：` }),
      h("b", { class: inside.length ? "warn" : "", text: inside.length ? `有效期内有 ${inside.map(eventName).join("、")}` : "有效期内没有已排期的重要发布" })));
  }
  if (!list.length) {
    box.append(h("p", { class: "empty", text: "未来一个月没有已排期的重要宏观发布。" }));
  } else {
    const days = new Map();
    for (const e of list) {
      const d = dayText(e.at_ms);
      if (!days.has(d)) days.set(d, []);
      days.get(d).push(e);
    }
    // one tile per Beijing date, like a calendar; tiles inside the current contract are marked
    const grid = h("ol", { class: "ev-grid" });
    for (const [d, items] of days) {
      const inLife = isNum(expiry) && items.some((e) => e.at_ms < expiry);
      grid.append(h("li", { class: `ev-day${inLife ? " in-life" : ""}` },
        h("div", { class: "ev-date" }, h("span", { text: d }),
          h("span", { class: "ev-until", text: until((items.find((e) => e.at_ms > now) || items[items.length - 1]).at_ms, now) })),
        ...items.map((e) => h("div", { class: "ev-item" },
          h("span", { class: "ev-time", text: hm(e.at_ms) }),
          h("span", { class: "ev-name" }, eventName(e), ...tags(e).map((t) => h("span", { class: "de-tag", text: t }))))),
        inLife ? h("span", { class: "ev-life", text: "在本期合约有效期内" }) : null));
    }
    box.append(grid);
  }
  box.append(h("p", { class: "factor-note", text: `${gapsText(ex.gaps)}只列已排期的六类美国宏观发布：FOMC 利率决议、CPI、非农就业、PCE、PPI、GDP。`
    + `时间为北京时间，来自官方日程（美国劳工统计局、美联储、美国经济分析局），日历版本 ${ex.calendar_version || "—"}，每年维护一次。只提示时间，不判断影响方向。` }));
  return box;
}
