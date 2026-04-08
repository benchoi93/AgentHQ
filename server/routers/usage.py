from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from server.auth import require_token
from server.config import CLAUDE_DATA_PATH
from server import store

log = logging.getLogger("agenthq-server")

router = APIRouter(prefix="/api/usage", tags=["usage"])

# ---------- Pricing per million tokens ----------

PRICING = {
    "opus": {
        "input": 15.0,
        "output": 75.0,
        "cache_creation": 18.75,
        "cache_read": 1.5,
    },
    "sonnet": {
        "input": 3.0,
        "output": 15.0,
        "cache_creation": 3.75,
        "cache_read": 0.3,
    },
    "haiku": {
        "input": 0.25,
        "output": 1.25,
        "cache_creation": 0.3,
        "cache_read": 0.03,
    },
}

PLAN_LIMITS = {
    "max20_monthly": {"label": "Max20 Monthly Overuse", "cost_limit": 200.0},
}

# ---------- In-memory cache ----------

_cache: dict[str, tuple[float, object]] = {}
_CACHE_TTL = 10  # seconds


def _cache_get(key: str):
    entry = _cache.get(key)
    if entry and (time.monotonic() - entry[0]) < _CACHE_TTL:
        return entry[1]
    return None


def _cache_set(key: str, value: object):
    _cache[key] = (time.monotonic(), value)


# ---------- Token / model / cost helpers ----------


def extract_tokens(data: dict) -> dict | None:
    """Extract token counts from a JSONL entry, handling all known variants."""
    sources: list[dict] = []
    msg = data.get("message", {})
    if isinstance(msg, dict) and "usage" in msg:
        sources.append(msg["usage"])
    if "usage" in data:
        sources.append(data["usage"])
    sources.append(data)

    for src in sources:
        if not isinstance(src, dict):
            continue
        input_t = src.get("input_tokens") or src.get("inputTokens") or 0
        output_t = src.get("output_tokens") or src.get("outputTokens") or 0
        if input_t > 0 or output_t > 0:
            return {
                "input_tokens": int(input_t),
                "output_tokens": int(output_t),
                "cache_creation_tokens": int(
                    src.get("cache_creation_input_tokens")
                    or src.get("cacheCreationInputTokens")
                    or 0
                ),
                "cache_read_tokens": int(
                    src.get("cache_read_input_tokens")
                    or src.get("cacheReadInputTokens")
                    or 0
                ),
            }
    return None


def extract_model(data: dict) -> str:
    """Extract model name from a JSONL entry."""
    msg = data.get("message", {})
    for candidate in [
        msg.get("model") if isinstance(msg, dict) else None,
        data.get("model"),
    ]:
        if candidate and isinstance(candidate, str):
            return candidate
    return "unknown"


def calculate_cost(model: str, tokens: dict) -> float:
    """Calculate USD cost for a set of token counts given the model."""
    model_lower = model.lower()
    if "opus" in model_lower:
        rates = PRICING["opus"]
    elif "haiku" in model_lower:
        rates = PRICING["haiku"]
    else:
        rates = PRICING["sonnet"]
    return (
        tokens["input_tokens"] / 1e6 * rates["input"]
        + tokens["output_tokens"] / 1e6 * rates["output"]
        + tokens["cache_creation_tokens"] / 1e6 * rates["cache_creation"]
        + tokens["cache_read_tokens"] / 1e6 * rates["cache_read"]
    )


# ---------- Timestamp parsing ----------


def _parse_timestamp(data: dict) -> datetime | None:
    """Parse timestamp from a JSONL entry, return UTC datetime or None."""
    ts_raw = data.get("timestamp")
    if ts_raw is None:
        return None
    if isinstance(ts_raw, (int, float)):
        return datetime.fromtimestamp(ts_raw, tz=timezone.utc)
    if isinstance(ts_raw, str):
        # Handle various ISO formats
        ts_str = ts_raw.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(ts_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None
    return None


# ---------- JSONL parsing ----------


def _iter_usage_entries(
    since: datetime | None = None,
) -> list[dict]:
    """
    Scan all JSONL files under CLAUDE_DATA_PATH and yield parsed entries
    with non-null token data and timestamps at or after `since`.
    """
    data_path = Path(CLAUDE_DATA_PATH).expanduser()
    if not data_path.exists():
        log.warning("CLAUDE_DATA_PATH does not exist: %s", data_path)
        return []

    entries: list[dict] = []
    for jsonl_file in data_path.rglob("*.jsonl"):
        try:
            with open(jsonl_file, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(data, dict):
                        continue

                    ts = _parse_timestamp(data)
                    if ts is None:
                        continue
                    if since and ts < since:
                        continue

                    tokens = extract_tokens(data)
                    if tokens is None:
                        continue

                    model = extract_model(data)
                    cost = (
                        data.get("costUSD")
                        or data.get("cost_usd")
                        or calculate_cost(model, tokens)
                    )

                    entries.append({
                        "timestamp": ts,
                        "model": model,
                        "tokens": tokens,
                        "cost_usd": float(cost),
                    })
        except Exception as exc:
            log.warning("Error reading %s: %s", jsonl_file, exc)

    entries.sort(key=lambda e: e["timestamp"])
    return entries


# ---------- Aggregation helpers ----------


def _empty_bucket() -> dict:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "cost_usd": 0.0,
        "message_count": 0,
    }


def _add_to_bucket(bucket: dict, entry: dict) -> None:
    t = entry["tokens"]
    bucket["input_tokens"] += t["input_tokens"]
    bucket["output_tokens"] += t["output_tokens"]
    bucket["cache_creation_tokens"] += t["cache_creation_tokens"]
    bucket["cache_read_tokens"] += t["cache_read_tokens"]
    bucket["cost_usd"] += entry["cost_usd"]
    bucket["message_count"] += 1


def _current_window_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Return (start, end) of the current 5-hour window in UTC."""
    hour = now.hour
    window_start_hour = (hour // 5) * 5
    window_start = now.replace(
        hour=window_start_hour, minute=0, second=0, microsecond=0
    )
    window_end = window_start.replace(hour=window_start_hour + 5)
    return window_start, window_end


# ---------- Merge local JSONL entries into bucket dicts ----------


def _entries_to_hourly_buckets(
    entries: list[dict],
) -> tuple[dict[str, dict], dict[str, dict[str, dict]]]:
    """Aggregate a list of parsed entries into hourly and hourly-by-model buckets."""
    hourly: dict[str, dict] = defaultdict(_empty_bucket)
    hourly_by_model: dict[str, dict[str, dict]] = defaultdict(
        lambda: defaultdict(_empty_bucket)
    )
    for entry in entries:
        ts = entry["timestamp"]
        hour_key = ts.replace(minute=0, second=0, microsecond=0).isoformat()
        _add_to_bucket(hourly[hour_key], entry)
        _add_to_bucket(hourly_by_model[hour_key][entry["model"]], entry)
    return hourly, hourly_by_model


def _merge_db_rows_into(
    hourly: dict[str, dict],
    hourly_by_model: dict[str, dict[str, dict]],
    db_rows: list[dict],
) -> None:
    """Merge DB usage_hourly rows into the running hourly/model dicts."""
    for row in db_rows:
        hk = row["hour"]
        if hk not in hourly:
            hourly[hk] = _empty_bucket()
        hourly[hk]["input_tokens"] += row["input_tokens"]
        hourly[hk]["output_tokens"] += row["output_tokens"]
        hourly[hk]["cache_creation_tokens"] += row["cache_creation_tokens"]
        hourly[hk]["cache_read_tokens"] += row["cache_read_tokens"]
        hourly[hk]["cost_usd"] += row["cost_usd"]
        hourly[hk]["message_count"] += row["message_count"]

        model = row["model"]
        if model not in hourly_by_model[hk]:
            hourly_by_model[hk][model] = _empty_bucket()
        hourly_by_model[hk][model]["input_tokens"] += row["input_tokens"]
        hourly_by_model[hk][model]["output_tokens"] += row["output_tokens"]
        hourly_by_model[hk][model]["cache_creation_tokens"] += row["cache_creation_tokens"]
        hourly_by_model[hk][model]["cache_read_tokens"] += row["cache_read_tokens"]
        hourly_by_model[hk][model]["cost_usd"] += row["cost_usd"]
        hourly_by_model[hk][model]["message_count"] += row["message_count"]


def _bucket_total_tokens(bucket: dict) -> int:
    return (
        bucket["input_tokens"]
        + bucket["output_tokens"]
        + bucket["cache_creation_tokens"]
        + bucket["cache_read_tokens"]
    )


def _bucket_to_output(bucket: dict) -> dict:
    return {
        "input_tokens": bucket["input_tokens"],
        "output_tokens": bucket["output_tokens"],
        "cache_creation_tokens": bucket["cache_creation_tokens"],
        "cache_read_tokens": bucket["cache_read_tokens"],
        "cost_usd": round(bucket["cost_usd"], 4),
        "message_count": bucket["message_count"],
    }


# ---------- Report model (agent -> server) ----------


class UsageHourlyRow(BaseModel):
    hour: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    message_count: int = 0


class UsageReportPayload(BaseModel):
    machine: str
    rows: list[UsageHourlyRow]


# ---------- Endpoints ----------


@router.post("/report")
async def usage_report(
    payload: UsageReportPayload,
    _token: str = Depends(require_token),
):
    """Receive hourly-aggregated usage data from an agent."""
    db_rows = [
        {
            "machine": payload.machine,
            "hour": r.hour,
            "model": r.model,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "cache_creation_tokens": r.cache_creation_tokens,
            "cache_read_tokens": r.cache_read_tokens,
            "cost_usd": r.cost_usd,
            "message_count": r.message_count,
        }
        for r in payload.rows
    ]
    count = await store.upsert_usage_hourly(db_rows)
    # Invalidate cache so next GET picks up new data
    _cache.clear()
    log.info("Usage report from %s: %d hourly rows", payload.machine, count)
    return {"ok": True, "rows_upserted": count}


@router.get("/current")
async def usage_current(
    machine: str | None = Query(default=None),
    _token: str = Depends(require_token),
):
    """Current 5-hour session window usage summary (all machines)."""
    cache_key = f"current_{machine or 'all'}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    now = datetime.now(timezone.utc)
    window_start, window_end = _current_window_bounds(now)

    # 1. DB rows from agents (preferred — avoids double-counting)
    db_rows = await store.query_usage_hourly(
        since=window_start.isoformat(),
        until=window_end.isoformat(),
        machine=machine,
    )

    # 2. Fall back to local JSONL only if NO agents have reported data
    entries: list[dict] = []
    if not db_rows:
        entries = _iter_usage_entries(since=window_start)
        entries = [e for e in entries if e["timestamp"] < window_end]

    # Build aggregates from whichever source we have
    totals = _empty_bucket()
    by_model: dict[str, dict] = defaultdict(_empty_bucket)
    by_machine: dict[str, dict] = defaultdict(_empty_bucket)

    # From local JSONL (only when no DB data)
    for entry in entries:
        _add_to_bucket(totals, entry)
        _add_to_bucket(by_model[entry["model"]], entry)
        _add_to_bucket(by_machine["local"], entry)

    # From DB rows
    for row in db_rows:
        for field in ("input_tokens", "output_tokens", "cache_creation_tokens",
                      "cache_read_tokens", "message_count"):
            totals[field] += row[field]
            by_model[row["model"]][field] += row[field]
            by_machine[row["machine"]][field] += row[field]
        totals["cost_usd"] += row["cost_usd"]
        by_model[row["model"]]["cost_usd"] += row["cost_usd"]
        by_machine[row["machine"]]["cost_usd"] += row["cost_usd"]

    total_tokens = _bucket_total_tokens(totals)
    elapsed_minutes = max((now - window_start).total_seconds() / 60, 1)
    burn_rate_tokens = total_tokens / elapsed_minutes
    burn_rate_cost = (totals["cost_usd"] / elapsed_minutes) * 60

    result = {
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "total_input_tokens": totals["input_tokens"],
        "total_output_tokens": totals["output_tokens"],
        "total_cache_creation_tokens": totals["cache_creation_tokens"],
        "total_cache_read_tokens": totals["cache_read_tokens"],
        "total_tokens": total_tokens,
        "total_cost_usd": round(totals["cost_usd"], 4),
        "message_count": totals["message_count"],
        "burn_rate_tokens_per_min": round(burn_rate_tokens, 1),
        "burn_rate_cost_per_hour": round(burn_rate_cost, 4),
        "by_model": {k: _bucket_to_output(v) for k, v in by_model.items()},
        "by_machine": {k: _bucket_to_output(v) for k, v in by_machine.items()},
        "plan_limits": PLAN_LIMITS,
    }

    _cache_set(cache_key, result)
    return result


@router.get("/history")
async def usage_history(
    hours: int = Query(default=48, ge=1, le=720),
    machine: str | None = Query(default=None),
    _token: str = Depends(require_token),
):
    """Hourly and daily usage breakdown (all machines combined)."""
    cache_key = f"history_{hours}_{machine or 'all'}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    now = datetime.now(timezone.utc)
    since = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=hours)

    # 1. DB rows from agents (preferred)
    db_rows = await store.query_usage_hourly(
        since=since.isoformat(), machine=machine,
    )

    # 2. Fall back to local JSONL only if no agents have reported
    hourly: dict[str, dict] = defaultdict(_empty_bucket)
    hourly_by_model: dict[str, dict[str, dict]] = defaultdict(
        lambda: defaultdict(_empty_bucket)
    )
    if not db_rows:
        entries = _iter_usage_entries(since=since)
        h, hm = _entries_to_hourly_buckets(entries)
        hourly.update(h)
        hourly_by_model.update(hm)
    else:
        _merge_db_rows_into(hourly, hourly_by_model, db_rows)

    # Build hourly output
    hours_out = []
    for hour_key in sorted(hourly.keys()):
        bucket = hourly[hour_key]
        model_data = {
            m: _bucket_to_output(mb) for m, mb in hourly_by_model[hour_key].items()
        }
        hours_out.append({
            "hour": hour_key,
            **_bucket_to_output(bucket),
            "total_tokens": _bucket_total_tokens(bucket),
            "by_model": model_data,
        })

    # Build daily output
    daily: dict[str, dict] = defaultdict(_empty_bucket)
    for hour_key, bucket in hourly.items():
        date_key = hour_key[:10]  # "YYYY-MM-DD"
        for field in ("input_tokens", "output_tokens", "cache_creation_tokens",
                      "cache_read_tokens", "message_count"):
            daily[date_key][field] += bucket[field]
        daily[date_key]["cost_usd"] += bucket["cost_usd"]

    daily_out = []
    for date_key in sorted(daily.keys()):
        bucket = daily[date_key]
        daily_out.append({
            "date": date_key,
            **_bucket_to_output(bucket),
            "total_tokens": _bucket_total_tokens(bucket),
        })

    result = {"hours": hours_out, "daily": daily_out}
    _cache_set(cache_key, result)
    return result
