import json

import pytest

from tools import astra_review_closure_cli_v243 as cli
from tools import astra_underwriting_v20 as core
from test_astra_underwriting_v20 import sample


def test_freeze_refuses_mutation_and_key_errors_do_not_disclose_content(tmp_path):
    path = tmp_path / "frozen.json"
    cli.freeze(path, {"value": 1})
    cli.freeze(path, {"value": 1})
    with pytest.raises(ValueError, match="frozen evidence differs"):
        cli.freeze(path, {"value": 2})
    key = tmp_path / "key.txt"
    key.write_text("sk-fixture-one sk-fixture-two", encoding="utf-8")
    with pytest.raises(ValueError) as error:
        cli.read_key(key)
    assert "sk-fixture" not in str(error.value)


@pytest.mark.parametrize("data", [{}, {"date_bjt": "2026-09-25", "http_calls_used": 0},
    {"date_bjt": "2026-99-25", "http_calls_used": 1, "http_calls_limit": 4, "events": []},
    {"date_bjt": "2026-09-25", "http_calls_used": -1, "http_calls_limit": 4, "events": []}])
def test_real_budget_requires_valid_existing_ledger(tmp_path, data, monkeypatch):
    path = tmp_path / "existing.json"
    monkeypatch.setattr(cli, "SHARED_RESEARCH_LEDGER", path)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        cli.existing_budget(path)
    assert json.loads(path.read_text()) == data


def test_budget_rejects_changed_path_or_expanded_limit(tmp_path, monkeypatch):
    authorized = tmp_path / "authorized.json"
    other = tmp_path / "other.json"
    monkeypatch.setattr(cli, "SHARED_RESEARCH_LEDGER", authorized)
    data = {"date_bjt": "2026-09-25", "http_calls_used": 1, "http_calls_limit": 4, "events": []}
    other.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="frozen shared"):
        cli.existing_budget(other)
    authorized.write_text(json.dumps({**data, "http_calls_limit": 99}), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        cli.existing_budget(authorized)
    authorized.write_text(json.dumps(data), encoding="utf-8")
    assert cli.existing_budget(authorized).limit == 4


def test_report_escapes_model_output_and_uses_program_financials_from_projection():
    snap = core.evaluate(sample(risk=False))
    projection = cli.review.build_review_input(snap, research_only=True)
    projection["deterministic_financials"]["account_basis"]["visible_credit_btc"] = 0.123456789
    result = {
        "status": "RESEARCH_OK",
        "applicability": "CONTESTED",
        "summary_cn": "<script>alert('x')</script>",
        "claim_catalog_hash": projection["claim_catalog_hash"],
        "selected_claim_ids": ["claim.contract.put_liability_direction"],
        "selected_counter_ids": ["counter.contract.put_fixed_btc_cap"],
        "selected_gap_ids": ["gap.fmz_current_fact_missing"],
        "selected_reassess_condition_ids": ["reassess.snapshot_rebuilt"],
        "supports": [{"ref": "contract", "claim_cn": "<b>程序展开支持</b>"}],
        "counters": [{"ref": "contract", "claim_cn": "程序展开反证"}],
        "gaps_cn": ["程序展开缺口"],
        "reassess_if_cn": ["程序展开重核条件"],
    }
    receipt = {"new_http_calls": 1}
    body = cli.render_report(snap, projection, result, receipt)
    assert "<script>" not in body
    assert "&lt;script&gt;" in body
    assert "0.12345679" in body
    assert "review_input.deterministic_financials" in body
    assert "模型只选择有限目录 ID" in body
    assert "不是模型自由生成段落" in body
    legacy_raw_title = "查看" + "原始 LLM" + "正文"
    assert legacy_raw_title not in body
    assert "claim.contract.put_liability_direction" in body
    assert "&lt;b&gt;程序展开支持&lt;/b&gt;" in body
    assert "synthetic_fixture" in body
    assert "生产附着：禁止" in body


def test_report_keeps_legacy_content_audit_rejection_visible_without_rewriting_evidence():
    snap = core.evaluate(sample())
    projection = cli.review.build_review_input(snap, research_only=True)
    receipt = {
        "new_http_calls": 0,
        "content_audit": {
            "status": "REJECTED_FOR_USE",
            "summary_cn": "旧正文违反确定性责任账",
            "findings": [{"examples": [{
                "settlement_usd": 1000,
                "payout_btc": 0.2,
                "static_net_btc": -0.1996375,
            }]}],
        },
    }
    body = cli.render_report(snap, projection, {"status": "RESEARCH_OK"}, receipt)
    assert "旧内容审计：不予采用" in body
    assert "旧内容审计反例" in body
    assert "不重写已有 .artifacts" in body


def test_dry_closure_has_no_network_or_attached_review(tmp_path, monkeypatch):
    source = tmp_path / "source.json"
    source.write_text(json.dumps(core.evaluate(sample())), encoding="utf-8")
    out = tmp_path / "out"
    monkeypatch.setattr(cli.review, "run_review", lambda *a, **k: pytest.fail("dry run made a review call"))
    assert cli.main(["--snapshot", str(source), "--output-dir", str(out)]) == 0
    receipt = json.loads((out / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["schema"] == "astra_local_closure_receipt@2.4.4"
    assert receipt["new_http_calls"] == 0
    assert not receipt["ledger_review_attached"]
    assert not receipt["execution_allowed"]
    assert receipt["research_only"] is True
    assert receipt["production_attach_allowed"] is False
    assert receipt["bounded_claim_ids_only"] is True
    assert receipt["program_generated_chinese"] is True
    assert receipt["claim_catalog_hash"] == json.loads((out / "review_input.json").read_text(encoding="utf-8"))["claim_catalog_hash"]
    assert receipt["deterministic_financials_schema"] == "astra_underwriting_deterministic_financials@2.4.4"
    assert receipt["result"]["status"] == "NOT_REQUESTED"
    legacy_raw_title = "查看" + "原始 LLM" + "正文"
    assert legacy_raw_title not in (out / "index.html").read_text(encoding="utf-8")
