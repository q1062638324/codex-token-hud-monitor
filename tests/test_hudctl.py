import importlib.util
import json
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "hudctl.py"
SPEC = importlib.util.spec_from_file_location("hudctl", SCRIPT)
HUDCTL = importlib.util.module_from_spec(SPEC)
sys.modules["hudctl"] = HUDCTL
assert SPEC.loader is not None
SPEC.loader.exec_module(HUDCTL)


def varint(value):
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


def field(number, wire, value):
    tag = varint((number << 3) | wire)
    if wire == 0:
        return tag + varint(value)
    return tag + varint(len(value)) + value


def key_value(key, value):
    any_value = field(3, 0, value)
    return field(1, 2, key.encode()) + field(2, 2, any_value)


class HudCollectorTests(unittest.TestCase):
    def test_json_usage_includes_cache_miss(self):
        records = HUDCTL.summaries_from_payload(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 60,
                    "output_tokens": 20,
                    "cached_output_tokens": 5,
                },
            }
        )
        self.assertEqual(len(records), 1)
        shown = HUDCTL.display_usage(records[0]["usage"])
        self.assertEqual(shown["uncached_input_tokens"], 40)
        self.assertEqual(shown["uncached_output_tokens"], 15)
        self.assertTrue(shown["output_cache_available"])

    def test_json_without_output_cache_is_explicitly_unavailable(self):
        shown = HUDCTL.display_usage(
            {
                "input_tokens": 100,
                "cached_input_tokens": 60,
                "output_tokens": 20,
                "cached_output_tokens": None,
                "reasoning_output_tokens": 2,
            }
        )
        self.assertFalse(shown["output_cache_available"])
        self.assertEqual(shown["uncached_output_tokens"], 20)

    def test_otlp_protobuf_log_attributes(self):
        log_record = (
            field(6, 2, key_value("input_tokens", 100))
            + field(6, 2, key_value("cached_input_tokens", 75))
            + field(6, 2, key_value("output_tokens", 12))
        )
        scope_logs = field(2, 2, log_record)
        resource_logs = field(2, 2, scope_logs)
        payload = field(1, 2, resource_logs)
        records = HUDCTL.protobuf_log_records(payload)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["input_tokens"], 100)
        self.assertEqual(records[0]["cached_input_tokens"], 75)

    def test_rollout_token_count_event(self):
        event = json.dumps(
            {
                "timestamp": "2026-07-14T04:00:00.000Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 1000,
                            "cached_input_tokens": 800,
                            "output_tokens": 50,
                            "reasoning_output_tokens": 10,
                        }
                    },
                },
            }
        )
        parsed = HUDCTL.parse_rollout_token_count(event)
        self.assertIsNotNone(parsed)
        info, timestamp = parsed
        self.assertEqual(info["last_token_usage"]["cached_input_tokens"], 800)
        self.assertEqual(timestamp.isoformat(), "2026-07-14T12:00:00+08:00")

    def test_rate_limits_expose_remaining_percent_and_reset(self):
        plan = HUDCTL.normalize_rate_limits(
            {
                "rateLimits": {
                    "limitId": "codex",
                    "planType": "plus",
                    "primary": {
                        "usedPercent": 35,
                        "windowDurationMins": 10080,
                        "resetsAt": 1784680070,
                    },
                    "secondary": {
                        "usedPercent": 80,
                        "windowDurationMins": 1440,
                        "resetsAt": 1784000000,
                    },
                    "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
                }
            }
        )
        assert plan is not None
        self.assertTrue(plan["available"])
        self.assertEqual(plan["plan_type"], "plus")
        self.assertEqual(plan["primary"]["remaining_percent"], 65)
        self.assertEqual(plan["secondary"]["remaining_percent"], 20)
        self.assertEqual(plan["primary"]["window_minutes"], 10080)

    def test_rate_limits_support_multi_bucket_response(self):
        plan = HUDCTL.normalize_rate_limits(
            {
                "rateLimits": {"planType": "free"},
                "rateLimitsByLimitId": {
                    "codex": {
                        "planType": "pro",
                        "primary": {"usedPercent": 101, "windowDurationMins": 60},
                    }
                },
            }
        )
        assert plan is not None
        self.assertEqual(plan["plan_type"], "pro")
        self.assertEqual(plan["primary"]["remaining_percent"], 0)

    def test_period_views_reset_after_day_and_iso_week_rollover(self):
        timezone = HUDCTL.dt.timezone(HUDCTL.dt.timedelta(hours=8))
        state = {
            "tracked": {
                "today": {"2026-07-14": {"input_tokens": 123}},
                "week": {"2026-W28": {"input_tokens": 456}},
            }
        }
        today, week = HUDCTL.period_usage_views(
            state,
            HUDCTL.dt.datetime(2026, 7, 15, 8, 0, tzinfo=timezone),
        )
        self.assertEqual(today["input_tokens"], 0)
        self.assertEqual(week["input_tokens"], 0)

    def test_period_views_keep_current_day_and_week_data(self):
        timezone = HUDCTL.dt.timezone(HUDCTL.dt.timedelta(hours=8))
        state = {
            "tracked": {
                "today": {"2026-07-15": {"input_tokens": 123}},
                "week": {"2026-W29": {"input_tokens": 456}},
            }
        }
        today, week = HUDCTL.period_usage_views(
            state,
            HUDCTL.dt.datetime(2026, 7, 15, 8, 0, tzinfo=timezone),
        )
        self.assertEqual(today["input_tokens"], 123)
        self.assertEqual(week["input_tokens"], 456)


if __name__ == "__main__":
    unittest.main()
