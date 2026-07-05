# Neutral Repair Opposite Flip Damage Carry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow a confirmed opposite DIE flip to carry a tightly bounded Anchor damage fact into the new NeutralRepair episode.

**Architecture:** The change stays inside the signal producer's `NeutralRepairSignalTracker`. It seeds only damage evidence during the opposite-event reset boundary, then keeps the existing cooldown, repair score, confirmation tick, audit card, and push gates unchanged.

**Tech Stack:** Python 3.12, single-file FMZ signal artifact, direct producer-level tests.

---

### Task 1: Producer State Machine Test

**Files:**
- Create: `demo/tests/test_neutral_repair_opposite_flip_damage_carry.py`
- Modify: none

- [ ] Write a failing test where a DOWN episode observes damage, two UP opposite ticks create a new UP episode while Anchor is below 60, and two later repaired ticks confirm `NR_REPAIR_CONFIRMED`.
- [ ] Add negative tests proving ordinary new DIE episodes and same-direction gap resets do not seed damage.
- [ ] Run the new test and confirm it fails because the current UP episode remains `NR_WAIT_ANCHOR_DAMAGE`.

### Task 2: Minimal State Machine Change

**Files:**
- Modify: `demo/最新交付物/neutral_regulation_demo_fmz.py`

- [ ] Add a small helper that seeds `anchor_damage_observed` only immediately after a confirmed opposite-event reset when the new context starts below `nr_anchor_repair_score`.
- [ ] Record a distinct evidence code and source metadata without copying old `episode_id`, old direction, `repair_confirm_count`, or `confirmed_at_ms`.
- [ ] Keep all non-opposite `_new_context()` paths unchanged.

### Task 3: Verification

**Files:**
- Modify: none

- [ ] Run `demo/tests/test_neutral_repair_opposite_flip_damage_carry.py`.
- [ ] Run existing signal closure/config/push regression tests.
- [ ] Parse `demo/最新交付物/neutral_regulation_demo_fmz.py` with Python AST.
- [ ] Ask a read-only reviewer to inspect the diff before final delivery.
