# P5 Release Closeout Report (`P5_RELEASE_CLOSEOUT`)

**Pre-Cutover Audit Status:** `PRE_RELEASE_VERIFIED_PENDING_QUIET_CUTOVER` *(pending independent Reviewer `EXPEDITED_RELEASE_APPROVED`, then atomic quiet-window cutover at `2026-11-24T08:15:00Z` -> `EXPEDITED_PROMOTED_PENDING_E2E` -> `MAIN_PLAYER_E2E_CONFIRMED` after `2026-11-24T16:00Z` deal)*  
**Historical Trail Preserved:** Prior closeout `958f0c58` (`6c945ca3`) retained honestly as `SHADOW_BLOCKED_OR_INCONCLUSIVE`; prior `2b7b42be` retained as `READY_BUT_NOT_DEPLOYED`. This round executes the user-authorized expedited promotion where two forward-sample gates (`>= 2` post-`6311071c` natural cycles and `5–7` shadow pairs) are explicitly accepted as residual risks while all non-waivable safety, contract, billing, compatibility, and quiet-window gates remain strictly enforced.

---

## 1. Identity & Artifact Manifest

| Layer | Pre-Cutover Live Baseline (`xx-eu`) | Expedited P5 Release Candidate (`codex/player-p5-candidate`) |
| :--- | :--- | :--- |
| **Git Commit SHA** | `6311071c887433efb5a9b754290a1d8050caa0d8` | *(updated on commit cleanly after docs convergence)* |
| **Active Plan** | `daily_r16@2` (`plan.daily_r16.v2.json`, LF `33dd99ad54a6`) | `daily_r16@3` (`plan.daily_r16.v3_p5.json`, `effective_at_ms=1795536000000`, `2026-11-24T16:00:00Z`, LF `7aebdfe5de1c...`)<br>Rollback: `daily_r16@4` (`plan.daily_r16.v4_rollback_formal_a.json`, `2026-11-25T16:00:00Z`, LF `84501140acf6...`) |
| **System Prompt** | `player_initial_system@1.0` (`2,831 B`, `653168703cd8...`) | `player_initial_system@1.2-p5-candidate` (`3,073 B`, `b746c2d2f7f996d290992e01b11d9e4c4792a0e1afcacb900bbf0dfc678b6f34`) |
| **Input / Contract / Auth** | `legacy_v1` / legacy rules / `execution_allowed=false` | `p0_strict@1` / `p2_strict@1` / `execution_allowed=false` (Hold C strictly read-only) |

### P5-Owned Runtime & Deployment Files (`LF SHA-256`)
- `tools/astra_anchor_relative_v1.py`: `e4a4c2a5e01c6540eabe0f08809113daf18e1b4c93909dc4300729b38a3870be` (`14,848 B`)
- `tools/astra_player_condition_v1.py`: `ad51e910f0665f3454ed1f6ff8c0fb7817de29c9e604786d7bacbcafeb582743` (`38,438 B`)
- `tools/astra_player_dossier_v1.py`: `9dce71a2fb624f3e6c17fbdd06e0bb8b22748822b95f44e6f05540dc9989872f` (`110,617 B`)
- `tools/astra_player_input_v1.py`: `082572f2445be8cf2e09e1554e46fb8c8cb245a782a5ef2a9af68a7d94100d42` (`79,548 B`)
- `tools/astra_player_review_v1.py`: `b48a2785aae41565e3018b45fa2e4f72f388f5fc77da6780c23aa6524ad0c959` (`145,294 B`)
- `tools/astra_player_tracking_v13.py`: `7073c1280634e7264d0f12d560fe0fca632551fdda8852864c373da68c313e97` (`198,727 B`, includes `MAX_BYTES = 32_000_000` resolving live `hand_a050f60e` `shadow_evidence_size_limit`)
- `deploy/player_dossier/plan.daily_r16.v3_p5.json`: `7aebdfe5de1c169c2821bf02ebedbd038dbce1c47b9788b9998e1a98a64b6dca` (`1,176 B`)
- `deploy/player_dossier/plan.daily_r16.v4_rollback_formal_a.json`: `84501140acf6a0b577fdf532f556165ef400aae99d959319720d69d7416890e0` (`904 B`)
- `deploy/player_dossier/production_manifest.json`: `4922bea9a88ce4ebbc67f8cb37c9f42fbbfc481ddfc3616ff41ffe61c729ec8d` (`8,683 B` LF / `8,845 B` raw)
- **Zero-Touch Guarantee:** `git diff 6311071c --` on `astra_underwriting_discovery_v20.py`, `astra_workbench_v20.py`, `astra_research_archive_v1.py`, `astra_data_engine_v23.py`, `astra_workbench_reading_v1.py`, `data_product_registry_v23.json`, and `plan.daily_r16.v2.json` = **0 bytes**.

---

## 2. Verification, Replay & Non-Waivable Gates Summary

- **`tests/test_astra_p5_candidate.py`:** **`39 passed in 15.45s`** (including 32 MB export cap & `2026-11-24T08:15Z` zero-`MISSED` cutover schedule test).
- **Full 24-File Regression (`pytest_p5_expedited_full.log`, `Python 3.12` + `jsonschema 4.26.0`):** **`514 collected -> 511 passed, 2 failed, 1 skipped, 0 errors` in `195.70s`**.
  - **2 Pre-Existing Baseline D02 Failures (independently reproduced on clean `6311071c` -> `2 failed, 44 passed in 2.81s`):** `test_astra_anchor_relative_v1.py:275` & `test_astra_ard_condition_v1.py:123` (unrelated to P5).
  - **1 Skipped:** `test_astra_locked_candidate_compare_v1.py:387` (missing optional local fixture).
- **Re-Graded Shadow Baseline (`step2_regrade_report.json`, Zero `outcomes/` Reads, `0` New Paid Calls):**
  - Executed pairs: **`3`** (`hand_8b126baf`, `hand_89df3d02`, `hand_d9598e9b`; `6/20` calls, `¥3.62856` conservative peak; `14/20` calls & `¥16.37144` remaining unspent).
  - First-attempt Arm B contract-compliant rate: **`2 / 3`** (`hand_8b126baf` & `hand_89df3d02` **`PASSED`**; **`hand_d9598e9b` retained as genuine `CONTRACT_FAILED`** due to `comparison -> confirmation_excerpt` cross-section copy; never reclassified as passing). `C_EARLY_WARNING_UNPROVEN`.

---

## 3. Accepted Exemptions, Cutover Window & Live Deployment Log

- **Explicitly Accepted Pre-Deploy Exemptions (User-Authorized):**
  1. Wait for `>= 2` post-`6311071c` natural deal cycles before promotion (live `xx-eu` at `2026-11-24T03:24Z` is in open `hand_a050f60e` expiring `08:00Z`, so `0/2` post-`6311071c` natural cycles exist prior to promotion).
  2. Expand shadow evaluation to `5–7` pairs prior to promotion (deferred to live formal operation with zero `MISSED`/retry guardrails).
- **Cutover Plan:** Wait until `hand_a050f60e` settles (`2026-11-24T08:00–08:06 UTC`), execute preflight backup + non-force push to `xxproject` (`codex/player-p5-candidate` & fast-forward `claude/astra`) + atomic `/opt/signal-audit-tools/` deploy during quiet window (`2026-11-24T08:15 UTC`), verify non-paid health (`EXPEDITED_PROMOTED_PENDING_E2E`), then audit the `2026-11-24T16:00Z` formal natural deal after `17:15 UTC` (`MAIN_PLAYER_E2E_CONFIRMED` vs `ROLLED_BACK`).
