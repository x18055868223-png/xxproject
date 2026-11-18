"""Offline A evidence replay. Invoke with python -m tools.astra_player_evidence_replay."""

import argparse
import concurrent.futures
from copy import deepcopy
from datetime import datetime, timezone
import difflib
import inspect
import json
import os
from pathlib import Path
import re
import threading
import time

from . import astra_player_dossier_v1 as dossier
from . import astra_player_input_v1 as player
from . import astra_player_replay_path as path_evidence


def _ms_stamp(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%H:%M:%S.%f")[:-3]


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def inspect_l2_snapshots(source, frozen):
    call_case = next(
        (case for case, card in zip(source["cases"], source["cards"]) if card["side"] == "call"),
        source["cases"][1],
    )
    short_call = player.values(call_case, "L1.contract")["short_strike_usd"]
    snapshots = []
    for case, card in zip(source["cases"], source["cards"]):
        short = player.entry(case, "L2.book.short")["values"]
        long = player.entry(case, "L2.book.long")["values"]
        quote = player.entry(case, "L2.quote")["values"]
        at = short["timestamp"]
        received = quote["received_ts_ms"]
        read = card["read_at_ms"]
        price = short["index_price"]
        if short["index_price"] != long["index_price"]:
            raise ValueError(f"inconsistent L2 index_price on {card['side']}")
        if not (max(at, long["timestamp"]) <= received <= read <= frozen):
            raise ValueError(f"L2 snapshot clock order failed on {card['side']}")
        snapshots.append({
            "candidate": card["side"],
            "price_usd": price,
            "observed_ms": at,
            "acquired_ms": received,
            "cutoff_ms": read,
            "call_short_depth_usd": round(price - short_call, 6),
            "case_id": case.get("case_id"),
            "field": "L2.book.short.values.index_price",
            "evidence_status": "OBSERVED_UNALIGNED",
        })
    return snapshots


def l2_snapshot_text(snapshots, short_call_usd=64_000):
    by_side = {row["candidate"]: row for row in snapshots}
    put, call = by_side["put"], by_side["call"]
    put_depth = put["price_usd"] - short_call_usd
    call_depth = call["price_usd"] - short_call_usd
    delta_s = abs(call["observed_ms"] - put["observed_ms"]) / 1000
    return (
        f"原候选另附 Deribit 盘口指数（与图表取样分别记录，UTC）。"
        f"Put 候选短腿盘口 {_ms_stamp(put['observed_ms'])} UTC -> {put['price_usd']:,.2f} USD，"
        f"{_ms_stamp(put['acquired_ms'])} UTC 已取得，此观测的 Call 短腿 {player.fmt(short_call_usd)} 责任区内深 {put_depth:.2f} USD；"
        f"Call 候选短腿盘口 {_ms_stamp(call['observed_ms'])} UTC -> {call['price_usd']:,.2f} USD，"
        f"{_ms_stamp(call['acquired_ms'])} UTC 已取得，此观测的 Call 短腿 {player.fmt(short_call_usd)} 责任区内深 {call_depth:.2f} USD。"
        f"证据状态：OBSERVED_UNALIGNED（同源相隔 {delta_s:.3f}s 仅计一次盘口快照证据；指数子字段无独立事件时钟，"
        f"与图表 15:59 分钟标签不可排序，禁止推断 16:00 或冻结截止时已确认在责任内或已确认在责任外）。"
    )


def inspect_spot(row, cutoff, frozen, bundle_acquired):
    flags = ("gap", "truncated", "cvd_degraded", "backfill_mixed", "window_backfill")
    fast_key, slow_key = "fast_4h", "slow_12h"
    result = {
        "grade": "missing",
        "windows": {},
        "previous_8h": None,
        "evidence_status": {
            "fast_4h": "UNKNOWN",
            "slow_12h": "UNKNOWN",
            "previous_8h": "UNKNOWN",
            "tail_5m_spot": "UNKNOWN",
        },
        "observation_ms": row.get("asof_ms"),
        "acquired_by_ms": bundle_acquired,
        "reasons": [],
    }
    if (not player.usable(row, cutoff, player.HOUR)
            or type(bundle_acquired) is not int or not row["asof_ms"] <= bundle_acquired <= frozen
            or type(row.get("available_at_ms")) is int and row["available_at_ms"] > frozen):
        result["reasons"].append("量价源时钟、取得凭据或质量未通过")
        return result
    values = row.get("values") or {}
    micro = values.get("micro_flow") or {}
    for key, hours in ((fast_key, 4), (slow_key, 12)):
        window = micro.get(key) or {}
        ends = {window[k] for k in ("end_ts_ms", "complete_ts_ms", "window_end_ms", "end_ms")
                if type(window.get(k)) is int}
        if (any(end > cutoff for end in ends)
                or (type(window.get("available_at_ms")) is int and window["available_at_ms"] > frozen)):
            result["reasons"].append(f"{hours}h 超过市场或取得截止")
            continue
        if window.get("data_ready") is not True:
            result["reasons"].append(f"{hours}h 读数未就绪")
            continue
        net = player.number(window.get("cvd_sum"))
        reported_return = player.number(window.get("momentum_return_pct"))
        if reported_return is None:
            reported_return = player.number(window.get("price_return_pct"))
        result["windows"][key] = {
            "hours": hours, "net": net, "return_pct": reported_return, "unit": window.get("cvd_unit"),
            "source": window.get("source"), "symbol": window.get("symbol"),
            "start_ms": window.get("window_start_ms"), "end_ms": next(iter(ends)) if len(ends) == 1 else None,
            "reported_end_clocks_ms": sorted(ends),
            "sample_count": window.get("bar_count"), "coverage": window.get("coverage_frac"),
            "total": player.number(window.get("total_volume_btc")),
        }
    if not any(w["net"] is not None or w["return_pct"] is not None for w in result["windows"].values()):
        return result
    result["grade"] = "reported_only"
    result["evidence_status"] = {
        "fast_4h": "OBSERVED_UNALIGNED",
        "slow_12h": "OBSERVED_UNALIGNED",
        "previous_8h": "UNKNOWN",
        "tail_5m_spot": "UNKNOWN",
    }
    if len(result["windows"]) != 2:
        return result
    fast, slow = (result["windows"][k] for k in (fast_key, slow_key))
    reasons = result["reasons"]
    if not all(fast[k] and fast[k] == slow[k] for k in ("source", "symbol")):
        reasons.append("无法核实相同来源和品种")
    if fast["unit"] != "BTC" or slow["unit"] != "BTC":
        reasons.append("无法核实统一 BTC 口径")
    if any(player.number(w["coverage"]) != 1 or (micro[k] or {}).get("bar_complete") is not True
           for k, w in result["windows"].items()) or any(
               scope.get(flag) is True for scope in (row, values, micro, micro[fast_key], micro[slow_key])
               for flag in flags):
        reasons.append("缺完整完成柱覆盖证明或存在质量缺口")
    if not (all(type(w[k]) is int for w in (fast, slow) for k in ("start_ms", "end_ms"))
            and fast["end_ms"] == slow["end_ms"]
            and all(w["end_ms"] - w["start_ms"] == w["hours"] * player.HOUR for w in (fast, slow))):
        reasons.append("无法核实实际完成时钟、共同截止和嵌套窗")
    if not all(w["net"] is not None and w["return_pct"] is not None and w["return_pct"] > -100
               for w in (fast, slow)):
        reasons.append("缺净量或有效收益")
    if not reasons:
        result["grade"] = "comparable"
        result["evidence_status"] = {
            "fast_4h": "OBSERVED_ALIGNED",
            "slow_12h": "OBSERVED_ALIGNED",
            "previous_8h": "OBSERVED_ALIGNED",
            "tail_5m_spot": "UNKNOWN",
        }
        result["previous_8h"] = {
            "method": "research_recalculation", "start_ms": slow["start_ms"], "end_ms": fast["start_ms"],
            "net_btc": slow["net"] - fast["net"],
            "return_pct": ((1 + slow["return_pct"] / 100) / (1 + fast["return_pct"] / 100) - 1) * 100,
        }
    return result


def spot_text(spot):
    text = []
    for window in spot["windows"].values():
        net = window["net"]
        net_text = "净主动量缺失" if net is None else f"报告净主动量 {net:+,.1f} {window['unit'] or '（单位未核实）'}"
        ret = window["return_pct"]
        ret_text = "价格变化缺失" if ret is None else f"报告价格变化 {ret:+.2f}%"
        text.append(f"{window['hours']}h {net_text}，{ret_text}")
    if spot["grade"] == "comparable":
        prev = spot["previous_8h"]
        text.append(f"前8小时研究重算，净主动量 {prev['net_btc']:+,.1f} BTC，复合价格变化 {prev['return_pct']:+.2f}%")
        text.append("冻结包未提供与指数末段对齐的现货主动成交及总量，也未确认成交机制")
    else:
        reasons = "、".join(spot["reasons"]) or "缺可核实窗口"
        text.append(
            "证据等级：4h 单窗 OBSERVED_UNALIGNED（缺完成柱截止与完整覆盖凭据，未满足下方“可核验的近4小时不利价流同向压力”成立前提，仅作未对齐报告值，不得升格为当前已成立反证）"
        )
        text.append("12h 单窗 OBSERVED_UNALIGNED")
        text.append(f"跨窗 12h−4h UNKNOWN（两窗不可比：{reasons}，未派生不重叠窗口）")
        text.append("末段 5m 同期现货主动流 UNKNOWN（冻结包未提供与指数末段对齐的现货主动成交及总量，也未确认成交机制）")
    return "；".join(text) + "。"


def without_derivable_cards(source, verified_by_side):
    cards = deepcopy(source["cards"])
    for card in cards:
        points = verified_by_side[card["side"]]
        if not points:
            raise ValueError("no verified index sample for side")
        card["index_usd"] = points[-1][1]
        # Before-24h summaries may have consumed the original boundary sample.
        for level in card["levels"]:
            level["before_24h"] = None
    return cards


def render_timeline(summary, time_audit, l2_snapshots=None):
    nodes = summary["nodes"]
    last = nodes[-1]
    lines = [
        f"**责任路径时间线（研究重算）** 共享市场截止 {player.stamp(time_audit['market_cutoff_ms'])}。"
        f"Deribit btc_usd 分钟标签的区间语义未证实，按标签后一整分钟仍在截止内取样。"
        f"最新合格标签 {player.stamp(last['label_ms'])}，标签到读取之间的路径未确认。"
    ]
    labels = {
        "entry": "进入短腿责任", "segment_starts_inside": "片段首点在责任内，进入时刻未知",
        "return": "返回责任外", "short_touch": "触短腿",
        "protection_touch": "触保护腿", "beyond_protection": "观察到保护腿外",
        "segment_starts_beyond_protection": "片段首点在保护腿外，穿越过程未知",
        "returns_from_beyond_protection": "离开保护腿外侧", "before_gap": "断点前末点", "after_gap": "断点后首点",
    }
    by_label = {}
    for ev in summary["events"]:
        side = ev["side"].title() + " " if ev["side"] else ""
        by_label.setdefault(ev["label_ms"], []).append(side + labels[ev["kind"]])
    for n in nodes:
        lines.append(f"- {player.stamp(n['label_ms'])} 指数 {player.fmt(n['index_usd'])} USD"
                     f" | Put {n['put_margin_usd']:+,.1f}（{n['put_state']}）"
                     f" | Call {n['call_margin_usd']:+,.1f}（{n['call_state']}）"
                     + ("｜" + "、".join(by_label[n["label_ms"]]) if n["label_ms"] in by_label else ""))
    if len(summary["segments"]) > 1:
        lines.append("时间线有采样断点；断点间未确认进入或回收顺序。")
    windows = []
    for minutes, w in summary["windows"].items():
        if "delta" in w:
            windows.append(f"{minutes}m 位移 {w['delta']:+,.1f}、振幅 {w['range_usd']:,.1f} USD")
    if windows:
        lines.append("同一末点的指数窗口：" + "；".join(windows) + "。")
    if l2_snapshots:
        by_side = {row["candidate"]: row for row in l2_snapshots}
        put, call = by_side["put"], by_side["call"]
        last_hms = datetime.fromtimestamp(last["label_ms"] / 1000, timezone.utc).strftime("%H:%M:%S")
        lines.append(
            "**异源盘口快照并列提醒（OBSERVED_UNALIGNED，与图表分钟标签不可排序，禁止遗漏或被时间线覆盖）**："
            f"Put 候选盘口 {_ms_stamp(put['observed_ms'])} UTC 所见指数 {put['price_usd']:,.2f} USD"
            f"（Call 短腿 64,000 责任内深 {put['call_short_depth_usd']:.2f} USD）；"
            f"Call 候选盘口 {_ms_stamp(call['observed_ms'])} UTC 所见指数 {call['price_usd']:,.2f} USD"
            f"（Call 短腿 64,000 责任内深 {call['call_short_depth_usd']:.2f} USD）。"
            f"冻结截止时刻的当前区内外状态为 UNKNOWN，禁止将 {last_hms} 图表末点延伸为“当前仍在责任外”。"
        )
    return "\n".join(lines)


class FactEdits:
    """Replace unique, explicitly recognized fact fragments and record byte hashes."""

    def __init__(self, original):
        self.original = original
        self.spans = []

    def add(self, label, before, after):
        if before == after:
            return
        if not before or self.original.count(before) != 1:
            raise ValueError(f"ambiguous fact fragment: {label}")
        start = self.original.index(before)
        self.spans.append((start, start + len(before), label, before, after))

    def build(self):
        end = 0
        parts, manifest = [], []
        for start, stop, label, before, after in sorted(self.spans):
            if start < end:
                raise ValueError("overlapping fact fragments")
            parts.append(self.original[end:start])
            offset = sum(map(len, parts))
            self_start, self_end = offset, offset + len(after)
            manifest.append({"reason": label, "old_span": [start, stop], "new_span": [self_start, self_end],
                             "old_sha256": dossier.sha(before.encode()), "new_sha256": dossier.sha(after.encode())})
            parts.append(after)
            end = stop
        parts.append(self.original[end:])
        changed = "".join(parts)
        restored = changed
        for span, row in zip(reversed(sorted(self.spans)), reversed(manifest)):
            start, stop = row["new_span"]
            restored = restored[:start] + span[3] + restored[stop:]
        if restored != self.original:
            raise ValueError("changed message cannot be restored to EXACT")
        return changed, manifest


def declared_line(text, prefix):
    lines = [line for line in text.splitlines() if line.startswith(prefix)]
    if len(lines) != 1:
        raise ValueError(f"unsupported EXACT evidence layout: {prefix}")
    return lines[0]


def replace_fact_edits(original, regenerated, contracts, timeline, spot, points_by_side):
    edits = FactEdits(original)
    for prefix, guards in (
        ("1. **责任。** ", ("已在责任内或行走方向之外的位置不作为入区前缓冲。",)),
        ("2. **空间背景", ("不证明本侧义务方向。",)),
        ("**锚相对动态ARD", ("覆盖数不是独立结构更新次数，也不证明吸附、定价完成或因果。",
                          "超过40分钟的旧锚、缺价或缺口不补齐。")),
    ):
        old, new = declared_line(original, prefix), declared_line(regenerated, prefix)
        if any(old.count(guard) != new.count(guard) for guard in guards):
            raise ValueError("evidence layout would change an interpretation guard")
        if old.endswith("不证明吸附。") and not new.endswith("不证明吸附。"):
            new += "不证明吸附。"
        if prefix.startswith("1."):
            new = new.replace(prefix, prefix + "以下指数位置用合格样本末点，末点至原读取时的价格待确认。", 1)
        if prefix.startswith("2."):
            new = (
                new.replace("现价在其下方", "合格图表末点在其下方")
                .replace("在现价上方", "在合格图表末点上方")
                .replace("在现价下方", "在合格图表末点下方")
            )
        edits.add("按冻结指数重算责任、空间或ARD读数", old, new)

    # The first research cohort uses this frozen formatting. Unrecognized mixed
    # facts/instructions require a reviewed mapping before they can enter a pair.
    for side, contract in contracts.items():
        prefix = f"- **短腿{player.fmt(contract['short_strike_usd'])}**"
        old, new = declared_line(original, prefix), declared_line(regenerated, prefix)
        new = new.replace("读取时指数", "合格末点指数")
        for guard in ("仅与本站路径并读。", "仅一处，不另算一站"):
            if old.count(guard) != new.count(guard):
                raise ValueError("short path interpretation guard differs")
        if "这是端点比较，不表示此后单调远离或压力解除。" in old:
            closest = min(points_by_side[side], key=lambda p: (abs(p[1] - contract["short_strike_usd"]), -p[0]))
            new += (f"已覆盖片段最接近标签 {player.stamp(closest[0])} 指数 {player.fmt(closest[1])} USD。"
                    "这是端点比较，不表示此后单调远离或压力解除。")
        edits.add("按冻结指数重算短腿历史路径", old, new)

    station_level = contracts["call"]["short_strike_usd"]
    if not (contracts["put"]["short_strike_usd"] < points_by_side["put"][-1][1] < station_level):
        raise ValueError("unsupported station shift for this frozen cohort")
    station = declared_line(original, f"- **{player.fmt(station_level)}**：")
    prefix, separator, rest = station.partition("。")
    parts = rest.split("。")
    if (not separator or len(parts) != 5 or not parts[0].startswith("穿过")
            or not parts[2].startswith("读取时在") or not parts[3].endswith("仅与本站路径并读")):
        raise ValueError("unsupported mixed station fragment")
    edits.add("旧站位保留属性和成交，改正空间与历史覆盖", station,
              prefix.replace("**：", "（合格末点处于该位下方，已在入区前路径之外）**：", 1) + "。"
              "末个合格样本在该位下方；原站点全窗穿越摘要的边缘样本未获核实。" + parts[3] + "。")

    old = "现价已到达或进入短腿责任，不再将现价与短腿间的位置称为入区前缓冲。"
    edits.add("校正 Call 的读取状态并保留入区规则", old,
              "末个合格样本在短腿责任外。若价格到达或进入短腿责任，不再将现价与短腿间的位置称为入区前缓冲。")
    fast_line = r"近5分钟指数从[^\r\n；]+ USD(?=；仅辅助定位，不代替上述责任路径。)"
    matches = list(re.finditer(fast_line, original))
    if len(matches) != 2:
        raise ValueError("unsupported five-minute evidence fragments")
    for match in matches:
        start, end = match.span()
        edits.spans.append((start, end, "五分钟原始端点改用共享合格窗口", match[0],
                            "五分钟指数端点及双侧余量见共用研究时间线"))

    old = declared_line(original, "5. **现货量价与主动流。** ")
    factual = old.split("。区分三点：", 1)[0]
    if factual == old or "前8小时不重叠窗口" not in factual:
        raise ValueError("unsupported mixed spot fragment")
    heading = "5. **现货量价与主动流。** "
    edits.add("保留原量价报告并按来源与时钟限制派生", factual[len(heading):] + "。", spot)
    marker = "路径窗为读取前24小时，指数按一分钟采样"
    edits.add("插入共享研究重算时间线", marker, timeline + "\n\n" + marker)
    return edits.build()


def replay(input_dir):
    input_dir = Path(input_dir)
    raw = {name: (input_dir / name).read_bytes() for name in
           ("request.json", "attempt.json", "input.md", "source.json", "identity.json")}
    request, attempt, source, meta = [json.loads(raw[key]) for key in
                                      ("request.json", "attempt.json", "source.json", "identity.json")]
    original = raw["input.md"].decode("utf-8")
    hashes = {name: dossier.sha(content) for name, content in raw.items()}
    if (hashes["request.json"] != attempt.get("request_sha256") or hashes["source.json"] != meta.get("source_sha256")
            or hashes["input.md"] != meta.get("input_sha256") or request.get("packet_hash") != hashes["input.md"]
            or request.get("fidelity") != "EXACT" or request.get("stage") != "initial"
            or [m["role"] for m in request["messages"]] != ["system", "user"]
            or not request["messages"][1]["content"].endswith(original)):
        raise ValueError("EXACT request or frozen source chain failed verification")

    def render(cards, path):
        return player.build_input(*source["cases"], *cards, index_path=path, ard_history=source["history"],
                                  ard_band_snapshot=source.get("band_snapshot"))

    minutes = source["index_path"]
    if render(source["cards"], minutes).splitlines() != original.splitlines():
        raise ValueError("baseline renderer differs from archived full evidence")
    contracts = {card["side"]: player.bind(case, card, card["side"])
                 for case, card in zip(source["cases"], source["cards"])}
    reads = {card["side"]: card["read_at_ms"] for card in source["cards"]}
    acquired, frozen = meta["source_bundle_available_at_ms"], meta["hand_frozen_at_ms"]
    if (list(reads.values()) != meta["source_observed_at_ms"] or source["received_at_ms"] != acquired
            or minutes["expiry_ms"] != contracts["put"]["expiry_ms"]):
        raise ValueError("inconsistent frozen market or availability clocks")
    cutoff = min(reads.values())
    points, clock = path_evidence.frozen_points(minutes, cutoff, frozen, acquired)
    points_by_side, side_clocks = {}, {}
    for side, limit in reads.items():
        points_by_side[side], side_clocks[side] = path_evidence.frozen_points(minutes, limit, frozen, acquired)
    summary = path_evidence.summarize(points, contracts, cutoff)
    safe_cards = without_derivable_cards(source, points_by_side)
    safe_text = render(safe_cards, {**minutes, "points": points})
    flow = inspect_spot(player.entry(source["cases"][0], "L5.volume_price"), cutoff, frozen, acquired)
    # Limit edits to the evidence block. Replaying the outer frame is forbidden.
    head, body = original.split("## 本局牌面\n", 1)
    board, tail = body.split("## 读完后另写", 1)
    changed, edits = replace_fact_edits(board, safe_text, contracts, render_timeline(summary, clock), spot_text(flow),
                                        {side: [(t, p) for t, p in pp if reads[side] - player.DAY < t]
                                         for side, pp in points_by_side.items()})
    replayed = head + "## 本局牌面\n" + changed + "## 读完后另写" + tail
    enhanced = deepcopy(request)
    enhanced["messages"][1]["content"] = request["messages"][1]["content"][:-len(original)] + replayed
    # Original request time is historical provenance, never an experiment send time.
    proof = {
        "scope": "historical_asof", "hand_id": attempt["hand_id"], "source_hashes": hashes,
        "baseline_revision": "f3911c06c7e5829e5101397d66814f996b541786",
        "original_messages_sha256": dossier.sha(dossier.encode(request["messages"])),
        "enhanced_messages_sha256": dossier.sha(dossier.encode(enhanced["messages"])),
        "facts": {"method": "research_recalculation", "shared_clock": clock, "side_clocks": side_clocks,
                  "index": summary, "spot": flow},
        "edit_offsets": "Unicode code-point offsets in the exact user evidence block, excluding its headings",
        "edits": edits,
    }
    if any((input_dir / name).read_bytes() != raw[name] for name in raw):
        raise ValueError("frozen source changed during replay")
    return raw["request.json"], enhanced, proof


def _board_parts(text):
    head, body = text.split("## 本局牌面\n", 1)
    board, tail = body.split("## 读完后另写", 1)
    return head, board, tail


def prepare_arms(input_dir):
    input_dir = Path(input_dir)
    raw, enhanced, proof = replay(input_dir)
    original = json.loads(raw)
    source = json.loads((input_dir / "source.json").read_bytes())
    frozen = proof["facts"]["shared_clock"]["freeze_ms"]
    snapshots = inspect_l2_snapshots(source, frozen)
    call_case = next(
        (case for case, card in zip(source["cases"], source["cards"]) if card["side"] == "call"),
        source["cases"][1],
    )
    short_call = player.values(call_case, "L1.contract")["short_strike_usd"]
    books = l2_snapshot_text(snapshots, short_call) + "\n\n"

    head, rendered, tail = _board_parts(enhanced["messages"][1]["content"])
    start = rendered.index("**责任路径时间线（研究重算）**")
    end = rendered.index("路径窗为读取前24小时", start)
    base = rendered[:start] + rendered[end:]
    base = base.replace(
        "1. **责任。** 以下指数位置用合格样本末点，末点至原读取时的价格待确认。",
        "1. **责任。** 以下图表位置取最新合格分钟标签（非确认的冻结现价；冻结截止当前状态为 UNKNOWN，须与下方候选盘口快照并列读取，禁止单凭任一来源断言当前已确认在责任外或责任内）。",
        1,
    )
    base = base.replace("2. **空间背景", books + "2. **空间背景", 1)

    fast = proof["facts"]["index"]["windows"]["5"]
    if base.count("五分钟指数端点及双侧余量见共用研究时间线") != 2:
        raise ValueError("unexpected five-minute placeholder count")
    fast_text = (
        f"五分钟合格图表标签 {_ms_stamp(fast['start_ms'])} 至 {_ms_stamp(fast['end_ms'])} UTC，"
        f"指数 {fast['start_price']:,.3f} → {fast['end_price']:,.3f} USD"
    )
    base = base.replace("五分钟指数端点及双侧余量见共用研究时间线", fast_text)

    returns = [e for e in proof["facts"]["index"]["events"] if e["kind"] == "return" and e["side"] == "call"]
    last_return = returns[-1]
    by_side = {row["candidate"]: row for row in snapshots}
    put_snap, call_snap = by_side["put"], by_side["call"]
    target_call_state = "末个合格样本在短腿责任外。若价格到达或进入短腿责任"
    if base.count(target_call_state) != 1:
        raise ValueError("unexpected Call state fragment count")
    base = base.replace(
        target_call_state,
        f"图表 {_ms_stamp(last_return['label_ms'])} UTC 标签记录 Call 返回责任外，"
        f"截至末个合格图表标签未确认再次进入；同窗异源盘口快照（{_ms_stamp(put_snap['observed_ms'])} UTC -> {put_snap['price_usd']:,.2f} USD、"
        f"{_ms_stamp(call_snap['observed_ms'])} UTC -> {call_snap['price_usd']:,.2f} USD，OBSERVED_UNALIGNED）所见 Call 短腿责任内深 "
        f"{put_snap['call_short_depth_usd']:.2f} 与 {call_snap['call_short_depth_usd']:.2f} USD，须与图表并列审查（不可排序，见第1节后盘口快照说明）。"
        "若价格到达或进入短腿责任",
        1,
    )

    timeline_a2 = render_timeline(
        proof["facts"]["index"],
        proof["facts"]["shared_clock"],
        l2_snapshots=snapshots,
    ) + "\n\n"
    rich = base.replace("路径窗为读取前24小时", timeline_a2 + "路径窗为读取前24小时", 1)
    if rich.replace(timeline_a2, "", 1) != base:
        raise ValueError("A1 -> A2 diff is not strictly isolated to timeline_a2")

    arms = {}
    arm_hashes = {}
    for arm, board_text in (("A0", None), ("A1", base), ("A2", rich)):
        msgs = [dict(m) for m in (original["messages"] if arm == "A0" else enhanced["messages"])]
        if board_text is not None:
            msgs[1]["content"] = head + "## 本局牌面\n" + board_text + "## 读完后另写" + tail
        orig_head, _, orig_tail = _board_parts(original["messages"][1]["content"])
        curr_head, _, curr_tail = _board_parts(msgs[1]["content"])
        if (curr_head, curr_tail) != (orig_head, orig_tail) or msgs[0] != original["messages"][0]:
            raise ValueError(f"outer frame changed on {arm}")
        payload = {"model": original["model"], "messages": msgs, **original["parameters"]}
        arms[arm] = payload
        arm_hashes[arm] = {
            "body_sha256": dossier.sha(dossier.encode(payload)),
            "messages_sha256": dossier.sha(dossier.encode(msgs)),
        }

    proof = deepcopy(proof)
    proof.update({
        "case": "B",
        "chart_timestamp_status": "UNRESOLVED",
        "quote_snapshots": snapshots,
        "arm_hashes": arm_hashes,
    })
    return arms, proof


def _strike_pattern(value):
    formatted = f"{int(round(float(value))):,}"
    return formatted.replace(",", ",?")


def _clause_restricts_beyond_protection(text, side, short_strike, long_strike):
    if not text:
        return False
    short_re = _strike_pattern(short_strike)
    long_re = _strike_pattern(long_strike)
    if side == "put":
        if re.search(rf"(?:高于|大于|>)\s*{long_re}", text):
            return True
        if re.search(rf"{long_re}[^。；\n]*边界触及不计入", text):
            return True
    else:
        if re.search(rf"(?:低于|小于|<)\s*{long_re}", text):
            return True
        if re.search(rf"{long_re}[^。；\n]*边界触及不计入", text):
            return True

    interval_re = (
        rf"(?:{long_re}\s*(?:至|到|-|—|～|~)\s*{short_re}|"
        rf"{short_re}\s*(?:至|到|-|—|～|~)\s*{long_re})"
    )
    has_interval_entry = False
    for clause in re.split(r"[。；\n]", text):
        if re.search(rf"(?:未|不|没有|从未)[^，,]*进入\s*{interval_re}", clause):
            continue
        if re.search(rf"(?:进入|落入)\s*{interval_re}", clause):
            has_interval_entry = True
    if not has_interval_entry:
        return False

    if side == "put":
        one_sided_def = re.search(
            rf"(?:定义为|即为|是指)[^。；\n]*(?:低于|小于|<)\s*{short_re}(?![^。；\n]*{long_re})",
            text,
        )
    else:
        one_sided_def = re.search(
            rf"(?:定义为|即为|是指)[^。；\n]*(?:高于|大于|>)\s*{short_re}(?![^。；\n]*{long_re})",
            text,
        )
    includes_beyond = re.search(r"越过保护腿|保护腿外侧?(?:同样|亦|也)?(?:计入|触发)", text)
    return not (one_sided_def or includes_beyond)


def _clause_has_one_sided_trigger(text, side, short_strike, long_strike):
    if not text:
        return False
    short_re = _strike_pattern(short_strike)
    long_re = _strike_pattern(long_strike)
    cmp_re = r"(?:低于|小于|<|跌破)" if side == "put" else r"(?:高于|大于|>|突破)"
    for clause in re.split(r"[。；\n]", text):
        if re.search(rf"{cmp_re}\s*{short_re}", clause) and not re.search(rf"{long_re}", clause):
            return True
    return False


def audit_reply_semantics(reply_text, parsed_reply, contracts, *, arm=None):
    parsed_reply = parsed_reply or {}
    if arm is None:
        arm = parsed_reply.get("arm")
    decision = parsed_reply.get("decision") or {}
    plan = parsed_reply.get("condition_plan") or {}
    side = (decision.get("side") or "put").lower()
    if isinstance(contracts, dict) and side in contracts:
        contract = contracts[side]
    elif isinstance(contracts, dict) and "short_strike_usd" in contracts:
        contract = contracts
    else:
        contract = {"side": side, "short_strike_usd": 63_000 if side == "put" else 64_000,
                    "long_strike_usd": 61_000 if side == "put" else 66_000}

    short_strike = contract["short_strike_usd"]
    long_strike = contract["long_strike_usd"]
    c_text = plan.get("accepted_conditions") or ""
    m_text = plan.get("confirmation_excerpt") or ""

    c_restricts = _clause_restricts_beyond_protection(c_text, side, short_strike, long_strike)
    m_restricts = _clause_restricts_beyond_protection(m_text, side, short_strike, long_strike)
    c_one_sided = _clause_has_one_sided_trigger(c_text, side, short_strike, long_strike)
    m_one_sided = _clause_has_one_sided_trigger(m_text, side, short_strike, long_strike)

    cm_internal_conflict = bool(
        (c_restricts and m_one_sided and not m_restricts)
        or (m_restricts and c_one_sided and not c_restricts)
    )
    m_excludes_beyond_protection = bool(m_restricts or (c_restricts and not m_one_sided))

    c_degenerates_to_m = bool(
        re.search(
            r"(?:只有|仅当|唯有)[^。；\n]*(?:严格(?:低于|高于|越过|进入)|new_entry|entry_not_returned)"
            r"[^。；\n]*才(?:构成|算|视作)?(?:破坏|触发)",
            c_text,
        )
        or re.search(r"未严格进入则仍继续|不提前算入破坏", c_text)
        or (
            re.search(r"即触发\s*(?:new_entry|entry_not_returned)\s*破坏", c_text)
            and not re.search(r"事中事件门|安全余量实质消耗|复核条件", c_text)
        )
    )

    l2_in_zone_cited = bool(
        re.search(r"64,?101\.86|64,?093\.32|101\.86|93\.32", reply_text or "")
    )

    chart_endpoint_called_current = bool(
        arm in {"A1", "A2"}
        and re.search(
            r"当前指数\s*63,?880\.3|现价\s*63,?880\.3|当前指数仍距短腿\s*880\.3|指数当前在责任区外|"
            r"当前仍留在短腿下方责任外|当前仍在责任外|当前仍在短腿上方",
            reply_text or "",
        )
    )

    unverified_4h_elevated = False
    if arm in {"A1", "A2"}:
        for clause in re.split(r"[。；\n]", reply_text or ""):
            if re.search(r"未满足|并非|不是|不构成|未确认|若存在", clause):
                continue
            if re.search(
                r"(?:四小时|4\s*小时|4h)[^。；\n]*(?:可核验[^。；\n]*当前已成立的反证|构成当前已成立的[^。；\n]*反证)|"
                r"承受已成立的(?:四小时|4\s*小时|4h)[^。；\n]*反证",
                clause,
            ):
                unverified_4h_elevated = True
                break

    has_put_distance = bool(re.search(r"880\.3|1,?044", reply_text or ""))
    has_zero_buffer = bool(
        re.search(
            r"没有已列主要位置|无中间结构缓冲|不存在可确认的缓冲结构|没有任何已证实的结构缓冲|"
            r"不能把\s*64,?000\s*当作可靠阻断|不构成可靠阻断",
            reply_text or "",
        )
    )
    put_buffer_distinction = bool(has_put_distance and has_zero_buffer)

    return {
        "m_excludes_beyond_protection": m_excludes_beyond_protection,
        "cm_internal_conflict": cm_internal_conflict,
        "c_degenerates_to_m": c_degenerates_to_m,
        "program_m_triggers_beyond_protection": True,
        "prose_vs_program_mismatch": bool(m_excludes_beyond_protection or cm_internal_conflict),
        "l2_in_zone_cited": l2_in_zone_cited,
        "chart_endpoint_called_current": chart_endpoint_called_current,
        "unverified_4h_elevated": unverified_4h_elevated,
        "Put_buffer_distinction": put_buffer_distinction,
        "put_buffer_distinction": put_buffer_distinction,
    }


def _iter_manifest_slots(manifest):
    for idx, item in enumerate(manifest["order"], 1):
        if isinstance(item, (tuple, list)) and len(item) == 2:
            yield int(item[0]), str(item[1])
        elif isinstance(item, dict):
            yield int(item.get("number", idx)), str(item["arm"])
        else:
            yield idx, str(item)


def _resolve_arm_body(manifest, arm):
    group = (manifest.get("groups") or {}).get(arm) or {}
    if "body_bytes" in group:
        raw = group["body_bytes"]
        body_bytes = raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
    elif "body" in group:
        raw = group["body"]
        if isinstance(raw, bytes):
            body_bytes = raw
        elif isinstance(raw, str):
            body_bytes = raw.encode("utf-8")
        else:
            body_bytes = dossier.encode(raw)
    elif "path" in group and Path(group["path"]).exists():
        body_bytes = Path(group["path"]).read_bytes()
    else:
        body_bytes = b""
    body_sha256 = group.get("body_sha256") or dossier.sha(body_bytes)
    return body_bytes, body_sha256


def _append_ledger_line(ledger_path, lock, record):
    try:
        line = json.dumps(record, ensure_ascii=True, sort_keys=True, default=str) + "\n"
    except BaseException as exc:
        fallback = {
            "event": record.get("event", "TERMINAL_RECEIPT"),
            "trial_id": record.get("trial_id"),
            "run_id": record.get("run_id"),
            "number": record.get("number"),
            "arm": record.get("arm"),
            "body_sha256": record.get("body_sha256"),
            "status": "WORKER_EXCEPTION",
            "billing_status": "PROVIDER_BILLING_UNKNOWN",
            "error_type": type(exc).__name__,
            "error_message": "unserializable_record",
        }
        line = json.dumps(fallback, ensure_ascii=True, sort_keys=True) + "\n"
    data = line.encode("utf-8")
    with lock:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with ledger_path.open("ab") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())


def _call_send_fn(send_fn, slot_number, arm, body_bytes):
    try:
        sig = inspect.signature(send_fn)
        params = [
            p for p in sig.parameters.values()
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        has_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in sig.parameters.values())
        if not has_varargs and len(params) == 2:
            return send_fn(slot_number, arm)
    except (ValueError, TypeError):
        pass
    return send_fn(slot_number, arm, body_bytes)


def execute_trial_plan(run_id, manifest, send_fn, ledger_path, *, max_workers=3, stagger_seconds=0.0):
    ledger_path = Path(ledger_path)
    if ledger_path.exists():
        for raw_line in ledger_path.read_bytes().splitlines():
            if not raw_line.strip():
                continue
            try:
                existing = json.loads(raw_line)
            except ValueError:
                continue
            if existing.get("run_id") == run_id:
                raise FileExistsError("run_id already exists in ledger; automatic retry or overwrite is forbidden")

    lock = threading.Lock()
    slots = list(_iter_manifest_slots(manifest))

    def _worker(slot_number, arm, body_bytes, body_sha256, trial_id, dispatched_at_utc):
        t0 = time.monotonic()
        try:
            raw_res = _call_send_fn(send_fn, slot_number, arm, body_bytes)
            if not isinstance(raw_res, dict):
                raise TypeError(f"send_fn must return dict, got {type(raw_res).__name__}")
            json.dumps(raw_res, ensure_ascii=True)
            status = raw_res.get("status") or ("RECEIVED" if raw_res.get("text", True) else "EMPTY_VISIBLE_REPLY")
            if status == "RECEIVED":
                billing = raw_res.get("billing_status") or (
                    "USAGE_RECORDED" if raw_res.get("usage") else "RECEIVED_NO_USAGE"
                )
            else:
                billing = raw_res.get("billing_status") or "PROVIDER_BILLING_UNKNOWN"
            record = {
                **raw_res,
                "event": "TERMINAL_RECEIPT",
                "trial_id": trial_id,
                "run_id": run_id,
                "number": slot_number,
                "arm": arm,
                "body_sha256": body_sha256,
                "requested_at_utc": raw_res.get("requested_at_utc", dispatched_at_utc),
                "received_at_utc": raw_res.get("received_at_utc", _utc_now_iso()),
                "latency_seconds": raw_res.get("latency_seconds", round(time.monotonic() - t0, 3)),
                "status": status,
                "billing_status": billing,
            }
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            record = {
                "event": "TERMINAL_RECEIPT",
                "trial_id": trial_id,
                "run_id": run_id,
                "number": slot_number,
                "arm": arm,
                "body_sha256": body_sha256,
                "requested_at_utc": dispatched_at_utc,
                "received_at_utc": _utc_now_iso(),
                "latency_seconds": round(time.monotonic() - t0, 3),
                "status": "WORKER_EXCEPTION",
                "billing_status": "PROVIDER_BILLING_UNKNOWN",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            }
        _append_ledger_line(ledger_path, lock, record)
        return record

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        pending = []
        for idx, (slot_number, arm) in enumerate(slots):
            body_bytes, body_sha256 = _resolve_arm_body(manifest, arm)
            trial_id = f"{run_id}:{slot_number:02d}:{arm}"
            dispatched_at_utc = _utc_now_iso()
            intent = {
                "event": "DISPATCH_INTENT",
                "trial_id": trial_id,
                "run_id": run_id,
                "number": slot_number,
                "arm": arm,
                "body_sha256": body_sha256,
                "dispatched_at_utc": dispatched_at_utc,
                "status": "DISPATCHED_PENDING",
                "billing_status": "PROVIDER_BILLING_UNKNOWN",
            }
            _append_ledger_line(ledger_path, lock, intent)
            pending.append(
                pool.submit(_worker, slot_number, arm, body_bytes, body_sha256, trial_id, dispatched_at_utc)
            )
            if stagger_seconds and idx + 1 < len(slots):
                time.sleep(stagger_seconds)

        for future in concurrent.futures.as_completed(pending):
            try:
                future.result()
            except BaseException:
                pass

    return summarize_trial_ledger(ledger_path, manifest, run_id=run_id)


def summarize_trial_ledger(ledger_path, manifest, *, run_id=None):
    ledger_path = Path(ledger_path)
    intents = {}
    terminals = {}
    if ledger_path.exists():
        for raw_line in ledger_path.read_bytes().splitlines():
            if not raw_line.strip():
                continue
            row = json.loads(raw_line)
            if run_id is not None and row.get("run_id") != run_id:
                continue
            key = (row.get("number"), row.get("arm"))
            if row.get("status") == "DISPATCHED_PENDING" or row.get("event") == "DISPATCH_INTENT":
                intents[key] = row
            else:
                terminals[key] = row

    slots_out = []
    for slot_number, arm in _iter_manifest_slots(manifest):
        key = (slot_number, arm)
        _, body_sha256 = _resolve_arm_body(manifest, arm)
        if key in terminals:
            slots_out.append(terminals[key])
        elif key in intents:
            intent = intents[key]
            slots_out.append({
                **intent,
                "status": "DISPATCHED_RECEIPT_LOST",
                "billing_status": "PROVIDER_BILLING_UNKNOWN",
            })
        else:
            slots_out.append({
                "number": slot_number,
                "arm": arm,
                "body_sha256": body_sha256,
                "status": "NOT_DISPATCHED",
                "billing_status": "NOT_BILLED",
            })

    return {
        "slots": slots_out,
        "by_number": {row["number"]: row for row in slots_out},
        "counts": {
            "total": len(slots_out),
            "received": sum(r["status"] == "RECEIVED" for r in slots_out),
            "worker_exception": sum(r["status"] == "WORKER_EXCEPTION" for r in slots_out),
            "receipt_lost": sum(r["status"] == "DISPATCHED_RECEIPT_LOST" for r in slots_out),
            "billing_unknown": sum(r.get("billing_status") == "PROVIDER_BILLING_UNKNOWN" for r in slots_out),
        },
    }


def unified_board_diff(before_msg, after_msg, fromfile, tofile):
    return "".join(
        difflib.unified_diff(
            before_msg.splitlines(keepends=True),
            after_msg.splitlines(keepends=True),
            fromfile=fromfile,
            tofile=tofile,
        )
    )


def evaluate_round1_replies(parsed_dir, input_dir):
    parsed_dir = Path(parsed_dir)
    input_dir = Path(input_dir)
    source = json.loads((input_dir / "source.json").read_bytes())
    contracts = {
        card["side"]: player.bind(case, card, card["side"])
        for case, card in zip(source["cases"], source["cards"])
    }
    rows = []
    for entry in sorted(parsed_dir.iterdir()):
        if not entry.is_dir():
            continue
        result_path = entry / "result.json"
        if not result_path.exists():
            continue
        result = json.loads(result_path.read_bytes())
        arm = result.get("arm")
        row = {
            "slot_dir": entry.name,
            "number": result.get("number"),
            "arm": arm,
            "status": result.get("status"),
            "protocol": result.get("protocol"),
            "decision_action": (result.get("decision") or {}).get("action"),
            "decision_side": (result.get("decision") or {}).get("side"),
            "handoff_mechanical_status": (result.get("handoff") or {}).get("status"),
            "mechanical_fixture_status": ((result.get("handoff") or {}).get("mechanical_fixture") or {}).get("status"),
            "beyond_protection_triggered": (
                ((result.get("handoff") or {}).get("beyond_protection_fixture") or {}).get("status") == "triggered"
            ),
            "beyond_protection_zone": (
                ((result.get("handoff") or {}).get("beyond_protection_fixture") or {}).get("current_zone")
            ),
            "production_decision_unchanged": True,
            "production_mechanical_trigger_unchanged": True,
        }
        reply_path = entry / "visible-reply.md"
        if result.get("protocol") == "VALID" and reply_path.exists():
            reply_text = reply_path.read_text(encoding="utf-8")
            sem = audit_reply_semantics(reply_text, result, contracts, arm=arm)
            scm_failed = bool(
                sem["m_excludes_beyond_protection"]
                or sem["cm_internal_conflict"]
                or sem["c_degenerates_to_m"]
            )
            ev_failed = bool(
                arm in {"A1", "A2"}
                and (
                    not sem["l2_in_zone_cited"]
                    or sem["chart_endpoint_called_current"]
                    or sem["unverified_4h_elevated"]
                )
            )
            row.update({
                "scm_semantic_status": "FAILED_SEMANTIC" if scm_failed else "PASSED_SEMANTIC",
                "evidence_attention_status": "FAILED_EVIDENCE" if ev_failed else "PASSED_EVIDENCE",
                "semantics": sem,
            })
        else:
            row.update({
                "scm_semantic_status": "NOT_AVAILABLE",
                "evidence_attention_status": "NOT_AVAILABLE",
                "semantics": None,
            })
        rows.append(row)

    return {
        "schema": "astra.ae.cm_semantic_evaluation.v1",
        "production_side_selection_modified": False,
        "production_mechanical_trigger_modified": False,
        "rows": rows,
    }


def render_cm_evaluation_md(eval_report):
    lines = [
        "# Round-1 C/M 与证据语义分离评测报告",
        "",
        f"- Schema: `{eval_report['schema']}`",
        f"- 生产择侧逻辑是否改动: `{eval_report['production_side_selection_modified']}`",
        f"- 生产机械触发规则是否改动: `{eval_report['production_mechanical_trigger_modified']}`",
        "",
        "| 槽位 | 组别 | 传输状态 | 原始裁决 | 机械续判 / 保护腿外测试 | C/M 语义评测 | 证据注意力评测 | 关键语义命中 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in eval_report["rows"]:
        decision = (
            f"{row['decision_action']} / {row['decision_side']}"
            if row["decision_action"]
            else "N/A"
        )
        mech = (
            f"{row['handoff_mechanical_status']} (`{row['beyond_protection_zone']}` triggered={row['beyond_protection_triggered']})"
            if row["handoff_mechanical_status"]
            else "N/A"
        )
        sem = row["semantics"]
        if sem is None:
            flags = "N/A"
        else:
            flags = ", ".join(
                name
                for name in (
                    "m_excludes_beyond_protection",
                    "cm_internal_conflict",
                    "c_degenerates_to_m",
                    "prose_vs_program_mismatch",
                    "chart_endpoint_called_current",
                    "unverified_4h_elevated",
                )
                if sem.get(name)
            )
            if not sem.get("l2_in_zone_cited"):
                flags = (flags + ", " if flags else "") + "l2_in_zone_omitted"
            if not flags:
                flags = "CLEAN"
        lines.append(
            f"| {row['slot_dir']} | {row['arm']} | {row['status']} | {decision} | {mech} | "
            f"**{row['scm_semantic_status']}** | **{row['evidence_attention_status']}** | `{flags}` |"
        )
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--parsed-dir", type=Path, default=None)
    args = parser.parse_args()
    request, enhanced, proof = replay(args.input_dir)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "A-original.request.json").write_bytes(request)
    (args.output_dir / "A-enhanced.research.json").write_bytes(dossier.encode({
        "fidelity": "RESEARCH_DERIVED", "sent": False, "model": enhanced["model"],
        "parameters": enhanced["parameters"], "messages": enhanced["messages"],
    }))
    (args.output_dir / "replay-evidence.json").write_bytes(dossier.encode(proof))
    for filename, messages in (("A-original.md", json.loads(request)["messages"]),
                               ("A-enhanced.md", enhanced["messages"])):
        content = "\n\n".join("【" + m["role"].upper() + "】\n" + m["content"] for m in messages)
        (args.output_dir / filename).write_bytes(content.encode("utf-8"))

    arms, arm_proof = prepare_arms(args.input_dir)
    for arm_name, payload in arms.items():
        (args.output_dir / f"{arm_name}.request.json").write_bytes(dossier.encode(payload))
        arm_md = "\n\n".join("【" + m["role"].upper() + "】\n" + m["content"] for m in payload["messages"])
        (args.output_dir / f"{arm_name}.md").write_bytes(arm_md.encode("utf-8"))

    (args.output_dir / "A0-A1.diff").write_text(
        unified_board_diff(
            arms["A0"]["messages"][1]["content"],
            arms["A1"]["messages"][1]["content"],
            "A0",
            "A1",
        ),
        encoding="utf-8",
    )
    (args.output_dir / "A1-A2.diff").write_text(
        unified_board_diff(
            arms["A1"]["messages"][1]["content"],
            arms["A2"]["messages"][1]["content"],
            "A1",
            "A2",
        ),
        encoding="utf-8",
    )
    (args.output_dir / "source_checks.p0.json").write_bytes(dossier.encode(arm_proof))

    parsed_dir = args.parsed_dir
    if parsed_dir is None:
        candidate = args.input_dir.parent / "round-1" / "parsed"
        if candidate.exists():
            parsed_dir = candidate
    if parsed_dir is not None and parsed_dir.exists():
        eval_report = evaluate_round1_replies(parsed_dir, args.input_dir)
        (args.output_dir / "cm_semantic_evaluation.json").write_bytes(dossier.encode(eval_report))
        (args.output_dir / "cm_semantic_evaluation.md").write_text(
            render_cm_evaluation_md(eval_report), encoding="utf-8"
        )


if __name__ == "__main__":
    main()

