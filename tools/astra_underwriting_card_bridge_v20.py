#!/usr/bin/env python3
"""Export one frozen underwriting annex as an independent local audit card.

This path only adds a candidate card. It never rematerializes an old signal card
or touches its existing LLM review. Site export is explicit and read-only with
respect to all pre-existing card files.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Mapping

if __package__:
    from . import astra_underwriting_store_v20 as store
    from . import astra_underwriting_v20 as core
    from . import astra_underwriting_review_v20 as review_core
else:
    import astra_underwriting_store_v20 as store
    import astra_underwriting_v20 as core
    import astra_underwriting_review_v20 as review_core


ATTEMPT_STATUSES = {
    "OK",
    "rejected",
    "failed",
    "daily_budget_deferred",
    "attempt_budget_exhausted",
    "persistence_failed",
    "snapshot_changed_no_automatic_recall",
    "stale_protocol_review",
}


def _review_for_card(
    snapshot: Mapping[str, Any],
    packet: Mapping[str, Any],
    review_entry: Mapping[str, Any],
) -> dict[str, Any] | None:
    review = review_entry.get("review") if review_entry.get("status") == "attached" else None
    if review_entry.get("status") == "attached" and review is None:
        raise ValueError("attached review body is missing")
    if review is None:
        return None
    if snapshot["data_identity"]["kind"] in {"historical_replay", "synthetic_fixture"}:
        raise ValueError("historical or synthetic candidate cannot present a live review")
    if (not isinstance(review, Mapping)
            or review.get("schema") != "astra_underwriting_causal_review@2.0.0"
            or review.get("status") != "OK"
            or review.get("reviewed_snapshot_id") != snapshot["snapshot_id"]
            or review.get("candidate_id") != snapshot["candidate_id"]
            or review.get("packet_hash") != core._hash(packet)
            or review_entry.get("review_hash") != store._hash(review)):
        raise ValueError("attached review does not match the frozen candidate packet")
    if review.get("response_protocol") == review_core.RESPONSE_PROTOCOL:
        store._validate_same_snapshot_review(snapshot, review)
        return deepcopy(review)
    legacy = deepcopy(dict(review))
    legacy["legacy_status"] = legacy.get("status")
    legacy["status"] = "stale_protocol_review"
    legacy["legacy_review"] = True
    legacy["current_protocol_qualified"] = False
    legacy["expected_response_protocol"] = review_core.RESPONSE_PROTOCOL
    return legacy


def annex_to_card(annex: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(annex, Mapping) or annex.get("schema") != store.CARD_ANNEX_SCHEMA:
        raise ValueError("v2.0 underwriting card annex is required")
    snapshot = deepcopy(annex.get("snapshot"))
    if not isinstance(snapshot, dict):
        raise ValueError("annex snapshot is missing")
    packet = core.llm_applicability_packet(snapshot)
    ledger_info = annex.get("store") or {}
    if ledger_info.get("snapshot_id") != snapshot["snapshot_id"]:
        raise ValueError("annex store and snapshot identities disagree")
    if annex.get("execution_allowed") is not False or snapshot.get("execution_allowed") is not False:
        raise ValueError("candidate card must remain read-only")
    review_entry = annex.get("review_annex") or {}
    review = _review_for_card(snapshot, packet, review_entry)
    attempt = annex.get("review_attempt")
    if attempt is not None:
        if (not isinstance(attempt, Mapping)
                or attempt.get("schema") != "astra_underwriting_review_attempt_summary@2.0.0"
                or attempt.get("snapshot_id") != snapshot["snapshot_id"]
                or attempt.get("candidate_id") != snapshot["candidate_id"]
                or attempt.get("status") not in ATTEMPT_STATUSES
                or type(attempt.get("http_calls")) is not int
                or not 0 <= attempt["http_calls"] <= 2):
            raise ValueError("review attempt does not match frozen candidate")
        if (review is not None
                and attempt["status"] != "OK"
                and not (review.get("status") == "stale_protocol_review"
                         and attempt["status"] == "stale_protocol_review")):
            raise ValueError("attached review contradicts attempt status")
    as_of = datetime.fromtimestamp(snapshot["market"]["valuation_ts_ms"] / 1000, tz=timezone.utc)
    identity = snapshot["data_identity"]
    card_id = "underwriting-" + snapshot["snapshot_id"][:24]
    identity_label = {"historical_replay": "历史回放", "synthetic_fixture": "合成演示",
                      "live_public_capture": "公开盘口采集截面", "manual_input": "手动输入"}[identity["kind"]]
    return {
        "schema": "astra_underwriting_display_card@2.0.0",
        "identity": {"card_id": card_id, "short_id": snapshot["snapshot_id"][:8],
                     "confirmed_at": as_of.isoformat(), "symbol": "BTC",
                     "strategy_name": "Astra 当前候选保单只读审计",
                     "event_type": "UNDERWRITING_CANDIDATE",
                     "is_synthetic": identity["kind"] == "synthetic_fixture"},
        "quality": {"overall": "CANDIDATE_ONLY"},
        "display_layers": {"headline": "候选保单 · " + snapshot["contract"]["side"].title()
                           + " · " + identity_label},
        "underwriting_snapshot": snapshot,
        "underwriting_review": deepcopy(review) if review is not None else None,
        "underwriting_review_attempt": deepcopy(attempt) if attempt is not None else None,
        "underwriting_store": deepcopy(ledger_info),
        "execution_allowed": False,
    }


def export_to_site(annex: Mapping[str, Any], site_root: str | Path) -> Path:
    """Append one independent card to a selected site copy; refuse overwrite."""
    card = annex_to_card(annex)
    root = Path(site_root).resolve()
    index_path = root / "signal_cards" / "index.json"
    if not index_path.is_file():
        raise ValueError("selected site has no signal_cards/index.json")
    index = store.read_json(index_path)
    if not isinstance(index, dict) or not isinstance(index.get("cards"), list):
        raise ValueError("site card manifest is invalid")
    card_id = card["identity"]["card_id"]
    relative = f"signal_cards/{card_id}.json"
    target = root / relative
    new_bytes = (store._canonical(card) + "\n").encode("utf-8")
    prior = None
    if target.exists() and target.read_bytes() != new_bytes:
        prior = store.read_json(target)
        if (not isinstance(prior, dict)
                or prior.get("schema") != card["schema"]
                or (prior.get("underwriting_snapshot") or {}).get("snapshot_id") != card["underwriting_snapshot"]["snapshot_id"]
                or prior.get("underwriting_review") is not None
                or card.get("underwriting_review") is None):
            raise ValueError("existing candidate card differs; historical card cannot be overwritten")
        core.llm_applicability_packet(prior["underwriting_snapshot"])
    existing = [row for row in index["cards"] if row.get("card_id") == card_id]
    if existing and (len(existing) != 1 or existing[0].get("path") != relative):
        raise ValueError("candidate card manifest identity conflicts")
    if not target.exists() or prior is not None:
        temporary = target.with_suffix(".json.tmp")
        temporary.write_bytes(new_bytes)
        os.replace(temporary, target)
    if not existing:
        index["cards"].append({"card_id": card_id,
                               "confirmed_at": card["identity"]["confirmed_at"],
                               "path": relative, "quality": "CANDIDATE_ONLY", "symbol": "BTC",
                               "event_type": "UNDERWRITING_CANDIDATE"})
        index["cards"][-1]["summary"] = {"display_layers": card["display_layers"]}
        store.write_json(index_path, index)
    elif (existing[0].get("event_type") != "UNDERWRITING_CANDIDATE"
          or existing[0].get("summary") != {"display_layers": card["display_layers"]}):
        existing[0]["event_type"] = "UNDERWRITING_CANDIDATE"
        existing[0]["summary"] = {"display_layers": card["display_layers"]}
        store.write_json(index_path, index)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annex", required=True, type=Path)
    parser.add_argument("--site-root", required=True, type=Path)
    args = parser.parse_args()
    path = export_to_site(store.read_json(args.annex), args.site_root)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
