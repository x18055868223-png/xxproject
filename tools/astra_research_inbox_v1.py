"""Research submissions E2: the researcher writes, the server stamps (docs/opus/08 §6, 2026-10-20).

The external researcher (account radar, group research) drops JSON files into the inbox.
On every tick the server validates each file, stamps it with the server time and its
sha256, appends it to the archive registry (hash-chained, append-only) and writes a
receipt the researcher can read. Submissions are data: nothing in them is executed.

Hypotheses must be computable. Their condition is a small JSON expression over fixed
fields of the contract path ledger; the outcome metric comes from a fixed list. That is
what lets E3 evaluate them forward, by program, on samples frozen after registration
("evidence fixed, outcome fixed, evaluation fixed"). A registered hypothesis is never
edited: a revision is a new id that names what it supersedes.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

SCHEMAS = {"hypothesis@0.1", "postmortem@0.1", "protocol_proposal@0.1", "research_note@0.1", "episode_view@0.1"}
MAX_BYTES = 1_000_000
ID = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.json$")
OPS = {"==", "!=", "<", "<=", ">", ">=", "in", "not_in"}
NUMERIC_FIELDS = {
    "ledger.credit_bid_ask_btc", "ledger.mark_spread_btc", "ledger.index_usd", "ledger.forward_usd",
    "ledger.short_from_index_pct", "ledger.long_from_index_pct", "ledger.breakeven_from_index_pct",
    "ledger.p_itm_short", "ledger.p_touch_short", "ledger.p_loss", "ledger.p_max_loss",
    "ledger.short.mark_iv", "ledger.long.mark_iv", "ledger.short.delta", "ledger.long.delta",
    "ledger.short.mark_price", "ledger.long.mark_price", "ledger.states.flip_usd",
    "calendar.hours_to_expiry", "calendar.hour_utc", "calendar.weekday",
}
TEXT_FIELDS = {
    "ledger.side", "ledger.states.space_gate", "ledger.states.m_die_level", "ledger.states.gamma_regime",
    "ledger.states.gex_market_state", "ledger.states.tmv_24h", "ledger.states.tmv_48h", "ledger.states.micro_flow",
    "ledger.displayed", "ledger.locked",
}
FIELDS = NUMERIC_FIELDS | TEXT_FIELDS
# Fixed outcome metrics (evaluated by E3 from the entry row onward); the researcher picks one.
METRICS = {
    "touched_short_after_entry": "到期前（入场后）是否触及短腿；基线对照为入场时 p_touch_short",
    "loss_at_expiry": "按入场时（未扣费）信用计算，到期是否净亏；基线对照为入场时 p_loss",
    "settle_vs_short_pct": "交割价相对短腿的不利距离（%，正数表示越过短腿）；与条件不成立组对照",
    "max_adverse_after_entry_pct": "入场后到到期的最大不利偏离（%）；与条件不成立组对照",
    "result_btc_at_entry_credit": "入场信用（未扣费）减到期赔付（BTC）；与条件不成立组对照",
}
DIRECTIONS = {"riskier", "safer"}
RISK_LEVELS = {"much_safer", "safer", "in_line", "riskier", "much_riskier"}
CONTRACT_KEY = re.compile(r"^BTC-\d{1,2}[A-Z]{3}\d{2}-\d+-[PC]\|BTC-\d{1,2}[A-Z]{3}\d{2}-\d+-[PC]$")


def check_condition(node: Any, path: str = "condition") -> list[str]:
    """Validate the JSON condition language: all / any / not / leaf {field, op, value}."""
    errors: list[str] = []
    if not isinstance(node, Mapping):
        return [f"{path}: 必须是对象"]
    keys = set(node)
    if keys == {"all"} or keys == {"any"}:
        items = node[next(iter(keys))]
        if not isinstance(items, list) or not 1 <= len(items) <= 12:
            return [f"{path}.{next(iter(keys))}: 需要 1–12 个子条件"]
        for i, child in enumerate(items):
            errors += check_condition(child, f"{path}.{next(iter(keys))}[{i}]")
        return errors
    if keys == {"not"}:
        return check_condition(node["not"], path + ".not")
    if keys != {"field", "op", "value"}:
        return [f"{path}: 叶子只能有 field/op/value"]
    field, op, value = node["field"], node["op"], node["value"]
    if field not in FIELDS:
        errors.append(f"{path}.field: 未知字段 {field!r}（可用字段见回执 allowed_fields）")
    if op not in OPS:
        errors.append(f"{path}.op: 未知运算 {op!r}")
    if op in {"in", "not_in"}:
        if not isinstance(value, list) or not value or len(value) > 20:
            errors.append(f"{path}.value: in/not_in 需要 1–20 个值的列表")
    elif field in NUMERIC_FIELDS and op in {"<", "<=", ">", ">="}:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{path}.value: 数值字段需要数字")
    elif field in TEXT_FIELDS and op in {"<", "<=", ">", ">="}:
        errors.append(f"{path}.op: 文本字段只能用 ==、!=、in、not_in")
    return errors


def evaluate_condition(node: Mapping[str, Any], row: Mapping[str, Any]) -> bool | None:
    """True / False, or None when a needed field is missing (the sample is then excluded)."""
    if "all" in node:
        results = [evaluate_condition(c, row) for c in node["all"]]
        return None if None in results else all(results)
    if "any" in node:
        results = [evaluate_condition(c, row) for c in node["any"]]
        return True if True in results else (None if None in results else False)
    if "not" in node:
        inner = evaluate_condition(node["not"], row)
        return None if inner is None else not inner
    value = _field(row, node["field"])
    if value is None:
        return None
    op, target = node["op"], node["value"]
    if op == "==":
        return value == target
    if op == "!=":
        return value != target
    if op == "in":
        return value in target
    if op == "not_in":
        return value not in target
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return {"<": value < target, "<=": value <= target, ">": value > target, ">=": value >= target}[op]


def _field(row: Mapping[str, Any], name: str) -> Any:
    root, _, rest = name.partition(".")
    current: Any = row.get(root) if root in {"ledger", "calendar"} else None
    for part in rest.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _text(value: Any, limit: int = 4000) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= limit


def validate(doc: Any) -> list[str]:
    if not isinstance(doc, Mapping):
        return ["顶层必须是 JSON 对象"]
    schema = doc.get("schema")
    if schema not in SCHEMAS:
        return [f"schema 必须是 {sorted(SCHEMAS)} 之一"]
    errors: list[str] = []
    if not isinstance(doc.get("id"), str) or not ID.match(doc["id"]):
        errors.append("id: 小写字母/数字/_/-，3–64 位")
    agent = doc.get("agent")
    if not isinstance(agent, Mapping) or not _text(agent.get("name"), 64) or not _text(agent.get("model"), 128):
        errors.append("agent: 需要 name 与 model")
    if schema == "hypothesis@0.1":
        for key in ("statement_cn", "rationale_cn", "falsification_cn"):
            if not _text(doc.get(key)):
                errors.append(f"{key}: 必填文本")
        errors += check_condition(doc.get("condition"))
        if doc.get("metric") not in METRICS:
            errors.append(f"metric: 必须是 {sorted(METRICS)} 之一")
        if doc.get("expected_direction") not in DIRECTIONS:
            errors.append("expected_direction: riskier 或 safer（条件成立时风险相对基线/对照组更高或更低）")
        formation = doc.get("formation")
        if not isinstance(formation, Mapping) or not _text(formation.get("description_cn")) \
                or not isinstance(formation.get("samples"), list):
            errors.append("formation: 需要 description_cn 与 samples（形成假设时看过的样本，contract_key 或日期列表）")
        for key in ("counterexamples", "collinear_with"):
            if not isinstance(doc.get(key), list):
                errors.append(f"{key}: 需要列表（可以为空）")
        if doc.get("supersedes") is not None and (not isinstance(doc["supersedes"], str) or not ID.match(doc["supersedes"])):
            errors.append("supersedes: 被取代的假设 id")
    elif schema == "postmortem@0.1":
        if not isinstance(doc.get("expiry_date"), str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", doc["expiry_date"]):
            errors.append("expiry_date: YYYY-MM-DD")
        if not _text(doc.get("findings_cn"), 20000):
            errors.append("findings_cn: 必填")
        for i, q in enumerate(doc.get("inquiry_review") or []):
            if not isinstance(q, Mapping) or not q.get("case_id") or not q.get("question_id") \
                    or q.get("verdict") not in {"pointed_to_realized_risk", "not_material", "wrong_premise", "unclear"}:
                errors.append(f"inquiry_review[{i}]: 需要 case_id、question_id、verdict")
    elif schema == "protocol_proposal@0.1":
        if doc.get("target") not in {"inquirer_prompt", "research_protocol", "ledger_fields", "case_file", "other"}:
            errors.append("target: inquirer_prompt / research_protocol / ledger_fields / case_file / other")
        for key in ("proposal_cn", "evidence_cn"):
            if not _text(doc.get(key), 20000):
                errors.append(f"{key}: 必填")
    elif schema == "episode_view@0.1":
        # the researcher's view on an open contract, counted only when it arrives before expiry
        if not isinstance(doc.get("contract_key"), str) or not CONTRACT_KEY.match(doc["contract_key"]):
            errors.append("contract_key: <短腿合约名>|<保护腿合约名>，见 index/episodes 或账本")
        view = doc.get("view")
        if not isinstance(view, Mapping):
            errors.append("view: 必填对象")
        else:
            if view.get("risk_vs_baseline") not in RISK_LEVELS:
                errors.append(f"view.risk_vs_baseline: {sorted(RISK_LEVELS)} 之一")
            if view.get("grade") not in {"A", "B", "C", "D", "E", "U"}:
                errors.append("view.grade: A–E 或 U")
            if view.get("lean") not in {"underwrite", "decline", "watch"}:
                errors.append("view.lean: underwrite / decline / watch")
            if view.get("confidence") not in {"high", "medium", "low"}:
                errors.append("view.confidence: high / medium / low")
            if not _text(view.get("summary_cn"), 8000) or not isinstance(view.get("drivers_cn"), list):
                errors.append("view.summary_cn 与 view.drivers_cn（列表）: 必填")
    elif schema == "research_note@0.1":
        if not _text(doc.get("title_cn"), 200) or not _text(doc.get("body_cn"), 50000):
            errors.append("title_cn 与 body_cn: 必填")
    return errors


REGISTRY = {"hypothesis@0.1": "registry/hypotheses.jsonl", "postmortem@0.1": "registry/postmortems.jsonl",
            "protocol_proposal@0.1": "registry/proposals.jsonl", "research_note@0.1": "registry/notes.jsonl",
            "episode_view@0.1": "registry/episode_views.jsonl"}


def ingest(archive: Any, inbox: str | Path, receipts: str | Path, *, now_ms: int,
           open_contracts: Mapping[str, int] | None = None) -> dict[str, Any]:
    """Validate, stamp and register every file in the inbox; write a receipt for each."""
    inbox, receipts = Path(inbox), Path(receipts)
    receipts.mkdir(parents=True, exist_ok=True)
    registered_ids = _registered_ids(archive)
    report = {"accepted": 0, "rejected": 0}
    for path in sorted(p for p in inbox.iterdir() if p.is_file()) if inbox.exists() else []:
        if path.name.endswith((".tmp", ".part")):
            continue   # still being written
        name = path.name if FILE.match(path.name) else "invalid-name.json"
        raw = path.read_bytes()[:MAX_BYTES + 1]
        sha = hashlib.sha256(raw).hexdigest()
        receipt: dict[str, Any] = {"file": name, "received_at_ms": now_ms, "sha256": sha}
        errors: list[str] = []
        doc = None
        if not FILE.match(path.name):
            errors.append("文件名只能用字母数字与 _ . -，并以 .json 结尾")
        if len(raw) > MAX_BYTES:
            errors.append("文件超过 1 MB")
        if not errors:
            try:
                doc = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                errors.append("不是 UTF-8 JSON")
        if doc is not None:
            errors += validate(doc)
            if not errors and doc["id"] in registered_ids.get(doc["schema"], set()):
                errors.append("id 已登记；已登记内容不可修改，修订请用新 id 并写 supersedes")
            if not errors and doc["schema"] == "hypothesis@0.1" and doc.get("supersedes") is not None:
                if doc["supersedes"] == doc["id"] or doc["supersedes"] not in registered_ids.get("hypothesis@0.1", set()):
                    errors.append("supersedes: 只能指向更早已登记、且不是自身的假设 id")
            if not errors and doc["schema"] == "episode_view@0.1":
                expiry = (open_contracts or {}).get(doc["contract_key"])
                if expiry is None:
                    errors.append("contract_key 不是进行中的合约（未跟踪或已到期）；判断须在到期前提交")
                elif now_ms >= expiry:
                    errors.append("合约已到期，判断不再计入")
        if errors:
            receipt.update({"status": "rejected", "errors": errors})
            archive.append_line("registry/rejected.jsonl", {**receipt, "schema": (doc or {}).get("schema")
                                if isinstance(doc, Mapping) else None}, at_ms=now_ms, kind="submission_rejected")
            report["rejected"] += 1
        else:
            entry = {"submission_id": f"{doc['schema'].split('@')[0]}:{doc['id']}", "received_at_ms": now_ms,
                     "sha256": sha, "document": doc}
            archive.append_line(REGISTRY[doc["schema"]], entry, at_ms=now_ms, kind="submission")
            registered_ids.setdefault(doc["schema"], set()).add(doc["id"])
            receipt.update({"status": "registered", "submission_id": entry["submission_id"],
                            "note_cn": "服务器时间戳即登记时间；前向验证只使用此时间之后冻结的样本"})
            report["accepted"] += 1
        if receipt["status"] == "rejected" and doc is not None and doc.get("schema") == "hypothesis@0.1":
            receipt["allowed_fields"] = sorted(FIELDS)
            receipt["allowed_metrics"] = METRICS
        (receipts / (name + ".receipt.json")).write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
        path.unlink()
    return report


def _registered_ids(archive: Any) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for schema, rel in REGISTRY.items():
        path = Path(archive.root) / rel
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    out.setdefault(schema, set()).add(json.loads(line)["document"]["id"])
    return out
