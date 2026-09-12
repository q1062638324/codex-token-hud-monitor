import importlib.util
import json
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "hudctl.py"
SPEC = importlib.util.spec_from_file_location("hudctl_payload_scope", SCRIPT)
HUDCTL = importlib.util.module_from_spec(SPEC)
sys.modules["hudctl_payload_scope"] = HUDCTL
assert SPEC.loader is not None
SPEC.loader.exec_module(HUDCTL)


class PayloadScopeTests(unittest.TestCase):
    def test_batch_events_keep_their_own_metadata(self):
        records = HUDCTL.summaries_from_payload(
            {
                "events": [
                    {
                        "type": "turn.completed",
                        "thread_id": "thread-a",
                        "turn_id": "turn-a",
                        "model": "model-a",
                        "timestamp": "time-a",
                        "payload": {"usage": {"input_tokens": 10}},
                    },
                    {
                        "type": "turn.completed",
                        "thread_id": "thread-b",
                        "turn_id": "turn-b",
                        "model": "model-b",
                        "timestamp": "time-b",
                        "payload": {"usage": {"input_tokens": 20}},
                    },
                ]
            }
        )

        self.assertEqual(
            [
                (record["usage"]["input_tokens"], record["thread_id"], record["model"])
                for record in records
            ],
            [(10, "thread-a", "model-a"), (20, "thread-b", "model-b")],
        )

    def test_nested_usage_inherits_and_overrides_metadata_without_sibling_leakage(self):
        records = HUDCTL.summaries_from_payload(
            {
                "thread_id": "root-thread",
                "model": "root-model",
                "events": [
                    {
                        "type": "event-a",
                        "payload": {"nested": {"usage": {"input_tokens": 1}}},
                    },
                    {
                        "type": "event-b",
                        "thread_id": "child-thread",
                        "model": "child-model",
                        "payload": {"nested": {"usage": {"input_tokens": 2}}},
                    },
                ],
            }
        )

        self.assertEqual(
            [
                (
                    record["usage"]["input_tokens"],
                    record["event_type"],
                    record["thread_id"],
                    record["model"],
                )
                for record in records
            ],
            [
                (1, "event-a", "root-thread", "root-model"),
                (2, "event-b", "child-thread", "child-model"),
            ],
        )

    def test_string_json_payload_preserves_same_event_sibling_metadata(self):
        payload = json.dumps(
            [
                {
                    "type": "event-a",
                    "thread_id": "thread-a",
                    "usage": {"output_tokens": 3},
                },
                {
                    "type": "event-b",
                    "thread_id": "thread-b",
                    "usage": {"output_tokens": 4},
                },
            ]
        )

        records = HUDCTL.summaries_from_payload(payload)

        self.assertEqual(
            [(record["usage"]["output_tokens"], record["thread_id"]) for record in records],
            [(3, "thread-a"), (4, "thread-b")],
        )

    def test_missing_ids_do_not_merge_independent_same_usage_events(self):
        records = HUDCTL.summaries_from_payload(
            [
                {"type": "event-a", "usage": {"input_tokens": 5}},
                {"type": "event-b", "usage": {"input_tokens": 5}},
            ]
        )

        self.assertEqual(len(records), 2)
        self.assertEqual([record["event_type"] for record in records], ["event-a", "event-b"])

    def test_same_event_usage_copy_is_deduplicated_by_reliable_event_key(self):
        records = HUDCTL.summaries_from_payload(
            {
                "type": "turn.completed",
                "thread_id": "thread-a",
                "turn_id": "turn-a",
                "usage": {"input_tokens": 7},
                "payload": {"usage": {"input_tokens": 7}},
            }
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["usage"]["input_tokens"], 7)


if __name__ == "__main__":
    unittest.main()
