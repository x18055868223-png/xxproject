import importlib.util
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]
SIGNAL_FILE = (
    ROOT
    / "demo"
    / "\u6700\u65b0\u4ea4\u4ed8\u7269"
    / "neutral_regulation_demo_fmz.py"
)


def load_signal_module():
    spec = importlib.util.spec_from_file_location(
        "nrd_neutral_repair_signal_loss", SIGNAL_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def mdie(value, data_state="OK"):
    return {"m_die": value, "data_status": {"data_state": data_state}}


def anchor(score, nd=0.0, state="OK", reasons=None):
    return {
        "state": state,
        "facts": {
            "anchor_gravity_ref_score": score,
            "normalized_deviation": nd,
        },
        "reasons": reasons or [],
    }


class Clock:
    def __init__(self, start_ms=1_782_599_500_000):
        self.value = start_ms

    def advance_min(self, minutes=1):
        self.value += int(minutes * 60_000)
        return self.value

    def now_ms(self):
        return self.value


def tracker_with_clock(mod, **overrides):
    config = dict(mod.CONFIG)
    config["nr_opposite_confirm_ticks"] = 2
    config["nr_repair_confirm_ticks"] = 2
    config.update(overrides)
    return mod.NeutralRepairSignalTracker(config), Clock()


def evidence(out):
    return out["anchor_context"]["anchor_damage_evidence"]


def test_nd_and_deviation_only_do_not_confirm(mod):
    for setup_anchor, label in (
            (anchor(60.50, nd=1.20), "ND-only"),
            (anchor(60.50, reasons=["ANCHOR_DEVIATION_WIDE"]),
             "deviation-only"),
    ):
        tracker, clock = tracker_with_clock(mod)
        original_now_ms = mod.now_ms
        mod.now_ms = clock.now_ms
        try:
            tracker.update(mdie(0.90), setup_anchor)
            clock.advance_min()
            tracker.update(mdie(0.50), anchor(60.70))
            clock.advance_min()
            out = tracker.update(mdie(0.50), anchor(60.80))
            assert_true(out["state"] == "NR_WAIT_ANCHOR_DAMAGE",
                        label + " must not replace same-chain Anchor<60")
            assert_true(not out["is_active"],
                        label + " must not emit repair signal")
        finally:
            mod.now_ms = original_now_ms


def test_unconfirmed_stale_non_active_emits_once_then_idle(mod):
    tracker, clock = tracker_with_clock(mod)
    original_now_ms = mod.now_ms
    mod.now_ms = clock.now_ms
    try:
        tracker.update(mdie(0.90), anchor(58.20))
        clock.advance_min(361)
        out = tracker.update(mdie(0.0), anchor(60.20))
        assert_true(out["state"] == "NR_REPAIR_STALE",
                    "first non-active tick after ttl should emit STALE")
        clock.advance_min()
        out = tracker.update(mdie(0.0), anchor(60.20))
        assert_true(out["state"] == "NR_IDLE",
                    "stale context should clear; next tick should be IDLE")
        assert_true(out["event_context"] is None,
                    "IDLE after stale clear should not expose old episode")
    finally:
        mod.now_ms = original_now_ms


def test_expired_active_tick_starts_new_episode(mod):
    tracker, clock = tracker_with_clock(mod)
    original_now_ms = mod.now_ms
    mod.now_ms = clock.now_ms
    try:
        old = tracker.update(mdie(0.90), anchor(58.20))
        old_id = old["event_context"]["episode_id"]
        clock.advance_min(361)
        out = tracker.update(mdie(-0.90), anchor(60.20))
        ctx = out["event_context"]
        assert_true(out["state"] == "NR_DISPLACEMENT_ACTIVE",
                    "expired active tick should start fresh episode")
        assert_true(ctx["episode_direction"] == "DOWN",
                    "fresh episode should use current direction")
        assert_true(ctx["episode_id"] != old_id,
                    "fresh episode should not reuse stale old episode")
    finally:
        mod.now_ms = original_now_ms


def assert_transient_loss_resets_counter(mod, make_loss_tick, label):
    tracker, clock = tracker_with_clock(mod)
    original_now_ms = mod.now_ms
    mod.now_ms = clock.now_ms
    try:
        started = tracker.update(mdie(0.90), anchor(58.20))
        episode_id = started["event_context"]["episode_id"]
        clock.advance_min()
        out = tracker.update(mdie(0.50), anchor(60.20))
        assert_true(out["state"] == "NR_REPAIR_CANDIDATE",
                    label + " setup should create candidate")
        clock.advance_min()
        out = make_loss_tick(tracker)
        assert_true(out["state"] == "NR_DATA_INSUFFICIENT",
                    label + " should report data insufficient")
        assert_true((out["event_context"] or {}).get("episode_id")
                    == episode_id,
                    label + " should retain handoff context")
        assert_true(out["anchor_context"]["repair_confirm_count"] == 0,
                    label + " should reset repair confirm count")
        assert_true("ANCHOR_SUBREPAIR_OBSERVED_BELOW_60" in evidence(out),
                    label + " should retain below-60 handoff")
        clock.advance_min()
        out = tracker.update(mdie(0.50), anchor(60.30))
        assert_true(out["state"] == "NR_REPAIR_CANDIDATE",
                    label + " recovery needs first fresh valid tick")
        clock.advance_min()
        out = tracker.update(mdie(0.50), anchor(60.40))
        assert_true(out["state"] == "NR_REPAIR_CONFIRMED",
                    label + " second fresh valid tick should confirm")
    finally:
        mod.now_ms = original_now_ms


def test_transient_mdie_bad_preserves_handoff(mod):
    assert_transient_loss_resets_counter(
        mod,
        lambda tracker: tracker.update(mdie(0.50, data_state="BAD"),
                                       anchor(60.30)),
        "M-DIE bad data",
    )


def test_transient_anchor_invalid_preserves_handoff(mod):
    invalid_state = getattr(mod, "STATE_INVALID", "Invalid")
    assert_transient_loss_resets_counter(
        mod,
        lambda tracker: tracker.update(mdie(0.50),
                                       anchor(60.30, state=invalid_state)),
        "Anchor invalid",
    )


def test_active_tick_interrupts_candidate_count(mod):
    tracker, clock = tracker_with_clock(mod)
    original_now_ms = mod.now_ms
    mod.now_ms = clock.now_ms
    try:
        tracker.update(mdie(0.90), anchor(58.20))
        clock.advance_min()
        tracker.update(mdie(0.50), anchor(60.20))
        clock.advance_min()
        out = tracker.update(mdie(0.650), anchor(60.30))
        assert_true(out["state"] == "NR_DISPLACEMENT_ACTIVE",
                    "exact event threshold should interrupt candidate")
        assert_true(out["anchor_context"]["repair_confirm_count"] == 0,
                    "active tick should reset repair confirm count")
        clock.advance_min()
        out = tracker.update(mdie(0.649), anchor(60.40))
        assert_true(out["state"] == "NR_REPAIR_CANDIDATE",
                    "just below threshold should restart at candidate")
        clock.advance_min()
        out = tracker.update(mdie(0.649), anchor(60.50))
        assert_true(out["state"] == "NR_REPAIR_CONFIRMED",
                    "second fresh below-threshold tick should confirm")
    finally:
        mod.now_ms = original_now_ms


def test_same_direction_gap_does_not_carry_old_handoff(mod):
    tracker, clock = tracker_with_clock(mod)
    original_now_ms = mod.now_ms
    mod.now_ms = clock.now_ms
    try:
        old = tracker.update(mdie(0.90), anchor(54.00))
        old_id = old["event_context"]["episode_id"]
        clock.advance_min()
        tracker.update(mdie(0.82), anchor(54.00))
        clock.advance_min(46)
        out = tracker.update(mdie(0.90), anchor(60.20))
        assert_true(out["event_context"]["episode_id"] != old_id,
                    "same-direction gap >45min should start new episode")
        assert_true("ANCHOR_SUBREPAIR_OBSERVED_BELOW_60" not in evidence(out),
                    "new same-direction episode must not carry old handoff")
        clock.advance_min()
        out = tracker.update(mdie(0.50), anchor(60.40))
        assert_true(out["state"] == "NR_WAIT_ANCHOR_DAMAGE",
                    "gap-reset episode should wait for fresh Anchor<60")
    finally:
        mod.now_ms = original_now_ms


def test_nd_only_old_episode_does_not_carry(mod):
    tracker, clock = tracker_with_clock(mod)
    original_now_ms = mod.now_ms
    mod.now_ms = clock.now_ms
    try:
        tracker.update(mdie(-0.90), anchor(60.50, nd=1.20))
        clock.advance_min()
        tracker.update(mdie(0.90), anchor(60.20))
        clock.advance_min()
        out = tracker.update(mdie(0.886), anchor(60.20))
        assert_true("ANCHOR_DAMAGE_OBSERVED_OPPOSITE_RESET_SUBREPAIR"
                    not in evidence(out),
                    "ND-only old episode must not carry pending handoff")
        assert_true(out["anchor_context"]["anchor_damage_seed"] is None,
                    "ND-only old episode must not seed reset damage")
    finally:
        mod.now_ms = original_now_ms


def test_pending_handoff_survives_repeated_opposite_resets(mod):
    tracker, clock = tracker_with_clock(mod)
    original_now_ms = mod.now_ms
    mod.now_ms = clock.now_ms
    try:
        origin = tracker.update(mdie(-0.90), anchor(58.20))
        origin_id = origin["event_context"]["episode_id"]
        origin_start_ms = origin["event_context"]["event_start_ms"]
        clock.advance_min()
        tracker.update(mdie(0.90), anchor(60.10))
        clock.advance_min()
        first_reset = tracker.update(mdie(0.90), anchor(60.20))
        assert_true(
            "ANCHOR_DAMAGE_OBSERVED_OPPOSITE_RESET_PENDING_HANDOFF"
            in evidence(first_reset),
            "first opposite reset should preserve pending handoff")

        clock.advance_min()
        tracker.update(mdie(-0.90), anchor(60.30))
        clock.advance_min()
        second_reset = tracker.update(mdie(-0.90), anchor(60.40))
        assert_true(
            "ANCHOR_DAMAGE_OBSERVED_OPPOSITE_RESET_PENDING_HANDOFF"
            in evidence(second_reset),
            "repeated opposite reset should preserve handoff provenance")
        seed = second_reset["anchor_context"]["anchor_damage_seed"]
        assert_true(seed.get("origin_episode_id") == origin_id,
                    "repeated reset must preserve origin episode id")
        assert_true(seed.get("origin_event_start_ms") == origin_start_ms,
                    "repeated reset must preserve origin start time")
        assert_true(seed.get("origin_min_anchor_score") == 58.20,
                    "repeated reset must preserve origin minimum Anchor")

        clock.advance_min()
        out = tracker.update(mdie(-0.50), anchor(60.50))
        assert_true(out["state"] == "NR_REPAIR_CANDIDATE",
                    "first repaired tick after repeated resets is candidate")
        clock.advance_min()
        out = tracker.update(mdie(-0.50), anchor(60.60))
        assert_true(out["state"] == "NR_REPAIR_CONFIRMED",
                    "pending handoff must survive resets and confirm")
    finally:
        mod.now_ms = original_now_ms


def test_pending_handoff_uses_original_context_ttl_not_five_min_gap(mod):
    tracker, clock = tracker_with_clock(mod)
    original_now_ms = mod.now_ms
    mod.now_ms = clock.now_ms
    try:
        tracker.update(mdie(-0.90), anchor(58.20))
        clock.advance_min(9)
        tracker.update(mdie(0.90), anchor(60.10))
        clock.advance_min()
        reset = tracker.update(mdie(0.90), anchor(60.20))
        assert_true(
            "ANCHOR_DAMAGE_OBSERVED_OPPOSITE_RESET_PENDING_HANDOFF"
            in evidence(reset),
            "repaired pending handoff should remain valid within origin ttl")
        clock.advance_min()
        out = tracker.update(mdie(0.50), anchor(60.30))
        assert_true(out["state"] == "NR_REPAIR_CANDIDATE",
                    "first post-reset repair tick should be candidate")
        clock.advance_min()
        out = tracker.update(mdie(0.50), anchor(60.40))
        assert_true(out["state"] == "NR_REPAIR_CONFIRMED",
                    "pending handoff within origin ttl should confirm")
    finally:
        mod.now_ms = original_now_ms


def test_pending_handoff_cannot_extend_original_context_ttl(mod):
    tracker, clock = tracker_with_clock(
        mod, nr_repair_context_ttl_min=10)
    original_now_ms = mod.now_ms
    mod.now_ms = clock.now_ms
    try:
        tracker.update(mdie(-0.90), anchor(58.20))
        clock.advance_min(8)
        tracker.update(mdie(0.90), anchor(60.10))
        clock.advance_min()
        reset = tracker.update(mdie(0.90), anchor(60.20))
        assert_true(
            "ANCHOR_DAMAGE_OBSERVED_OPPOSITE_RESET_PENDING_HANDOFF"
            in evidence(reset),
            "setup should carry pending handoff before origin ttl")
        clock.advance_min(2)
        out = tracker.update(mdie(0.50), anchor(60.30))
        assert_true(out["state"] == "NR_REPAIR_STALE",
                    "opposite reset must not extend original handoff ttl")
        assert_true(tracker.context is None,
                    "origin-expired pending handoff must be cleared")
    finally:
        mod.now_ms = original_now_ms


def test_expired_context_freezes_on_invalid_tick_then_clears(mod):
    tracker, clock = tracker_with_clock(
        mod, nr_repair_context_ttl_min=3)
    original_now_ms = mod.now_ms
    mod.now_ms = clock.now_ms
    try:
        started = tracker.update(mdie(0.90), anchor(58.20))
        episode_id = started["event_context"]["episode_id"]
        clock.advance_min(4)
        invalid = tracker.update(
            mdie(0.20, data_state="BAD"), anchor(61.0))
        assert_true(invalid["state"] == "NR_DATA_INSUFFICIENT",
                    "invalid tick should freeze before stale evaluation")
        assert_true((invalid["event_context"] or {}).get("episode_id")
                    == episode_id,
                    "invalid tick should retain expired context for audit")
        clock.advance_min()
        stale = tracker.update(mdie(0.20), anchor(61.0))
        assert_true(stale["state"] == "NR_REPAIR_STALE",
                    "next valid tick should terminally clear expired context")
        assert_true(tracker.context is None,
                    "valid stale evaluation must clear the frozen context")
    finally:
        mod.now_ms = original_now_ms


def main():
    mod = load_signal_module()
    test_nd_and_deviation_only_do_not_confirm(mod)
    test_unconfirmed_stale_non_active_emits_once_then_idle(mod)
    test_expired_active_tick_starts_new_episode(mod)
    test_transient_mdie_bad_preserves_handoff(mod)
    test_transient_anchor_invalid_preserves_handoff(mod)
    test_active_tick_interrupts_candidate_count(mod)
    test_same_direction_gap_does_not_carry_old_handoff(mod)
    test_nd_only_old_episode_does_not_carry(mod)
    test_pending_handoff_survives_repeated_opposite_resets(mod)
    test_pending_handoff_uses_original_context_ttl_not_five_min_gap(mod)
    test_pending_handoff_cannot_extend_original_context_ttl(mod)
    test_expired_context_freezes_on_invalid_tick_then_clears(mod)
    print("neutral_repair_signal_loss_regressions: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("neutral_repair_signal_loss_regressions: FAIL - " + str(exc))
        sys.exit(1)
