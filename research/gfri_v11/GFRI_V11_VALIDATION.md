# GFRI v1.1 Validation & Experimental Evidence Report

## 1. G0 Re-Verification & v1.0 Defects Resolved

Independent re-check of `.worktrees/gfri-v1-research/research/gfri_v1/` confirmed Snap1 (`gfri_down_1pct=3.0347`, `gfri_up_1pct=3.2011`) and Snap2 (`3.0882`, `3.5094`) exactness and identified 6 v1.0 gaps fixed in v1.1:
1. Float64 `1.0 + 0.005` IEEE-754 boundary rounding -> fixed via exact `decimal.Decimal` price bounds.
2. Missing GEX-spot vs OB-mid alignment gate -> added `>0.2%` `DEGRADED` and `>0.5%` `UNKNOWN` fail-closed gate.
3. Missing `requested_depth >= 1000` + minimum-levels check (without falsely breaking on real `<1000`-level Deribit books) -> fixed (`min_levels=10`, `requested_depth>=1000`, outer-band coverage).
4. Silent fallback to `spot_linked_12h` & `ERROR` status producing numeric GFRI -> eliminated (`price_only_curve` strict priority; `NET_GEX_LINEAR` forced `DEGRADED`; `ERROR/FAILED/UNAVAILABLE` forced `UNKNOWN`).
5. `now_ms=None` bypassing expiration -> `freeze_at_ms` always enforced as upper clock bound (no future data).
6. Audit/confirmation integration gaps (`DEGRADED` propagation, `bid_levels`/`ask_levels` key mapping, and stable 5-key `comparability` tuple in `history.py`) -> caught during live integration and verified fixed.

## 2. Automated Test Suite Results (Categories A–G)

Executed in repository root against `.worktrees/gfri-v11`:
- Command: `C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe -m pytest .worktrees/gfri-v11/research/gfri_v11/tests -v`
- Result: **`35 passed`** (`0 failed`, `0 skipped`).

| Category | Test File & Scope | Verified Behaviors |
| :--- | :--- | :--- |
| **A. Math & Units** | [`test_core.py`](tests/test_core.py) | Negative/positive Gamma signs, dampening clamp (`gfri=0.0` preserving `raw_hedge_flow_usd`), liquidity halving doubles GFRI, zero/negative denominator fails closed, exact Decimal boundary tick `50250.0` |
| **B. Data Quality** | [`test_core.py`](tests/test_core.py) | Missing/future/expired clocks, `ERROR -> UNKNOWN`, `DEGRADED -> DEGRADED`, no silent `spot_linked_12h` fallback, `depth=100` & truncated band rejection, `0.2%/0.5%` price divergence, spread/crossed book guards, Deribit `contract_size` |
| **C. Extremes & Confirm** | [`test_core.py`](tests/test_core.py) | Sustained stress across 3 books preserved; single transient blip rejected from `gfri_confirmed` while retained in `gfri_fast_max`; `shares_same_gex_snapshot=true`; partial side truncation; OKX `ctVal=0.01` non-blocking cross-venue calculation |
| **D. Historical Ref** | [`test_history_policy.py`](tests/test_history_policy.py) | 4h `<4` `WARMING_UP`, 60d cutoff, comparability mismatch exclusion, intraday cap against 200-sample day flood, `<7d`/`7-29d`/`>=30d` cold start (never fake `P99`), `regime_drift_warning`, driver decomposition |
| **E. Shadow Policy** | [`test_history_policy.py`](tests/test_history_policy.py) | Dampening `NO_VETO` + caveat, `UNKNOWN` never emits `NO_VETO`, `partial_pause_side`, blip suppression, 2-cycle entry, hysteresis hold at `2.2x` & 2-cycle release `<2.0x`, append-only `ShadowLedger`, minimal `<=12`-line renderer |
| **F/G. E2E & Isolation** | [`test_collector_and_isolation.py`](tests/test_collector_and_isolation.py) | Mocked 3-sample cycle + ledger roundtrip, `--scheduled` exits `0` cleanly when disabled, `gfri_v11.env.example` preflight, tampered `ShadowFlags` raises `ValueError` |

## 3. Live Real-Data Capture & Cross-Venue Evidence (`2026-11-18T14:46Z`)

Command: `$env:PYTHONPATH=".worktrees/gfri-v11"; C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe -m research.gfri_v11.collector --once --interval-sec 5 --save-sanitized-audit .worktrees/gfri-v11/research/gfri_v11/samples/gfri_v11_real_audit_sanitized.json`  
Sanitized deliverable: [`samples/gfri_v11_real_audit_sanitized.json`](samples/gfri_v11_real_audit_sanitized.json)

| Metric / Field | Live Observed Value (`freeze_at_ms = 1795013209448`) | Verification Note |
| :--- | :--- | :--- |
| **GEX Spot vs Deribit Mid** | `$63,138.64` vs `$63,138.75` (`0.00017%`) | `gex_data_ts`: `1795012929336` (`gex_age_ms = 280,112` = 4.67m) |
| **Hedge Flow `price_only_curve ±1%`** | DOWN `-$127,640,648.89` / UP `+$125,308,560.07` | Native `price_only_curve`; `total_open_interest = 394,356.6` (`8h` Deribit OI lag noted); `0dte_share = 4.23%` |
| **Deribit 3-Sample Depth (`±1%`)** | PUT median `$26,897,650` (`675` bid lvls) / CALL median `$17,903,810` (`984` ask lvls) | Intraday dump tightened Deribit liquidity vs earlier `12:47Z` (`$41.64M / $37.66M`); 3 samples `190972`, `197730`, `206917` |
| **GFRI Confirmed vs Fast Max** | **PUT `+4.75x`** (max `4.77x`) / **CALL `+7.00x`** (max `8.60x`) | Sample 3 ask depth thinned to `$14.58M` -> Fast channel kept `8.60x`, median confirmed `7.00x` |
| **OKX Cross-Venue Ref (`BTC-USDT-SWAP`)** | Bids `$104.91M` (`3.92x` sample-3 Deribit) -> `1.22x`; Asks `$86.92M` (`5.96x` sample-3 Deribit / `4.85x` vs `$17.90M` median) -> `1.44x` | Empirically proves single-venue sensitivity (`non_blocking = true`, `5000` levels) |
| **Decision & Data Quality** | `WARMING_UP` (`0/4` near, `0/30d` long) / **`DEGRADED`** | Accurately surfaced `secondary_venues_unavailable:bybit,okx,binance`; `SHADOW_ONLY`, `live_veto=false` |

## 4. Regression & Workspace Isolation Verification

- Production / sibling branch audit: Checked `git status` across root (`seal/snapshot`), `.worktrees/player-dossier-v13` (clean on `f3911c06`), and sibling `codex/player-ae-replay` / `codex/player-p2-contract` / `codex/llm-retirement-20261117` — zero shared modified files, zero Prompt A / dossier / FMZ edits, zero live scheduled tasks registered.
