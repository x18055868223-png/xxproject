"""Opt-in local research closure; no production receipt, trading or scheduler."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import re
from typing import Any

if __package__:
    from . import astra_underwriting_v20 as core
    from . import astra_underwriting_review_v20 as review
    from . import astra_underwriting_store_v20 as store
    from . import signal_llm_review as llm
else:
    import astra_underwriting_v20 as core
    import astra_underwriting_review_v20 as review
    import astra_underwriting_store_v20 as store
    import signal_llm_review as llm

SHARED_RESEARCH_LEDGER = (Path(__file__).resolve().parents[1] /
    ".artifacts/astra-underwriting-v20-local-20260925-r4/research_llm_usage_ledger.json")
RESEARCH_DAILY_LIMIT = 4
RECEIPT_SCHEMA = "astra_local_closure_receipt@2.4.4"


def encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def freeze(path: Path, value: Any) -> None:
    """Evidence is immutable; reruns must use identical frozen inputs."""
    body = encoded(value)
    if path.exists():
        if path.read_bytes() != body:
            raise ValueError(f"frozen evidence differs: {path.name}")
    else:
        with path.open("xb") as output:
            output.write(body)


def read_key(path: Path) -> str:
    # Accept a bare key or a labelled key file, never include its text in errors.
    keys = set(re.findall(r"sk-[A-Za-z0-9_-]+", path.read_text(encoding="utf-8-sig")))
    if len(keys) != 1:
        raise ValueError("credential file must contain exactly one key")
    return keys.pop()


def existing_budget(path: Path) -> llm.DailyHttpBudget:
    # DailyHttpBudget itself tolerates malformed/missing files. The opt-in CLI
    # must fail closed before using that lower-level primitive for a real call.
    if path.resolve() != SHARED_RESEARCH_LEDGER.resolve():
        raise ValueError("this closure must use the frozen shared research usage ledger")
    data = json.loads(path.read_text(encoding="utf-8"))
    limit, used = data.get("http_calls_limit"), data.get("http_calls_used")
    if (type(limit) is not int or limit != RESEARCH_DAILY_LIMIT or type(used) is not int or used < 0
            or not isinstance(data.get("events"), list)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(data.get("date_bjt", "")))):
        raise ValueError("existing shared research usage ledger is invalid")
    datetime.strptime(data["date_bjt"], "%Y-%m-%d")
    return llm.DailyHttpBudget(path, limit=limit)


def _fmt_btc(value: Any) -> str:
    if value is None:
        return "缺失"
    try:
        numeric = float(value)
        return f"{0.0 if abs(numeric) < 0.000000005 else numeric:.8f}"
    except (TypeError, ValueError):
        return str(value)


def _deterministic_financials(projection: dict) -> dict:
    financials = projection.get("deterministic_financials") if isinstance(projection, dict) else None
    if not isinstance(financials, dict):
        raise ValueError("review_input missing deterministic_financials")
    return financials


def _catalog_text_rows(result: dict, field: str) -> list[dict]:
    rows = result.get(field) or []
    return [row for row in rows if isinstance(row, dict)]


def _ids(result: dict, field: str) -> list[str]:
    values = result.get(field) or []
    return [str(value) for value in values]


def render_report(snapshot: dict, projection: dict, result: dict, receipt: dict) -> str:
    esc = lambda value: html.escape(str(value))
    contract = snapshot["contract"]
    financials = _deterministic_financials(projection)
    account = financials.get("account_basis") or {}
    frozen_at = datetime.fromtimestamp(snapshot["market"]["valuation_ts_ms"] / 1000, timezone.utc).isoformat()
    metrics = [("可见净权利金 c", account.get("visible_credit_btc")),
               ("覆盖费用", account.get("fee_btc")),
               ("风险参考 μ", account.get("mu_ref_btc")),
               ("参考余量 M", account.get("reference_margin_btc"))]
    cards = "".join(f"<div><small>{esc(label)}</small><strong>{_fmt_btc(value)}</strong><small>BTC</small></div>"
                    for label, value in metrics)
    points = financials.get("points") or []
    point_rows = "".join("<tr><td>" + esc(row.get("label_cn", row.get("id", ""))) + "</td><td>"
                         + (f"{row['settlement_usd']:,.2f}" if row.get("settlement_usd") is not None else "缺失") + "</td><td>"
                         + _fmt_btc(row.get("payout_btc")) + "</td><td>"
                         + _fmt_btc(row.get("static_net_btc")) + "</td></tr>"
                         for row in points if isinstance(row, dict))
    if point_rows:
        account_table = ("<h2>程序确定性责任账</h2><p>以下数值来自 review_input.deterministic_financials，"
                         "不是模型输出。</p><table><tr><th>位置</th><th>交割价 USD</th>"
                         "<th>赔付 BTC</th><th>静态净额 BTC</th></tr>" + point_rows + "</table>")
    else:
        account_table = "<h2>程序确定性责任账</h2><p>review_input 未给出责任账点位。</p>"
    content_audit = receipt.get("content_audit") or {}
    audit_notice = ("旧内容审计：不予采用。" + str(content_audit.get("summary_cn", ""))
                    if content_audit.get("status") == "REJECTED_FOR_USE"
                    else "模型在新协议下只选择 claim_catalog ID；中文正文由本地程序展开，仍需与确定性责任账核对。")
    counterexamples = ""
    if content_audit.get("status") == "REJECTED_FOR_USE":
        examples = (content_audit.get("findings") or [{}])[0].get("examples", [])
        rows = "".join(f"<tr><td>{row['settlement_usd']:,.0f}</td><td>{_fmt_btc(row['payout_btc'])}</td><td>{_fmt_btc(row['static_net_btc'])}</td></tr>" for row in examples)
        counterexamples = ("<h2>旧内容审计反例</h2><p>该块只兼容显示历史拒收证据，不重写已有 .artifacts。"
                           "Put 深尾没有固定 BTC 赔付上限；短腿处净额仍为正；盈亏转负要看冻结账中的盈亏平衡点。</p>"
                           "<table><tr><th>交割价 USD</th><th>赔付 BTC</th><th>静态净额 BTC</th></tr>" + rows + "</table>")
    sections = (("模型选择的支持ID", "selected_claim_ids", "supports"),
                ("模型选择的反证ID", "selected_counter_ids", "counters"),
                ("模型选择的缺口ID", "selected_gap_ids", "gaps_cn"),
                ("模型选择的重核条件ID", "selected_reassess_condition_ids", "reassess_if_cn"))
    blocks = []
    for title, id_field, text_field in sections:
        ids = _ids(result, id_field)
        if text_field in {"supports", "counters"}:
            text_rows = [row.get("claim_cn", "") for row in _catalog_text_rows(result, text_field)]
        else:
            text_rows = [str(row) for row in (result.get(text_field) or [])]
        length = max(len(ids), len(text_rows))
        if length == 0:
            content = "<li>未选择</li>"
        else:
            items = []
            for index in range(length):
                claim_id = ids[index] if index < len(ids) else "未提供ID"
                text_cn = text_rows[index] if index < len(text_rows) else "未展开正文"
                items.append("<li><code>" + esc(claim_id) + "</code><br><span>" + esc(text_cn) + "</span></li>")
            content = "".join(items)
        blocks.append(f"<section><h2>{title}</h2><ul>{content}</ul></section>")
    q1, q2 = result.get("q1") or {}, result.get("q2") or {}
    strongest = q2.get("strongest_counter") or {}
    strongest_text = strongest.get("claim_cn", "") if isinstance(strongest, dict) else str(strongest)
    questions = ("<div class=grid><section><h2>Q1 · 当前环境与具体责任</h2><p>" + esc(q1.get("summary_cn", "尚无同版两问回执"))
                 + "</p><p>竞争解释：" + esc(q1.get("competing_explanation_cn", "未提供"))
                 + "</p></section><section><h2>Q2 · 补偿与等待取舍</h2><p>" + esc(q2.get("summary_cn", "尚未复核"))
                 + "</p><p>交换理由：" + esc(q2.get("exchange_reason_cn", "未提供"))
                 + "</p><p>覆盖关系：" + esc(q2.get("coverage_cn", "未提供"))
                 + "</p><p>合同责任边界：" + esc(q2.get("liability_cn", "未提供"))
                 + "</p><p>本单可观察变化：" + esc("；".join(q2.get("observable_changes_cn") or []))
                 + "</p><p>等待代价：" + esc(q2.get("waiting_cost_cn", "未知"))
                 + "</p><p>最强反证：" + esc(strongest_text or "未提供")
                 + "</p><p>重新核对：" + esc("；".join(q2.get("reassess_if_cn") or [])) + "</p></section></div>")
    detail = lambda title, value: f"<details><summary>{title}</summary><pre>{esc(json.dumps(value, ensure_ascii=False, indent=2))}</pre></details>"
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>本地收口复核 · 研究回执</title><style>
body{{margin:0;background:#f6f5f1;color:#202522;font:16px/1.7 system-ui,"Microsoft YaHei",sans-serif}}main{{max-width:1080px;margin:auto;padding:40px 24px}}h1{{font-size:32px;line-height:1.3}}h2{{font-size:19px}}small{{color:#5e655f}}.notice{{padding:16px;border-left:4px solid #677b68;background:#e9ede5}}.metrics{{display:grid;grid-template-columns:repeat(4,1fr);border-block:1px solid #ccd0c9;margin:26px 0}}.metrics div{{padding:18px 8px}}strong{{display:block;font-size:24px;font-variant-numeric:tabular-nums}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:0 32px}}li{{margin-bottom:10px}}details{{border-top:1px solid #ccd0c9;padding:14px 0}}summary{{cursor:pointer}}pre{{font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere}}code{{overflow-wrap:anywhere}}a{{color:#355640}}@media(max-width:650px){{main{{padding:24px 16px}}h1{{font-size:26px}}.metrics{{grid-template-columns:1fr 1fr}}strong{{font-size:21px}}.grid{{grid-template-columns:1fr}}}}
table{{width:100%;max-width:760px;border-collapse:collapse;margin-bottom:24px;font-size:14px;font-variant-numeric:tabular-nums}}th,td{{padding:8px 6px;border-bottom:1px solid #ccd0c9;text-align:right}}th:first-child,td:first-child{{text-align:left}}
</style><main><small>ASTRA / LOCAL CLOSURE / v2.4.7</small><h1>冻结输入 → 确定性核算 → 有界复核</h1>
<p class="notice">本地研究 · 输入身份：{esc(snapshot['data_identity']['kind'])}。模拟数值只验证合同核算与流程。真实 LLM 调用属于研究复核，不能进入生产回执或授予交易许可。</p>
<p>{esc(contract['short_instrument'])} / {esc(contract['long_instrument'])} · {esc(contract['quantity_btc'])} BTC · 宽度 {esc(contract['width_usd'])} USD<br><small>冻结核算时间：{esc(frozen_at)}；结果只对应这一冻结体。</small></p>
<div class="metrics">{cards}</div>{account_table}<h2>结构校验：{esc(result.get('status'))} / {esc(result.get('applicability', '尚未调用'))}</h2>
<p class="notice">{esc(audit_notice)}</p>
<p>本次新增 HTTP：{receipt['new_http_calls']}；该快照累计 HTTP：{result.get('http_calls', 0)}。生产附着：禁止；执行许可：关闭。</p>
{questions}{counterexamples}<details><summary>查看有界目录选择与程序生成中文</summary><p>模型只选择有限目录 ID；下列中文由本地程序按 claim_catalog 展开，不是模型自由生成段落。</p><div class="grid">{''.join(blocks)}</div></details>
<p>快照：<code>{esc(snapshot['snapshot_id'])}</code><br>复核投影：<code>{esc(projection.get('projection_hash', core._hash(projection)))}</code><br>目录哈希：<code>{esc(projection.get('claim_catalog_hash', result.get('claim_catalog_hash', '缺失')))}</code></p>
{detail('程序确定性责任账与完整缺口', {'deterministic_financials': financials, 'deterministic_account': projection.get('deterministic_account'), 'gap_reasons': snapshot['gap_reasons']})}
{detail('调用与额度证据', receipt)}{detail('完整研究结果', result)}{detail('实际复核投影', projection)}
<p><a href="snapshot.json">冻结快照</a> · <a href="review_input.json">复核输入</a> · <a href="receipt.json">执行回执</a></p>
</main></html>'''


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--call-llm", action="store_true")
    parser.add_argument("--usage-ledger", type=Path)
    parser.add_argument("--key-file", type=Path)
    args = parser.parse_args(argv)
    if args.call_llm and (not args.usage_ledger or not args.key_file):
        parser.error("a real call requires the existing research usage ledger and key file")
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    packet = core.llm_applicability_packet(snapshot)
    projection = review.build_review_input(snapshot, research_only=True)
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    freeze(out / "snapshot.json", snapshot)
    freeze(out / "review_input.json", projection)
    freeze(out / "request.json", llm._strip_local_request_fields(
        review.build_request(snapshot, "deepseek-flash", research_only=True)))
    ledger = store.UnderwritingLedger(out / "frozen.jsonl")
    ledger.record_snapshot(snapshot)
    before = after = None
    state_path = review._review_state_path(out / "review_state" / "research_only",
                                          snapshot["candidate_id"], snapshot["snapshot_id"])
    state_before = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    attempts_before = len(state_before.get("attempts", []))
    result = {"status": "NOT_REQUESTED", "research_only": True, "http_calls": 0}
    if args.call_llm:
        budget = existing_budget(args.usage_ledger)
        before = budget.snapshot()
        result = review.run_review(snapshot, ledger, out / "review_state", api_key=read_key(args.key_file),
                                   model="deepseek-flash", budget=budget, research_only=True,
                                   endpoint=llm.OPENAI_CHAT_COMPLETIONS_ENDPOINT)
        after = budget.snapshot()
    state_after = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    attempts = state_after.get("attempts", [])
    provider_attempts = []
    for attempt in attempts:
        raw = attempt.get("raw_response") or {}
        provider_attempts.append({"number": attempt.get("number"), "status": attempt.get("status"),
            "response_model": raw.get("model"), "usage": raw.get("usage"),
            "finish_reasons": [choice.get("finish_reason") for choice in raw.get("choices", [])],
            "request_hash": attempt.get("request_hash"), "response_hash": attempt.get("response_hash"),
            "error_reason": attempt.get("error_reason")})
    receipt = {"schema": RECEIPT_SCHEMA, "recorded_at": datetime.now(timezone.utc).isoformat(),
               "snapshot_id": snapshot["snapshot_id"], "packet_hash": core._hash(packet),
               "projection_hash": projection["projection_hash"], "research_only": True,
               "production_attach_allowed": False, "execution_allowed": False,
               "new_http_calls": len(attempts) - attempts_before,
               "requested_model": "deepseek-flash", "response_protocol": result.get("response_protocol"),
               "claim_catalog_hash": projection.get("claim_catalog_hash"),
               "deterministic_financials_schema": (projection.get("deterministic_financials") or {}).get("schema"),
               "bounded_claim_ids_only": True, "program_generated_chinese": True,
               "provider_attempts": provider_attempts,
               "attempt_state_path": str(state_path.relative_to(out)),
               "budget_before": before, "budget_after": after, "result": result,
               "full_packet_bytes": len(encoded(packet)), "review_input_bytes": len(encoded(projection)),
               "ledger_review_attached": bool(ledger.state()["snapshots"][snapshot["snapshot_id"]].get("review"))}
    if receipt["ledger_review_attached"]:
        raise ValueError("research result unexpectedly attached to production review ledger")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    freeze(out / f"run-{stamp}.json", receipt)
    (out / "receipt.json").write_bytes(encoded(receipt))
    (out / "index.html").write_text(render_report(snapshot, projection, result, receipt), encoding="utf-8")
    print(json.dumps({"status": result["status"], "snapshot_id": snapshot["snapshot_id"],
                      "new_http_calls": receipt["new_http_calls"], "report": str(out / "index.html")}, ensure_ascii=False))
    return 0 if result["status"] in {"OK", "RESEARCH_OK", "NOT_REQUESTED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
