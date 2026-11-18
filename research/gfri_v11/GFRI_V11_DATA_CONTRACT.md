# GFRI v1.1 Data Contract & Quality Engineering

## 1. Core Quantitative Definition

For shock direction $d \in \{\text{DOWN}, \text{UP}\}$ ($\operatorname{sign}(\Delta S) \in \{-1, +1\}$) and primary standardized spot shock $x = 0.01$ ($1.0\%$):

$$
R_d^{\text{signed}} = \frac{H_d \cdot \operatorname{sign}(\Delta S)}{L_d}, \qquad
R_d^{\text{amplify}} = \max\left(0,\; R_d^{\text{signed}}\right)
$$

- **PUT Risk**: `gfri_down_1pct` $= R_{\text{DOWN}}^{\text{amplify}}$ (hedge selling $H_{\text{DOWN}} < 0$ during $-1\%$ drop against bid depth $L_{\text{DOWN}}$).
- **CALL Risk**: `gfri_up_1pct` $= R_{\text{UP}}^{\text{amplify}}$ (hedge buying $H_{\text{UP}} > 0$ during $+1\%$ rally against ask depth $L_{\text{UP}}$).
- **Dampening**: When $H_d \cdot \operatorname{sign}(\Delta S) \le 0$, `gfri` clamps to `0.0` while `raw_hedge_flow_usd` and negative `signed_ratio` are preserved.
- **0.5% Submetric**: Segregated inside `research_submetrics` (`NET_GEX_LINEAR`), never mixed into primary `1%` UI or policy decisions.

## 2. Numerator ($H_d$) Contract & Strict Metadata Proof (P0-5)

Source: `https://gexmonitor.com/api/gex-latest?asset=BTC&exchange=all&lite=true`

| Tier | Condition | Method / Tier Tag | Quality Impact |
| :--- | :--- | :--- | :--- |
| **1 (Primary)** | `hedging_flows.price_only_curve` contains `±1%` **AND** `scenario_sets.price_only` dict is present with `iv_mode == "fixed"`, `time_forward_hours == 0`, **AND** `units.hedging_usd == "usd_notional"` | `HEDGE_FLOW_NATIVE` / `NATIVE_PRICE_ONLY` | Preserves upstream `quality.status` |
| **2 (Fallback)** | Primary proof absent/incomplete; `total_net_gex` finite with `units.total_gamma == "usd_delta_per_1pct_spot_move"` | `NET_GEX_LINEAR` / `LINEAR_PROXY_FALLBACK` | Forces `DEGRADED` |
| **3 (Fail-Closed)** | Missing/expired/future timestamp, `spot_usd` missing/invalid, stale, `quality.status in {ERROR, FAILED, UNAVAILABLE}`, or missing/unverified units | `UNKNOWN` | Fails closed (`gfri = null`) |

- **`spot_linked_12h` Isolation**: Stripped `/v1/info` `scenarios` contain 12h theta/charm + IV stress (`+$17.40M` at `0%` move in v1.0 Snap1). v1.1 keeps `spot_linked_12h` strictly in diagnostics and never substitutes it for missing `price_only` metadata.

## 3. Denominator ($L_d$) Contract, Contract Cache (P0-1) & Side Independence (P0-2)

### Primary Liquidity Reference: Deribit `BTC-PERPETUAL`
- Endpoints: `public/get_instrument?instrument_name=BTC-PERPETUAL` + `public/get_order_book?...&depth=1000`
- **Mandatory Spec Proof (P0-1)**: `depth_unit = "usd"` is **never assumed**. `evaluate_single_observation` requires either valid live `get_instrument` (`quote_currency == "USD"`, `instrument_type == "reversed"`, `contract_size == 10.0`, tagged `deribit_spec_source = "live_instrument"`) OR a valid schema-versioned TTL cache (`deribit_spec_cache.json`, `schema_version = 1`, `24h` TTL, tagged `"verified_cache"`). Missing, wrong-unit, or expired spec caches fail closed to `UNKNOWN` (`deribit_instrument_verified = false`).
- **Exact Decimal Bounds**: `down_bound = S_ref * (1 - 0.01)` and `up_bound = S_ref * (1 + 0.01)` use `decimal.Decimal` so IEEE-754 float64 rounding never drops boundary ticks.
- **Completeness & Global vs Side Separation (P0-2)**:
  - **Global gates** (`global_data_quality`): GEX validity, Deribit spec proof, clocks (`<= freeze_at_ms`), spread `<= 1.0%`, non-crossed book, and GEX spot vs OB mid alignment (`> 0.20%` -> `DEGRADED`, `> 0.50%` -> `UNKNOWN`).
  - **Side gates** (`put` / `call`): `requested_depth >= 1000`, `len(bids/asks) >= 10`, and outer-band coverage (`min_bid <= down_bound`, `max_ask >= up_bound`). If only PUT truncates while `global_data_quality` is valid/degraded, CALL preserves its true `status`, `gfri_confirmed`, and side policy decision (`partial_valid_side = "CALL"`), while top-level `data_quality` remains `UNKNOWN`.

### Secondary Cross-Venue Reference (Non-Blocking): OKX `BTC-USDT-SWAP`
- `public/instruments?instType=SWAP&instId=BTC-USDT-SWAP` + `market/books-full?instId=BTC-USDT-SWAP&sz=5000` (`ctVal = 0.01 BTC`, `linear`, USD notional `= contracts * ctVal * price`). Recorded strictly in `cross_venue_ref` (`non_blocking = true`).

## 4. Temporal Validity, Confirmation Conflict Guard (P0-4) & Archive Hashes (P0-7)

- **Clocks Recorded**: `cycle_asof_ms` (`freeze_at_ms`, `clock_role = "SHADOW_HIGH_FREQ_CYCLE"`), `gex_data_ts_ms`, `gex_computed_at_ms`, `gex_received_at_ms`, `gex_age_ms`, `ob_timestamp_ms` (`ob_exchange_ts_ms`), `ob_received_at_ms`, `ob_age_ms`, `ob_received_age_ms`, `ob_exchange_to_recv_ms`, `ob_skew_ms`.
- **Independent Order-Book Freshness (P0-3, Candidate Params)**:
  - `max_ob_age_ms = 60,000 ms` (`60s` max age vs `freeze_at_ms`, paired with `3` pre-freeze samples spaced `15s` apart -> `~31s` total capture window inside `60s` with observed `0.3–0.8s` exchange->HTTP RTT).
  - `max_ob_receive_lag_ms = 30,000 ms` (`30s` max lag `ob_received_at_ms - ob_timestamp_ms`). A 20-minute stale book with `ob_skew_ms == 0` fails closed to `UNKNOWN` (`order_book_expired`).
  - No-future-data gate: all clocks `<= freeze_at_ms + 5,000 ms`; GEX age `<= 60 min`, GEX-vs-OB skew `<= 15 min`.
  - Documented `8h` Deribit public OI reporting lag recorded explicitly (`oi_source_lag_hours = 8`, `oi_lag_note`).
- **Dual-Channel Confirmation & Conflict Guard (P0-4)**:
  - Collects `3` pre-freeze Deribit books spaced `15s` apart (`>= 2` valid required). Preserves Fast unclipped `gfri_fast_max`.
  - **2-sample conflict guard**: If only `2` valid samples exist and they severely diverge (`fast_max / fast_min > 2.0` or `0.0 vs >= 2.0`), emits `INSUFFICIENT_CONFIRMATIONS` (`gfri_confirmed = null`, reason `two_sample_severe_divergence`, preserving `gfri_fast_max`). Agreeing `2/2` (`[4.0, 4.1]`) or majority `2/3` (`[0.5, 4.0, 4.1]`) confirms cleanly (`CONFIRMED`), while `1/3` isolated outliers (`[0.5, 0.6, 8.0]`) trigger `SINGLE_BLIP_FILTERED`.
  - Explicit `shares_same_gex_snapshot` and `independent_gamma_estimates = 1` prevent counting 3 OB observations as 3 independent Gamma estimates.
- **Raw Archive & Dual-Hash Provenance (P0-7)**:
  - Writes exact raw HTTP response bytes atomically when `GFRI_RAW_CAPTURE_DIR` is set, verifies disk SHA-256 readback, records both `raw_http_sha256` and canonical `reserialized_json_sha256` inside `manifest.json` + `raw_archive_status`, and surfaces `FAILED` / `PARTIAL` (`complete_replayable = false`) directly in `failure_reasons` — never silently swallowed.
