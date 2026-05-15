from __future__ import annotations

import csv
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import load_env_file


LOGGER = logging.getLogger(__name__)

TIMEFRAMES = {
    "M1": "TIMEFRAME_M1",
    "M5": "TIMEFRAME_M5",
    "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1",
    "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_env_file()

    try:
        import MetaTrader5 as mt5  # type: ignore[import-not-found]
    except ImportError:
        LOGGER.error("MetaTrader5 package is not installed. This exporter must run on an MT5-capable environment.")
        return 1

    symbol = os.getenv("MT5_SYMBOL", os.getenv("SIGNAL_SYMBOL", "XAUUSD")).strip()
    timeframe_name = os.getenv("MT5_TIMEFRAME", os.getenv("SIGNAL_TIMEFRAME", "H1")).strip().upper()
    bars = _get_int_env("MT5_BARS", 300, 60)
    output_path = Path(os.getenv("MT5_OUTPUT_CSV", "data/mt5_ohlcv.csv").strip())

    timeframe_attr = TIMEFRAMES.get(timeframe_name)
    if timeframe_attr is None:
        LOGGER.error("Unsupported MT5_TIMEFRAME=%s. Supported: %s", timeframe_name, ", ".join(TIMEFRAMES))
        return 1

    if not mt5.initialize():
        LOGGER.error("MT5 initialize failed: %s", mt5.last_error())
        return 1

    try:
        timeframe = getattr(mt5, timeframe_attr)
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
        if rates is None or len(rates) == 0:
            LOGGER.error("No MT5 rates returned for %s %s: %s", symbol, timeframe_name, mt5.last_error())
            return 1
        _write_rates(output_path, rates)
        LOGGER.info("Exported %s bars to %s", len(rates), output_path)
        return 0
    finally:
        mt5.shutdown()


def _write_rates(path: Path, rates: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for rate in rates:
            timestamp = datetime.fromtimestamp(int(rate["time"]), tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
            writer.writerow([timestamp, rate["open"], rate["high"], rate["low"], rate["close"], rate["tick_volume"]])


def _get_int_env(name: str, default: int, minimum: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        LOGGER.error("%s must be an integer", name)
        raise
    if value < minimum:
        raise ValueError(f"{name} must be greater than or equal to {minimum}")
    return value


if __name__ == "__main__":
    sys.exit(main())
