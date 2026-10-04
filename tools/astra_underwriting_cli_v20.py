#!/usr/bin/env python3
"""CLI for local Astra v2.0 underwriting snapshots.

It reads one JSON payload, optionally records it to an append-only ledger,
and can emit a card annex plus the packet that a separate human/LLM review
process may read. It never calls a model endpoint or places orders.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

if __package__:
    from . import astra_underwriting_store_v20 as store
    from . import astra_underwriting_v20 as core
    from . import astra_underwriting_fact_context_v20 as fmz_facts
    from . import astra_underwriting_public_quote_v20 as public_quote
else:  # direct execution from tools/
    import astra_underwriting_store_v20 as store
    import astra_underwriting_v20 as core
    import astra_underwriting_fact_context_v20 as fmz_facts
    import astra_underwriting_public_quote_v20 as public_quote


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="Frozen underwriting payload JSON.")
    source.add_argument("--public-short", help="Explicit short Deribit BTC instrument; read-only public capture.")
    parser.add_argument("--public-long", help="Explicit protective Deribit BTC instrument.")
    parser.add_argument("--quantity-btc", type=float, help="Target BTC option quantity for public capture.")
    parser.add_argument("--fmz-snapshots", type=Path,
                        help="Existing FMZ EvaluationSnapshot JSONL to bind, without modifying it.")
    parser.add_argument("--ledger", type=Path, help="Optional append-only underwriting JSONL ledger.")
    parser.add_argument(
        "--origin",
        choices=sorted(core.ORIGINS),
        action="append",
        default=[],
        help="Entry origin to add to the payload origins. May be repeated.",
    )
    parser.add_argument("--annex-output", type=Path, help="Optional card annex JSON output.")
    parser.add_argument("--packet-output", type=Path, help="Optional LLM applicability packet JSON output.")
    parser.add_argument("--review", type=Path, help="Optional pre-existing review JSON to bind to the snapshot.")
    parser.add_argument("--snapshot-id", help="Optional explicit snapshot id for review binding.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.public_short:
        if not args.public_long or args.quantity_btc is None:
            raise ValueError("--public-short needs --public-long and --quantity-btc")
        payload = public_quote.capture_candidate(args.public_short, args.public_long,
                                                 args.quantity_btc)
    else:
        if args.public_long or args.quantity_btc is not None:
            raise ValueError("--public-long and --quantity-btc require --public-short")
        payload = store.load_payload(args.input)
    if args.fmz_snapshots:
        valuation = int(payload["market"]["valuation_ts_ms"])
        payload["fmz_fact_context"] = fmz_facts.latest_from_jsonl(args.fmz_snapshots, valuation)
    if args.origin:
        payload = store.add_origins(payload, args.origin)

    if args.ledger:
        ledger = store.UnderwritingLedger(args.ledger)
        annex = ledger.record_payload(payload)
        if args.review:
            review = store.read_json(args.review)
            annex = ledger.attach_review(
                review,
                snapshot_id=args.snapshot_id or annex["snapshot"]["snapshot_id"],
            )
        packet = ledger.llm_packet(annex["snapshot"]["snapshot_id"]) if args.packet_output else None
    else:
        if args.review:
            raise ValueError("--review requires --ledger so the snapshot binding can be checked")
        snapshot = core.evaluate(payload)
        annex = store.transient_card_annex(snapshot)
        packet = core.llm_applicability_packet(snapshot) if args.packet_output else None

    if args.annex_output:
        store.write_json(args.annex_output, annex)
    if args.packet_output and packet is not None:
        store.write_json(args.packet_output, packet)

    sys.stdout.write(store._canonical(annex) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
