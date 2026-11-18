# GFRI v1.1 Architecture Specification

> **Status**: Shadow-Only Engineering Candidate (`decision_authority = SHADOW_ONLY`, `live_veto_enabled = false`, `player_integration_enabled = false`, `collector_enabled = false`)  
> **Baseline Commit**: `f3911c06` (`xxproject/claude/astra`) on isolated branch `codex/gfri-v11`  

## 1. Purpose & Design Boundary

**GFRI (Gamma Feedback Risk Indicator)** is a standalone, deterministic quantitative risk gauge measuring the ratio between option-dealer trend-amplifying hedge demand under a standardized ±1% BTC spot shock and visible perpetual order-book liquidity on the shock side.

- **Does**: Quantify directional feedback stress (`PUT` on DOWN -1%, `CALL` on UP +1%) with fail-closed quality gates, 3-sample confirmation, near/long historical references, and deterministic shadow policy tracking.
- **Does NOT**: Predict spot direction or crash probability, estimate full cross-market slippage, infer true dealer inventory direction, construct 0–100 composite scores, or duplicate AI player Pin / Gamma-wall / responsibility-path reasoning.

## 2. Minimal Module Architecture

```text
[GEXMonitor gex-latest] + [Deribit BTC-PERPETUAL depth=1000 (x3)] + [OKX BTC-USDT-SWAP (optional ref)]
                                      │
                                      ▼
                     research/gfri_v11/core.py
        (Decimal bands, quality/skew/basis gates, Fast+Confirm median)
                                      │
                                      ▼
                     research/gfri_v11/history.py
        (Append-only ShadowLedger JSONL, 4h near ref, 60d/14d half-life ref)
                                      │
                                      ▼
                     research/gfri_v11/policy.py
        (Hysteresis state machine -> NO_VETO / SHADOW_PAUSE_* / WARMING_UP / UNKNOWN
         + minimal <=12-line console renderer + gfri_audit_v11 JSON)
                                      │
                                      ▼
     research/gfri_v11/collector.py  <──  deploy/gfri_v11/ (systemd oneshot, DEFAULT OFF)
```

| Module / Asset | Responsibility | Authority / Safety Lock |
| :--- | :--- | :--- |
| [`config.py`](config.py) | `GfriConfig` & `ShadowFlags`; holds endpoints, gates, and `UNVALIDATED_CANDIDATE_PARAMS` | `ShadowFlags.validate_locked()` raises on `live_veto`, `player_integration`, or non-`SHADOW_ONLY` |
| [`core.py`](core.py) | GEX `price_only_curve` extraction, Deribit/OKX depth, Decimal ±1% bounds, 3-sample Fast+Confirm | Fail-closed `UNKNOWN` on stale/future/truncated/divergent inputs; zero fallback to `spot_linked_12h` |
| [`history.py`](history.py) | Append-only `ShadowLedger` (`O_APPEND` + `fsync` + lockfile), 4h near driver decomposition, 60d/14d rolling ref | Cold-start safe (`ACCUMULATING` <7d, `COARSE_BAND_ONLY` <30d/ESS<30); anti-masking `regime_drift_warning` |
| [`policy.py`](policy.py) | Deterministic per-side hysteresis state machine, sensitivity presets, minimal console view, audit builder | Emits only `NO_VETO / SHADOW_PAUSE_{PUT,CALL,BOTH} / WARMING_UP / UNKNOWN`; never equates `NO_VETO` to safe |
| [`collector.py`](collector.py) | HTTP JSON+SHA256 fetcher, 3-sample pre-freeze loop, `--scheduled` (off by default) & `--once` dry run | Exits `0` (`COLLECTOR_DISABLED`) in `--scheduled` when `GFRI_COLLECTOR_ENABLED=0` |
| [`../../deploy/gfri_v11/`](../../deploy/gfri_v11/gfri_v11.env.example) | Systemd oneshot/timer templates, env template, preflight validator, enable/disable/rollback scripts | Not installed or enabled on host; blocked until external acceptance sign-off |

## 3. Isolation & Non-Interference Guarantees

1. **Zero Touch on Existing Production / Sibling Branches**: All v1.1 code lives in `research/gfri_v11/` and `deploy/gfri_v11/`. Zero modifications to `tools/`, `05_GEX监控API_数据增强接口/`, Prompt A, AI player formal inputs, `case_file` schema, FMZ scripts, or `.worktrees/{player-dossier-v13,player-ae-replay,player-p2-contract,llm-retirement-20261117}`.
2. **Direct Upstream Path**: Fetches GEXMonitor `gex-latest` directly instead of `gexmonitorapi /v1/info` (which strips `price_only_curve`, `units`, and `model`, leaving only 12h charm/IV-shifted `scenarios`).
3. **Prospective Only**: Because historical `gex-latest` curves and Deribit `depth=1000` books are absent for historical dossiers (`N=1` closed settlement), v1.1 operates Day-1 without percentiles and relies on default-off prospective shadow accumulation once approved.
