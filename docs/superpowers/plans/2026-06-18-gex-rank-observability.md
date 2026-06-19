# GEX Rank Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface GEX Monitor rank percentiles in the FMZ signal status panel, local audit cards, and the signal-layer chart without changing decision, confidence, blocking, or execution behavior.

**Architecture:** Preserve the GEX Monitor API `rank` object under `factor_snapshot.gex_info.rank`, let existing audit JSON copying expose it as `factor_cross_section.gex_info.rank`, and render it in a new local frontend section. Charting uses only `gex_info.total_net_gex` scaled to million USD on its own axis.

**Tech Stack:** Python single-file FMZ artifact, Python contract tests, static HTML/CSS/JavaScript audit frontend.

---

### Task 1: Signal-Layer Rank And Chart Contract

**Files:**
- Modify: `demo/最新交付物/neutral_regulation_demo_fmz.py`
- Modify/Create: `demo/tests/test_signal_status_chart_contract.py`

- [ ] Write failing tests that assert `/v1/info` rank is preserved in `parse_info_payload`, `_gex_info_table` renders a rank row, and `DemoChart` no longer uses the stale `NRD 0.4.1 前置信号观察图` title while adding `processed_net_gamma_musd`.
- [ ] Run the new tests and confirm they fail for missing rank/chart behavior.
- [ ] Implement minimal production changes in `neutral_regulation_demo_fmz.py`.
- [ ] Re-run the new tests, compile the FMZ file, and run existing signal audit contract tests.

### Task 2: Local Audit Frontend Rank Section

**Files:**
- Modify: `C:\Users\Xu\Documents\信号审计前端页面设计\archives\signal-audit-final-20260618\app.js`
- Modify: `C:\Users\Xu\Documents\信号审计前端页面设计\archives\signal-audit-final-20260618\index.html`
- Modify: `C:\Users\Xu\Documents\信号审计前端页面设计\archives\signal-audit-final-20260618\signal_cards\*.json`
- Modify: `C:\Users\Xu\Documents\信号审计前端页面设计\archives\signal-audit-final-20260618\signal_cards\fallback.js`
- Mirror after local approval prep: `deploy/signal_audit/frontend/app.js`, `deploy/signal_audit/frontend/index.html`, and matching local sample card assets only.

- [ ] Add a failing/static check that the frontend has `renderGexRank` and sample cards contain `factor_cross_section.gex_info.rank`.
- [ ] Implement a new `GEX Rank 分位` section after Gamma/GEX overview and before decision.
- [ ] Add restrained CSS for a compact rank grid, reusing the document-reader visual style.
- [ ] Inject realistic rank fixture data into local sample cards and fallback assets for preview.
- [ ] Serve the local archive over HTTP and verify index and cards load.

### Task 3: Final Verification And Review

**Files:**
- Review all modified files.

- [ ] Run compile and contract tests.
- [ ] Start a local static preview server for the archive.
- [ ] Report changed files, test evidence, local preview URL, and explicitly state that the server was not updated.
