from __future__ import annotations

import csv
import time
from pathlib import Path

from .models import Candle


REQUIRED_COLUMNS = {"timestamp", "open", "high", "low", "close", "volume"}
CSV_READ_RETRIES = 3
CSV_READ_RETRY_SECONDS = 0.2


def load_candles_from_csv(path: str) -> list[Candle]:
    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")

    last_error: ValueError | None = None
    for attempt in range(CSV_READ_RETRIES + 1):
        try:
            return _load_candles_from_path(csv_path)
        except ValueError as exc:
            last_error = exc
            if attempt >= CSV_READ_RETRIES:
                break
            time.sleep(CSV_READ_RETRY_SECONDS)

    raise ValueError(f"CSV read failed after retries: {path}") from last_error


def _load_candles_from_path(csv_path: Path) -> list[Candle]:
    with csv_path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None:
            raise ValueError("CSV file is empty")
        missing_columns = REQUIRED_COLUMNS.difference(reader.fieldnames)
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise ValueError(f"CSV missing required columns: {missing}")

        candles = [_parse_row(row, index + 2) for index, row in enumerate(reader)]

    if not candles:
        raise ValueError("CSV does not contain candle rows")
    return candles


def _parse_row(row: dict[str, str | None], line_number: int) -> Candle:
    try:
        timestamp = _required_cell(row, "timestamp", line_number).strip()
        candle = Candle(
            timestamp=timestamp,
            open=float(_required_cell(row, "open", line_number)),
            high=float(_required_cell(row, "high", line_number)),
            low=float(_required_cell(row, "low", line_number)),
            close=float(_required_cell(row, "close", line_number)),
            volume=float(_required_cell(row, "volume", line_number)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid candle data at CSV line {line_number}") from exc

    if not candle.timestamp:
        raise ValueError(f"Missing timestamp at CSV line {line_number}")
    if candle.high < candle.low:
        raise ValueError(f"High is lower than low at CSV line {line_number}")
    if candle.open <= 0 or candle.high <= 0 or candle.low <= 0 or candle.close <= 0:
        raise ValueError(f"Price must be positive at CSV line {line_number}")
    if candle.volume < 0:
        raise ValueError(f"Volume cannot be negative at CSV line {line_number}")
    if candle.open > candle.high or candle.open < candle.low:
        raise ValueError(f"Open price is outside high/low range at CSV line {line_number}")
    if candle.close > candle.high or candle.close < candle.low:
        raise ValueError(f"Close price is outside high/low range at CSV line {line_number}")
    return candle


def _required_cell(row: dict[str, str | None], column: str, line_number: int) -> str:
    value = row[column]
    if value is None or not value.strip():
        raise ValueError(f"Missing {column} at CSV line {line_number}")
    return value
