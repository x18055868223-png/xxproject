import datetime as dt
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from astra_joint_events import OBSERVATION_CONTEXT_SCHEMA, StreamingReplay, compute_m_die, replay, stream_replay
from astra_joint_data import MINUTE_MS


BASE_MS = 1_609_459_200_000


def row(index, price):
    open_ms = BASE_MS + index * MINUTE_MS
    return row_at(open_ms, price)


def row_at(open_ms, price):
    close = float(price)
    return {
        "open_time_ms": open_ms,
        "open_time": open_ms,
        "open": close,
        "high": close + 0.3,
        "low": close - 0.3,
        "close": close,
        "volume": 10.0,
        "quote_volume": close * 10.0,
        "taker_buy_base_volume": 5.5,
        "close_time_ms": open_ms + MINUTE_MS - 1,
        "close_time": open_ms + MINUTE_MS - 1,
    }


def dummy_mdie(values_by_index):
    def _inner(klines, config=None, now_ms=None):
        index = int((klines[-1]["open_time_ms"] - BASE_MS) // MINUTE_MS)
        value = values_by_index.get(index, 0.0)
        return {
            "last_closed_bar_time": klines[-1]["close_time_ms"],
            "m_die": value,
            "score": abs(value),
            "direction": "UP" if value > 0 else "DOWN" if value < 0 else "NO_DIRECTION",
        }

    return _inner


def dummy_mdie_by_open(values_by_open):
    def _inner(klines, config=None, now_ms=None):
        value = values_by_open.get(int(klines[-1]["open_time_ms"]), 0.0)
        return {
            "last_closed_bar_time": klines[-1]["close_time_ms"],
            "m_die": value,
            "score": abs(value),
            "direction": "UP" if value > 0 else "DOWN" if value < 0 else "NO_DIRECTION",
        }

    return _inner


class JointEventTests(unittest.TestCase):
    def test_compute_m_die_matches_fmz_function_on_same_klines(self):
        fmz_path = Path(__file__).resolve().parents[1] / "demo" / "最新交付物" / "neutral_regulation_demo_fmz.py"
        spec = importlib.util.spec_from_file_location("fmz_strategy_for_joint_event_test", fmz_path)
        fmz = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fmz)

        prices = [100.0]
        for step in (0.10, 0.06, 0.11, 0.08, 0.07, 0.12, 0.09, 0.10, 0.06, 0.11, 0.08, 0.07, 0.12, 0.09, 0.10, 0.06):
            prices.append(prices[-1] * (1.0 + step / 100.0))
        klines = [row(i, price) for i, price in enumerate(prices)]
        ours = compute_m_die(klines)
        expected = fmz.compute_m_die(klines)
        self.assertEqual(ours["direction"], expected["direction"])
        self.assertEqual(ours["level"], expected["level"])
        self.assertEqual(ours["move_shape"], expected["move_shape"])
        self.assertAlmostEqual(ours["m_die"], expected["m_die"], places=12)
        self.assertAlmostEqual(ours["components"]["displacement"]["raw"]["window_log_return"], expected["components"]["displacement"]["raw"]["window_log_return"], places=12)

    def test_replay_emits_shock_cooldown_and_post_cooldown_checkpoints(self):
        rows = [row(i, 100 + i * 0.1) for i in range(50)]
        result = replay(rows, mdie_func=dummy_mdie({0: 0.8, 1: 0.0}))
        kinds = [item["observation_kind"] for item in result["observations"]]
        self.assertEqual(kinds[:4], ["shock", "cooldown", "plus15", "plus30"])
        self.assertLess(result["observations"][0]["as_of_ms"], result["observations"][1]["as_of_ms"])
        self.assertEqual(result["observations"][0]["entry_ms"], result["observations"][0]["as_of_ms"] + 1)
        self.assertIsNone(result["observations"][0]["entry_price"])
        self.assertIn("ret_15", result["observations"][0]["feature_names"])

    def test_gap_terminates_active_episode_and_resets_continuity(self):
        rows = [row(0, 100), row(1, 101), row(5, 102), row(6, 103)]
        result = replay(rows, mdie_func=dummy_mdie({0: 0.8, 5: 0.8}))
        event_types = [item["event_type"] for item in result["events"]]
        self.assertIn("terminated_by_gap", event_types)
        self.assertEqual(event_types.count("shock_started"), 2)
        gap = next(item for item in result["events"] if item["event_type"] == "terminated_by_gap")
        self.assertEqual(gap["as_of_ms"], rows[2]["close_time_ms"])
        self.assertEqual(gap["gap_after_open_ms"], rows[1]["open_time_ms"])

    def test_opposite_shock_requires_two_consecutive_closed_bars(self):
        rows = [row(i, 100 + i) for i in range(6)]
        result = replay(rows, mdie_func=dummy_mdie({0: 0.8, 1: -0.8, 3: -0.9, 4: -0.9}))
        event_types = [item["event_type"] for item in result["events"]]
        self.assertIn("terminated_by_opposite_confirmed", event_types)
        self.assertEqual(event_types.count("shock_started"), 2)
        shock_observations = [item for item in result["observations"] if item["observation_kind"] == "shock"]
        self.assertEqual(shock_observations[-1]["as_of_ms"], rows[4]["close_time_ms"])

    def test_fixed_checkpoint_emits_on_target_minute_even_when_strong(self):
        rows = [row(i, 100 + i * 0.1) for i in range(40)]
        result = replay(rows, mdie_func=dummy_mdie({0: 0.8, 1: 0.0, 16: 0.9, 31: 0.9}))
        by_kind = {item["observation_kind"]: item for item in result["observations"]}
        self.assertEqual(by_kind["plus15"]["as_of_ms"], rows[16]["close_time_ms"])
        self.assertEqual(by_kind["plus30"]["as_of_ms"], rows[31]["close_time_ms"])
        event_types = [item["event_type"] for item in result["events"]]
        self.assertIn("same_direction_shock_merged", event_types)

    def test_observation_context_records_reheat_and_rebalance_path(self):
        rows = [row(i, 100 + i * 0.05) for i in range(60)]
        result = replay(rows, mdie_func=dummy_mdie({20: 0.8, 21: 0.2, 23: 0.55, 36: 0.6, 51: 0.3}))
        by_kind = {item["observation_kind"]: item for item in result["observations"]}

        shock = by_kind["shock"]
        self.assertEqual(shock["event_context_schema"], OBSERVATION_CONTEXT_SCHEMA)
        self.assertEqual(shock["initial_abs_m_die"], 0.8)
        self.assertTrue(shock["event_context"]["boundaries"]["does_not_require_return_to_original_price"])
        self.assertTrue(shock["event_context"]["boundaries"]["not_consumed_by_natural_nr_model"])

        plus15 = by_kind["plus15"]
        context = plus15["event_context"]
        self.assertTrue(plus15["post_cooldown_reheated"])
        self.assertEqual(plus15["post_cooldown_reheat_as_of_ms"], rows[23]["close_time_ms"])
        self.assertAlmostEqual(plus15["post_cooldown_max_abs_m_die"], 0.6)
        self.assertEqual(context["cooldown"]["reheat_as_of_ms"], rows[23]["close_time_ms"])
        self.assertEqual(context["price_path"]["status"], "available")
        self.assertEqual(context["price_path"]["bar_count"], 17)
        self.assertIsNotNone(plus15["range_fraction_since_shock"])
        self.assertIsNotNone(plus15["range_expansion_since_shock"])
        self.assertIsNotNone(plus15["vwap_migration_since_shock"])

    def test_future_rows_do_not_change_prior_observation_context(self):
        rows = [row(i, 100 + i * 0.05) for i in range(60)]
        early = replay(rows[:37], mdie_func=dummy_mdie({20: 0.8, 21: 0.2, 36: 0.5}))
        future_rows = [dict(item) for item in rows]
        future_rows[45] = row(45, 150.0)
        full = replay(future_rows, mdie_func=dummy_mdie({20: 0.8, 21: 0.2, 36: 0.5, 45: 0.0}))
        early_plus15 = next(item for item in early["observations"] if item["observation_kind"] == "plus15")
        full_plus15 = next(item for item in full["observations"] if item["observation_kind"] == "plus15")
        self.assertEqual(json.dumps(early_plus15, sort_keys=True), json.dumps(full_plus15, sort_keys=True))

    def test_opposite_confirmed_and_gap_do_not_carry_context_between_episodes(self):
        opposite_rows = [row(i, 100 + i) for i in range(6)]
        opposite = replay(opposite_rows, mdie_func=dummy_mdie({0: 0.8, 1: -0.8, 2: -0.8}))
        new_shock = [item for item in opposite["observations"] if item["observation_kind"] == "shock"][-1]
        self.assertEqual(new_shock["shock_direction"], "DOWN")
        self.assertEqual(
            new_shock["event_context"]["origin"]["confirmed_from_opposite_first_as_of_ms"],
            opposite_rows[1]["close_time_ms"],
        )
        self.assertEqual(new_shock["opposite_streak_count"], 0)

        gap_rows = [row(0, 100), row(1, 101), row(5, 102), row(6, 103)]
        gap = replay(gap_rows, mdie_func=dummy_mdie({0: 0.8, 5: 0.8}))
        gap_shock = [item for item in gap["observations"] if item["observation_kind"] == "shock"][-1]
        self.assertEqual(gap_shock["shock_as_of_ms"], gap_rows[2]["close_time_ms"])
        self.assertIsNone(gap_shock["event_context"]["origin"]["confirmed_from_opposite_first_as_of_ms"])
        self.assertEqual(gap_shock["event_context"]["price_path"]["bar_count"], 1)

    def test_clock_rows_are_labeled_as_clock_reference_not_non_event(self):
        start = 1_609_491_480_000  # 2021-01-01 08:58:00 UTC
        clock_rows = []
        for index in range(5):
            item = row(index, 100 + index)
            open_ms = start + index * MINUTE_MS
            item.update(open_time_ms=open_ms, open_time=open_ms, close_time_ms=open_ms + MINUTE_MS - 1, close_time=open_ms + MINUTE_MS - 1)
            clock_rows.append(item)
        result = replay(clock_rows, mdie_func=dummy_mdie({}))
        self.assertEqual(len(result["clock_rows"]), 1)
        self.assertEqual(result["clock_rows"][0]["observation_kind"], "clock")
        self.assertEqual(result["clock_rows"][0]["event_family"], "clock_reference")
        self.assertFalse(result["clock_rows"][0]["in_episode"])
        self.assertEqual(result["clock_rows"][0]["abs_m_die"], 0.0)

    def test_as_of_upper_excludes_unclosed_future_bars(self):
        rows = [row(i, 100 + i) for i in range(4)]
        result = replay(rows, as_of_upper_ms=rows[1]["close_time_ms"], mdie_func=dummy_mdie({0: 0.8, 2: 0.8}))
        self.assertEqual(len([item for item in result["events"] if item["event_type"] == "shock_started"]), 1)

    def test_as_of_upper_requires_millisecond_after_bar_close(self):
        rows = [row(0, 100.0)]
        at_close = rows[0]["close_time_ms"]

        at_close_result = replay(rows, as_of_upper_ms=at_close, mdie_func=dummy_mdie({0: 0.8}))
        after_close_result = replay(rows, as_of_upper_ms=at_close + 1, mdie_func=dummy_mdie({0: 0.8}))

        self.assertEqual(at_close_result["events"], [])
        self.assertEqual(
            len([item for item in after_close_result["events"] if item["event_type"] == "shock_started"]),
            1,
        )

    def test_streaming_replay_matches_batch_across_month_boundary(self):
        start = int(dt.datetime(2021, 1, 31, 23, 50, tzinfo=dt.UTC).timestamp() * 1000)
        rows = [row_at(start + index * MINUTE_MS, 100 + index * 0.1) for index in range(50)]
        values = {
            rows[4]["open_time_ms"]: 0.8,
            rows[5]["open_time_ms"]: 0.0,
            rows[25]["open_time_ms"]: 0.0,
            rows[35]["open_time_ms"]: 0.0,
        }
        batch = replay(rows, mdie_func=dummy_mdie_by_open(values))
        stream_events = []
        stream_observations = []
        stream_clock = []
        runner = StreamingReplay(
            mdie_func=dummy_mdie_by_open(values),
            on_event=stream_events.append,
            on_observation=stream_observations.append,
            on_clock=stream_clock.append,
        )
        for item in rows[:10]:
            runner.push(item)
        for item in rows[10:]:
            runner.push(item)
        runner.finish()
        self.assertEqual(json.dumps(stream_events, sort_keys=True), json.dumps(batch["events"], sort_keys=True))
        self.assertEqual(json.dumps(stream_observations, sort_keys=True), json.dumps(batch["observations"], sort_keys=True))
        self.assertEqual(json.dumps(stream_clock, sort_keys=True), json.dumps(batch["clock_rows"], sort_keys=True))

    def test_streaming_snapshot_resume_matches_batch_with_context(self):
        rows = [row(i, 100 + i * 0.05) for i in range(60)]
        values = {20: 0.8, 21: 0.2, 23: 0.55, 36: 0.6, 51: 0.3}
        batch = replay(rows, mdie_func=dummy_mdie(values))
        stream_events = []
        stream_observations = []
        stream_clock = []
        runner = StreamingReplay(
            mdie_func=dummy_mdie(values),
            on_event=stream_events.append,
            on_observation=stream_observations.append,
            on_clock=stream_clock.append,
        )
        for item in rows[:30]:
            runner.push(item)
        snapshot = runner.to_snapshot()
        resumed = StreamingReplay.from_snapshot(
            snapshot,
            mdie_func=dummy_mdie(values),
            on_event=stream_events.append,
            on_observation=stream_observations.append,
            on_clock=stream_clock.append,
        )
        for item in rows[30:]:
            resumed.push(item)
        resumed.finish()

        self.assertEqual(json.dumps(stream_events, sort_keys=True), json.dumps(batch["events"], sort_keys=True))
        self.assertEqual(json.dumps(stream_observations, sort_keys=True), json.dumps(batch["observations"], sort_keys=True))
        self.assertEqual(json.dumps(stream_clock, sort_keys=True), json.dumps(batch["clock_rows"], sort_keys=True))

    def test_legacy_snapshot_missing_new_context_keeps_unknowns_and_marks_scope(self):
        rows = [row(i, 100 + i * 0.05) for i in range(60)]
        values = {20: 0.8, 21: 0.2, 31: 0.55, 36: 0.6}
        observations = []
        runner = StreamingReplay(mdie_func=dummy_mdie(values), on_observation=observations.append)
        for item in rows[:30]:
            runner.push(item)
        snapshot = runner.to_snapshot()
        active = snapshot["state"]["active"]
        for key in (
            "initial_m_die",
            "initial_abs_m_die",
            "max_abs_m_die_since_shock",
            "max_abs_m_die_as_of_ms",
            "m_die_observation_count",
            "post_cooldown_max_abs_m_die",
            "post_cooldown_max_as_of_ms",
            "post_cooldown_reheated",
            "post_cooldown_reheat_as_of_ms",
            "max_abs_m_die_in_context_scope",
            "max_abs_m_die_scope_as_of_ms",
            "post_cooldown_max_abs_m_die_in_context_scope",
            "post_cooldown_max_scope_as_of_ms",
            "post_cooldown_reheat_observed_in_context_scope",
            "post_cooldown_reheat_scope_as_of_ms",
            "m_die_context_coverage",
            "m_die_context_restored_from_legacy_snapshot",
            "m_die_context_missing_legacy_fields",
        ):
            active.pop(key, None)

        resumed_observations = []
        resumed = StreamingReplay.from_snapshot(
            snapshot,
            mdie_func=dummy_mdie(values),
            on_observation=resumed_observations.append,
        )
        for item in rows[30:38]:
            resumed.push(item)
        plus15 = next(item for item in resumed_observations if item["observation_kind"] == "plus15")
        context = plus15["event_context"]
        self.assertEqual(context["coverage"]["m_die_path_scope"], "after_snapshot_restore")
        self.assertTrue(context["coverage"]["restored_from_legacy_snapshot"])
        self.assertIn("post_cooldown_reheated", context["coverage"]["missing_legacy_fields"])
        self.assertEqual(context["coverage"]["post_cooldown_reheat_status"], "unknown_before_snapshot_restore")
        self.assertIsNone(plus15["max_abs_m_die_since_shock"])
        self.assertIsNone(plus15["post_cooldown_reheated"])
        self.assertIsNone(context["cooldown"]["reheated_after_cooldown"])
        self.assertTrue(context["cooldown"]["reheat_observed_in_context_scope"])
        self.assertEqual(context["cooldown"]["reheat_scope_as_of_ms"], rows[31]["close_time_ms"])
        self.assertAlmostEqual(context["shock"]["max_abs_m_die_observed_in_context_scope"], 0.6)
        self.assertIsNone(context["shock"]["max_abs_m_die_since_shock"])

    def test_episode_id_is_stable_for_same_shock_as_of(self):
        rows = [row(i, 100 + i * 0.1) for i in range(8)]
        first = replay(rows, mdie_func=dummy_mdie({2: 0.8}))
        second = replay(rows[1:], mdie_func=dummy_mdie({2: 0.8}))
        first_id = next(item["episode_id"] for item in first["events"] if item["event_type"] == "shock_started")
        second_id = next(item["episode_id"] for item in second["events"] if item["event_type"] == "shock_started")
        self.assertEqual(first_id, second_id)
        self.assertIn(str(rows[2]["close_time_ms"]), first_id)
        self.assertEqual(
            next(item["episode_sequence"] for item in first["events"] if item["event_type"] == "shock_started"),
            1,
        )

    def test_stream_replay_yields_records_without_persistent_output_lists(self):
        rows = [row(i, 100 + i * 0.01) for i in range(10)]
        yielded = list(stream_replay(rows, mdie_func=dummy_mdie({0: 0.8, 1: 0.0})))
        self.assertTrue(any(kind == "event" for kind, _ in yielded))
        self.assertTrue(any(kind == "observation" for kind, _ in yielded))

    def test_streaming_replay_keeps_bounded_history(self):
        runner = StreamingReplay(mdie_func=dummy_mdie({}))
        for index in range(2000):
            runner.push(row(index, 100 + index * 0.001))
        runner.finish()
        self.assertLessEqual(len(runner.bars), 1441)
        self.assertLessEqual(len(runner.rolling), 16)

    def test_cli_streams_inputs_and_writes_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csv_path = root / "sample.csv"
            out_dir = root / "out"
            with csv_path.open("w", encoding="utf-8", newline="") as stream:
                for index in range(20):
                    item = row(index, 100 + index * 0.01)
                    stream.write(
                        ",".join(
                            str(item[key])
                            for key in (
                                "open_time_ms",
                                "open",
                                "high",
                                "low",
                                "close",
                                "volume",
                                "close_time_ms",
                                "quote_volume",
                            )
                        )
                        + ",1,5,500,0\n"
                    )
            completed = subprocess.run(
                [sys.executable, "tools/astra_joint_events.py", "--input", str(csv_path), "--output-dir", str(out_dir)],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=True,
            )
            payload = json.loads(completed.stdout)
            manifest = json.loads((out_dir / "event_manifest.json").read_text(encoding="utf-8"))
            self.assertTrue((out_dir / "price_rebalance_events.jsonl").exists())
        self.assertEqual(payload["input_rows"], 20)
        self.assertEqual(manifest["input_rows"], 20)


if __name__ == "__main__":
    unittest.main()
