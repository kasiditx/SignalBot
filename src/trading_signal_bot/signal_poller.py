from __future__ import annotations

import hashlib
import fcntl
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from .auto_trade import process_auto_trade
from .chart import render_signal_chart
from .config import load_auto_trade_config, load_env_file, load_signal_config, load_telegram_config
from .message import format_signal_message
from .models import Candle, SignalAction
from .multitimeframe import execution_candles, load_timeframe_candles
from .strategy import generate_signal
from .telegram import send_telegram_message, send_telegram_photo
from .time_utils import parse_candle_timestamp


LOGGER = logging.getLogger(__name__)


class StaleCandleError(ValueError):
    """Raised when the latest exported market candle is too old to trade safely."""


def main() -> int:
    load_env_file()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    interval_seconds = _get_int_env("SIGNAL_POLL_SECONDS", 30, 5)
    stale_log_seconds = _get_int_env("SIGNAL_STALE_LOG_SECONDS", 300, 30)
    error_log_seconds = _get_int_env("SIGNAL_ERROR_LOG_SECONDS", 60, 5)
    state_path = Path(os.getenv("SIGNAL_STATE_PATH", "logs/signal_poller_state.json"))
    lock_path = Path(os.getenv("SIGNAL_LOCK_PATH", "logs/signal_poller.lock"))
    lock_file = _acquire_lock(lock_path)
    if lock_file is None:
        LOGGER.error("Signal poller is already running. Lock file: %s", lock_path)
        return 1

    signal_config = load_signal_config()
    telegram_config = load_telegram_config()
    auto_trade_config = load_auto_trade_config()

    LOGGER.info("Signal poller started. CSV=%s interval=%ss", signal_config.csv_path, interval_seconds)
    last_fingerprint = _read_last_fingerprint(state_path)
    last_log_at: dict[str, float] = {}

    while True:
        try:
            candles_by_timeframe = load_timeframe_candles(signal_config)
            candles = execution_candles(candles_by_timeframe, signal_config)
            _validate_all_candle_ages(candles_by_timeframe, signal_config.max_candle_age_minutes)
            signal = generate_signal(candles, signal_config, candles_by_timeframe)
            message = format_signal_message(signal)
            fingerprint = _fingerprint(message)

            if fingerprint == last_fingerprint:
                LOGGER.info("Signal unchanged; skipped notification.")
            elif signal.action == SignalAction.WAIT and not signal_config.send_wait:
                LOGGER.info("Signal is WAIT and SIGNAL_SEND_WAIT=false; skipped notification.")
                last_fingerprint = fingerprint
                _write_last_fingerprint(state_path, fingerprint)
            elif signal_config.dry_run:
                LOGGER.info("Dry-run enabled; Telegram message was not sent.")
                print(message)
                auto_trade_result = process_auto_trade(signal, auto_trade_config)
                LOGGER.info("Auto trade status: %s - %s", auto_trade_result.status, auto_trade_result.message)
                last_fingerprint = fingerprint
                _write_last_fingerprint(state_path, fingerprint)
            else:
                send_telegram_message(telegram_config, message)
                if _get_bool_env("SIGNAL_SEND_CHART", True):
                    chart_path = render_signal_chart(
                        candles,
                        signal,
                        os.getenv("SIGNAL_CHART_PATH", "logs/latest_signal_chart.png"),
                    )
                    send_telegram_photo(
                        telegram_config,
                        chart_path,
                        "กราฟประกอบ signal ล่าสุด | ใช้ดู context ไม่ใช่คำแนะนำทางการเงิน",
                    )
                auto_trade_result = process_auto_trade(signal, auto_trade_config)
                LOGGER.info("Auto trade status: %s - %s", auto_trade_result.status, auto_trade_result.message)
                LOGGER.info("Telegram signal sent for %s %s", signal.symbol, signal.action.value)
                last_fingerprint = fingerprint
                _write_last_fingerprint(state_path, fingerprint)
        except KeyboardInterrupt:
            LOGGER.info("Signal poller stopped")
            return 0
        except StaleCandleError as exc:
            if _should_log(last_log_at, "stale_candle", stale_log_seconds):
                LOGGER.warning(
                    "%s Trading paused until MT5 exports a fresh M5 candle. "
                    "Check MT5 is open, connected, and TradingSignalCsvExporter is attached to the correct symbol.",
                    exc,
                )
        except FileNotFoundError as exc:
            if _should_log(last_log_at, "missing_csv", error_log_seconds):
                LOGGER.warning("%s. Waiting for MT5 exporter to create the CSV.", exc)
        except Exception as exc:
            if _should_log(last_log_at, f"polling_error:{type(exc).__name__}", error_log_seconds):
                LOGGER.error("Polling failed: %s", exc)

        try:
            time.sleep(interval_seconds)
        except KeyboardInterrupt:
            LOGGER.info("Signal poller stopped")
            return 0


def _fingerprint(message: str) -> str:
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


def _read_last_fingerprint(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    fingerprint = data.get("fingerprint")
    return fingerprint if isinstance(fingerprint, str) else None


def _write_last_fingerprint(path: Path, fingerprint: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fingerprint": fingerprint}, ensure_ascii=False), encoding="utf-8")


def _should_log(last_log_at: dict[str, float], key: str, throttle_seconds: int) -> bool:
    now = time.monotonic()
    previous = last_log_at.get(key)
    if previous is not None and now - previous < throttle_seconds:
        return False
    last_log_at[key] = now
    return True


def _validate_latest_candle_age(timestamp: str, max_age_minutes: int) -> None:
    latest = parse_candle_timestamp(timestamp)
    age_minutes = (datetime.now(tz=UTC) - latest).total_seconds() / 60
    if age_minutes < 0:
        age_minutes = 0
    if age_minutes > max_age_minutes:
        raise StaleCandleError(
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


def _get_int_env(name: str, default: int, minimum: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be greater than or equal to {minimum}")
    return value


def _get_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _acquire_lock(path: Path) -> object | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = path.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return None
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


if __name__ == "__main__":
    sys.exit(main())
