"""采集去重与增量读取回归测试，所有状态和日志均使用临时模拟文件。"""

import hashlib
import importlib.util
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "hudctl_reliability", Path(__file__).parents[1] / "scripts" / "hudctl.py"
)
HUD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HUD)


class IngestReliabilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hud-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.stamp = HUD.dt.datetime(2026, 9, 13, 12, tzinfo=HUD.dt.timezone.utc)
        overrides = patch.multiple(
            HUD, DATA_ROOT=self.root, STATE_PATH=self.root / "state.json",
            ROLLOUT_TRACKER_PATH=self.root / "tracker.json", now_local=lambda: self.stamp,
        )
        overrides.start()
        self.addCleanup(overrides.stop)
        self.record = {
            "thread_id": "test-thread", "turn_id": "test-turn", "model": "gpt-5.6-luna",
            "event_type": "turn.completed", "usage": HUD.extract_usage({"input_tokens": 100, "output_tokens": 20}),
            "timestamp": "2026-09-13T12:00:00Z",
        }

    def total(self):
        return HUD.load_state()["today"]["input_tokens"]

    def test_same_event_from_multiple_sources_is_counted_once_after_reload(self):
        HUD.ingest_record(self.record, "http")
        HUD.ingest_record({**self.record, "event_type": "token_count"}, "codex-rollout")
        self.assertEqual(self.total(), 100)
        state = HUD.load_state()
        self.assertEqual(state["week"]["input_tokens"], 100)
        self.assertEqual(state["month"]["input_tokens"], 100)
        self.assertEqual(state["tracked"]["today_models"]["2026-09-13"]["gpt-5.6-luna"]["input_tokens"], 100)

    def test_concurrent_replays_are_counted_once(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda source: HUD.ingest_record(self.record, source), ["http", "otel-protobuf"] * 4))
        self.assertEqual(self.total(), 100)

    def test_same_timestamp_in_iso_and_nanoseconds_deduplicates(self):
        HUD.ingest_record(self.record, "http")
        nano = str(int(self.stamp.timestamp()) * 1_000_000_000)
        HUD.ingest_record({**self.record, "timestamp": nano, "turn_id": None}, "otel-protobuf")
        self.assertEqual(self.total(), 100)

    def test_equal_usage_at_different_times_or_threads_is_preserved(self):
        HUD.ingest_record(self.record, "http")
        HUD.ingest_record({**self.record, "timestamp": "2026-09-13T12:00:01Z"}, "http")
        HUD.ingest_record({**self.record, "thread_id": "other-thread"}, "http")
        self.assertEqual(self.total(), 300)

    def test_nanosecond_precision_does_not_merge_distinct_events(self):
        first = {**self.record, "timestamp": "2026-09-13T12:00:00.123456700Z"}
        second = {**self.record, "timestamp": "2026-09-13T12:00:00.123456800Z"}
        HUD.ingest_record(first, "http")
        HUD.ingest_record(second, "http")
        nano = str(int(self.stamp.timestamp()) * 1_000_000_000 + 123456700)
        HUD.ingest_record({**first, "timestamp": nano}, "otel-protobuf")
        self.assertEqual(self.total(), 200)

    def test_timezone_equivalent_event_is_counted_once(self):
        HUD.ingest_record(self.record, "http")
        HUD.ingest_record({**self.record, "timestamp": "2026-09-13T20:00:00+08:00"}, "other-source")
        self.assertEqual(self.total(), 100)

    def test_anonymous_equal_usage_is_not_assumed_to_be_same_event(self):
        record = {"usage": self.record["usage"]}
        HUD.ingest_record(record, "http")
        HUD.ingest_record(record, "http")
        self.assertEqual(self.total(), 200)

    def test_batch_ingestion_preserves_anonymous_events_and_model_buckets(self):
        HUD.ingest_payload([
            {"type": "turn.completed", "usage": {"input_tokens": 10}},
            {"type": "turn.completed", "usage": {"input_tokens": 10}},
            {"type": "turn.completed", "thread_id": "a", "turn_id": "a",
             "model": "gpt-5.6-luna", "usage": {"input_tokens": 20}},
            {"type": "turn.completed", "thread_id": "b", "turn_id": "b",
             "model": "gpt-5.6-sol", "usage": {"input_tokens": 30}},
        ], "http")
        self.assertEqual(self.total(), 70)
        buckets = HUD.load_state()["tracked"]["today_models"]["2026-09-13"]
        self.assertEqual(buckets["__unknown__"]["input_tokens"], 20)
        self.assertEqual(buckets["gpt-5.6-luna"]["input_tokens"], 20)
        self.assertEqual(buckets["gpt-5.6-sol"]["input_tokens"], 30)

    def test_completed_turn_without_timestamp_deduplicates(self):
        record = {**self.record, "timestamp": None}
        HUD.ingest_record(record, "http")
        HUD.ingest_record(record, "codex-jsonl-wrapper")
        self.assertEqual(self.total(), 100)

    def test_nonfinal_events_in_same_turn_without_timestamp_remain_distinct(self):
        record = {**self.record, "timestamp": None, "event_type": "response.usage"}
        HUD.ingest_record(record, "http")
        HUD.ingest_record(record, "http")
        self.assertEqual(self.total(), 200)

    def test_legacy_fingerprint_is_respected_and_upgraded(self):
        HUD.ingest_record(self.record, "http")
        state = HUD.load_state()
        legacy = hashlib.sha256(json.dumps({"source": "http", **self.record}, sort_keys=True).encode()).hexdigest()
        state["seen"] = [legacy]
        HUD.save_state(state)
        HUD.ingest_record(self.record, "http")
        HUD.ingest_record(self.record, "other-source")
        self.assertEqual(self.total(), 100)

    def rollout_line(self, total, timestamp="2026-09-13T12:00:00Z"):
        return (json.dumps({
            "timestamp": timestamp, "type": "event_msg", "note": "模拟数据",
            "payload": {"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": total},
                "last_token_usage": {"input_tokens": total},
            }},
        }, ensure_ascii=False) + "\r\n").encode("utf-8")

    def scan(self, path):
        with patch.object(HUD, "discover_rollouts", return_value={str(path): "gpt-5.6-luna"}):
            HUD.scan_rollouts_once()

    def test_partial_utf8_line_is_retried_only_after_newline(self):
        path = self.root / "rollout-11111111-1111-1111-1111-111111111111.jsonl"
        first = self.rollout_line(100)
        second = self.rollout_line(150, "2026-09-13T12:00:01Z")
        split = second.index("模".encode("utf-8")) + 1
        path.write_bytes(first + second[:split])
        self.scan(path)
        self.assertEqual(self.total(), 100)
        self.assertEqual(HUD.load_rollout_tracker()["files"][str(path)]["offset"], len(first))
        with path.open("ab") as handle:
            handle.write(second[split:-1])
        self.scan(path)
        self.assertEqual(self.total(), 100)
        with path.open("ab") as handle:
            handle.write(second[-1:])
        self.scan(path)
        self.scan(path)
        self.assertEqual(self.total(), 150)
        self.assertEqual(HUD.load_rollout_tracker()["files"][str(path)]["offset"], len(first + second))

    def test_malformed_complete_line_does_not_block_following_event(self):
        path = self.root / "rollout.jsonl"
        path.write_bytes(b"broken json\n" + self.rollout_line(100))
        self.scan(path)
        self.assertEqual(self.total(), 100)

    def test_forwarded_rollout_event_and_watcher_share_identity(self):
        path = self.root / "rollout-11111111-1111-1111-1111-111111111111.jsonl"
        path.write_bytes(self.rollout_line(100))
        HUD.ingest_record({
            **self.record, "thread_id": "11111111-1111-1111-1111-111111111111",
            "usage": HUD.extract_usage({"input_tokens": 100}),
        }, "http")
        self.scan(path)
        self.assertEqual(self.total(), 100)


if __name__ == "__main__":
    unittest.main()
