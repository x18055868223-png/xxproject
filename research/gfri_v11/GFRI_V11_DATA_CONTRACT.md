# GFRI v1.1 Data Contract & Quality Engineering

## 1. Core Quantitative Definition

For shock direction $d \in \{\text{DOWN}, \text{UP}\}$ ($\operatorname{sign}(\Delta S) \in \{-1, +1\}$) and primary standardized spot shock $x = 0.01$ ($1.0\%$):

$$
R_d^{\text{signed}} = \frac{H_d \cdot \operatorname{sign}(\Delta S)}{L_d}, \qquad
R_d^{\text{amplify}} = \max\left(0,\; R_d^{\text{signed}}\right)
$$

- **PUT Risk**: `gfri_down_1pct` $= R_{\text{DOWN}}^{\text{amplify}}$ (hedge selling $H_{\text{DOWN}} < 0$ during $-1\%$ drop amplifies sell-off against bid depth $L_{\text{DOWN}}$).
- **CALL Risk**: `gfri_up_1pct` $= R_{\text{UP}}^{\text{amplify}}$ (hedge buying $H_{\text{UP}} > 0$ during $+1\%$ rally amplifies rally against ask depth $L_{\text{UP}}$).
- **Dampening**: When $H_d \cdot \operatorname{sign}(\Delta S) \le 0$ (positive-Gamma buy-on-dip / sell-on-rip), `gfri` clamps to `0.0` while `raw_hedge_flow_usd` and negative `signed_ratio` are preserved in audit JSON.
- **0.5% Submetric**: Segregated inside `research_submetrics` using verified `NET_GEX_LINEAR` (`-0.5 * total_net_gex * sign_ds`); never mixed into primary `1%` UI or policy decisions.

## 2. Numerator ($H_d$) Contract & Priority

Source: `https://gexmonitor.com/api/gex-latest?asset=BTC&exchange=all&lite=true`

| Tier | Condition | Method / Tier Tag | Quality Impact |
| :--- | :--- | :--- | :--- |
| **1 (Primary)** | `hedging_flows.price_only_curve` contains `±1%`, `units.hedging_usd == "usd_notional"`, and `iv_mode == "fixed"`, `time_forward_hours == 0` | `HEDGE_FLOW_NATIVE` / `NATIVE_PRICE_ONLY` | Preserves upstream `quality.status` |
| **2 (Fallback)** | `price_only_curve` unavailable; `total_net_gex` finite with `units.total_gamma == "usd_delta_per_1pct_spot_move"` | `NET_GEX_LINEAR` / `LINEAR_PROXY_FALLBACK` | Forces `DEGRADED` |
| **3 (Fail-Closed)** | Missing/expired/future timestamp, stale, `quality.status in {ERROR, FAILED, UNAVAILABLE}`, or missing/unverified units | `UNKNOWN` | Fails closed (`gfri = null`) |

- **`spot_linked_12h` Isolation**: Stripped `/v1/info` `scenarios` contain 12h theta/charm + IV stress (`+$17.40M` at `0%` move in v1.0 Snap1, `+30.5%` bias on `+1%`). v1.1 records it only inside `spot_linked_12h_diagnostic` and **never** silently substitutes it for `price_only_curve`.

## 3. Denominator ($L_d$) Contract & Cross-Venue Reference

### Primary Liquidity Reference: Deribit `BTC-PERPETUAL`
- Endpoints: `public/get_instrument?instrument_name=BTC-PERPETUAL` + `public/get_order_book?...&depth=1000`
- Contract verification: `quote_currency == "USD"`, `instrument_type == "reversed"`, `contract_size == 10.0` -> raw level `[price, amount]` is directly USD notional (`depth_unit = "usd"`).
- Reference mid $S_{\text{ref}} = \frac{1}{2}(\text{best\_bid} + \text{best\_ask})$.
- **Decimal Exact Bounds (v1.0 Defect Fix)**: Bounds `down_bound = S_ref * (1 - 0.01)` and `up_bound = S_ref * (1 + 0.01)` use `decimal.Decimal` so IEEE-754 float64 rounding never drops exact boundary ticks.
- **Completeness Gates**:
  - `requested_depth >= 1000` (`depth=100` covers only `-0.16%` and is rejected).
  - Real Deribit books legitimately return `< 1000` price levels when fewer than 1000 distinct ticks exist (e.g., `675` bids / `984` asks observed live on `2026-11-18T14:46Z`). Therefore v1.1 enforces `len(bids/asks) >= 10` **plus** strict outer-band coverage (`min_bid <= down_bound` and `max_ask >= up_bound`); any truncation fails that side closed to `UNKNOWN`.
  - Spread sanity guard (`(best_ask - best_bid) / ref_price <= 1.0%`) and crossed-book rejection (`best_bid <= best_ask`).
  - Spot basis alignment vs GEX `asset_price`: `> 0.20%` marks `DEGRADED`; `> 0.50%` fails closed to `UNKNOWN`.

### Secondary Cross-Venue Reference (Non-Blocking): OKX `BTC-USDT-SWAP`
- Endpoints: `public/instruments?instType=SWAP&instId=BTC-USDT-SWAP` + `market/books-full?instId=BTC-USDT-SWAP&sz=5000` (Binance `fapi.binance.com` returned `HTTP 451` and OKX standard `books?sz=400` covered only `~0.15%`, whereas `books-full?sz=5000` covered beyond `±1%`).
- Unit conversion: `ctVal = 0.01`, `ctValCcy = "BTC"`, `ctType = "linear"` -> `USD notional = contracts * ctVal * price`.
- Recorded strictly in `cross_venue_ref` (`non_blocking = true`) to quantify single-venue sensitivity (`~3.9x–6.0x` Deribit depth live); never alters primary `gfri_confirmed`.

## 4. Temporal Validity, OI Lag Proof & 3-Sample Confirmation

- **Explicit Clocks Recorded**: `gex_data_ts_ms`, `gex_computed_at_ms`, `gex_received_at_ms`, `ob_timestamp_ms` (`ob_exchange_ts_ms`), `ob_received_at_ms`, `freeze_at_ms`.
- **No-Future-Data Enforcement**: Any upstream or order-book timestamp `> freeze_at_ms + 5,000 ms` fails closed (`UNKNOWN`).
- **Freshness Windows**: GEX age `<= 3,600,000 ms` (`60 min`); GEX-vs-OB skew `<= 900,000 ms` (`15 min`).
- **Deribit Public OI Lag Verification**:
  - Live query on `2026-11-18T14:23Z` showed Deribit `get_book_summary_by_currency(BTC, option)` had `options_count=938`, `total_oi=394,596.9`, matching GEXMonitor (`options_count=938`, `total_open_interest=394,595.2`, `data_source="deribit"`).
  - Deribit public API documentation specifies `open_interest` on public endpoints reflects an **8-hour delay**, corroborated by GEXMonitor `total_open_interest` remaining exact at `393,989.2` across Snap1 (`12:47Z`) and Snap2 (`12:52Z`) while spot and marks updated every minute, shifting to `394,595.2` by `14:17Z`.
  - Therefore `HTTP 200` with `<60s` `data_age_ms` proves mark/price/model-repricing freshness only—not real-time dealer inventory changes. GFRI v1.1 embeds `oi_source_lag_hours = 8` and `oi_lag_note` in every record rather than treating known structural lag as transport failure.
- **Dual-Channel Extreme & Noise Control**:
  - **Confirmation Channel**: Collects `3` Deribit `depth=1000` samples prior to `freeze_at_ms` (`20s` default spacing, `<= 180s` span, `>= 2` valid required). Outputs `gfri_confirmed = median(valid)` and `SINGLE_BLIP_FILTERED` when a lone transient spike exceeds candidate trigger / `2.0x` spread while median stays below.
  - **Fast Channel**: Preserves unclipped `gfri_fast_max`, `gfri_fast_min`, `gfri_latest_raw`, and `spread_ratio` in audit JSON so real flash thinning is never hidden by smoothing.
  - **Gamma Independence Accounting**: Explicitly tracks `shares_same_gex_snapshot` and `independent_gamma_estimates` (`1` when 3 books share one GEX snapshot) so 3 book samples are never misrepresented as 3 independent Gamma estimates.
