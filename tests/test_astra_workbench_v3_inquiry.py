"""v3 question sheets (docs/opus/14): what the page says about the inquirer's sheets."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INQUIRY = (ROOT / "deploy" / "astra_workbench_v3" / "inquiry.js").as_uri()


def run(expr, payload):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    script = (f'import * as I from "{INQUIRY}";\n'
              'import fs from "node:fs";\n'
              'const p = JSON.parse(fs.readFileSync(0, "utf8"));\n'
              f'console.log(JSON.stringify({expr}));')
    result = subprocess.run([node, "--input-type=module", "-e", script], input=json.dumps(payload),
                            text=True, encoding="utf-8", capture_output=True, check=False, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


SLOT = 1_792_533_600_000     # 2026-10-20 22:00 UTC = 10-21 06:00 Beijing


def meta(**kw):
    return {"file": "inquiry/2026-10-20/22_put_cf_x.json", "slot_ms": SLOT, "trigger": "new_contract",
            "trigger_inferred": True, "status": "PARTIAL", "has_sheet": True, "asof_ms": SLOT - 60_000, **kw}


def sheet():
    return {"questions": [{"id": f"K{i}", "priority": p, "type": "temporal", "bears_on": ["touch_prob", "tail_loss"],
                           "answer_source": "history", "question_cn": f"Q{i}",
                           "premises": [{"refs": [f"L{i}.a"]}]}
                          for i, p in enumerate(["high", "high", "medium", "low"])],
            "anomalies": [{"id": "A1", "refs": ["L9.z"]}], "fact_brief": {"layers": [{"facts": [{"refs": ["L8.only"]}]}]}}


def test_identity_says_slot_trigger_case_age_and_status_in_beijing_time():
    text = run("I.identityText(p.m, p.now)", {"m": meta(), "now": SLOT + 3 * 3_600_000})
    assert text.startswith("10/21 06:00 新合约即时（按时刻推断）") and "案卷 10/21 05:59" in text
    assert "部分通过校验" in text and "小时前" in text
    assert "定时" in run("I.identityText(p.m)", {"m": meta(trigger="scheduled", trigger_inferred=False)})
    assert "状态 WEIRD" in run("I.identityText(p.m)", {"m": meta(status="WEIRD")})


def test_sheet_state_none_failed_only_and_failed_after():
    assert run("I.sheetState(p.r).kind", {"r": {"sheets": [], "selected": None}}) == "none"
    failed = meta(has_sheet=False, status="ERROR", slot_ms=SLOT + 4 * 3_600_000)
    only = run("I.sheetState(p.r)", {"r": {"sheets": [failed], "selected": None}})
    assert only["kind"] == "failed_only" and only["failedAfter"]["status"] == "ERROR"
    ok = run("I.sheetState(p.r)", {"r": {"sheets": [meta(), failed], "selected": meta()}})
    assert ok["kind"] == "ok" and ok["failedAfter"]["slot_ms"] == SLOT + 4 * 3_600_000


def test_validator_flags_are_said_in_words():
    out = run("I.flagsOf(p.x)", {"x": {"forbidden_wording": "胜率", "untraced_numbers": ["72", "0.5"]}})
    assert out == ["措辞越界：“胜率”", "含案卷外数字：72、0.5"]
    assert run("I.flagsOf(p.x)", {"x": {"text_cn": "plain"}}) == []


def test_summary_and_ref_sets():
    resp = {"sheets": [meta()], "selected": {**meta(), "sheet": sheet(), "entries": {"L0.a": {}}, "issues": []}}
    s = run("I.toSummary(p.r).selected", {"r": resp})
    assert "sheet" not in s and "entries" not in s
    assert [q["id"] for q in s["summary"]["questions"]] == ["K0", "K1", "K2"]
    assert s["summary"]["by_priority"] == {"high": 2, "medium": 1, "low": 1} and s["summary"]["anomaly_count"] == 1
    assert s["summary"]["questions"][0]["bears_on"] == ["touch_prob", "tail_loss"]
    assert sorted(run("[...I.mainRefs(p.s)]", {"s": sheet()})) == ["L0.a", "L1.a", "L2.a", "L3.a", "L9.z"]   # brief-only refs left out
    assert run("[...I.questionRefs(p.s, 'K2')]", {"s": sheet()}) == ["L2.a"]
    assert run("I.contractKey(p.c)", {"c": {"short_instrument": "A", "long_instrument": "B"}}) == "A|B"
    assert run("I.contractKey(p.c)", {"c": {"short_instrument": "A"}}) is None


def test_labels_cover_every_code_the_inquirer_may_emit():
    from tools import astra_inquiry_v1 as inq
    maps = run("({t: Object.keys(I.TYPE_CN), b: Object.keys(I.BEARS_CN), s: Object.keys(I.SOURCE_CN), "
               "d: Object.keys(I.DIRECTION_CN), a: Object.keys(I.ANOMALY_CN), l: Object.keys(I.LAYER_STATUS_CN), "
               "p: Object.keys(I.PRIORITY_CN), layers: Object.keys(I.LAYER_CN)})", {})
    assert set(maps["t"]) == inq.QUESTION_TYPES and set(maps["b"]) == inq.BEARS_ON
    assert set(maps["s"]) == inq.ANSWER_SOURCES and set(maps["d"]) == inq.DIRECTIONS
    assert set(maps["a"]) == inq.ANOMALY_KINDS and set(maps["l"]) == inq.LAYER_STATUS
    assert set(maps["p"]) == inq.PRIORITIES and set(maps["layers"]) == set(inq.BRIEF_LAYERS)


TERMS = (ROOT / "deploy" / "astra_workbench_v3" / "inquiry_terms.js").as_uri()


def run_terms(expr, payload):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    script = (f'import * as T from "{TERMS}";\nimport fs from "node:fs";\n'
              'const p = JSON.parse(fs.readFileSync(0, "utf8"));\n'
              f'console.log(JSON.stringify({expr}));')
    result = subprocess.run([node, "--input-type=module", "-e", script], input=json.dumps(payload),
                            text=True, encoding="utf-8", capture_output=True, check=False, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_machine_words_read_as_chinese_and_numbers_stay():
    text = ("L2.compensation 给 tier=fair，L8 Q2 写 below_market；net_credit_btc 0.0029；gex.raw.board.v1 为 MISSING；"
            "producer_state=STRUCTURE_BLOCKED；BTC-22OCT26-82500-P 距 82,500 为 0.50%；L3.unknown_ref；odd_code_x")
    out = run_terms("T.cnText(p.t, p.e)", {"t": text, "e": {"L3.unknown_ref": {"label_cn": "某条目（说明）"}}})
    assert out.startswith("「补偿档」 给 补偿档：公允，程序读数层 Q2 风险补偿 写 低于市场估价")
    assert "净信用（BTC） 0.0029" in out and "GEX 原始看板 为 缺失" in out and "生产端状态：结构关闭" in out
    assert "BTC-22OCT26-82500-P 距 82,500 为 0.50%" in out          # contract names and numbers untouched
    assert "「某条目」" in out and "odd_code_x" in out                 # entry label; unknown codes stay as written
    assert run_terms("T.cnText(p.t)", {"t": "纯中文 1.5%"}) == "纯中文 1.5%"
    assert run_terms("[T.fieldWord('best_bid_price'), T.valueWord('STALE'), T.valueWord(3)]", {}) == ["最优买价", "过期", 3]


def test_numbers_read_in_m_dates_and_per_mille():
    out = run_terms("p.t.map((s) => T.cnText(s))", {"t": [
        "有效翻转位 81751.10827929445、gex_usd -6804758.55、对冲流 +141014036.64",
        "最新费率 9.83e-05、阈值 0.0001、权利金 0.0007 BTC",
        "last_bar_end_ms 1792526400000，合约 BTC-22OCT26-82500-P，距短腿 -0.9102%",
        "protection 80000、starts_at 82000"]})
    assert out[0] == "有效翻转位 81751.11、GEX（美元） -6.80M、对冲流 +141.01M"
    assert out[1] == "最新费率 0.983‱、阈值 0.0001、权利金 0.0007 BTC"            # only scientific notation changes
    assert out[2] == "最后一根 K 线时刻 10/21 04:00，合约 BTC-22OCT26-82500-P，距短腿 -0.9102%"
    assert out[3] == "保护腿 80000、起赔价 82000"
    assert run_terms("[T.valueWord(266107643.88), T.valueWord(0.0005), T.valueWord(1792526400000)]", {}) == ["266.11M", 0.0005, "10/21 04:00"]


def test_millisecond_durations_read_in_minutes_or_hours():
    out = run_terms("T.cnText(p.t)", {"t": "（age_ms 1460000）、overlap_ms=7200000、时效 1460000 ms"})
    assert out == "（数据年龄 24 分钟）、重叠时长：2.0 小时、时效 24 分钟"
