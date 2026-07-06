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
        "nrd_neutral_repair_opposite_flip", SIGNAL_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def mdie(value):
    return {
        "m_die": value,
        "data_status": {"data_state": "OK"},
    }


def anchor(score, nd=0.0, reasons=None):
    return {
        "state": "OK",
        "facts": {
            "anchor_gravity_ref_score": score,
            "normalized_deviation": nd,
        },
        "reasons": reasons or [],
    }


class Clock:
    def __init__(self, start_ms=1_782_599_500_000):
        self.value = start_ms

    def set(self, ms):
        self.value = ms

    def advance_min(self, minutes=1):
        self.value += int(minutes * 60_000)
        return self.value

    def now_ms(self):
        return self.value


def tracker_with_clock(mod):
    config = dict(mod.CONFIG)
    config["nr_opposite_confirm_ticks"] = 2
    config["nr_repair_confirm_ticks"] = 2
    return mod.NeutralRepairSignalTracker(config), Clock()


def run_opposite_flip_repair_sequence(mod):
    tracker, clock = tracker_with_clock(mod)
    original_now_ms = mod.now_ms
    mod.now_ms = clock.now_ms
    try:
        out = tracker.update(mdie(-0.90), anchor(54.0))
        assert_true(out["event_context"]["episode_direction"] == "DOWN",
                    "initial episode should be DOWN")
        clock.advance_min()
        out = tracker.update(mdie(-0.80), anchor(54.0))
        assert_true(out["gating"]["anchor_damage_ok"],
                    "initial DOWN context should observe Anchor damage")

        clock.advance_min()
        out = tracker.update(mdie(0.90), anchor(56.306))
        assert_true(out["state"] == "NR_OPPOSITE_EVENT_CONFLICT",
                    "first opposite tick should wait for confirmation")

        clock.advance_min()
        out = tracker.update(mdie(0.886), anchor(56.306))
        assert_true(out["event_context"]["episode_direction"] == "UP",
                    "second opposite tick should create a new UP episode")

        clock.advance_min()
        out = tracker.update(mdie(0.0), anchor(59.08))
        assert_true(out["state"] in (
            "NR_WAIT_ANCHOR_REPAIR", "NR_REPAIR_CANDIDATE",
            "NR_REPAIR_CONFIRMED",
        ), "new UP episode should carry damage and wait for repair")

        clock.advance_min()
        out = tracker.update(mdie(0.0), anchor(60.02))
        assert_true(out["state"] == "NR_REPAIR_CANDIDATE",
                    "first repaired tick should become a candidate")

        clock.advance_min()
        out = tracker.update(mdie(0.0), anchor(60.44))
        return out
    finally:
        mod.now_ms = original_now_ms


def test_opposite_flip_subrepair_anchor_can_confirm(mod):
    out = run_opposite_flip_repair_sequence(mod)
    assert_true(out["state"] == "NR_REPAIR_CONFIRMED",
                "opposite flip below repair score should confirm after repair")
    assert_true(out["is_active"], "confirmed repair should be active")
    assert_true(out["event_context"]["episode_direction"] == "UP",
                "confirmed episode should keep the new UP direction")
    evidence = out["anchor_context"]["anchor_damage_evidence"]
    assert_true("ANCHOR_DAMAGE_OBSERVED_OPPOSITE_RESET_SUBREPAIR" in evidence,
                "carried damage should be explicit in evidence")


def test_ordinary_die_subrepair_then_anchor_60_confirms(mod):
    tracker, clock = tracker_with_clock(mod)
    original_now_ms = mod.now_ms
    mod.now_ms = clock.now_ms
    try:
        out = tracker.update(mdie(0.90), anchor(58.20))
        assert_true(out["state"] == "NR_DISPLACEMENT_ACTIVE",
                    "DIE threshold should create an active episode")
        clock.advance_min()
        out = tracker.update(mdie(0.55), anchor(60.02))
        assert_true(out["state"] == "NR_REPAIR_CANDIDATE",
                    "first Anchor reclaim tick should become candidate")
        clock.advance_min()
        out = tracker.update(mdie(0.50), anchor(60.44))
        assert_true(out["state"] == "NR_REPAIR_CONFIRMED",
                    "second Anchor reclaim tick should confirm signal")
        assert_true(out["is_active"], "confirmed repair should be active")
        evidence = out["anchor_context"]["anchor_damage_evidence"]
        assert_true("ANCHOR_SUBREPAIR_OBSERVED_BELOW_60" in evidence,
                    "subrepair handoff evidence must be explicit")
    finally:
        mod.now_ms = original_now_ms


def test_anchor_60_reclaim_needs_two_confirm_ticks(mod):
    tracker, clock = tracker_with_clock(mod)
    original_now_ms = mod.now_ms
    mod.now_ms = clock.now_ms
    try:
        tracker.update(mdie(0.90), anchor(58.20))
        clock.advance_min()
        out = tracker.update(mdie(0.50), anchor(60.02))
        assert_true(out["state"] == "NR_REPAIR_CANDIDATE",
                    "first repaired tick is candidate only")
        assert_true(not out["is_active"],
                    "one repaired tick must not emit an active signal")
    finally:
        mod.now_ms = original_now_ms


def test_die_with_anchor_already_repaired_does_not_confirm(mod):
    tracker, clock = tracker_with_clock(mod)
    original_now_ms = mod.now_ms
    mod.now_ms = clock.now_ms
    try:
        tracker.update(mdie(0.90), anchor(60.50))
        clock.advance_min()
        tracker.update(mdie(0.50), anchor(60.70))
        clock.advance_min()
        out = tracker.update(mdie(0.50), anchor(60.80))
        assert_true(out["state"] != "NR_REPAIR_CONFIRMED",
                    "Anchor must actually return from subrepair")
    finally:
        mod.now_ms = original_now_ms


def test_anchor_above_60_with_nd_only_does_not_confirm(mod):
    tracker, clock = tracker_with_clock(mod)
    original_now_ms = mod.now_ms
    mod.now_ms = clock.now_ms
    try:
        tracker.update(mdie(0.90), anchor(60.50, nd=1.20))
        clock.advance_min()
        tracker.update(mdie(0.50), anchor(60.70, nd=0.40))
        clock.advance_min()
        out = tracker.update(mdie(0.50), anchor(60.80, nd=0.30))
        assert_true(out["state"] != "NR_REPAIR_CONFIRMED",
                    "ND-only disturbance is not an Anchor return-to-60 signal")
    finally:
        mod.now_ms = original_now_ms


def test_subrepair_reclaim_confirms_even_when_nd_remains_wide(mod):
    tracker, clock = tracker_with_clock(mod)
    original_now_ms = mod.now_ms
    mod.now_ms = clock.now_ms
    try:
        tracker.update(mdie(0.90), anchor(58.20, nd=1.20))
        clock.advance_min()
        out = tracker.update(mdie(0.50), anchor(60.20, nd=1.10))
        assert_true(out["state"] == "NR_REPAIR_CANDIDATE",
                    "first Anchor reclaim tick should become candidate")
        clock.advance_min()
        out = tracker.update(mdie(0.50), anchor(60.40, nd=1.05))
        assert_true(out["state"] == "NR_REPAIR_CONFIRMED",
                    "ND is audit evidence, not a hard repair blocker")
    finally:
        mod.now_ms = original_now_ms


def test_anchor_above_60_with_deviation_reason_only_does_not_confirm(mod):
    tracker, clock = tracker_with_clock(mod)
    original_now_ms = mod.now_ms
    mod.now_ms = clock.now_ms
    try:
        tracker.update(mdie(0.90), anchor(
            60.50, reasons=["ANCHOR_DEVIATION_WIDE"]))
        clock.advance_min()
        tracker.update(mdie(0.50), anchor(60.70))
        clock.advance_min()
        out = tracker.update(mdie(0.50), anchor(60.80))
        assert_true(out["state"] != "NR_REPAIR_CONFIRMED",
                    "wide-deviation-only evidence must not confirm")
    finally:
        mod.now_ms = original_now_ms


def test_opposite_reset_does_not_carry_nd_only_old_episode_damage(mod):
    tracker, clock = tracker_with_clock(mod)
    original_now_ms = mod.now_ms
    mod.now_ms = clock.now_ms
    try:
        tracker.update(mdie(-0.90), anchor(60.50, nd=1.20))
        clock.advance_min()
        out = tracker.update(mdie(0.90), anchor(56.306))
        assert_true(out["state"] == "NR_OPPOSITE_EVENT_CONFLICT",
                    "first opposite tick should wait for confirmation")
        clock.advance_min()
        out = tracker.update(mdie(0.886), anchor(56.306))
        evidence = out["anchor_context"]["anchor_damage_evidence"]
        assert_true("ANCHOR_DAMAGE_OBSERVED_OPPOSITE_RESET_SUBREPAIR"
                    not in evidence,
                    "ND-only old damage must not carry into new episode")
        assert_true("ANCHOR_SUBREPAIR_OBSERVED_BELOW_60" in evidence,
                    "new episode should keep its own subrepair evidence")
    finally:
        mod.now_ms = original_now_ms


def test_same_direction_gap_reset_subrepair_confirms_from_own_observation(mod):
    tracker, clock = tracker_with_clock(mod)
    original_now_ms = mod.now_ms
    mod.now_ms = clock.now_ms
    try:
        tracker.update(mdie(0.90), anchor(54.0))
        clock.advance_min()
        tracker.update(mdie(0.82), anchor(54.0))
        clock.advance_min(50)
        out = tracker.update(mdie(0.88), anchor(56.306))
        assert_true(out["event_context"]["episode_direction"] == "UP",
                    "same-direction gap reset should create an UP context")
        clock.advance_min()
        out = tracker.update(mdie(0.0), anchor(60.02))
        assert_true(out["state"] == "NR_REPAIR_CANDIDATE",
                    "first repaired tick should become a candidate")
        clock.advance_min()
        out = tracker.update(mdie(0.0), anchor(60.44))
        assert_true(out["state"] == "NR_REPAIR_CONFIRMED",
                    "same-direction reset can confirm from own subrepair")
        evidence = out["anchor_context"]["anchor_damage_evidence"]
        assert_true("ANCHOR_SUBREPAIR_OBSERVED_BELOW_60" in evidence,
                    "new episode subrepair evidence should be explicit")
    finally:
        mod.now_ms = original_now_ms


def test_opposite_flip_without_prior_damage_confirms_from_own_subrepair(mod):
    tracker, clock = tracker_with_clock(mod)
    original_now_ms = mod.now_ms
    mod.now_ms = clock.now_ms
    try:
        tracker.update(mdie(-0.90), anchor(60.50))
        clock.advance_min()
        out = tracker.update(mdie(0.90), anchor(56.306))
        assert_true(out["state"] == "NR_OPPOSITE_EVENT_CONFLICT",
                    "first opposite tick should wait for confirmation")
        clock.advance_min()
        out = tracker.update(mdie(0.886), anchor(56.306))
        assert_true(out["event_context"]["episode_direction"] == "UP",
                    "confirmed opposite tick should create an UP context")
        clock.advance_min()
        out = tracker.update(mdie(0.0), anchor(60.02))
        assert_true(out["state"] == "NR_REPAIR_CANDIDATE",
                    "own subrepair observation should create a candidate")
        clock.advance_min()
        out = tracker.update(mdie(0.0), anchor(60.44))
        assert_true(out["state"] == "NR_REPAIR_CONFIRMED",
                    "own subrepair observation should confirm")
        evidence = out["anchor_context"]["anchor_damage_evidence"]
        assert_true("ANCHOR_DAMAGE_OBSERVED_OPPOSITE_RESET_SUBREPAIR"
                    not in evidence,
                    "no prior damage means no carried damage evidence")
        assert_true("ANCHOR_SUBREPAIR_OBSERVED_BELOW_60" in evidence,
                    "new episode subrepair evidence should be explicit")
    finally:
        mod.now_ms = original_now_ms


def test_opposite_flip_carry_window_expired_can_confirm_from_own_subrepair(mod):
    tracker, clock = tracker_with_clock(mod)
    original_now_ms = mod.now_ms
    mod.now_ms = clock.now_ms
    try:
        tracker.update(mdie(-0.90), anchor(54.0))
        clock.advance_min()
        tracker.update(mdie(-0.82), anchor(54.0))
        clock.advance_min(6)
        tracker.update(mdie(0.90), anchor(56.306))
        clock.advance_min()
        out = tracker.update(mdie(0.886), anchor(56.306))
        evidence = out["anchor_context"]["anchor_damage_evidence"]
        assert_true("ANCHOR_DAMAGE_OBSERVED_OPPOSITE_RESET_SUBREPAIR"
                    not in evidence,
                    "expired carry window must not seed subrepair damage")
        clock.advance_min()
        out = tracker.update(mdie(0.0), anchor(60.02))
        assert_true(out["state"] == "NR_REPAIR_CANDIDATE",
                    "expired carry can still use own subrepair evidence")
        clock.advance_min()
        out = tracker.update(mdie(0.0), anchor(60.44))
        assert_true(out["state"] == "NR_REPAIR_CONFIRMED",
                    "own subrepair observation should confirm")
        evidence = out["anchor_context"]["anchor_damage_evidence"]
        assert_true("ANCHOR_DAMAGE_OBSERVED_OPPOSITE_RESET_SUBREPAIR"
                    not in evidence,
                    "expired carry window must remain absent")
    finally:
        mod.now_ms = original_now_ms


def test_opposite_flip_already_repaired_anchor_does_not_seed(mod):
    tracker, clock = tracker_with_clock(mod)
    original_now_ms = mod.now_ms
    mod.now_ms = clock.now_ms
    try:
        tracker.update(mdie(-0.90), anchor(54.0))
        clock.advance_min()
        tracker.update(mdie(-0.82), anchor(54.0))
        clock.advance_min()
        tracker.update(mdie(0.90), anchor(60.02))
        clock.advance_min()
        out = tracker.update(mdie(0.886), anchor(60.02))
        evidence = out["anchor_context"]["anchor_damage_evidence"]
        assert_true("ANCHOR_DAMAGE_OBSERVED_OPPOSITE_RESET_SUBREPAIR"
                    not in evidence,
                    "already repaired Anchor must not seed subrepair damage")
        clock.advance_min()
        out = tracker.update(mdie(0.0), anchor(60.44))
        assert_true(out["state"] == "NR_WAIT_ANCHOR_DAMAGE",
                    "already repaired reset should still wait for damage")
    finally:
        mod.now_ms = original_now_ms


def test_signal_event_tracker_records_carried_episode_once(mod):
    out = run_opposite_flip_repair_sequence(mod)
    event_tracker = mod.SignalEventTracker(dict(mod.CONFIG))
    factor_snapshot = {"neutral_repair_signal": out}
    first = event_tracker.maybe_record(
        out, factor_snapshot=factor_snapshot,
        runtime_facts={"current_price": 60150})
    second = event_tracker.maybe_record(
        out, factor_snapshot=factor_snapshot,
        runtime_facts={"current_price": 60155})
    assert_true(first is True, "confirmed carried episode should be recorded")
    assert_true(second is False, "same episode should not be recorded twice")


def main():
    mod = load_signal_module()
    test_opposite_flip_subrepair_anchor_can_confirm(mod)
    test_ordinary_die_subrepair_then_anchor_60_confirms(mod)
    test_anchor_60_reclaim_needs_two_confirm_ticks(mod)
    test_die_with_anchor_already_repaired_does_not_confirm(mod)
    test_anchor_above_60_with_nd_only_does_not_confirm(mod)
    test_subrepair_reclaim_confirms_even_when_nd_remains_wide(mod)
    test_anchor_above_60_with_deviation_reason_only_does_not_confirm(mod)
    test_opposite_reset_does_not_carry_nd_only_old_episode_damage(mod)
    test_same_direction_gap_reset_subrepair_confirms_from_own_observation(mod)
    test_opposite_flip_without_prior_damage_confirms_from_own_subrepair(mod)
    test_opposite_flip_carry_window_expired_can_confirm_from_own_subrepair(mod)
    test_opposite_flip_already_repaired_anchor_does_not_seed(mod)
    test_signal_event_tracker_records_carried_episode_once(mod)
    print("neutral_repair_opposite_flip_damage_carry: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("neutral_repair_opposite_flip_damage_carry: FAIL - " + str(exc))
        sys.exit(1)
