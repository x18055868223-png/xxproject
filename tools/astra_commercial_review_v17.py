"""Frozen v1.7 unit economics; reuses v1.6 actions and prices, never trades or trains."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import astra_underwriting_v16 as core
import astra_commercial_math_v17 as math17

POLICIES = ("B0_ALWAYS", "B1_GEOM", "B2_STAT")
ACTUAL_UNKNOWN = ("actual_short_fill_btc", "actual_long_fill_btc", "actual_option_fee_btc",
                  "actual_hedge_net_btc", "actual_execution_net_btc", "actual_margin_btc",
                  "actual_operating_cost_btc", "actual_expected_EV_btc")


def utc(ms):
    return pd.to_datetime(ms, unit="ms", utc=True).isoformat()


def ratio_interval(dates, numerator, denominator, block_days=7, repetitions=4000):
    """Paired block ratio, including inactive days; a homogeneous cost sensitivity."""
    x, y = np.asarray(numerator, float), np.asarray(denominator, float)
    if len(x) != len(y) or len(x) != len(dates):
        raise ValueError("ratio lengths differ")
    if not np.isfinite(x).all() or not np.isfinite(y).all() or (y < 0).any():
        raise ValueError("unknown or invalid ratio inputs; cannot silently drop")
    if y.sum() <= 0:
        return {"mean": None, "lower": None, "upper": None, "active_weight": 0.0}
    blocks = (pd.to_datetime(dates).map(lambda t: (t-pd.Timestamp("1970-01-01")).days) // block_days)
    b = pd.DataFrame({"block": np.asarray(blocks), "x": x, "y": y}).groupby("block").sum()
    if len(b) < 2:
        return {"mean": float(x.sum()/y.sum()), "lower": None, "upper": None, "active_weight": float(y.sum())}
    rng = np.random.default_rng(core.SEED)
    ix = rng.integers(0, len(b), (repetitions, len(b)))
    den = b.y.to_numpy()[ix].sum(axis=1)
    num = b.x.to_numpy()[ix].sum(axis=1)
    good = den > 0
    sims = num[good]/den[good]
    return {"mean": float(x.sum()/y.sum()), "lower": float(np.quantile(sims,.025)),
            "upper": float(np.quantile(sims,.975)), "active_weight": float(y.sum()),
            "blocks": len(b), "block_days": block_days, "undefined_replicates": int((~good).sum())}


def verify_sources(seal_path):
    seal = core.read(seal_path)
    for path, expected in seal["source_hashes"].items():
        if core.digest(path) != expected:
            raise ValueError("sealed source changed: " + path)
    return seal


def actual_quote_appendix(research, out, q):
    amendment = core.read(research/"quote_appendix_amendment.json")
    seal = verify_sources(research/"quote_appendix_seal.json")
    if core.digest(research/"quote_appendix_amendment.json") != seal["amendment_sha256"]:
        raise ValueError("quote amendment changed")
    source = Path(amendment["original_quote_root"])
    comp = core.read(source/"comparison.json")
    snapshot = core.read(source/"snapshot/snapshot.json")
    receipt = core.read(research/"quote_appendix/delivery_receipt.json")
    st = receipt.get("selected_delivery", {}).get("delivery_price")
    if st is not None:
        if core.digest(research/"quote_appendix/delivery_response.json") != receipt["response_sha256"]:
            raise ValueError("new official delivery response changed")
        if st <= 0 or receipt["selected_delivery"]["date"] != "2026-09-15":
            raise ValueError("invalid delivery")
    rows = []
    for c in comp["candidates"]:
        if c["status"] != "observed_quote_basis":
            raise ValueError("quote status not eligible for arithmetic")
        meta = next(x for x in snapshot["candidates"] if x["id"] == c["id"])
        ps, pl = q*c["short_bid_btc"], q*c["long_ask_btc"]
        net = ps-pl  # This is BEFORE fees; old comparison.net_credit_btc was AFTER assumed fees.
        fee = q*c["assumed_standard_entry_fees_btc"]
        payout = None if st is None else math17.inverse_spread_payout(c["side"],c["short_strike"],c["long_strike"],st,q)
        cf = math17.static_cashflow(net,payout,fee)
        rows.append({"candidate_id": c["id"],"population": "fixed_quote_candidate_not_natural_NR",
            "quote_identity": "real_sequential_public_BBO_not_fills",
            "short_name": c["short_name"],"long_name": c["long_name"],"side": c["side"],
            "short_strike": c["short_strike"],"long_strike": c["long_strike"],
            "expiry_utc": utc(c["expiry_ms"]),"dte_hours": snapshot["dte_hours_at_start"],
            "snapshot_start_utc": snapshot["exchange_clock_utc"],
            "snapshot_captured_utc": snapshot["captured_at_utc"],
            "short_book_utc": utc(c["short_book_ms"]),"long_book_utc": utc(c["long_book_ms"]),
            "leg_time_gap_seconds": c["leg_time_gap_seconds"],
            "quantity_scenario": q,"actual_quantity": None,"index_price": snapshot["index_price"],
            "reference_btc": q*c["width_value_btc_at_index"],
            "short_bid_premium_btc": ps,"protection_ask_premium_btc": pl,
            "net_credit_before_fees_btc": net,"archived_standard_entry_fee_scenario_btc": fee,
            "credit_after_entry_fee_scenario_btc": net-fee,
            "max_expected_payout_before_remaining_costs_btc": net-fee,
            "minimum_observed_top_depth_btc": c["top_depth_btc"],
            "depth_is_fill_or_capacity_guarantee": False,
            "short_settlement_period": meta["short_metadata"]["settlement_period"],
            "long_settlement_period": meta["long_metadata"]["settlement_period"],
            "official_delivery_price": st,"native_spread_payout_btc": payout,
            "quoted_hold_to_expiry_net_after_archived_entry_fee_btc": cf["static_net_btc"],
            "actual_full_execution_net_btc": None,"actual_account_fees_btc": None,
            "actual_hedge_net_btc": None,"remaining_costs_btc": None,
            "risk_prediction_at_quote": None,"actual_EV": None,
            "outcome_identity": "post_freeze_official_delivery_appendix_only",
            "source_comparison_sha256": core.digest(source/"comparison.json"),
            "source_snapshot_sha256": core.digest(source/"snapshot/snapshot.json"),
            "outcome_response_sha256": receipt.get("response_sha256")})
    if {x["candidate_id"] for x in rows} != {"put_0","put_1","put_2","call_0","call_1","call_2"}:
        raise ValueError("original candidate denominator changed")
    core.save(out/"observed_quote_cashflows.json", rows)
    pd.DataFrame(rows).to_csv(out/"observed_quote_cashflows.csv", index=False)
    return rows


def run(root, research, run_name):
    out = research/run_name
    out.mkdir(exist_ok=False)
    core.save(out/"run_started.json", {"started_at_utc":datetime.now(timezone.utc).isoformat(),"protocol_sha256":core.digest(research/"protocol_v17.json")})
    try:
        seal = verify_sources(research/"protocol_seal.json")
        if core.digest(research/"protocol_v17.json") != seal["protocol_sha256"]:
            raise ValueError("protocol changed")
        protocol = core.read(research/"protocol_v17.json")
        prior = Path(protocol["inputs"]["prior_research"])
        q = protocol["scope"]["quantity_btc_equivalent"]
        base = pd.read_csv(prior/"run_01/base_rows.csv")
        primary = core.schedule(base,"UTC08")
        if len(primary) != 2914 or primary.row_id.duplicated().any():
            raise ValueError("primary population changed")
        needed = ["row_id","short_name","long_name","option_source_sha256","delivery_source_sha256","preoutcome_row_hash","outcome_status"]
        raw = pd.concat([pd.read_csv(p,usecols=needed) for p in (root/".artifacts/astra-joint-v11-20260915/step30/model_input").glob("model_rows-*.csv")])
        primary = primary.merge(raw,on="row_id",validate="one_to_one",how="left")
        if primary[needed].isna().any().any():
            raise ValueError("missing original identity")
        primary["quantity_scenario"] = q
        primary["reference_btc"] = q*primary.actual_width/primary.entry_price
        primary["native_payout_btc"] = q*primary.payout_btc
        calc = [math17.inverse_spread_payout(x.side,x.short_strike,x.long_strike,x.settlement_price,q) for x in primary.itertuples()]
        payout_error = float(np.max(np.abs(np.asarray(calc)-primary.native_payout_btc)))
        if payout_error > 1e-12:
            raise ValueError("native true payoff mismatch")
        if not (primary.price_observation_ms.lt(primary.as_of_ms) & primary.short_creation_ms.le(primary.as_of_ms) & primary.long_creation_ms.le(primary.as_of_ms)).all():
            raise ValueError("known-time contract violation")
        prior_summary = core.read(prior/"run_01/underwriting_summary.json")
        summaries, ledgers = [], []
        max_quote_error = max_prior_error = 0.0
        for scenario in protocol["inputs"]["main_prices"]:
            entry = pd.read_csv(prior/f"run_01/entry_UTC08_{scenario}.csv")
            columns = ["row_id","credit","cost","assumed_iv"]+[f"{p}_{kind}" for p in POLICIES for kind in ("action","pnl")]
            f = primary.merge(entry[columns],on="row_id",how="left",validate="one_to_one")
            if f[columns].isna().any().any():
                raise ValueError("missing prior scenario row")
            f["scenario"] = scenario
            f["price_identity"] = "shared_exogenous_scenario_not_BBO"
            f["quantity_identity"] = "hypothetical_0.1_not_account_capacity"
            f["input_known_by_utc"] = f.as_of_ms.map(utc)
            f["label_maturity_utc"] = f.expiry_ms.map(utc)
            prices = [math17.bs_leg_prices_btc(x.side,x.entry_price,x.short_strike,x.long_strike,x.dte_hours,x.assumed_iv,q) for x in f.itertuples()]
            f["short_premium_scenario_btc"], f["protection_premium_scenario_btc"] = np.asarray(prices).T
            if not np.isfinite(np.asarray(prices)).all() or (np.asarray(prices)<0).any():
                raise ValueError("nonfinite or negative synthetic leg premium; do not clip")
            f["net_credit_before_fees_btc"] = f.credit*f.reference_btc
            f["option_cost_scenario_btc"] = f.cost*f.reference_btc
            err = np.max(np.abs(f.short_premium_scenario_btc-f.protection_premium_scenario_btc-f.net_credit_before_fees_btc))
            max_quote_error = max(max_quote_error,float(err))
            if err > 1e-12:
                raise ValueError("leg quote decomposition differs from frozen v16")
            f["all_contract_static_net_scenario_btc"] = f.net_credit_before_fees_btc-f.native_payout_btc-f.option_cost_scenario_btc
            f["conditional_terminal_USD_if_BTC_cashflows_held"] = f.all_contract_static_net_scenario_btc*f.settlement_price
            for model in ("geom","stat"):
                f[f"expected_spread_payout_{model}_btc"] = f[f"mu_{model}"]*f.reference_btc
                f[f"required_net_credit_{model}_scenario_btc"] = f[f"expected_spread_payout_{model}_btc"]+f.option_cost_scenario_btc
                f[f"required_short_premium_{model}_scenario_btc"] = f.protection_premium_scenario_btc+f[f"required_net_credit_{model}_scenario_btc"]
                f[f"estimated_margin_{model}_scenario_btc"] = f.net_credit_before_fees_btc-f[f"required_net_credit_{model}_scenario_btc"]
                f[f"extra_cost_headroom_{model}_estimated_norm"] = f.credit-f[f"mu_{model}"]-f.cost
            for col in ACTUAL_UNKNOWN:
                f[col] = np.nan
            f["actual_fee_status"] = "unknown; aggregate scenario cost is not an account fee"
            f["hedge_status"] = "static_unhedged_diagnostic; actual hedge unknown"
            for policy, model in zip(POLICIES,(None,"geom","stat")):
                a = f[f"{policy}_action"].to_numpy(bool)
                expected_action = np.ones(len(f),bool) if model is None else (f.credit-f[f"mu_{model}"]-f.cost).to_numpy()>0
                if not np.array_equal(a,expected_action):
                    raise ValueError("old actions do not match frozen definition")
                pnl = a*(f.credit-f.loss_normalized-f.cost)
                err = np.max(np.abs(pnl-f[f"{policy}_pnl"]))
                max_prior_error = max(max_prior_error,float(err))
                if err > 1e-12:
                    raise ValueError("old pnl changed")
                f[f"{policy}_net_scenario_btc"] = pnl*f.reference_btc
            for side in ("put","call"):
                g = f.loc[f.side.eq(side)].copy()
                if len(g)!=1457 or g.delivery_date.duplicated().any() or not g.original_weight.eq(1).all():
                    raise ValueError("daily side budget changed")
                old = next(x for x in prior_summary if x["schedule"]=="UTC08" and x["scenario"]==scenario and x["side"]==side)
                for policy,model in zip(POLICIES,(None,"geom","stat")):
                    a = g[f"{policy}_action"].to_numpy(bool)
                    pnl = g[f"{policy}_pnl"].to_numpy()
                    btc = g[f"{policy}_net_scenario_btc"].to_numpy()
                    excess = ratio_interval(g.delivery_date,pnl,a)
                    excess["block14"] = ratio_interval(g.delivery_date,pnl,a,14)
                    excess_btc = ratio_interval(g.delivery_date,btc,a)
                    mu = None if model is None else g[f"mu_{model}"].to_numpy()
                    selected_mean = lambda x: float(np.asarray(x)[a].mean()) if a.any() else None
                    summary = {"side":side,"scenario":scenario,"policy":policy,"original_days":len(g),"action_days":int(a.sum()),
                        "coverage":float(a.mean()),"data_identity":"seen_history_static_scenario","actual_EV":None,
                        "mean_net_norm":float(pnl.mean()),"mean_net_btc_at_q01":float(btc.mean()),
                        "sum_net_btc_at_q01":float(btc.sum()),"absolute_norm":core.contrast(g,pnl),
                        "absolute_btc_at_q01":core.contrast(g,btc),
                        "extra_cost_headroom_norm_per_fixed_action":excess,
                        "extra_cost_headroom_btc_per_fixed_q01_action":excess_btc,
                        "has_nonnegative_extra_cost_headroom_at_point":bool(excess["mean"] is not None and excess["mean"]>=0),
                        "selected_mean_short_premium_btc":selected_mean(g.short_premium_scenario_btc),
                        "selected_mean_protection_premium_btc":selected_mean(g.protection_premium_scenario_btc),
                        "selected_mean_net_credit_norm":selected_mean(g.credit),
                        "selected_mean_actual_payout_norm":selected_mean(g.loss_normalized),
                        "selected_mean_cost_norm":selected_mean(g.cost),
                        "selected_mean_predicted_margin_norm":None if mu is None else selected_mean(g.credit-mu-g.cost),
                        "selected_mean_risk_underprediction_norm":None if mu is None else selected_mean(g.loss_normalized-mu),
                        "net_loss_ES95_norm":core.weighted_es(-pnl,np.ones(len(g))),
                        "net_loss_ES95_btc_at_q01":core.weighted_es(-btc,np.ones(len(g))),
                        "max_net_loss_norm":float((-pnl).max()),"max_native_payout_btc":float(g.native_payout_btc.max()),
                        "D_above_1_original":int(g.loss_normalized.gt(1).sum()),
                        "D_above_1_selected":int((g.loss_normalized.gt(1)&a).sum()),
                        "foregone_profitable_fraction_original":float(((~a)&((g.credit-g.loss_normalized-g.cost)>0)).mean()),
                        "foregone_positive_norm_per_original":float(np.where(~a,np.maximum(g.credit-g.loss_normalized-g.cost,0),0).mean()),
                        "known_scenario_fraction":1.0,"actual_quote_fraction":0.0,"actual_execution_fraction":0.0,
                        "delta_vs_B0":core.contrast(g,pnl-g.B0_ALWAYS_pnl),
                        "delta_vs_B1":core.contrast(g,pnl-g.B1_GEOM_pnl),
                        "previous_v16_method_metrics":old["methods"][policy]}
                    if abs(summary["mean_net_norm"]-old["methods"][policy]["complete_net_per_all_original_weight"])>1e-12:
                        raise ValueError("v16 summary changed")
                    summaries.append(summary)
            ledgers.append(f)
        ledger = pd.concat(ledgers,ignore_index=True)
        ledger.to_csv(out/"unit_cashflow_ledger.csv",index=False)
        core.save(out/"price_feasibility.json",summaries)
        flat = []
        for s in summaries:
            h=s["extra_cost_headroom_norm_per_fixed_action"]
            flat.append({k:s[k] for k in ("side","scenario","policy","original_days","action_days","coverage","mean_net_norm","mean_net_btc_at_q01","net_loss_ES95_norm","foregone_profitable_fraction_original","selected_mean_predicted_margin_norm","selected_mean_risk_underprediction_norm")} |
                        {"absolute_lower":s["absolute_norm"]["lower"],"absolute_upper":s["absolute_norm"]["upper"],"extra_cost_headroom_norm":h["mean"],"headroom_lower":h["lower"],"headroom_upper":h["upper"],"actual_EV":None})
        pd.DataFrame(flat).to_csv(out/"price_feasibility.csv",index=False)
        first=primary.loc[primary.side.eq("put")].sort_values(["as_of_ms","row_id"]).iloc[0]
        examples={"selection":"earliest complete original UTC08 Put by time,row_id; not outcome selected",
                  "historical_candidate":ledger.loc[ledger.row_id.eq(first.row_id)].replace({np.nan:None}).to_dict("records"),
                  "synthetic_boundaries":[]}
        for side,short,long,settlements in [("put",100000,98000,[101000,99000,97000,50000]),("call",100000,102000,[99000,101000,103000,150000])]:
            for st in settlements:
                d=math17.inverse_spread_payout(side,short,long,st,q)
                examples["synthetic_boundaries"].append({"side":side,"short":short,"long":long,"entry_spot":100000,"settlement":st,"quantity":q,
                    "reference_btc":q*abs(short-long)/100000,"payout_btc":d,"D":d/(q*abs(short-long)/100000),"identity":"synthetic stress, not probability or historical outcome"})
        core.save(out/"native_examples.json",examples)
        quotes=actual_quote_appendix(research,out,q)
        core.save(out/"completion_receipt.json",{"completed_at_utc":datetime.now(timezone.utc).isoformat(),
            "primary_rows":len(primary),"scenario_ledger_rows":len(ledger),"policy_summaries":len(summaries),
            "quote_appendix_candidates":len(quotes),"native_payoff_max_error":payout_error,"prior_action_pnl_max_error":max_prior_error,"quote_leg_max_error":max_quote_error,
            "source_hashes":{str(Path(__file__)):core.digest(__file__),str(Path(math17.__file__)):core.digest(math17.__file__),str(Path(core.__file__)):core.digest(core.__file__)},
            "protocol_sha256":seal["protocol_sha256"],"training_jobs":0,"new_rules_adopted":False,"actual_EV":None,
            "output_csv_unknown_encoding":"empty cell; never zero","public_outcome_new_data_identity":"appendix only; no historical merge"})
        print(json.dumps({"run":str(out),"rows":len(ledger),"summaries":len(summaries),"native_error":payout_error}))
    except Exception as exc:
        core.save(out/"failure.json",{"failed_at_utc":datetime.now(timezone.utc).isoformat(),"error":type(exc).__name__+": "+str(exc)})
        raise


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--research",type=Path,required=True)
    parser.add_argument("--run-name",required=True)
    args=parser.parse_args()
    if Path(args.run_name).name!=args.run_name:
        raise ValueError("run-name must be a directory name")
    run(args.root,args.research,args.run_name)
