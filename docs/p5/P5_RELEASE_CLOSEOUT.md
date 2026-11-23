# P5 Release Closeout Report (`P5_RELEASE_CLOSEOUT`)

**Terminal Status:** `SHADOW_BLOCKED_OR_INCONCLUSIVE`  
**Production Disposition:** Formal A (`b3d79695f82e3a5f6392d0cbdba668d4406f4724`, `daily_r16@2`, `player_initial_system@1.0 + legacy_v1`) **retained untouched on `xx-eu`** (verified `2026-11-23T16:29:31Z`). Promotion **blocked** across Gates **A, B, C, E** (`CONDITIONAL PASS` on **D**); Route D is **not** substituted as rollback.

---

## 1. Identity & Diff Manifest

| Layer | Live Production (`xx-eu`) | P5 RC (`codex/player-p5-candidate`) |
| :--- | :--- | :--- |
| **Git Commit SHA** | `b3d79695f82e3a5f6392d0cbdba668d4406f4724` | `958f0c5873d62417dc29b25f7648e88f4f018119` (+ Step 7 closeout docs commit) |
| **Active Plan** | `daily_r16@2` (`plan.daily_r16.v2.json`, LF `33dd99ad54a6`) | `daily_r16@3` (`plan.daily_r16.v3_p5.json`, LF `9f7c8dc770df`), rollback `daily_r16@4` (`5c7d57864e6e`) |
| **System Prompt** | `player_initial_system@1.0` (`2,831 B`, `653168703cd8...`) | `player_initial_system@1.2-p5-candidate` (`3,073 B`, `b746c2d2f7f996d290992e01b11d9e4c4792a0e1afcacb900bbf0dfc678b6f34`) |
| **Input / Contract** | `legacy_v1` / legacy rules | `p0_strict@1` / `p2_strict@1` |
| **`execution_allowed`** | `false` | `false` (initial + Hold C read-only) |

### P5-Owned Runtime & Deployment Files (`LF SHA-256` in RC `958f0c58`)
- `tools/astra_anchor_relative_v1.py`: `e4a4c2a5e01c6540eabe0f08809113daf18e1b4c93909dc4300729b38a3870be` (`14,848 B`; live `xx-eu`: `f4e7ffb137b2`)
- `tools/astra_player_condition_v1.py`: `526fd0c02d7921612c68f9c1094763f58bf3883b27874678a1385cc3dfe71332` (`37,539 B`; live `xx-eu`: `1d177bea8a3c`)
- `tools/astra_player_dossier_v1.py`: `d5a0d632b84e6d3dacd934ed65545adf8a51e4e8aadb9aff57cec3bf66c45e18` (`104,815 B`; live `xx-eu`: `278d410c035d`)
- `tools/astra_player_input_v1.py`: `082572f2445be8cf2e09e1554e46fb8c8cb245a782a5ef2a9af68a7d94100d42` (`79,548 B`; live `xx-eu`: `f8b06225db3d`)
- `tools/astra_player_review_v1.py`: `f121727c28bcbe47b43597bc39e5dfabfed28152044ee1253aa9c74754593989` (`141,867 B`; live `xx-eu`: `90ec1049c0a9`)
- `tools/astra_player_tracking_v13.py`: `3dea86fae0b4c3f3ecae59e368650f8a1b5da12048504936d5a6aa5e17d71373` (`189,628 B`; live `xx-eu`: `5aa70f53e7fe`)
- `deploy/player_dossier/plan.daily_r16.v3_p5.json` (`9f7c8dc770df`, `1,176 B`), `plan.daily_r16.v4_rollback_formal_a.json` (`5c7d57864e6e`, `904 B`), `production_manifest.json` (`ab81d990de99` LF, `7,077 B`).
- **Untouched Boundaries:** `plan.daily_r16.v2.json` and all 5 parallel upstream files (`astra_workbench_v20.py`, `astra_underwriting_discovery_v20.py`, `astra_data_engine_v23.py`, `astra_research_archive_v1.py`, `astra_workbench_reading_v1.py`), plus all sibling worktrees (`.worktrees/deribit-discovery-reliability@607d66fe`, `.worktrees/astra-audit-minimal-fix@b3d79695`, `.worktrees/deribit-ws-probe@6ef8454d`) have **0 edits**. No remote push or merge to `main`/`claude/astra` performed.

---

## 2. Deterministic Verification & As-Of Replay

- **`tests/test_astra_p5_candidate.py`:** `33 passed in 6.87s` (0 failed, 0 skipped).
- **Clean 23-file affected suite (`pytest_p5_full.log` with `jsonschema 4.26.0` installed):** **`492 collected -> 489 passed, 2 failed, 1 skipped, 0 errors`** in `123.34s`.
  - **2 isolated pre-existing `b3d79695` D02 failures:** `tests/test_astra_anchor_relative_v1.py::test_real_ard_replays_identical_A_without_hindsight` (`L275`) and `tests/test_astra_ard_condition_v1.py::test_real_hand_and_synthetic_continuation_stay_separate_and_reproducible` (`L123`) — reproduced verbatim on clean `b3d79695` baseline (`66,767.6` vs `66,739.4 + 双as-of核验`).
  - **1 skipped test:** `tests/test_astra_locked_candidate_compare_v1.py::test_real_initial_public_view_context_refs_keep_missing_source_clock_none`.
  - **Note on non-pytest file:** `tests/test_signal_llm_review_pipeline.py` uses standalone `(tool)` signatures rather than pytest fixtures (yields `26` setup errors + `1` skip if passed to `pytest`), so it is excluded from the 23-file pytest invocation.
- **Strict As-Of Replay (`step3_replay_audit.json`, deal clock `max(max_read, received_at_ms)`):**
  - `SOURCE_READY`: `H2` (`hand_af89529f`), `H3` (`hand_471b8255`), `H1_P4_unrun` (`hand_8b126baf`), `Nov19` (`hand_89df3d02`, `82/82` native ARD), `Nov20` (`hand_d9598e9b`, `82/82` native ARD), plus Nov 23 `16:00Z` (`hand_a050f60e`). Exact byte parity with `b3d79695` under `legacy_v1`.
  - `UPSTREAM_BLOCKED`: `H4` (`hand_0cdc0af5`: `source_card_*_data_gaps`, `source_band_snapshot_missing`, `source_history_no_native_clocked_rows`), Nov 22 (`hand_c613fed0`), and Nov 23 `08:00Z` ref (`hand_af87fde0`, `0` native put dossiers).

---

## 3. Bounded Read-Only Dual-Arm Shadow Evaluation

- **Scope & Spend:** `3` unseen/P4-unrun qualified `r16` hands (`6 / 20` hard cap calls, `retries=0`, `execution_allowed=false`, `deepseek-v4-pro`, `reasoning_effort=high`, isolated `/tmp` budget).  
  **Total Spend:** **`3.62856 RMB`** conservative peak (`3.588174 RMB` cache-aware peak; cap `<= 20.0 RMB`), **`0`** edits to `/var/lib/xxproject/llm/signal_llm_usage_ledger.json`, **zero `outcomes/` reads**.
- **Blind Audit (`b420a936`, randomized X/Y):** Arm B (`@1.2-p5-candidate`) won **`3/3`**, with **`0` false rejections**; however, Blind Review uncovered **`1` `[HIGH]` false pass** on `hand_d9598e9b`.

| Hand (`dossier`) | Order | Arm A `@1.0` (`p0_strict@1`) | Arm A Cross-Eval (P5 Gate) | Arm B `@1.2-p5-candidate` (`p0_strict@1`) | Arm B Cost / Latency | Blind Winner | Segregated Legacy Ref (`@1.0+legacy_v1`, diff input) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`hand_89df3d02`** (`Nov19`) | `A -> B` | `Accept/put` (`0.6068 RMB`, `255.5s`, `24,238 tok`) | `CONTRACT_FAILED`: `restricts_entry_to_finite_interval`, `:00/:05` grid drift, empty C falsifier | `Accept/put`, `new_entry`, 8/8 `COMPLETE_PARAGRAPH`, 4/4 C1 `true` — **Fair Pass** | `0.6007 RMB`, `262.4s`, `24,090 tok` | **`Arm B`** | `Accept/call` (`P5 REJECTED`: `confirmation_excerpt` truncated, 3 C1=`false`) |
| **`hand_d9598e9b`** (`Nov20`) | `B -> A` | `Accept/call` (`0.6459 RMB`, `276.2s`, `25,616 tok`) | `CONTRACT_FAILED`: 8x excerpt truncation + `restricts_entry_to_finite_interval` | `Accept/call`, `entry_not_returned` prose crisp & 4/4 C1 `true`, **BUT JSON `confirmation_excerpt` copied `comparison` (`FALSE PASS`)** | `0.7543 RMB`, `321.1s`, `29,445 tok` | **`Arm B`** *(with JSON bug)* | `Accept/call` (`P5 REJECTED`: 2x truncated, 2x M violations, 4 C1=`false`) |
| **`hand_8b126baf`** (`H1 Nov17`) | `A -> B` | `Accept/put` (`0.4953 RMB`, `194.1s`, `20,324 tok`) | `CONTRACT_FAILED`: truncated M + `restricts_entry_to_finite_interval` + `"不等待检查点"` | `Accept/put`, `new_entry`, 8/8 `COMPLETE_PARAGRAPH`, 4/4 C1 `true`, isolates native book `64.1k` — **Fair Pass** | `0.5256 RMB`, `203.3s`, `21,442 tok` | **`Arm B`** | `Accept/put` (`LEGACY_PUBLIC_ONLY`, finite `[61k, 63k]`) |

---

## 4. C Capability Assessment (`C_EARLY_WARNING_UNPROVEN`, Zero `outcomes/`)

- **Proven in Shadow:** On all `3/3` hands, `@1.2-p5-candidate` output specific, non-trading, 4-element C clauses (`borne_adverse`, `non_preclusive`, `uncertainty`, pre-M Hold-feed falsifiers) with 0 false rejections and no blocking Layer-2 advisories.
- **`C_EARLY_WARNING_UNPROVEN` (`hold_timeline_audit.json`):**
  - `hand_89df3d02`: Arm B picked `put 62,000` with C falsifiers checking aligned 4h/12h spot net sell, margin shrink toward `62,000`, and Pin/ARD migration. Non-outcome trajectory telemetry recorded `ARD_STRUCTURE_SHIFT` `64k -> 63k` at `17:02Z`, `cvd4 = -263 -> -377`, and margin contraction `1,040.6 -> 171.1 USD` **3.5h prior** to `20:30Z` `< 62,000` (`501` breach points). However, production locked the opposite `call 63.5k` side, and trajectory exports emit `flow_asof_ms=None` / `flow_window_basis={'source': None, 'start_ms': None, 'symbol': None}` (`flow_aligned_true_cnt=0`), blocking Category `D` Hold triggers (`astra_player_review_v1.py:1809`).
  - `hand_8b126baf`: Zero M breaches (`spot` `63,432.79`–`65,160.41`), `1` Cat `B` `HOLD`. `hand_d9598e9b`: `LINK_UNVERIFIED` (`exact_current_contract_not_found`), `0` trajectory points.

---

## 5. Release Gate (A–E) Verdicts & Open Blockers

| Gate | Verdict | Rationale / Citations |
| :--- | :--- | :--- |
| **A. Upstream `SOURCE_READY` & Coherence** | **`FAIL`** | Live `xx-eu` runs `b3d79695` (`discovery_status=None`, no `607d66fe`). Nov 22 & Nov 23 `08:00Z` refs `DATA_BLOCKED`; Nov 23 `16:00Z` (`hand_a050f60e`) dealt under `daily_r16@2` (`AI_ACCEPTED`, ledger `http_calls_used: 1` at `16:02:13Z`) then failed live lock with `tracking_quote_stale_or_unsynced`, `exact_current_contract_not_found`, and `shadow_evidence_size_limit`. `0` post-upstream-fix natural deal cycles exist. |
| **B. Regression, RC Safety & Rollback** | **`FAIL`** | Unpatched `[HIGH]` false pass: `astra_player_review_v1.py:966-981` + `astra_player_dossier_v1.py:1330-1336` accept `confirmation_excerpt` matching `"comparison"` instead of verifying `section == key` (+ no positive M checks). Unpatched `[MEDIUM]` Step 4B finding: `astra_player_dossier_v1.py:535-550` omits `prompt_version` on `@1.0` `MANUAL_IMPORT`, breaking `daily_r16@4` `episode()` (`L722`) (+ `[LOW]` CLI `LATE`/pre-contract persistence). 2 pre-existing D02 baseline failures remain in `pytest_p5_full.log`. |
| **C. Shadow Volume, Natural Cycles & Quality** | **`FAIL`** | `3` offline pairs (< required `5–10`), `0` post-upstream-fix natural deal cycles (< required `>= 2`), and 1/3 JSON binding false pass on `hand_d9598e9b`. Zero false rejections. |
| **D. Read-Only C & Hold Mappability** | **`CONDITIONAL PASS`** | Concrete 4-part non-auto-executing C prose (`3/3`), `C_EARLY_WARNING_UNPROVEN`; live trajectory `flow_asof_ms=None` currently blocks Cat `D`. |
| **E. Independent Signed `RELEASE_PASS`** | **`FAIL`** | Blind + Gate reviewer `b420a936` signed **`RELEASE_BLOCKED`** pending RC patches + post-upstream-fix Gate C completion. |

### Open Blockers & Clear Ownership
1. **P5 Workstream (`.worktrees/player-p5-candidate`):**
   - **[HIGH] Gate Section-Identity False Pass:** Require `chk["section"] == key` for expected-section excerpts in `validate_initial_reply_contract` + add positive M-essential assertions on `confirmation_excerpt`.
   - **[MEDIUM] `daily_r16@4` Rollback `MANUAL_IMPORT` Metadata & Gate Polish:** Persist `prompt_version` on explicit `@1.0` `MANUAL_IMPORT` under `daily_r16@4`; add CLI `response` persistence for `LATE`/pre-contract `ValueError`; block `:00/:05` variants (`UTC五分钟网格`/`每5分钟UTC网格`) under `p2_strict@1`; tighten `c1_degenerates_to_m` regex.
   - **Post-Fix Shadow Gate C:** Complete `5–10` pairs including `>= 2` post-upstream-fix natural deal cycles (`<= 14` calls / `<= 16.37 RMB` remaining within cap only if authorized).
2. **Parallel Upstream Workstream (`.worktrees/deribit-discovery-reliability` @ `607d66fe`):**
   - Land & deploy `SOURCE_READY` discovery fix, Stage 1.5.7 Accept exact-contract locking (`exact_current_contract_not_found`, `tracking_quote_stale_or_unsynced`, `shadow_evidence_size_limit`), and populate `flow_asof_ms` + `flow_window_basis` in locked trajectory exports so Cat `D` Hold triggers can fire.

---

## 6. Live State, Backup & Rollback Readiness

- **Live `xx-eu` Untouched (`2026-11-23T16:29:31Z`):** All `/opt/signal-audit-tools/*.py` (`f4e7ffb137b2`, `1d177bea8a3c`, `278d410c035d`, `f8b06225db3d`, `90ec1049c0a9`, `5aa70f53e7fe`), `astra_workbench_v20.py` (`lf 8a0b5b924d28`), `player-plan.json` (`daily_r16@2`), and `/var/lib/xxproject/llm/signal_llm_usage_ledger.json` (`1/20` at `16:02:13Z` from Formal A) remain intact; `/tmp/p5_shadow_eval_20261123` deleted.
- **Rollback Switch Ready:** Pre-cutover backup of `b3d79695` `/opt/signal-audit-tools/*` + `plan.daily_r16.v2.json` remains trivial; 1-step rollback target `plan.daily_r16.v4_rollback_formal_a.json` (`daily_r16@4`) operates cleanly during `08:05–15:50 UTC` quiet window once `[MEDIUM]` `MANUAL_IMPORT` metadata persistence is fixed.
