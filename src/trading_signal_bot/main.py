from __future__ import annotations

import logging
import os
import sys
from datetime import UTC, datetime

from .auto_trade import process_auto_trade
from .chart import render_signal_chart
from .config import load_auto_trade_config, load_env_file, load_signal_config, load_telegram_config
from .message import format_signal_message
from .models import Candle, SignalAction
from .multitimeframe import execution_candles, load_timeframe_candles
from .strategy import generate_signal
from .telegram import send_telegram_message, send_telegram_photo
from .time_utils import parse_candle_timestamp


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger(__name__)


def main() -> int:
    try:
        load_env_file()
        signal_config = load_signal_config()
        telegram_config = load_telegram_config()
        auto_trade_config = load_auto_trade_config()
        candles_by_timeframe = load_timeframe_candles(signal_config)
        candles = execution_candles(candles_by_timeframe, signal_config)
        _validate_all_candle_ages(candles_by_timeframe, signal_config.max_candle_age_minutes)
        signal = generate_signal(candles, signal_config, candles_by_timeframe)
        message = format_signal_message(signal)

        if signal.action == SignalAction.WAIT and not signal_config.send_wait:
            LOGGER.info("Signal is WAIT and SIGNAL_SEND_WAIT=false; message was not sent.")
            print(message)
            return 0

        if signal_config.dry_run:
            LOGGER.info("Dry-run enabled; Telegram message was not sent.")
            print(message)
            auto_trade_result = process_auto_trade(signal, auto_trade_config)
            LOGGER.info("Auto trade status: %s - %s", auto_trade_result.status, auto_trade_result.message)
            return 0

        send_telegram_message(telegram_config, message)
        if _get_bool_env("SIGNAL_SEND_CHART", True):
            chart_path = render_signal_chart(candles, signal, os.getenv("SIGNAL_CHART_PATH", "logs/latest_signal_chart.png"))
            send_telegram_photo(telegram_config, chart_path, _chart_caption(signal))
        auto_trade_result = process_auto_trade(signal, auto_trade_config)
        LOGGER.info("Auto trade status: %s - %s", auto_trade_result.status, auto_trade_result.message)
        LOGGER.info("Telegram signal sent for %s %s", signal.symbol, signal.action.value)
        return 0
    except Exception as exc:
        LOGGER.error("Signal bot failed: %s", exc)
        return 1


def _validate_latest_candle_age(timestamp: str, max_age_minutes: int) -> None:
    latest = parse_candle_timestamp(timestamp)
    age_minutes = (datetime.now(tz=UTC) - latest).total_seconds() / 60
    if age_minutes < 0:
        age_minutes = 0
    if age_minutes > max_age_minutes:
        raise ValueError(
            f"Latest candle is stale: {timestamp}. "
            f"Max allowed age is {max_age_minutes} minutes. Check MT5 symbol/timeframe/history."
        )


def _validate_all_candle_ages(candles_by_timeframe: dict[str, list[Candle]], max_age_minutes: int) -> None:
    for timeframe, candles in candles_by_timeframe.items():
        if not candles:
            raise ValueError(f"No candles for timeframe {timeframe}")
        if timeframe != "M5":
            continue
        _validate_latest_candle_age(candles[-1].timestamp, max_age_minutes)


def _get_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _chart_caption(signal: object) -> str:
    return "กราฟประกอบ signal ล่าสุด | ใช้เพื่อดู context เท่านั้น ไม่ใช่คำแนะนำทางการเงิน"


if __name__ == "__main__":
    sys.exit(main())
