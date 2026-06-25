import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]
ARCHIVE = pathlib.Path(
    r"C:\Users\Xu\Documents\信号审计前端页面设计\archives"
    r"\signal-audit-final-20260618"
)
DEPLOY_FRONTEND = ROOT / "deploy" / "signal_audit" / "frontend"


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def read(path):
    return path.read_text(encoding="utf-8")


def json_files(root):
    return sorted(path for path in (root / "signal_cards").glob("*.json")
                  if path.name != "index.json")


def has_rank(card):
    gex = (((card.get("factor_cross_section") or {}).get("gex_info")) or {})
    rank = gex.get("rank")
    return (
        isinstance(rank, dict)
        and isinstance(rank.get("window"), dict)
        and isinstance(rank.get("metrics"), dict)
        and "gex_board.total_net_gex" in rank["metrics"]
    )


def first_fixture_with_rank(root):
    for path in json_files(root):
        card = json.loads(path.read_text(encoding="utf-8"))
        if has_rank(card):
            return path, card
    return None, None


def main():
    roots = [DEPLOY_FRONTEND]
    archive_is_current = (
        ARCHIVE.exists()
        and (ARCHIVE / "app.js").exists()
        and (ARCHIVE / "index.html").exists()
        and read(ARCHIVE / "app.js") == read(DEPLOY_FRONTEND / "app.js")
        and read(ARCHIVE / "index.html") == read(DEPLOY_FRONTEND / "index.html")
    )
    if archive_is_current:
        roots.append(ARCHIVE)
    for root in roots:
        assert_true(root.exists(), "frontend root missing " + str(root))
        app = read(root / "app.js")
        html = read(root / "index.html")
        assert_true("function renderGexRank(doc)" in app,
                    "app.js should define renderGexRank")
        assert_true("GEX Rank 分位" in app,
                    "rank section title should be in app.js")
        gamma_idx = app.find("${renderGammaOverview(doc)}")
        rank_idx = app.find("${renderGexRank(doc)}")
        session_idx = app.find("${renderSignalSessionContext(doc)}")
        decision_idx = app.find("${renderDecision(doc)}")
        decision_matrix_idx = app.find("${renderDecisionMatrix(doc)}")
        assert_true(gamma_idx != -1 and rank_idx != -1 and session_idx != -1,
                    "renderDocument should call gamma, rank, and session-context sections")
        assert_true(gamma_idx < rank_idx < session_idx,
                    "rank section should render after Gamma/GEX and before session/context sections")
        assert_true(decision_idx == -1 and decision_matrix_idx == -1,
                    "renderDocument should not call removed low-signal decision sections")
        assert_true(".rank-grid" in html and ".rank-note" in html,
                    "index.html should include compact rank styles")

        path, card = first_fixture_with_rank(root)
        assert_true(path is not None, "at least one local fixture should include rank")
        metrics = card["factor_cross_section"]["gex_info"]["rank"]["metrics"]
        assert_true(metrics["gex_board.total_net_gex"].get("abs_rank_pct") is not None,
                    "netGEX rank fixture should include abs_rank_pct")

        fallback = read(root / "signal_cards" / "fallback.js")
        assert_true("\"rank\"" in fallback and "gex_board.total_net_gex" in fallback,
                    "fallback.js should include rank fixture data for file mode")

    if archive_is_current:
        assert_true(read(ARCHIVE / "app.js") == read(DEPLOY_FRONTEND / "app.js"),
                    "archive and deploy app.js should stay mirrored")
        assert_true(read(ARCHIVE / "index.html") == read(DEPLOY_FRONTEND / "index.html"),
                    "archive and deploy index.html should stay mirrored")
    print("signal_audit_frontend_rank_contract: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_audit_frontend_rank_contract: FAIL - " + str(exc))
        sys.exit(1)
