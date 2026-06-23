# Project Memory

## Project overview

- This repository is the integration workspace for the neutral-loop trading system. It gathers system-level design docs, module snapshots, demo integration code, latest FMZ-ready single-file deliverables, and the signal audit archive sample.
- The main implementation language present in the deliverables is Python. The repository also contains Markdown documentation plus a static HTML/JSON audit archive sample under `audit_archive/`.
- The current deployable signal-layer artifact is `demo/最新交付物/neutral_regulation_demo_fmz.py`. Its verified in-file version is `demo_version = "1.3.0"` and `schema_version = "nrd.schema.v1.0.0"`.
- The current deployable execution-layer artifact is `demo/最新交付物/spm_calendar_protected_short_v1.py`. Its verified in-file version is `STRATEGY_VERSION = "2.5.0"`, with entry/exit/hedge/live trading gates still default-safe/off.
- The current GEX Monitor API snapshot is `05_GEX监控API_数据增强接口/`, with `__version__ = "0.2.0"` and rank output using `rolling_30d_or_available`.
- The current LLM review sidecar is `tools/gemini_signal_llm_review.py`, defaulting to Gemini `gemini-3.5-flash`, `signal_llm_review@1.3.0`, and `gemini_signal_review_prompt@1.3.0`. From r3.2 onward each new card uses two Gemini calls when enabled: blind theoretical review first, then full audit reconciliation.
- Documentation r2.2 aligns `05_GEX监控API_数据增强接口/` and `deploy/signal_audit/` with the 00-04 module convention by adding `因子文档/` indexes, Chinese semantic entrypoints, and `deploy/signal_audit/frontend/VERSION.json`; it does not move service source code or change runtime behavior.
- `demo/最新交付物/README.md` states that `demo/最新交付物/` contains the latest FMZ-ready single-file strategies, while `demo/副本快照/` is the historical timeline.
- `x18055868223-png/xxproject` is the primary project repository and default authority for project baseline, releases, tags, and server deployment instructions. `signal-audit-deploy` is only a deployment/helper mirror for the static audit surface; do not treat it as the project main repository.
- Before any commit, tag, push, or deployment instruction, verify `git remote -v`. If `origin` points to `signal-audit-deploy`, use/add the `xxproject` remote for project-level releases and do not conclude the project baseline is updated from a deployment-mirror push alone.

## Architecture and boundaries

- The root documentation presents the system as core areas `01_信号层_中性回路/`, `02_执行层_Deribit/`, `03_VRP门_建仓前定价/`, `04_对冲模块/`, plus the current auxiliary running assets `05_GEX监控API_数据增强接口/` and `deploy/signal_audit/`, with `demo/` as the integration sandbox.
- The signal-layer FMZ file is read-only observation by default. It does not select legs, quote, or place orders.
- Signal v1.4.0 uses full local JSON audit records, `session_context`/decision-matrix temporal durability fields, a short FMZ push summary, and out-of-process LLM sidecar review tooling. Runtime recording writes through `JsonlRecorder` to `demo/logs/<name>.jsonl`; the signal review recorder name is `signal_review`.
- Current signal audit JSON output is aligned to the finalized static frontend card schema used by the external archive `信号审计前端页面设计/archives/signal-audit-final-20260618`. That frontend reads `signal_cards/index.json`, then lazy-loads `signal_cards/*.json`; direct file mode also uses `signal_cards/fallback.js`.
- The deploy signal-audit frontend baseline is r3.2 as of 2026-06-23: compact EDB evidence ledger, `source_ref` anchors back to raw trace groups, raw trace grouped navigation, mandatory LLM review/pending section, MACRO/GGR auxiliary semantics, and production/default materializer output excluding synthetic/local preview cards.
- `audit_archive/` in this repo is an older sample/archive scaffold, not the current finalized frontend. Do not treat `audit_archive/public/index.html` as the authoritative audit page.
- The execution-layer FMZ file is a vertical credit spread execution chain with a single `run_cycle` main path. Its configured signal source default is `OFFLINE_MANUAL`.
- Execution trading gates are default-safe: `ALLOW_ENTRY_TRADING`, `ALLOW_EXIT_TRADING`, `ALLOW_HEDGE_TRADING`, `KILL_NEW_RISK`, `EMERGENCY_REDUCE_ONLY`, and legacy `ALLOW_TRADING` are all verified as `False` in the current deployable execution file.
- VRP is documented and coded as a pricing/filtering gate. It must not decide direction, select expiry, enter plan weights, or unlock trading gates.

## Build, test and validation commands

- Verified during initialization: syntax compilation of the current deployable FMZ files works with Python 3.12:

```powershell
<python-3.12> -m py_compile `
  demo\最新交付物\neutral_regulation_demo_fmz.py `
  demo\最新交付物\spm_calendar_protected_short_v1.py
```

- Verified during initialization: `.codex/config.toml` and `.codex/agents/*.toml` can be parsed by Python `tomllib`.
- Verified during initialization: `audit_archive/public` can be served as static files over local HTTP, and both `/index.html` and `/data/index.json` return HTTP 200.
- Verified during 2026-06-18 signal audit alignment: `demo/tests/test_signal_audit_frontend_contract.py` passes, and `demo/最新交付物/neutral_regulation_demo_fmz.py` compiles with Python 3.12.
- Verified during 2026-06-23 signal-audit r3.2 closure: `tests/test_materializer_tail_window.py`, `tests/test_signal_session_context_deploy_assets.py`, `tests/test_signal_audit_frontend_render_contract.py`, `tests/test_signal_audit_deploy_llm_systemd.py`, `tests/test_signal_llm_review_pipeline.py`, `demo/tests/test_gemini_signal_llm_review.py`, `demo/tests/test_signal_audit_frontend_llm_review_contract.py`, `demo/tests/test_signal_session_context_contract.py`, `demo/tests/test_signal_llm_review_contract.py`, and `demo/tests/test_signal_blocking_and_anchor_contract.py` pass with Python 3.12.
- Verified during 2026-06-23 server migration asset update: `tests/test_server_bootstrap_assets.py` passes with Python 3.12. New-server signal-stack bootstrap lives at `tools/server_bootstrap_signal_stack.sh`, with migration guidance in `deploy/signal_audit/SERVER_MIGRATION.md`; the bootstrap release default is `r3.2`.
- Documented in `demo/最新交付物/README.md` but not re-run during this initialization: execution-layer full regression command `python demo/execution_build/realsrc/tests/run_all.py`.
- Documented in `demo/最新交付物/README.md` but not re-run during this initialization: execution bundle check command `python demo/execution_build/realsrc/build_bundle.py --check`.
- Documented in `demo/HANDOFF.md` but not re-run during this initialization: signal bundle check command `python demo/signal_build/build_signal_bundle.py --check`.
- No `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle`, `requirements*.txt`, `tox.ini`, `pytest.ini`, `mypy.ini`, or `.flake8` file was found at this initialization scan.

## Coding conventions

- Keep deployable FMZ files in `demo/最新交付物/`. Historical copies belong in `demo/副本快照/` with a dated version folder and `快照说明.md`.
- Prefer repository-relative paths in documentation and project memory. Do not write secrets or local machine sensitive paths into project memory.
- For signal audit data, keep machine-readable raw values in JSON. Presentation layers may translate labels, but should not overwrite raw enum or numeric values.
- For static audit data, use an index JSON for list/filter fields and per-card JSON files for detail. The finalized frontend expects `signal_cards/index.json` plus `signal_cards/*.json`; do not require browsers to parse the full JSONL on every page load.
- Any future change to the signal audit frontend must include a full frontend audit before delivery: verify desktop and mobile layout proportions, horizontal overflow, table/card readability, raw evidence visibility, and data integrity against representative real cards. The audit must explicitly check that important raw fields are not dropped or hidden behind truncation, object/array values do not render as `[object Object]`, deploy and preview/dist frontend assets stay mirrored where applicable, and the relevant frontend/materializer contract tests pass.
- Keep signal-layer changes in the observability and signal-evidence boundary unless the user explicitly reopens signal logic, EDB weights, or direction classification.
- Keep execution-layer live trading disabled by default unless the user explicitly asks to change gates and the required live validation has been completed.

## Important invariants

- Signal audit v1.3.0 changes are observability-only: full JSON record, local JSONL, and short push summary must not change direction, EDB score, confidence, blocking, or execution contracts.
- FMZ push is an alert/navigation channel only. Full audit evidence belongs in JSONL and the static audit page.
- FMZ push text must remain a single short message. Prior versions were truncated by FMZ/email clients, so long four-layer audit text must not be restored. In the current signal file, the old `render_review_card_push` / `signal_review_push_compact` path has been removed.
- `signal_review_push_enabled` and `signal_review_push_test` are verified as `False` by default. Push testing must be explicitly enabled and then turned off after verification. When enabled, the current self-test writes one synthetic audit record to `signal_review.jsonl` and pushes one `非真实信号` short alert with a JSON write status marker.
- Synthetic/local preview signal audit cards must not be present in the default deploy manifest, fallback fixture, or inline `signal-data` fallback. Use the materializer's explicit include-synthetic preview mode only for local visual testing.
- Static audit site URL configuration is optional. If `audit_static_base_url` is empty, push summaries should point to FMZ log/card references rather than pretending the static site is deployed.
- Execution gates default to dry-run/empty trading behavior. Do not flip trading gates as part of unrelated refactors.
- Execution `GetCommand` interaction requires real FMZ robot dry-run validation; backtest behavior is not enough for that path.
- The current `audit_archive/` data is synthetic sample data, not proof of production signal ingestion.

## Known pitfalls

- The workspace root can now be a Git repository; always verify the active remote. The safe default is `xxproject` for project work. `signal-audit-deploy` must be treated as a deploy mirror only, and project releases should preserve the full xxproject asset tree rather than force-moving main from the deploy mirror history.
- `python` was not available on `PATH` in this environment during initialization. Use an available Python 3.12 interpreter explicitly when needed.
- `audit_archive/public/index.html` is a placeholder from the older scaffold. The current finalized static audit page lives outside this repo in the `signal-audit-final-20260618` archive and expects the `signal_cards/` layout.
- Runtime signal records are not automatically exported by the FMZ strategy itself. The runtime writes `demo/logs/signal_review.jsonl`; `tools/materialize_signal_cards.py` materializes that JSONL into `signal_cards/index.json`, single-card JSON, and `fallback.js` for the finalized static page. Server automation is provided as an optional systemd timer under `deploy/signal_audit/`.
- New-server rebuilds should start from `xxproject` release tags using `tools/server_bootstrap_signal_stack.sh`; for the current migration asset use `r3.2`. The script creates env templates only; `/etc/signal-audit/llm.env`, `/etc/gexmonitorapi.env`, FMZ runtime JSONL, and historical sidecar JSONL remain server-local and must not be committed.
- If the API usage page shows zero Gemini calls after new signals, treat that as a deployment/config fault until proven otherwise. First verify `/etc/signal-audit/llm.env` has at least one Gemini channel key loaded: `GEMINI_CHANNEL1_API_KEY` for low-cost/free first pass and `GEMINI_CHANNEL2_API_KEY` for paid fallback. Then run `LLM_REQUIRED=1 tools/server_self_check_signal_stack.sh --run-oneshots`; r3.2 self-check must fail when the latest signal card lacks an OK two-call sidecar review with `blind_review_mode=two_call_strict` and `llm_call_count>=2`. Sidecar `api_key_route` / `llm_call_routes` shows whether the review used channel 1, channel 2, or mixed routing.
- `demo/副本快照/2026-06-18_信号v1.3.0_执行v1.6.2_JSON留档+简要推送/` contains an execution file whose content verifies as `STRATEGY_VERSION = "2.0.0"` despite the folder/file naming saying v1.6.2.
- The old validation scripts documented in upstream/source repositories may not exist in this integration checkout. Verify actual file presence before running a documented command.

## Durable decisions

- Decision: keep `demo/最新交付物/` as the current deployable FMZ artifact folder. Basis: `demo/最新交付物/README.md`. Impact: update this folder after regeneration and preserve dated history in `demo/副本快照/`.
- Decision: signal v1.4.0 uses frontend-aligned full JSON audit records plus short FMZ push summaries and server-side LLM sidecar review. Basis: current signal file comments/config, `demo/最新交付物/README.md`, and signal audit tests. Impact: future audit work should build the JSONL-to-static-page materializer and sidecar merge instead of re-expanding FMZ push bodies or calling LLM from FMZ.
- Decision: current static frontend deployment should target the `signal_cards/index.json` plus `signal_cards/*.json` contract from `signal-audit-final-20260618`; the repo `audit_archive/` scaffold is sample/reference only. Impact: export tools should generate the finalized frontend layout, including a stable missing-source representation for optional GEX data.
- Decision: execution v2.5.0 is vertical-only and default dry-run with separated gates plus the v2.5.0 risk-chain audit fixes. Basis: current execution file constants, `demo/最新交付物/README.md`, and `docs/执行层完整说明_v2.1.md`. Impact: do not treat disabled gates as a bug, and do not reintroduce calendar or KPF execution paths.
- Decision: project-level complex tasks should use bounded subagent delegation through `.codex/agents/` when the task has independent exploration, implementation, or review streams. Basis: root `AGENTS.md` created during this initialization. Impact: future complex work should read `PROJECT_MEMORY.md`, classify complexity, delegate where useful, and verify final integration.

## Unconfirmed items

- Whether the full upstream source repository `中性回路 - opus4.8` is available in this workspace was not confirmed during this initialization.
- Whether the documented source-side tools `tools/static_validate_demo.ps1`, `tools/runtime_check_demo.ps1`, `tools/signal_review_check.py`, `tools/gex_info_check.py`, and `tools/greeks_freshness_check.py` are available outside this integration checkout was not confirmed.
- Whether a production static audit host, HTTPS access control, or sync job already exists outside this repository was not confirmed.
- Whether future Codex sessions will load the newly created `.codex/agents/` definitions without reopening the session was not confirmed; start a fresh Codex session after this initialization.
