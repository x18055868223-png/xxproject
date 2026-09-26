# Astra KPF Light Producer

This package is a server-local, no-port KPF producer for MAP. It keeps the
original KPF algorithm parameters intact: Binance USD-M BTCUSDT aggTrades,
90 day history, 60 day fresh evidence, 100 BTC volume bars, 50 USD internal
bins, and the original evidence rules.

The runtime writes only a thin three-file bundle:

- `snapshot.json`
- `audit.json`
- `raw_zones.json`

`current.json` points at one immutable bundle version directory and
`previous.json` keeps the prior pointer for bundle rollback. The MAP consumer
should read only `current.json`.

The producer has no HTTP API, no key, and no trading permission. It is intended
to run as a one-shot worker from a systemd timer under the fixed namespace
`/opt/astra-kpf-light/current`. The installer stages immutable releases under
`/opt/astra-kpf-light/releases`, verifies the vendored source manifest, then
atomically switches the `current` symlink. `rollback_kpf_light.sh` switches
`current` back to the previous release without deleting data.

Each run downloads at most one missing official Binance daily ZIP plus its
`.CHECKSUM`, verifies SHA256 before use, and never stores full parsed CSV, PDF
reports, replay artifacts, or a cached 90 day window-bar array. Complete windows
are computed by streaming volume bars into the original density accumulator.
The streaming path keeps the original daily parse discipline: per-day
`last_agg_id`, `last_trade`, and timestamp checks reset for each Binance daily
file, while one `VolumeBarBuilder` carries volume-bar phase across the whole
window. Cross-day clock or agg-id rollback is treated as a schema conflict and
fails closed instead of silently deleting rows.

Source retention is limited to files owned by this producer and matching the
strict names `BTCUSDT-aggTrades-YYYY-MM-DD.zip`,
`BTCUSDT-aggTrades-YYYY-MM-DD.zip.CHECKSUM`, or
`BTCUSDT-aggTrades-YYYY-MM-DD.zip.source.json`. Unknown files, symlinks,
quarantine files, research directories, and non-source paths are not touched.
Before disk preflight, the worker frees expired source files while retaining the
current required 90 days plus the last READY window during rollover. Once the
new READY bundle publishes, retention contracts to the current 90 days.

Same-cutoff READY runs skip recomputation only when the current manifest still
hash-verifies all three artifacts, the vendored algorithm fingerprint is
unchanged, and the recorded source stat/checksum/source-metadata fingerprint is
unchanged. The fast source fingerprint includes size, mtime, ctime, inode where
available, and a bounded head/middle/tail content sample; it is an operational
skip guard, not a full proof that every byte of every ZIP was rehashed on that
timer tick. Source ZIP contents are fully checksum-verified when a new complete
window is computed, and the audit records first and most recent full-checksum
verification clocks for each used source file.

## Resource envelope

- `MemoryMax=240M`
- `CPUQuota=50%`
- `Nice=10`
- `TimeoutStartSec=12h` bounds a full new-window computation; the five-minute
  timer waits for the prior oneshot to finish and never overlaps two runs.
- fixed data root: `/var/lib/astra-kpf-light`
- single lock: `/var/lib/astra-kpf-light/run.lock`
- minimum disk free: 6 GiB
- KPF data-root budget: 5 GiB

If the complete 90 day window is not available, status is `WARMUP` and the
worker does not replay already downloaded days. When a complete new window is
available, the worker streams density first, then uses at most one public
Binance USD-M BTCUSDT quote request before ranking targets. Quote payloads must
carry a finite price plus exchange `time` or `closeTime`; future, stale
(`>300s`), or clockless quotes produce `NO_QUOTES`. Density is not changed by a
quote failure, and the next timer run may recover; a failed quote is never
cached as an unchanged successful cutoff. No zone becomes usable unless it is an
original public target with A/B evidence, data quality is `OK`, and the self
audit passes.

Published version files are immutable. Only current and previous owned bundles
are retained; repeated WARMUP updates preserve the last READY as historical
previous. Unknown files and symlinks are never removed by this cleanup.

## Local commands

```bash
python3 /opt/astra-kpf-light/current/tools/astra_kpf_light_v1.py verify-vendor
python3 /opt/astra-kpf-light/current/tools/astra_kpf_light_v1.py preflight
python3 /opt/astra-kpf-light/current/tools/astra_kpf_light_v1.py oneshot --fetch-quote
python3 /opt/astra-kpf-light/current/tools/astra_kpf_light_v1.py status
```
