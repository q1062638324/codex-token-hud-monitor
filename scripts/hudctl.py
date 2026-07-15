"""Codex Token HUD 的本地采集服务与状态存储。"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import queue
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable


HOST = "127.0.0.1"
PORT = 38427
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "CodexTokenHUD"
STATE_PATH = DATA_ROOT / "state.json"
STATE_LOCK = threading.RLock()
PLAN_USAGE_REFRESH_SECONDS = 60
PLAN_USAGE_TIMEOUT_SECONDS = 8

METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    "input_tokens": ("input_tokens", "inputTokens", "prompt_tokens", "promptTokens"),
    "cached_input_tokens": (
        "cached_input_tokens",
        "cachedInputTokens",
        "cache_read_input_tokens",
        "cacheReadInputTokens",
    ),
    "output_tokens": ("output_tokens", "outputTokens", "completion_tokens", "completionTokens"),
    "reasoning_output_tokens": (
        "reasoning_output_tokens",
        "reasoningOutputTokens",
        "reasoning_tokens",
    ),
    "cached_output_tokens": ("cached_output_tokens", "cachedOutputTokens", "cache_read_output_tokens"),
}


def now_local() -> dt.datetime:
    return dt.datetime.now().astimezone()


def empty_usage() -> dict[str, int | None]:
    return {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "cached_output_tokens": None,
    }


def as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        return max(int(value), 0)
    if isinstance(value, str):
        try:
            return max(int(value), 0)
        except ValueError:
            return None
    return None


def extract_usage(value: Any) -> dict[str, int | None] | None:
    """从 JSON、OTLP body 或属性字典中寻找 token usage。"""
    if not isinstance(value, dict):
        return None
    usage = empty_usage()
    found = False
    for metric, aliases in METRIC_ALIASES.items():
        for alias in aliases:
            if alias in value:
                parsed = as_int(value[alias])
                if parsed is not None:
                    usage[metric] = parsed
                    found = True
                    break
    if not found:
        return None
    return usage


def iter_json_objects(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_json_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_json_objects(child)
    elif isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            yield from iter_json_objects(json.loads(value))
        except json.JSONDecodeError:
            return


def find_first(value: Any, names: tuple[str, ...]) -> str | None:
    for obj in iter_json_objects(value):
        for name in names:
            item = obj.get(name)
            if isinstance(item, (str, int, float)):
                return str(item)
    return None


def summaries_from_payload(payload: Any) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for obj in iter_json_objects(payload):
        usage = extract_usage(obj)
        if usage is None:
            continue
        event_type = find_first(payload, ("type", "event_type", "eventType", "name")) or "usage"
        thread_id = find_first(payload, ("thread_id", "threadId", "conversation_id", "conversationId"))
        turn_id = find_first(payload, ("turn_id", "turnId"))
        model = find_first(payload, ("model", "model_slug", "modelSlug"))
        timestamp = find_first(payload, ("timestamp", "time_unix_nano", "timeUnixNano"))
        item = {
            "usage": usage,
            "event_type": event_type,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "model": model,
            "timestamp": timestamp,
        }
        fingerprint = hashlib.sha256(json.dumps(item, sort_keys=True).encode("utf-8")).hexdigest()
        if fingerprint not in seen:
            seen.add(fingerprint)
            results.append(item)
    return results


def read_varint(data: bytes, offset: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while offset < len(data):
        byte = data[offset]
        offset += 1
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result, offset
        shift += 7
        if shift > 70:
            raise ValueError("protobuf varint 超出长度")
    raise ValueError("protobuf varint 不完整")


def parse_proto(data: bytes) -> list[tuple[int, int, bytes | int]]:
    fields: list[tuple[int, int, bytes | int]] = []
    offset = 0
    while offset < len(data):
        tag, offset = read_varint(data, offset)
        field_number = tag >> 3
        wire_type = tag & 7
        if wire_type == 0:
            value, offset = read_varint(data, offset)
        elif wire_type == 1:
            value = data[offset : offset + 8]
            offset += 8
        elif wire_type == 2:
            length, offset = read_varint(data, offset)
            value = data[offset : offset + length]
            offset += length
        elif wire_type == 5:
            value = data[offset : offset + 4]
            offset += 4
        else:
            raise ValueError(f"不支持的 protobuf wire type: {wire_type}")
        fields.append((field_number, wire_type, value))
    return fields


def any_value(data: bytes) -> Any:
    for field, wire, value in parse_proto(data):
        if wire == 2 and field == 1:
            return value.decode("utf-8", errors="replace")
        if wire == 0 and field == 2:
            return bool(value)
        if wire == 0 and field == 3:
            return value
        if wire == 1 and field == 4 and isinstance(value, bytes):
            return struct.unpack("<d", value)[0]
        if wire == 2 and field == 5 and isinstance(value, bytes):
            return [any_value(item) for item_field, item_wire, item in parse_proto(value) if item_field == 1 and item_wire == 2]
        if wire == 2 and field == 6 and isinstance(value, bytes):
            return key_values(value)
        if wire == 2 and field == 7 and isinstance(value, bytes):
            return value.hex()
    return None


def parse_key_value_message(data: bytes) -> tuple[str | None, Any]:
    key = None
    parsed_value = None
    for child_field, child_wire, child_value in parse_proto(data):
        if child_field == 1 and child_wire == 2 and isinstance(child_value, bytes):
            key = child_value.decode("utf-8", errors="replace")
        elif child_field == 2 and child_wire == 2 and isinstance(child_value, bytes):
            parsed_value = any_value(child_value)
    return key, parsed_value


def key_values(data: bytes) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field, wire, value in parse_proto(data):
        if field != 1 or wire != 2 or not isinstance(value, bytes):
            continue
        key, parsed_value = parse_key_value_message(value)
        if key:
            result[key] = parsed_value
    return result


def protobuf_log_records(data: bytes) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for field, wire, resource_logs in parse_proto(data):
        if field != 1 or wire != 2 or not isinstance(resource_logs, bytes):
            continue
        for scope_field, scope_wire, scope_logs in parse_proto(resource_logs):
            if scope_field != 2 or scope_wire != 2 or not isinstance(scope_logs, bytes):
                continue
            for record_field, record_wire, record in parse_proto(scope_logs):
                if record_field != 2 or record_wire != 2 or not isinstance(record, bytes):
                    continue
                attributes: dict[str, Any] = {}
                timestamp = None
                body: Any = None
                for item_field, item_wire, item_value in parse_proto(record):
                    if item_field == 1 and item_wire == 0:
                        timestamp = str(item_value)
                    elif item_field == 5 and item_wire == 2 and isinstance(item_value, bytes):
                        body = any_value(item_value)
                    elif item_field == 6 and item_wire == 2 and isinstance(item_value, bytes):
                        key, parsed_value = parse_key_value_message(item_value)
                        if key:
                            attributes[key] = parsed_value
                if isinstance(body, dict):
                    attributes.update(body)
                if timestamp:
                    attributes["time_unix_nano"] = timestamp
                records.append(attributes)
    return records


def merge_usage(target: dict[str, int | None], incoming: dict[str, int | None]) -> dict[str, int | None]:
    for key in target:
        if incoming.get(key) is None:
            continue
        target[key] = int(target.get(key) or 0) + int(incoming[key] or 0)
    return target


def display_usage(raw: dict[str, int | None]) -> dict[str, Any]:
    input_tokens = int(raw.get("input_tokens") or 0)
    cached_input = int(raw.get("cached_input_tokens") or 0)
    output_tokens = int(raw.get("output_tokens") or 0)
    cached_output = raw.get("cached_output_tokens")
    cached_output_value = int(cached_output) if cached_output is not None else None
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input,
        "uncached_input_tokens": max(input_tokens - cached_input, 0),
        "input_cache_hit_rate": round(cached_input / input_tokens, 4) if input_tokens else 0,
        "output_tokens": output_tokens,
        "cached_output_tokens": cached_output_value,
        "uncached_output_tokens": max(output_tokens - cached_output_value, 0) if cached_output_value is not None else output_tokens,
        "output_cache_available": cached_output_value is not None,
        "reasoning_output_tokens": int(raw.get("reasoning_output_tokens") or 0),
    }


def empty_plan_usage(message: str = "等待 Codex 套餐数据") -> dict[str, Any]:
    """返回不带账号信息的套餐额度占位状态。"""
    return {
        "available": False,
        "plan_type": None,
        "limit_id": None,
        "limit_name": None,
        "primary": None,
        "secondary": None,
        "credits": None,
        "rate_limit_reached_type": None,
        "updated_at": None,
        "stale": False,
        "message": message,
        "source": None,
    }


def normalize_rate_limit_window(value: Any) -> dict[str, int | None] | None:
    """把 app-server 的 rate limit 窗口转换成 HUD 使用的稳定字段。"""
    if not isinstance(value, dict):
        return None
    used = as_int(value.get("usedPercent", value.get("used_percent")))
    if used is None:
        return None
    used = min(max(used, 0), 100)
    duration = as_int(value.get("windowDurationMins", value.get("window_duration_mins")))
    reset_at = as_int(value.get("resetsAt", value.get("resets_at")))
    return {
        "used_percent": used,
        "remaining_percent": 100 - used,
        "window_minutes": duration,
        "resets_at": reset_at,
    }


def normalize_rate_limits(value: Any) -> dict[str, Any] | None:
    """解析 Codex app-server 的 account/rateLimits/read 返回值。"""
    if not isinstance(value, dict):
        return None
    buckets = value.get("rateLimitsByLimitId")
    snapshot = buckets.get("codex") if isinstance(buckets, dict) else None
    if not isinstance(snapshot, dict):
        snapshot = value.get("rateLimits")
    if not isinstance(snapshot, dict):
        return None

    credits_value = snapshot.get("credits")
    credits = None
    if isinstance(credits_value, dict):
        balance = credits_value.get("balance")
        if not isinstance(balance, (str, int, float)) or isinstance(balance, bool):
            balance = None
        credits = {
            "has_credits": bool(credits_value.get("hasCredits", credits_value.get("has_credits", False))),
            "unlimited": bool(credits_value.get("unlimited", False)),
            "balance": str(balance) if balance is not None else None,
        }

    plan_type = snapshot.get("planType", snapshot.get("plan_type"))
    if not isinstance(plan_type, str) or not plan_type.strip():
        plan_type = None
    limit_id = snapshot.get("limitId", snapshot.get("limit_id"))
    if not isinstance(limit_id, str) or not limit_id.strip():
        limit_id = None
    limit_name = snapshot.get("limitName", snapshot.get("limit_name"))
    if not isinstance(limit_name, str) or not limit_name.strip():
        limit_name = None
    return {
        "available": True,
        "plan_type": plan_type,
        "limit_id": limit_id,
        "limit_name": limit_name,
        "primary": normalize_rate_limit_window(snapshot.get("primary")),
        "secondary": normalize_rate_limit_window(snapshot.get("secondary")),
        "credits": credits,
        "rate_limit_reached_type": snapshot.get("rateLimitReachedType", snapshot.get("rate_limit_reached_type")),
        "updated_at": None,
        "stale": False,
        "message": "已读取 Codex 套餐用量",
        "source": "codex-app-server",
    }


def find_codex_cli() -> str | None:
    """定位 Codex CLI，优先使用用户显式指定或用户目录安装的版本。"""
    configured = os.environ.get("CODEX_CLI_PATH")
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path)
    install_root = Path.home() / "AppData" / "Local" / "OpenAI" / "Codex" / "bin"
    candidates = list(install_root.glob("*/codex.exe")) if install_root.exists() else []
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    if candidates:
        return str(candidates[0])
    return shutil.which("codex") or shutil.which("codex.exe")


def fetch_rate_limits() -> dict[str, Any] | None:
    """通过 Codex 自带 app-server 读取额度，不接触本地认证文件。"""
    executable = find_codex_cli()
    if executable is None:
        return None
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            [executable, "app-server", "--listen", "stdio://"],
            cwd=str(PLUGIN_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creation_flags,
        )
    except (OSError, ValueError):
        return None

    lines: queue.Queue[str | None] = queue.Queue()

    def read_stdout() -> None:
        if process.stdout is None:
            lines.put(None)
            return
        try:
            for line in process.stdout:
                lines.put(line)
        finally:
            lines.put(None)

    reader = threading.Thread(target=read_stdout, name="codex-app-server-reader", daemon=True)
    reader.start()

    def send(message: dict[str, Any]) -> None:
        if process.stdin is None:
            raise OSError("app-server stdin 不可用")
        process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        process.stdin.flush()

    try:
        send(
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "codex-token-hud", "version": "0.1.5"},
                    "capabilities": {"experimentalApi": True},
                },
            }
        )
        deadline = time.monotonic() + PLAN_USAGE_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            remaining = max(deadline - time.monotonic(), 0.05)
            try:
                line = lines.get(timeout=remaining)
            except queue.Empty:
                return None
            if line is None:
                return None
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue
            if message.get("id") == 1:
                if message.get("error") is not None:
                    return None
                send({"method": "initialized", "params": {}})
                send({"id": 2, "method": "account/rateLimits/read", "params": None})
            elif message.get("id") == 2:
                if message.get("error") is not None:
                    return None
                return normalize_rate_limits(message.get("result"))
        return None
    except (OSError, ValueError, BrokenPipeError):
        return None
    finally:
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=0.8)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass


def refresh_plan_usage() -> None:
    """刷新套餐状态，失败时保留上次成功值并标记为过期。"""
    snapshot = fetch_rate_limits()
    stamp = now_local().isoformat(timespec="seconds")
    with STATE_LOCK:
        state = load_state()
        previous = state.get("plan_usage")
        if snapshot is not None:
            snapshot["updated_at"] = stamp
            state["plan_usage"] = snapshot
        elif isinstance(previous, dict) and previous.get("available"):
            previous["stale"] = True
            previous["message"] = "套餐数据暂时无法刷新"
            state["plan_usage"] = previous
        else:
            state["plan_usage"] = empty_plan_usage("等待 Codex 套餐数据")
        save_state(state)


def plan_usage_watcher() -> None:
    refresh_plan_usage()
    while True:
        time.sleep(PLAN_USAGE_REFRESH_SECONDS)
        try:
            refresh_plan_usage()
        except Exception:
            pass


def base_state() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": None,
        "current": None,
        "today": display_usage(empty_usage()),
        "week": display_usage(empty_usage()),
        "plan_usage": empty_plan_usage(),
        "tracked": {"today": {}, "week": {}},
        "source": None,
        "message": "等待 Codex usage 数据",
        "seen": [],
    }


def load_state() -> dict[str, Any]:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    if not STATE_PATH.exists():
        return base_state()
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else base_state()
    except (OSError, json.JSONDecodeError):
        return base_state()


def save_state(state: dict[str, Any]) -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    temp_path = STATE_PATH.with_suffix(".tmp")
    temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(STATE_PATH)


def ingest_record(
    record: dict[str, Any],
    source: str,
    received_at: dt.datetime | None = None,
    update_current: bool = True,
) -> None:
    usage = record.get("usage")
    if not isinstance(usage, dict):
        return
    stamp = received_at.astimezone() if received_at is not None else now_local()
    day_key = stamp.date().isoformat()
    iso = stamp.isocalendar()
    week_key = f"{iso.year}-W{iso.week:02d}"
    event_key = hashlib.sha256(json.dumps({"source": source, **record}, sort_keys=True).encode("utf-8")).hexdigest()
    with STATE_LOCK:
        state = load_state()
        seen = state.setdefault("seen", [])
        if event_key in seen:
            return
        seen.append(event_key)
        state["seen"] = seen[-500:]
        tracked = state.setdefault("tracked", {"today": {}, "week": {}})
        today_raw = tracked.setdefault("today", {}).setdefault(day_key, empty_usage())
        week_raw = tracked.setdefault("week", {}).setdefault(week_key, empty_usage())
        merge_usage(today_raw, usage)
        merge_usage(week_raw, usage)
        state["today"] = display_usage(today_raw)
        state["week"] = display_usage(week_raw)
        if update_current:
            state["current"] = {
                **display_usage(usage),
                "event_type": record.get("event_type"),
                "thread_id": record.get("thread_id"),
                "turn_id": record.get("turn_id"),
                "model": record.get("model"),
                "received_at": stamp.isoformat(timespec="seconds"),
            }
        state["updated_at"] = stamp.isoformat(timespec="seconds")
        state["source"] = source
        state["message"] = "已收到 Codex usage 数据"
        save_state(state)


def ingest_payload(payload: Any, source: str) -> int:
    records = summaries_from_payload(payload)
    for record in records:
        ingest_record(record, source)
    return len(records)


CODEX_HOME = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
ROLLOUT_TRACKER_PATH = DATA_ROOT / "rollout-tracker.json"
ROLLOUT_ID_PATTERN = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.IGNORECASE)


def parse_event_time(value: Any) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def snapshot_usage(value: Any) -> dict[str, int | None] | None:
    if not isinstance(value, dict):
        return None
    return extract_usage(value)


def subtract_usage(current: dict[str, int | None], previous: dict[str, int | None] | None) -> dict[str, int | None]:
    result = empty_usage()
    for key in result:
        current_value = current.get(key)
        if current_value is None:
            result[key] = None
            continue
        previous_value = previous.get(key) if previous else None
        result[key] = max(int(current_value) - int(previous_value or 0), 0)
    return result


def has_usage(value: dict[str, int | None]) -> bool:
    return any(int(item or 0) > 0 for item in value.values())


def parse_rollout_token_count(line: str) -> tuple[dict[str, Any], dt.datetime | None] | None:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict) or event.get("type") != "event_msg":
        return None
    payload = event.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return None
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    return info, parse_event_time(event.get("timestamp"))


def normalize_rollout_path(value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path_text = value.strip()
    if path_text.startswith("\\\\?\\"):
        path_text = path_text[4:]
    path = Path(path_text)
    return path if path.exists() and path.is_file() else None


def discover_rollouts() -> dict[str, str | None]:
    paths: dict[str, str | None] = {}
    database = CODEX_HOME / "state_5.sqlite"
    if database.exists():
        try:
            with sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=1.0) as connection:
                rows = connection.execute("select rollout_path, model from threads where rollout_path is not null")
                for rollout_path, model in rows:
                    path = normalize_rollout_path(rollout_path)
                    if path:
                        paths[str(path)] = model if isinstance(model, str) else None
        except sqlite3.Error:
            pass
    cutoff = time.time() - 8 * 24 * 60 * 60
    return {path: model for path, model in paths.items() if Path(path).stat().st_mtime >= cutoff}


def load_rollout_tracker() -> dict[str, Any]:
    if not ROLLOUT_TRACKER_PATH.exists():
        return {"files": {}}
    try:
        payload = json.loads(ROLLOUT_TRACKER_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {"files": {}}
    except (OSError, json.JSONDecodeError):
        return {"files": {}}


def save_rollout_tracker(tracker: dict[str, Any]) -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    temporary = ROLLOUT_TRACKER_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(tracker, ensure_ascii=False), encoding="utf-8")
    temporary.replace(ROLLOUT_TRACKER_PATH)


def update_current_snapshot(
    usage: dict[str, int | None],
    thread_id: str | None,
    model: str | None,
    received_at: dt.datetime | None,
) -> None:
    stamp = received_at or now_local()
    with STATE_LOCK:
        state = load_state()
        previous = state.get("current") or {}
        previous_time = parse_event_time(previous.get("received_at"))
        if previous_time and stamp < previous_time:
            return
        state["current"] = {
            **display_usage(usage),
            "event_type": "token_count",
            "thread_id": thread_id,
            "turn_id": None,
            "model": model,
            "received_at": stamp.isoformat(timespec="seconds"),
        }
        state["updated_at"] = stamp.isoformat(timespec="seconds")
        state["source"] = "codex-rollout"
        state["message"] = "已读取 Codex 桌面端 session usage"
        save_state(state)


def scan_rollouts_once() -> None:
    tracker = load_rollout_tracker()
    files = tracker.setdefault("files", {})
    latest: tuple[dt.datetime, dict[str, int | None], str | None, str | None] | None = None
    for path_text, model in discover_rollouts().items():
        path = Path(path_text)
        try:
            file_size = path.stat().st_size
        except OSError:
            continue
        entry = files.setdefault(path_text, {})
        offset = int(entry.get("offset", 0))
        previous_total = entry.get("total_usage")
        if not isinstance(previous_total, dict) or offset > file_size:
            offset = 0
            previous_total = None
        thread_match = ROLLOUT_ID_PATTERN.search(path.name)
        thread_id = thread_match.group(1) if thread_match else None
        last_usage = snapshot_usage(entry.get("last_usage"))
        last_timestamp = parse_event_time(entry.get("last_timestamp"))
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                handle.seek(offset)
                for line in handle:
                    parsed = parse_rollout_token_count(line)
                    if parsed is None:
                        continue
                    info, event_time = parsed
                    total_usage = snapshot_usage(info.get("total_token_usage"))
                    current_usage = snapshot_usage(info.get("last_token_usage"))
                    if total_usage is not None:
                        delta = subtract_usage(total_usage, previous_total)
                        if previous_total is None:
                            delta = total_usage
                        if has_usage(delta):
                            ingest_record(
                                {
                                    "usage": delta,
                                    "event_type": "token_count",
                                    "thread_id": thread_id,
                                    "turn_id": None,
                                    "model": model,
                                    "timestamp": event_time.isoformat() if event_time else None,
                                },
                                "codex-rollout",
                                received_at=event_time,
                                update_current=False,
                            )
                        previous_total = total_usage
                    if current_usage is not None:
                        last_usage = current_usage
                        if event_time and (last_timestamp is None or event_time >= last_timestamp):
                            last_timestamp = event_time
                        if event_time and (latest is None or event_time >= latest[0]):
                            latest = (event_time, current_usage, thread_id, model)
                offset = handle.tell()
        except OSError:
            continue
        entry["offset"] = offset
        entry["size"] = file_size
        entry["total_usage"] = previous_total
        entry["last_usage"] = last_usage
        entry["last_timestamp"] = last_timestamp.isoformat() if last_timestamp else None
    tracker["files"] = files
    save_rollout_tracker(tracker)
    if latest is not None:
        update_current_snapshot(latest[1], latest[2], latest[3], latest[0])


def rollout_watcher() -> None:
    while True:
        try:
            scan_rollouts_once()
        except Exception:
            pass
        time.sleep(1)


class HudHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_json(200, {"ok": True})
            return
        if self.path in ("/", "/v1/state"):
            with STATE_LOCK:
                self.send_json(200, load_state())
            return
        self.send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if not self.path.startswith("/v1/ingest"):
            self.send_json(404, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        source = self.headers.get("X-Codex-Hud-Source", "http")
        try:
            if self.headers.get("Content-Type", "").startswith("application/json") or body.lstrip().startswith((b"{", b"[")):
                count = ingest_payload(json.loads(body.decode("utf-8")), source)
            else:
                count = 0
                for record in protobuf_log_records(body):
                    count += ingest_payload(record, "otel-protobuf")
            self.send_json(200, {"ok": True, "records": count})
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            self.send_json(400, {"ok": False, "error": str(error)})


def serve() -> None:
    threading.Thread(target=rollout_watcher, name="codex-rollout-watcher", daemon=True).start()
    threading.Thread(target=plan_usage_watcher, name="codex-plan-usage-watcher", daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), HudHandler)
    print(f"Codex Token HUD collector listening on http://{HOST}:{PORT}")
    server.serve_forever()


def is_server_ready() -> bool:
    try:
        import urllib.request

        with urllib.request.urlopen(f"http://{HOST}:{PORT}/health", timeout=0.4) as response:
            return response.status == 200
    except Exception:
        return False


def start_collector() -> None:
    if is_server_ready():
        return
    creation_flags = 0
    if os.name == "nt":
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "serve"],
        cwd=str(PLUGIN_ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )
    for _ in range(20):
        if is_server_ready():
            return
        time.sleep(0.1)
    raise RuntimeError("本地采集服务启动超时")


def start_hud() -> str:
    candidates = [
        PLUGIN_ROOT / "app" / "src-tauri" / "target" / "release" / "codex-token-hud.exe",
        PLUGIN_ROOT / "app" / "target" / "release" / "codex-token-hud.exe",
    ]
    for executable in candidates:
        if executable.exists():
            subprocess.Popen([str(executable)], cwd=str(executable.parent))
            return str(executable)
    return "未找到已构建的 HUD，可先执行 cargo build --manifest-path app/src-tauri/Cargo.toml"


def command_ensure() -> None:
    start_collector()
    print(json.dumps({"collector": f"http://{HOST}:{PORT}", "hud": start_hud()}, ensure_ascii=False))


def command_ingest() -> None:
    count = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            count += ingest_payload(json.loads(line), "codex-jsonl")
        except json.JSONDecodeError:
            continue
    print(json.dumps({"records": count}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Codex Token HUD 本地采集器")
    parser.add_argument("command", choices=("serve", "ensure", "ingest"))
    args = parser.parse_args()
    if args.command == "serve":
        serve()
    elif args.command == "ensure":
        command_ensure()
    else:
        command_ingest()


if __name__ == "__main__":
    main()
