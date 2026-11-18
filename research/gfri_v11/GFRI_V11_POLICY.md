# GFRI v1.1 Historical Reference, Cold Start & Shadow Policy

> **Calibration Notice**: Every window length, weighting parameter, and decision threshold below carries `param_calibration_status = "UNVALIDATED_CANDIDATE_PARAMS"`. Prospective calibration requires `>= 30–50` settled outcomes with official `outcome.json`.

## 1. Day-1 Independence & Two-Tier Historical Reference

Primary GFRI (`gfri_down_1pct`, `gfri_up_1pct`) computes deterministically on **Day 1** with zero history. Historical references are strictly additive context:

### Tier 1: Near-Term Reference (`4h` Window)
- Looks back `4 hours` strictly `< freeze_at_ms`, matching exact `comparability` tuple (`methodology_version`, `universe_policy`, `primary_method`, `depth_venue`, `code_version`) and excluding `UNKNOWN`.
- **`min_near_samples = 4`**: Emits `WARMING_UP` until at least `4` prior valid cycles exist in the 4h window.
- **Driver Decomposition**: Compares current confirmed `liquidity_depth_usd` and `|raw_hedge_flow_usd|` against 4h baselines (`±10%` threshold) to classify movements into `LIQUIDITY_DROP`, `HEDGE_FLOW_RISE`, `BOTH`, or `STABLE`.

### Tier 2: Long-Term Rolling Reference (`60d` Window)
- **Decayed Daily-Capped Weights**: Weight $w_i = \frac{0.5^{\text{age\_days}_i / 14}}{N_{\text{UTC\_day}(i)}}$. Dividing by the number of cycles in that UTC day prevents high-frequency intra-day collection bursts from overwhelming daily baseline representation.
- **Cold-Start Protection (Anti-Fake-Precision)**:
  - `< 7` distinct UTC days -> `ACCUMULATING` (`display_label = "积累中 (X/30天)"`, `percentile = null`).
  - `7–29` distinct days or Kish effective sample size $n_{\text{eff}} = \frac{(\sum w)^2}{\sum w^2} < 30$ -> `COARSE_BAND_ONLY` (`LOW / NORMAL / ELEVATED / HIGH`, `percentile = null`).
  - `>= 30` distinct days **and** $n_{\text{eff}} \ge 30$ -> `READY`, weighted empirical percentile `Pxx`. (Note: with 14-day half-life and daily normalization, 1 sample/day takes ~40+ days for $n_{\text{eff}} \ge 30$, whereas `>=2` samples/day reaches $n_{\text{eff}} \ge 30$ near day 30.)
- **Adaptive Regime-Drift Guard**: If 60d weighted median $\ge 2.50\times$, sets `regime_drift_warning = true`. A low relative percentile in an already stressed market **never** overrides absolute pause conditions or suppresses `SHADOW_PAUSE`.

## 2. Deterministic Shadow State Machine

Per-side (`PUT` from DOWN, `CALL` from UP) evaluation produces `{NO_VETO, SHADOW_PAUSE, WARMING_UP, UNKNOWN}`, combined into one of 6 top-level decisions:

`{NO_VETO, SHADOW_PAUSE_PUT, SHADOW_PAUSE_CALL, SHADOW_PAUSE_BOTH, WARMING_UP, UNKNOWN}`

1. **Missingness Gate (Strict Precedence)**: Any side with `data_quality == UNKNOWN` or missing/truncated GFRI emits `UNKNOWN` on that side—never `NO_VETO`. If one side is `SHADOW_PAUSE` while the other is `UNKNOWN`, top-level emits `UNKNOWN` while `partial_pause_side` preserves the paused side (`PUT` or `CALL`).
2. **Cold-Start Gate**: Near-term `WARMING_UP` emits `WARMING_UP` (never fires single-shot cold-start pause).
3. **Positive-Gamma Dampening**: Zero amplifying pressure emits `NO_VETO` (`POSITIVE_GAMMA_DAMPENING`) alongside mandatory flag `no_veto_does_not_imply_safe = true`.
4. **Candidate Entry Rules** (require `confirmation_status == "CONFIRMED"`, `not single_blip_detected`, amplifying flow):
   - **Sustained High Stress**: `gfri_confirmed >= 2.50x` (`trigger_abs_gfri`) for `>= 2` consecutive cycles (`trigger_consecutive_cycles`); OR
   - **Confirmed Rapid Deterioration**: `gfri_confirmed >= 2.00x` (`elevated_abs_gfri`) AND `delta_ratio >= 1.50x` vs 4h median on a confirmed 3-sample cycle.
5. **Hysteresis Release**: Once in `SHADOW_PAUSE`, stays paused until `gfri_confirmed < 2.00x` (`release_abs_gfri`) for `>= 2` consecutive valid cycles. Single dips between `2.00x–2.50x` or brief missingness do not chatter.
6. **Parallel Candidate Sensitivity Shadow Tracking**: Evaluates `sensitive (2.00x / 1.60x)`, `baseline (2.50x / 2.00x)`, and `conservative (3.50x / 2.80x)` side-by-side inside `sensitivity_comparison` for prospective evaluation without post-hoc threshold picking.

## 3. Minimal Console Contract (G5)

```text
GFRI v1.1                 SHADOW

PUT  下行压力  +4.75x
状态：预热中

CALL 上行压力  +7.00x
状态：预热中

近期变化：PUT 预热(0/4) | CALL 预热(0/4)
历史位置：PUT 积累中 (0/30天) | CALL 积累中 (0/30天)
数据质量：DEGRADED
```

Console view stays `<= 12` lines with zero Pin, Gamma-wall, or underwriting prose; full reproducible inputs, SHA-256 hashes, timestamps, Fast/Confirm ratios, OKX reference, history states, and `decision_authority: SHADOW_ONLY` reside in `gfri_audit_v11`.
