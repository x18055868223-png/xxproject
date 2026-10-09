"""Reading-depth guarantees: readable first, exact evidence still reachable."""
from html.parser import HTMLParser
import json
import re
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from astra_workbench_frontend_harness import render_workbench
from test_astra_observation_lineage_frontend import _lineage_candidate
from test_astra_wpem_frontend import _workbench


MACHINE_CODE = re.compile(
    r"[a-f0-9]{24,}|\b\w+_\w+\b|review_input@|evidence_points@|"
    r"bounded_claim_ids@|C:\\|/home/|\.json\b|\bnull\b"
)


class Node:
    def __init__(self, tag="root", attrs=(), parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []

    def all_text(self):
        return " ".join(child if isinstance(child, str) else child.all_text()
                        for child in self.children)

    def summary(self):
        return next((child for child in self.children
                     if isinstance(child, Node) and child.tag == "summary"), None)


class ReadingTree(HTMLParser):
    """Read native details semantics, rather than scanning hidden raw markup."""
    VOID = {"br", "hr", "img", "input", "meta", "link", "area", "base", "wbr", "source", "embed"}

    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.current = self.root
        self.nodes = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        self.nodes.append(node)
        if tag not in self.VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        node = self.current
        while node.parent is not None:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, text):
        self.current.children.append(text)

    def find_details(self, summary_contains):
        return [node for node in self.nodes if node.tag == "details"
                and node.summary() and summary_contains in node.summary().all_text()]

    def visible(self, opened=(), subtree=None):
        opened = set(opened)
        for node in list(opened):
            while node.parent:
                node = node.parent
                if node.tag == "details":
                    opened.add(node)

        def walk(node):
            if isinstance(node, str):
                return node
            if node.tag in {"script", "style"} or "hidden" in node.attrs:
                return ""
            if node.tag == "details" and node not in opened and "open" not in node.attrs:
                summary = node.summary()
                return walk(summary) if summary else ""
            return " ".join(walk(child) for child in node.children)

        return re.sub(r"\s+", " ", walk(subtree or self.root)).strip()


def _accepted_ui():
    candidate = _lineage_candidate()
    review = candidate["review_display"]["current"]["review"]
    original = "contract 与 wpem.level 均为 outside_liability；窗口 INSUFFICIENT 不构成安全判断。"
    review["q1"]["llm_explanation"]["points"][0] = {
        "text_cn": original, "evidence_refs": ["contract"]}
    review["decision_trace"]["input_evidence"].append({
        "ref": "contract", "label_cn": "本单合同责任", "group": "contract",
        "facts": {"side": "put", "short_strike_usd": 97000,
                  "long_strike_usd": 95000, "liability_zone": "outside_liability",
                  "adverse_direction_cn": "下跌"},
        "asof": 1790323200000, "scope": "current_contract",
        "source_family": "Options", "qualification": "PARTIAL",
        "limitations": ["不能据位置直接判断安全"],
        "source_path": "snapshot.contract", "content_hash": "a" * 64,
    })
    return candidate, original


def test_default_overview_has_no_machine_codes_and_preserves_original_review():
    candidate, original = _accepted_ui()
    result = render_workbench(_workbench([candidate]), capture=True)
    tree = ReadingTree(result["areas"]["candidateArea"])
    visible = tree.visible()
    assert not MACHINE_CODE.search(visible), visible
    assert "Q1" in visible and "Q2" in visible
    assert "责任区外" in visible and "不构成安全判断" in visible
    assert "主评审未准入" not in visible
    assert original in tree.root.all_text()
    frozen = result["captures"][0]["public_view"]["candidates"][0]
    assert frozen["review_display"]["current"]["review"]["q1"]["llm_explanation"]["points"][0]["text_cn"] == original
    assert frozen["review_gate"]["enabled"] is False


def test_point_evidence_first_depth_is_readable_and_raw_is_exactly_reachable():
    candidate, original = _accepted_ui()
    tree = ReadingTree(render_workbench(_workbench([candidate]))["areas"]["candidateArea"])
    point = tree.find_details("本点依据")[0]
    visible = tree.visible([point], subtree=point)
    assert "本单合同责任" in visible
    assert "下跌" in visible
    assert "97,000" in visible and "95,000" in visible
    assert "1790323200000" not in visible
    assert "不能据位置直接判断安全" in visible
    assert not MACHINE_CODE.search(visible), visible
    raw = [node for node in tree.nodes if node.tag == "details"
           and "ia-raw" in node.attrs.get("class", "")]
    assert raw
    raw_text = tree.visible(raw)
    assert "snapshot.contract" in raw_text and "a" * 64 in raw_text
    assert original in raw_text


def test_audit_summary_keeps_reading_and_rendering_distinct_without_raw_codes():
    candidate, _ = _accepted_ui()
    tree = ReadingTree(render_workbench(_workbench([candidate]))["areas"]["candidateArea"])
    audit = tree.find_details("复盘这版判断")[0]
    text = tree.visible([audit], subtree=audit)
    assert not MACHINE_CODE.search(text), text
    assert "未记录" in text and ("阅读" in text or "看过" in text)
    assert "原始" in text
    assert "1970/" not in text


def test_historical_and_rejected_content_cannot_win_default_primary():
    candidate = _lineage_candidate(accepted=False)
    tree = ReadingTree(render_workbench(_workbench([candidate]))["areas"]["candidateArea"])
    visible = tree.visible()
    assert "程序事实" in visible
    assert "模型 Q1 点一" not in visible and "模型 Q2 点一" not in visible
    assert "模型 Q1 点一" in tree.root.all_text()


def test_global_research_and_audit_are_closed_and_dom_targets_are_preserved():
    root = Path(__file__).resolve().parents[1]
    tree = ReadingTree((root / "deploy/signal_audit/frontend/index.html").read_text(encoding="utf-8"))
    ids = {node.attrs.get("id"): node for node in tree.nodes if node.attrs.get("id")}
    for key in ("researchLibrary", "auditLibrary"):
        assert ids[key].tag == "details" and "open" not in ids[key].attrs
    for key in ("candidateArea", "manualReviewState", "btcMap", "marketContext", "spaceGate", "dataHealth", "historyArea"):
        assert key in ids
    assert "Q1" in tree.visible() and "Q2" in tree.visible()


def test_all_accepted_points_and_numeric_qualifiers_survive_reading_mapping():
    candidate, _ = _accepted_ui()
    review = candidate["review_display"]["current"]["review"]
    points = [{"text_cn": f"第{index}点：-0.25%不是安全判断，24h/48h不能合并。contract 保持原义。",
               "evidence_refs": ["contract"]} for index in range(1, 6)]
    review["q1"]["llm_explanation"]["points"] = points
    output = render_workbench(_workbench([candidate]), capture=True)
    tree = ReadingTree(output["areas"]["candidateArea"])
    visible = tree.visible()
    for index in range(1, 6):
        assert f"第{index}点：-0.25%不是安全判断，24h/48h不能合并。" in visible
    frozen = output["captures"][0]["public_view"]["candidates"][0]
    assert frozen["review_display"]["current"]["review"]["q1"]["llm_explanation"]["points"] == points


def test_point_evidence_uses_frozen_input_and_preserves_unknown_observation_time():
    candidate, _ = _accepted_ui()
    review = candidate["review_display"]["current"]["review"]
    contract_input = review["decision_trace"]["input_evidence"][-1]
    contract_input["asof"] = None
    candidate["snapshot"]["contract"].update(short_strike_usd=123456, long_strike_usd=122222)
    tree = ReadingTree(render_workbench(_workbench([candidate]))["areas"]["candidateArea"])
    point = tree.find_details("本点依据")[0]
    visible = tree.visible([point], subtree=point)
    assert "97,000" in visible and "95,000" in visible
    assert "123,456" not in visible and "122,222" not in visible
    assert "1970" not in visible
