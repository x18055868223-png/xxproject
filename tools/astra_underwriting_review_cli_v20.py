#!/usr/bin/env python3
"""Explicit one-candidate LLM applicability review using the shared HTTP ledger.

No scheduler invokes this entrypoint. It cannot review synthetic or historical
replay cards, and it never changes the contract/economics snapshot.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

if __package__:
    from . import astra_underwriting_card_bridge_v20 as bridge
    from . import astra_underwriting_review_v20 as review
    from . import astra_underwriting_store_v20 as store
    from . import signal_llm_review as llm_core
else:
    import astra_underwriting_card_bridge_v20 as bridge
    import astra_underwriting_review_v20 as review
    import astra_underwriting_store_v20 as store
    import signal_llm_review as llm_core


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--usage-ledger", type=Path, required=True,
                        help="The existing normal-review signal_llm_usage_ledger.json; never a new quota file.")
    parser.add_argument("--model", default=llm_core.DEFAULT_MODEL)
    parser.add_argument("--base-url", default=os.environ.get("LLM_BASE_URL") or None)
    parser.add_argument("--daily-http-limit", type=int, default=llm_core.DEFAULT_DAILY_HTTP_LIMIT)
    parser.add_argument("--annex-output", type=Path)
    parser.add_argument("--site-root", type=Path,
                        help="Optional explicit site copy for this independent candidate card.")
    args = parser.parse_args(argv)
    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        parser.error("LLM_API_KEY must be available in the environment")
    ledger = store.UnderwritingLedger(args.ledger)
    state = ledger.state()
    entry = state["snapshots"].get(args.snapshot_id)
    if entry is None:
        parser.error("snapshot id does not exist in the selected underwriting ledger")
    budget = llm_core.DailyHttpBudget(args.usage_ledger, limit=args.daily_http_limit)
    result = review.run_review(entry["snapshot"], ledger, args.state_dir,
                               api_key=api_key, model=args.model, budget=budget,
                               endpoint=args.base_url)
    annex = store.build_card_annex(ledger.state(), args.snapshot_id)
    annex["review_attempt"] = {
        "schema": "astra_underwriting_review_attempt_summary@2.0.0",
        "snapshot_id": args.snapshot_id,
        "candidate_id": entry["snapshot"]["candidate_id"],
        "status": result.get("status"),
        "http_calls": result.get("http_calls", 0),
    }
    if args.annex_output:
        store.write_json(args.annex_output, annex)
    if args.site_root:
        bridge.export_to_site(annex, args.site_root)
    print(json.dumps({"status": result.get("status"),
                      "reviewed_snapshot_id": result.get("reviewed_snapshot_id"),
                      "http_calls": result.get("http_calls", 0)},
                     ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") in {"OK", "snapshot_changed_no_automatic_recall",
                                       "daily_budget_deferred"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
