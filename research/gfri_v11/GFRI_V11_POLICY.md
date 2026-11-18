# GFRI v1.1 Historical Reference, Cold Start & Shadow Policy

> **Calibration Notice**: Every window length, weighting parameter, and decision threshold below carries `param_calibration_status = "UNVALIDATED_CANDIDATE_PARAMS"`. Prospective calibration requires `>= 30–50` settled outcomes with official `outcome.json`.

## 1. Day-1 Independence & Bounded Two-Tier Historical Reference

Primary GFRI (`gfri_down_1pct`, `gfri_up_1pct`) computes deterministically on **Day 1** with zero history. All full records append to `gfri_shadow_ledger.jsonl`, while `gfri_history_index.jsonl` stores a bounded ~350B projection per cycle so 60-day (`8,640`-cycle) history loading stays `< 10 MiB` RAM (verified `8.44 MiB` peak via `tracemalloc`) inside `MemoryMax=128M`, producing bit-identical near/long refs.

### Tier 1: Near-Term Reference (`4h` Window)
- Looks back `4 hours` strictly `< cycle_asof_ms`, matching exact 5-key `comparability` signature and excluding globally invalid or side-`UNKNOWN` entries.
- **`min_near_samples = 4`**: Emits `WARMING_UP` until at least `4` prior valid cycles exist in the 4h window.
- **Driver Decomposition**: Compares confirmed `liquidity_depth_usd` and `|raw_hedge_flow_usd|` against 4h baselines (`±10%` threshold) -> `LIQUIDITY_DROP`, `HEDGE_FLOW_RISE`, `BOTH`, or `STABLE`.

### Tier 2: Long-Term Rolling Reference (`60d` Window)
- **Decayed Daily-Capped Weights**: Weight $w_i = \frac{0.5^{\text{age\_days}_i / 14}}{N_{\text{UTC\_day}(i)}}$ prevents intra-day sampling bursts from skewing daily weights.
- **Cold-Start Protection**:
  - `< 7` distinct UTC days -> `ACCUMULATING` (`"积累中 (X/30天)"`, `percentile = null`).
  - `7–29` distinct days or Kish $n_{\text{eff}} < 30$ -> `COARSE_BAND_ONLY` (`LOW / NORMAL / ELEVATED / HIGH`, `percentile = null`).
  - `>= 30` distinct days **and** $n_{\text{eff}} \ge 30$ -> `READY`, weighted empirical `Pxx`.
- **Adaptive Regime-Drift Guard**: If 60d weighted median $\ge 2.50\times$, sets `regime_drift_warning = true`; low relative percentile never masks or suppresses `SHADOW_PAUSE`.

## 2. Deterministic Shadow State Machine & Clock Semantics

Per-side (`PUT` from DOWN, `CALL` from UP) evaluation produces independent `{NO_VETO, SHADOW_PAUSE, WARMING_UP, UNKNOWN}` combined into `{NO_VETO, SHADOW_PAUSE_PUT, SHADOW_PAUSE_CALL, SHADOW_PAUSE_BOTH, WARMING_UP, UNKNOWN}`.

1. **Global vs Single-Side Independence (P0-2)**: Only `global_data_quality == UNKNOWN` invalidates both sides. If PUT truncates (`put_decision = UNKNOWN`) while `global_data_quality` is `VALID`/`DEGRADED` and CALL is valid, CALL retains its exact `gfri_confirmed` and decision (`NO_VETO` / `SHADOW_PAUSE` / `WARMING_UP`), exposed via `partial_valid_side` and `partial_pause_side`, while top-level `shadow_decision` stays `UNKNOWN`.
2. **Cold-Start Guard**: Near-term `WARMING_UP` emits `WARMING_UP` (never fires single-shot cold-start pause).
3. **Positive-Gamma Dampening**: Zero amplifying pressure emits `NO_VETO` (`POSITIVE_GAMMA_DAMPENING`) with `no_veto_does_not_imply_safe = true`.
4. **Candidate Entry & Confirmation Rules** (per-side `confirmation_status == "CONFIRMED"`, `not single_blip_detected`, amplifying):
   - **Sustained Stress**: `gfri_confirmed >= 2.50x` for `>= 2` consecutive cycles; OR
   - **Confirmed Rapid Deterioration**: `gfri_confirmed >= 2.00x` AND `delta_ratio >= 1.50x` vs 4h median on a `CONFIRMED` cycle.
5. **Hysteresis Release**: Stays `SHADOW_PAUSE` until `gfri_confirmed < 2.00x` for `>= 2` consecutive valid cycles.
6. **Freeze Clock Binding (P1-2)**: Each 10-minute timer run stamps `clock_role = "SHADOW_HIGH_FREQ_CYCLE"` (`cycle_asof_ms`), distinct from the daily `14:00 UTC` `episode_freeze`. Downstream dossier binding MUST use `select_snapshot_asof_episode(records, episode_freeze_ms)` so only complete pre-freeze snapshots (`freeze_at_ms <= episode_freeze_ms`) are ever paired—never post-freeze lookahead backfills.

## 3. Minimal Console Contract (G5)

Latest real live dry run (`2026-11-18T16:59Z`, `cycle_asof_ms = 1795021161079`):

```text
GFRI v1.1                 SHADOW

PUT  下行压力  +5.33x
状态：预热中

CALL 上行压力  +5.34x
状态：预热中

近期变化：PUT 预热(0/4) | CALL 预热(0/4)
历史位置：PUT 积累中 (0/30天) | CALL 积累中 (0/30天)
数据质量：DEGRADED
```

Console view stays `<= 12` lines with zero Pin, Gamma-wall, or underwriting prose; full reproducible inputs, SHA-256 hashes, timestamps, Fast/Confirm ratios, OKX reference, `raw_archive_status`, `global_data_quality`, and `decision_authority: SHADOW_ONLY` reside in `gfri_audit_v11`.
