from __future__ import annotations

from datetime import UTC, datetime


def parse_candle_timestamp(timestamp: str) -> datetime:
    normalized = timestamp.strip().replace(".", "-")
    return datetime.strptime(normalized, "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
