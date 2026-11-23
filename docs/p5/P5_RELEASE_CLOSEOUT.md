# P5 Release Closeout Report (`P5_RELEASE_CLOSEOUT`)

**Terminal Status:** `READY_BUT_NOT_DEPLOYED`  
**Main Player Production Disposition:** Formal A (`6311071c887433efb5a9b754290a1d8050caa0d8` runtime + `daily_r16@2`, `player_initial_system@1.0 + legacy_v1`, `execution_allowed=false`) **retained untouched on `xx-eu`** (last verified `2026-11-23T17:40Z`). All P5 code-level blocking defects are cleared with **0 CRITICAL / 0 HIGH** remaining at RC commit `c39b3d278585b94fbdccd9cc320befe4b47871ad`; live promotion and remote push are held solely because **0 / 2** post-`6311071c` natural `r16` deal cycles have elapsed yet (Gates **A / C / E** pending post-`6311071c` soak on Nov 24–25 `16:00Z`). Rollback target remains **Formal A** (`daily_r16@4` / `daily_r16@2`), never Route D.

---

## 1. Identity & Diff Manifest

| Layer | Live Production (`xx-eu`) | P5 RC (`codex/player-p5-candidate`) |
| :--- | :--- | :--- |
| **Git Commit SHA** | `6311071c887433efb5a9b754290a1d8050caa0d8` | `c39b3d278585b94fbdccd9cc320befe4b47871ad` (+ closeout docs convergence commit) |
| **Active Plan** | `daily_r16@2` (`plan.daily_r16.v2.json`, raw `bf0ec9eb8668` / LF `33dd99ad54a6`) | `daily_r16@3` (`plan.daily_r16.v3_p5.json`, LF `9f7c8dc770df`), rollback `daily_r16@4` (`5c7d57864e6e`) |
| **System Prompt** | `player_initial_system@1.0` (`2,831 B`, `653168703cd8...`) | `player_initial_system@1.2-p5-candidate` (`3,073 B`, `b746c2d2f7f996d290992e01b11d9e4c4792a0e1afcacb900bbf0dfc678b6f34`) |
| **Input / Contract** | `legacy_v1` / legacy rules | `p0_strict@1` / `p2_strict@1` |
| **`execution_allowed`** | `false` | `false` (initial + Hold C strictly read-only) |

### P5-Owned Runtime & Deployment Artifacts (`c39b3d27`, LF SHA-256)
- `tools/astra_anchor_relative_v1.py`: `e4a4c2a5e01c6540eabe0f08809113daf18e1b4c93909dc4300729b38a3870be` (`14,848 B`; live `xx-eu` `6311071c`: `f4e7ffb137b2`)
- `tools/astra_player_condition_v1.py`: `ad51e910f0665f3454ed1f6ff8c0fb7817de29c9e604786d7bacbcafeb582743` (`38,438 B`; live `xx-eu` `6311071c`: `1d177bea8a3c`)
- `tools/astra_player_dossier_v1.py`: `9dce71a2fb624f3e6c17fbdd06e0bb8b22748822b95f44e6f05540dc9989872f` (`110,617 B`; live `xx-eu` `6311071c`: `278d410c035d`)
- `tools/astra_player_input_v1.py`: `082572f2445be8cf2e09e1554e46fb8c8cb245a782a5ef2a9af68a7d94100d42` (`79,548 B`; live `xx-eu` `6311071c`: `f8b06225db3d`)
- `tools/astra_player_review_v1.py`: `b48a2785aae41565e3018b45fa2e4f72f388f5fc77da6780c23aa6524ad0c959` (`145,294 B`; live `xx-eu` `6311071c`: `90ec1049c0a9`)
- `tools/astra_player_tracking_v13.py`: `7d9a036b40fe106bd5d778e8656b57d13041cd8c26cb3a3feebeb6f6ddfa4c9a` (`198,726 B`; live `xx-eu` `6311071c`: `0eeeedc116f5`)
- `deploy/player_dossier/plan.daily_r16.v3_p5.json`: `9f7c8dc770df` (`1,176 B`; **deploy note:** bump `effective_at_ms` to the post-soak cutover `16:00Z` slot at deployment time)
- `deploy/player_dossier/plan.daily_r16.v4_rollback_formal_a.json`: `5c7d57864e6e` (`904 B`)
- `deploy/player_dossier/production_manifest.json`: LF `e0c7b4273546` (`8,597 B`; raw `47f8ed02f13f`, `8,758 B`)
- **Upstream & Sibling Isolation Confirmed:** `git diff 6311071c --` across `astra_underwriting_discovery_v20.py` (`204646328c5f`), `astra_workbench_v20.py` (LF `b8fb1a43acc8` / raw `779ee4d370c0`), `astra_research_archive_v1.py` (`c35f34134b7b`), `astra_data_engine_v23.py` (`a91ff6e67975`), `astra_workbench_reading_v1.py` (`c9a3c709def0`), `data_product_registry_v23.json` (`bd99a3104d16`), and `plan.daily_r16.v2.json` is empty (**0 bytes**). Sibling worktrees `.worktrees/deribit-rest-pacing` (`6311071c`) and `.worktrees/astra-audit-minimal-fix` (`b3d79695`) are clean and untouched. Zero `git push` or merge to `main`/`claude/astra`.

---

## 2. Deterministic Verification, Fault Injection & Offline Re-Grade

- **`tests/test_astra_p5_candidate.py`:** **`38 passed in 10.59s`** (0 failed, 0 skipped).
- **24-File Full Affected Regression (`pytest_p5_step2_full.log`, `jsonschema 4.26.0`):** **`513 collected -> 510 passed, 2 failed, 1 skipped, 0 errors` in `173.23s`**.
  - **2 Pre-Existing D02 Baseline Failures (reproduced identically on clean `6311071c` in `.worktrees/deribit-rest-pacing` -> `2 failed, 44 passed in 0.87s`):** `test_astra_anchor_relative_v1.py::test_real_ard_replays_identical_A_without_hindsight` (`L275`) and `test_astra_ard_condition_v1.py::test_real_hand_and_synthetic_continuation_stay_separate_and_reproducible` (`L123`). Legacy `@1.0 + legacy_v1` parity vs `b3d79695` passes on H2/H3/H4.
  - **1 Skipped Test:** `test_astra_locked_candidate_compare_v1.py::test_real_initial_public_view_context_refs_keep_missing_source_clock_none` (optional `.artifacts/locked_candidate_followup_20261008_01/initial_public_view.json` fixture absent).
- **Fault Injection, Interop & Rollback Verified in `tests/test_astra_p5_candidate.py`:** HTTP 401/403 non-retryable 1-call cap (`REQUEST_FAILED`), `TimeoutError` (`retries=0`, 0 duplicate calls across `auto_deal` + `recover_initial`), lost receipt after provider response, 8-thread `DailyHttpBudget` contention (`limit=3`), `daily_r16@4` `@1.0` `MANUAL_IMPORT` + `episode()` + `daily_r16@2` omission compat, CLI `LATE` + pre-contract `PARSE_FAILED`, `6311071c` `max_discovery_age_ms=300_000` / `data_health.usage_decisions.default`, and `PENDING_LOCK` -> `DELAYED_LOCK` P5 interop.
- **Offline Replay & Hardened Re-Grade (`step2_regrade_report.json`, Zero `outcomes/` Reads):**
  - `H2` / `H3` `SOURCE_READY`, `H4` `UPSTREAM_BLOCKED`; `live_xxeu_6311071c_snapshot.json` evaluates `SOURCE_READY` & `accept_lock_ready=True` (turns `UPSTREAM_BLOCKED(discovery_observed_stale)` at `+301s`).
  - Nov 23 `hand_a050f60ea6bb7d73acf68a3f` (`16:00Z` deal **prior** to `16:49:44Z` `6311071c` deploy): excludes from post-`6311071c` soak count; production `@1.0` reply fails P5 strict gate (`5` truncations + finite interval + `c_degenerates_to_m=true`), audited strictly as contract delta rather than side error.

| Saved Shadow Hand (`pre-6311071c`) | Arm A `@1.0` (`p0_strict@1`) | Arm A P5 Cross-Eval Note | Arm B `@1.2-p5-candidate` (`c39b3d27` Re-Grade) | Peak Cost (A / B) |
| :--- | :--- | :--- | :--- | :--- |
| **`hand_8b126baf41e5883b410c1d06`** (`H1 Nov17`) | `Accept / put` | `CONTRACT_FAILED` + `c_degenerates_to_m=true` (legacy schema diff, not side error) | **`PASSED`** (`Accept / put`, `new_entry`, 8/8 section match, 4/4 C1 `true`, `c_degenerates_to_m=false`) | `0.4953 / 0.5256 RMB` |
| **`hand_89df3d02846e56192937f6df`** (`Nov19`) | `Accept / put` | `CONTRACT_FAILED` (`restricts_entry_to_finite_interval`, `:00/:05` grid) | **`PASSED`** (`Accept / put`, `new_entry`, 8/8 section match, 4/4 C1 `true`, `c_degenerates_to_m=false`) | `0.6068 / 0.6007 RMB` |
| **`hand_d9598e9be936b9b777c5295f`** (`Nov20`) | `Accept / call` | `CONTRACT_FAILED` (`8x` truncation + finite interval) | **FLIPPED `PASSED` -> `CONTRACT_FAILED`** (`WRONG_SECTION:comparison`, `j1_excerpt_duplicate_section`, `missing_m_mechanical_anchor`; prose OK, JSON copy bug blocked) | `0.6459 / 0.7543 RMB` |

---

## 3. Cumulative Shadow Spend & Live Host State

- **Cumulative Shadow Usage (`0` new paid calls this round):** `6 / 20` calls, `¥3.62856` conservative peak (`¥3.588174` cache-aware peak), remaining budget **`14` calls / `¥16.37144`** (`cap = 20 calls / ¥20.0`).
- **Live `xx-eu` Formal A Ledger (`2026-11-23T17:40Z` check):** `date_bjt="2026-11-24"`, `http_calls_used=2 / 20`, `updated_at_utc="2026-11-23T16:50:08+00:00"` (untouched by P5), `/opt/signal-audit-tools/` hashes match `6311071c`, active systemd units running (`astra-workbench-v20.service`, `astra-research-archive.timer`, `astra-player-*.timer`).

---

## 4. Release Gates A–E & Final Disposition

| Gate | Verdict | Rationale / Citations |
| :--- | :--- | :--- |
| **A. Live `6311071c` Soak & `SOURCE_READY`** | **`PENDING_SOAK`** | `6311071c` live (`16:49:44Z–16:49:57Z`), live snapshot `SOURCE_READY` & `accept_lock_ready=True`, `evaluate_source_readiness` hardened for `300s` discovery TTL + `data_health`; awaiting `>= 2` post-`6311071c` natural daily cycles (`Nov 24–25 16:00Z`). |
| **B. Security, Contract & `@1.0` Compat** | **`PASS`** | `[HIGH]` `WRONG_SECTION` cross-section false pass closed (`hand_d9598e9b` Arm B cleanly rejected), positive M anchor enforced, `daily_r16@4` `@1.0` `MANUAL_IMPORT`/`episode()` + `daily_r16@2` byte compat + CLI `LATE`/`PARSE_FAILED` persistence all pass; `0` unresolved CRITICAL/HIGH. |
| **C. Runtime Reliability & Shadow Volume** | **`PENDING_SHADOW`** | Fault-injection (`401/403`, timeout, lost receipt, `DailyHttpBudget` concurrency, `PENDING_LOCK` interop, `execution_allowed=false`) **PASS**; re-grade `2/3` passed (`0` false rejections, `0` false passes), awaiting `>= 2` post-`6311071c` natural cycles + bounded shadow completion (`14` calls / `¥16.37144` remaining) to reach `5–7` total qualified pairs. |
| **D. Read-Only C & Authority Boundary** | **`PASS` (`C_EARLY_WARNING_UNPROVEN`)** | Concrete 4-part read-only C (`4/4` on valid B replies), Layer-2 `不接受...按破坏规则处理` tightened, `execution_allowed=false` strictly preserved; proactive early-warning utility remains honestly `C_EARLY_WARNING_UNPROVEN`. |
| **E. Independent Signed Review & Cutover Readiness** | **`HELD_FOR_GATE_A_C`** | Fresh independent adversarial reviewer `59cca70d` verified **`0 CRITICAL / 0 HIGH`** defects on RC `c39b3d27` and recommended **`READY_BUT_NOT_DEPLOYED`** (refresh `v3_p5.json` `effective_at_ms` to cutover `16:00Z` slot at post-soak deploy time). |

**Unlock Checklist for Next Deployment Window (Post `2026-11-25 16:00 UTC`):**
1. Confirm `2/2` post-`6311071c` natural `r16` cycles (`Nov 24` & `Nov 25 16:00Z`) achieve clean `SOURCE_READY` & Stage 1.5.7 locking.
2. Spend bounded remaining shadow budget (`<= 14` calls / `<= ¥16.37144`) on `>= 2` post-`6311071c` qualified natural pairs (bringing total to `5–7` pairs) with `0` false rejections / `0` false passes and signed `RELEASE_PASS`.
3. Update `plan.daily_r16.v3_p5.json` `effective_at_ms` to target cutover `16:00Z` slot during quiet window `08:05–15:50 UTC`, deploy exact RC files + systemd check, keep Formal A (`daily_r16@4` / `v2`) backup hot, and verify the next natural deal E2E (`PROMOTED_PENDING_E2E` -> `MAIN_PLAYER_E2E_CONFIRMED`).
