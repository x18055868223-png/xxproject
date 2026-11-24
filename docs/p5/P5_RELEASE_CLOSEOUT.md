# P5 + Shadow Exit Release Closeout Report (`P5_RELEASE_CLOSEOUT`)

**Pre-Cutover Status:** `RELEASE_APPROVED_PENDING_UNIFIED_DEPLOY` *(all 572 pre-cleanup + 62/99 post-cleanup tests pass; independent Reviewer `ddaed22e` signed `RELEASE_APPROVED` with `0 CRITICAL / 0 HIGH / 0 MEDIUM`; `Deribit_BTC_20261124T080000Z` settled cleanly at `08:06:11 UTC`; currently inside safe deploy window `08:35–15:50 UTC`)*  
**Historical Trail Preserved:** Prior `958f0c58` (`6c945ca3`) retained honestly as `SHADOW_BLOCKED_OR_INCONCLUSIVE`; `2b7b42be` retained as `READY_BUT_NOT_DEPLOYED`; `52264db3` promoted and merged with live `xx-eu` `server-native-release@2802262a` (superseding `c49fed0d` with Pre-Trade P1/P2 fixes) plus the urgent closed-loop `REVOKE -> SHADOW_EXITED` & frozen valuation fix. Two forward-sample gates (`>= 2` natural cycles and `5–7` shadow pairs) remain explicitly documented exemptions.

---

## 1. Identity & Artifact Manifest

| Layer | Pre-Cutover Live Baseline (`xx-eu`) | Unified Release Candidate (`codex/player-p5-candidate`) |
| :--- | :--- | :--- |
| **Git Commit SHA** | `2802262aa9c5cba6aca5f4d83f6934a1ebbb7f3e` (`server-native-release`, on `6311071c` + Pre-Trade P1/P2) | *(set in post-commit / post-deploy update)* |
| **Release Package ID** | `xxproject-server-2802262aa9c5-d7245ea39a9c` | *(set on clean commit build & deploy)* |
| **Active Plan** | `daily_r16@2` (`plan.daily_r16.v2.json`, LF `33dd99ad54a6...`) | `daily_r16@3` (`plan.daily_r16.v3_p5.json`, `effective_at_ms=1795536000000`, `2026-11-24T16:00:00Z`, LF `7aebdfe5de1c...`)<br>Rollback target: `xxproject-server-2802262aa9c5-d7245ea39a9c` / `daily_r16@4` (`plan.daily_r16.v4_rollback_formal_a.json`) |
| **System Prompt** | `player_initial_system@1.0` (`2,831 B`, `653168703cd8...`) | `player_initial_system@1.2-p5-candidate` (`3,073 B`, `b746c2d2f7f996d290992e01b11d9e4c4792a0e1afcacb900bbf0dfc678b6f34`) |
| **Input / Contract / Auth** | `legacy_v1` / legacy rules / `execution_allowed=false` | `p0_strict@1` / `p2_strict@1` / `execution_allowed=false` (Hold C strictly read-only; no real trading/closing) |

### Key Release File Checksums (`LF SHA-256`)
- **Immutable Prompt / Input / Condition / Anchor Modules (`0` bytes changed):**
  - `tools/astra_anchor_relative_v1.py`: `e4a4c2a5e01c6540eabe0f08809113daf18e1b4c93909dc4300729b38a3870be`
  - `tools/astra_player_condition_v1.py`: `ad51e910f0665f3454ed1f6ff8c0fb7817de29c9e604786d7bacbcafeb582743`
  - `tools/astra_player_input_v1.py`: `082572f2445be8cf2e09e1554e46fb8c8cb245a782a5ef2a9af68a7d94100d42`
  - `tools/astra_player_review_v1.py`: `b48a2785aae41565e3018b45fa2e4f72f388f5fc77da6780c23aa6524ad0c959`
  - `deploy/player_dossier/plan.daily_r16.v2.json`: `33dd99ad54a67c3fc7d558eadf96df232f8a4eaf1a2ae26898046143cdae3577`
- **Modified Runtime / UI / Unified Server Release Assets:**
  - `tools/astra_player_dossier_v1.py`: `8f99cfaa2740520ec8ed0a1a20d1726cb4aa4aaf9f8a693cd903fce25acd0e13`
  - `tools/astra_player_tracking_v13.py`: `5c7645a5c47aea5058efbe28ae9a7338a451d203d7b808952332eb9297bb450f`
  - `tools/astra_locked_candidate_store_v1.py`: `869ddc2de49ab66e20772ada13c4dff7ce26ea900ceb7a58822bfeb5a393cb7f`
  - `tools/astra_workbench_v20.py`: `d733a1700498a4564c08d2249c85a5a56974ffafe34a203741bb82a92f3d8026`
  - `tools/xxproject_server_health_v1.py`: `00f7deb01ece00e015e45870cd9009ae79cd00ab718556e89012c5ed2d858548`
  - `deploy/astra_workbench_v3/current-hand.js`: `65e748ff01739a80ec180a9f6af51a7bae6b7fd526a50010fc38d15e48d86f0b`
  - `deploy/astra_workbench_v3/locks.js`: `da9a1e21c916dbf8aa3bccdb69b82b6a33a844728936229cbe1d3c9cc7a9e5e5`
  - `deploy/astra_workbench_v3/player-audit.js`: `0d70d09a47628c59df935385f27bd598fc316c92a48aadcbd624d824d51fdf33`
  - `deploy/player_dossier/plan.daily_r16.v3_p5.json`: `7aebdfe5de1c169c2821bf02ebedbd038dbce1c47b9788b9998e1a98a64b6dca`
  - `deploy/player_dossier/plan.daily_r16.v4_rollback_formal_a.json`: `84501140acf6a0b577fdf532f556165ef400aae99d959319720d69d7416890e0`
  - `deploy/player_dossier/production_manifest.json`: `7e81ac286ece3ef0c60ffc0a1043a9a7672ced763cc76ec95de5bb3c3decf895`
  - `deploy/xxproject_server/systemd_units_unified/astra-player-prewarm.service`: `fa66b20364995bfe296c3746cc72c62fd194db7f676509ac34b66fa8dccadc52`
  - `deploy/xxproject_server/systemd_units_unified/astra-player-deal.service`: `45c004e700318b767093f5bad4b02321e1f5a527c86947beefa2b05e3a166a24`
  - `deploy/xxproject_server/build_release.py`: `6e686b6773500bd2c8e3b710d71c820167df3c770f4ef27706cca8eeaaa4429a`
  - `deploy/xxproject_server/deploy_release.py`: `335bb636d12034ab8e553171fa9c3601acc144a9c0f6aab18489025a48080e53`

---

## 2. Verification & Independent Audit Summary

- **Pre-cleanup full 31-file affected suite (`pytest_p5_shadow_exit_full.log`):** **`572 passed, 1 skipped, 0 failed in 348.95s`**.
- **Post-cleanup suites (every modified Python & JS file re-tested):**
  - `node --input-type=module` ES import checks (`current-hand.js`, `locks.js`, `player-audit.js`): `JS_OK`.
  - `pytest test_astra_p5_shadow_exit.py test_astra_workbench_v3_locks.py test_astra_workbench_v3_player_audit.py test_astra_player_tracking_v13.py -q`: **`62 passed in 176.64s`**.
  - `pytest test_astra_p5_candidate.py test_astra_player_e2e_v13.py test_astra_audit_minimal_fixes.py test_player_initial_recovery.py test_astra_workbench_v3_readings.py test_astra_locked_followup_frontend.py test_xxproject_server_release.py -q`: **`99 passed in 80.21s`**.
- **Independent Adversarial Reviewer (`ddaed22e`):** Signed **`RELEASE_APPROVED`** (`0 CRITICAL`, `0 HIGH`, `0 MEDIUM`, `1 non-blocking LOW` operational deploy window note `08:35–15:50 UTC`).
- **Shadow Baseline Preserved:** `3` executed pairs, **`2 / 3`** 1st-attempt Arm B contract-compliant (`hand_d9598e9b` retained as genuine `CONTRACT_FAILED`); `C_EARLY_WARNING_UNPROVEN`.

---

## 3. Cutover & Live Verification Log

- **Single Operator Scheduling:** Cancelled superseded pre-patch `52264db3`-only timer `task-5801` at `07:05Z`; confirmed `Deribit_BTC_20261124T080000Z` expired `08:00Z`, settled cleanly via `astra-player-settle.service` at `08:06:11Z` (`outcome_38a533146d3ceec6dc23eb7e`, `terminal.json` `settlement_state=settled`), `0` open locks, `0` pending LLM calls.
- **Deployment Target:** Push to `xxproject` (`codex/player-p5-candidate` + fast-forward `claude/astra`), atomic server-native release install via `deploy_release.py` on `xx-eu` inside `08:35–15:50 UTC`, append-only `reconcile-shadow-exit` closure on `hand_a050f60ea6bb7d73acf68a3f`, and scheduled natural-deal E2E check at `2026-11-24T17:15:00Z`.
