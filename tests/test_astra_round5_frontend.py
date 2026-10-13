"""Candidate navigation and question ownership, using real frozen review output."""
import copy

import pytest

from tools import astra_workbench_preview_v20 as preview
from astra_workbench_frontend_harness import render_workbench
from test_astra_round3_frontend import _node_harness, _run_node
from test_astra_wpem_frontend import Q1_CANONICAL


def reviewed_view(scenario="W1"):
    view = preview.build_preview(1790500000000, scenario_id=scenario)
    for candidate in view["candidates"]:
        _, review = preview._wire_response(candidate["snapshot"], scenario)
        review.update(status="SIMULATED", reviewed_at_ms=view["updated_at_ms"])
        candidate["review"] = review
        view["manual_review_state"]["last_result"] = {
            "status": "SIMULATED", "candidate_id": candidate["candidate_id"],
            "side": candidate["side"], "snapshot_id": candidate["snapshot"]["snapshot_id"],
            "map_id": candidate["btc_map"]["map_id"], "review": copy.deepcopy(review),
        }
    return view


def _round5_node_harness(extra_js: str) -> str:
    return _node_harness(r'''
sandbox.window.setTimeout = (callback) => {
  if (typeof callback === "function") callback();
  return 0;
};
''' + extra_js)


def test_candidate_receipt_links_choose_attached_side_without_requests():
    script = _round5_node_harness(r'''
let links = [];
const area = elements.candidateArea;
area.querySelectorAll = (selector) => {
  if (selector !== ".candidate-review-link") return [];
  links = Array.from(area.innerHTML.matchAll(/<a class="candidate-review-link" data-candidate-id="([^"]+)"/g), (match) => ({
    getAttribute() { return match[1]; },
    addEventListener(_, handler) { this.click = handler; },
  }));
  return links;
};
api.state.workbench = payload;
api.state.apiOnline = true;
api.renderAll();
const output = [];
for (const side of ["put", "call", "put"]) {
  const id = payload.candidates.find((candidate) => candidate.side.toLowerCase() === side).candidate_id;
  const link = links.find((item) => item.getAttribute() === id);
  if (!link) throw new Error("missing candidate receipt link");
  link.click();
  output.push({side: api.state.selectedMapCandidateKey, html: elements.manualReviewState.innerHTML});
}
console.log(JSON.stringify({output, last: api.state.workbench.manual_review_state.last_result}));
''')
    view = reviewed_view()
    result = _run_node(script, view)
    for row, side in zip(result["output"], ("put", "call", "put")):
        assert row["side"] == side
        primary = row["html"].split("查看全局最近一次回执")[0]
        assert f"当前查看 · {side.title()} 保单" in primary
        assert "模拟同版保单" in primary
        candidate = next(c for c in view["candidates"] if c["side"].lower() == side)
        assert candidate["snapshot"]["contract"]["short_instrument"] in primary
        assert candidate["review"]["q1"]["summary_cn"].rstrip("。") in primary
        assert candidate["review"]["q2"]["summary_cn"].rstrip("。") in primary
    assert result["last"] == view["manual_review_state"]["last_result"]
    # The shared harness throws on fetch/timers, so these clicks cannot POST/poll.


def test_selected_missing_or_changed_snapshot_never_promotes_global_receipt():
    old = reviewed_view()
    changed = preview.build_preview(1790500000000, old["manual_review_state"], scenario_id="W4")
    changed["__test_state"] = {"selectedMapCandidateKey": "put"}
    html = render_workbench(changed)["areas"]["manualReviewState"]
    assert "该候选尚无同版回执" in html
    assert "历史回执 · 当前候选尚无同版结论" in html
    assert "模拟同版保单" not in html
    # Even a malformed attachment must not pass the existing exact-snapshot guard.
    changed["candidates"][0]["review"] = copy.deepcopy(old["candidates"][0]["review"])
    changed["__test_state"] = {"selectedMapCandidateKey": "put"}
    html = render_workbench(changed)["areas"]["manualReviewState"]
    assert "模拟同版保单" not in html


@pytest.mark.parametrize("scenario,side,expected", [
    ("W3", "put", "放弃这份冻结账"),
    ("W4", "call", "放弃这份冻结账"),
    ("MISSING_MU", "put", "资料不足"),
])
def test_overall_status_does_not_replace_environment_or_q2(scenario, side, expected):
    view = reviewed_view(scenario)
    candidate = next(c for c in view["candidates"] if c["side"].lower() == side)
    view["__test_state"] = {"selectedMapCandidateKey": side}
    html = render_workbench(view)["areas"]["manualReviewState"].split("查看全局最近一次回执")[0]
    assert "综合复核状态：" in html and "适配暂不能判定" not in html
    q1 = html.split(Q1_CANONICAL)[1].split("Q2 ·")[0]
    assert candidate["review"]["q1"]["summary_cn"].rstrip("。") in q1
    assert expected in html
    if scenario in {"W3", "W4"}:
        assert "补偿侧反证归入 Q2" in q1
        assert "冻结净补偿未覆盖" not in q1
        assert candidate["review"]["q2"]["disposition"] == "reference_compensation_insufficient"
    if scenario == "MISSING_MU":
        assert candidate["snapshot"]["economics"]["mu_ref_btc"] is None
        assert candidate["review"]["q2"]["disposition"] == "information_gap"
    if scenario == "W5":
        assert candidate["review"]["q2"]["strongest_counter"]["id"] == "counter.structure.opening_blocked"
