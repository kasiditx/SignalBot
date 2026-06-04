from __future__ import annotations

import os
import sys
from pathlib import Path

from .config import load_env_file, load_signal_config
from .forward_validation import ForwardValidationConfig
from .models import AutoTradeConfig, SignalConfig
from .realtime_forward_runner import (
    RealtimeForwardLoopResult,
    RealtimeForwardRunnerConfig,
    run_realtime_forward_loop,
)


DEFAULT_OUTPUT_DIR = Path("logs/forward_validation")
DEFAULT_TIMEFRAMES = ("H4", "H1", "M30", "M15", "M5", "M1")
SAFETY_TEXT = "No order was sent.\nNo MT5 order intent was written.\nRealtime forward dry-run only."


def _get_int_env(name: str, default: int, minimum: int | None = None) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        value = default
    else:
        try:
            value = int(raw_value.strip())
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _get_optional_int_env(name: str, minimum: int | None = None) -> int | None:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return None
    try:
        value = int(raw_value.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _get_bool_env(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"true", "1", "yes", "y", "on"}:
        return True
    if normalized in {"false", "0", "no", "n", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _optional_text(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value.strip()


def realtime_forward_output_dir_from_env() -> Path:
    raw_path = os.getenv("REALTIME_FORWARD_OUTPUT_DIR")
    if raw_path is None or not raw_path.strip():
        return DEFAULT_OUTPUT_DIR
    return Path(raw_path.strip())


def build_forward_validation_config_from_realtime_output_dir(
    output_dir: Path,
) -> ForwardValidationConfig:
    return ForwardValidationConfig(
        record_csv_path=output_dir / "forward_records.csv",
        record_jsonl_path=output_dir / "forward_records.jsonl",
        daily_summary_path=output_dir / "daily_summary.csv",
        weekly_summary_path=output_dir / "weekly_summary.csv",
    )


def build_realtime_forward_runner_config_from_env(
    signal_config: SignalConfig,
) -> RealtimeForwardRunnerConfig:
    output_dir = realtime_forward_output_dir_from_env()
    symbol = _optional_text(os.getenv("REALTIME_FORWARD_SYMBOL")) or signal_config.symbol
    return RealtimeForwardRunnerConfig(
        symbol=symbol,
        timeframes=DEFAULT_TIMEFRAMES,
        execution_timeframe=os.getenv("REALTIME_FORWARD_EXECUTION_TIMEFRAME", "M1").strip().upper() or "M1",
        candle_count=_get_int_env("REALTIME_FORWARD_CANDLE_COUNT", 300, minimum=1),
        interval_seconds=_get_int_env("REALTIME_FORWARD_INTERVAL_SECONDS", 60, minimum=1),
        output_dir=output_dir,
        state_path=output_dir / "realtime_state.json",
        stop_file_path=output_dir / "STOP_REALTIME_FORWARD",
        mode=os.getenv("REALTIME_FORWARD_MODE", "paper").strip().lower() or "paper",
        max_iterations=_get_optional_int_env("REALTIME_FORWARD_MAX_ITERATIONS", minimum=1),
        session=_optional_text(os.getenv("REALTIME_FORWARD_SESSION")),
        high_impact_news_nearby=_get_bool_env("REALTIME_HIGH_IMPACT_NEWS_NEARBY", False),
    )


def build_safe_realtime_paper_sizing_config() -> AutoTradeConfig:
    return AutoTradeConfig(
        False,
        "paper",
        "logs/forward_validation/disabled.csv",
        "logs/forward_validation/realtime_journal.csv",
        10000.0,
        1.0,
        100.0,
        0.01,
        10.0,
        0.01,
        True,
        20260528,
        "RealtimeForward",
    )


def format_realtime_forward_summary(
    result: RealtimeForwardLoopResult,
    config: RealtimeForwardRunnerConfig,
) -> str:
    return "\n".join(
        [
            "Realtime Forward Dry-run Summary",
            f"Mode: {config.mode}",
            f"Symbol: {config.symbol}",
            f"Status: {result.status}",
            f"Iterations: {result.iterations}",
            f"Processed: {result.processed_count}",
            f"Skipped: {result.skipped_count}",
            f"Errors: {result.error_count}",
            f"Stopped: {result.stopped}",
            f"Output directory: {config.output_dir}",
            f"State file: {config.state_path}",
            f"Stop file: {config.stop_file_path}",
            SAFETY_TEXT,
        ]
    )


def format_realtime_forward_error(message: str) -> str:
    return "\n".join(
        [
            "Realtime forward failed",
            f"error={message}",
            SAFETY_TEXT,
        ]
    )


def main() -> int:
    try:
        load_env_file()
        signal_config = load_signal_config()
        runner_config = build_realtime_forward_runner_config_from_env(signal_config)
        if runner_config.mode.lower() == "live":
            print("Realtime forward live mode is not allowed.")
            print(SAFETY_TEXT)
            return 1

        validation_config = build_forward_validation_config_from_realtime_output_dir(
            runner_config.output_dir,
        )
        result = run_realtime_forward_loop(
            config=runner_config,
            signal_config=signal_config,
            sizing_config=build_safe_realtime_paper_sizing_config(),
            validation_config=validation_config,
            continue_on_error=_get_bool_env("REALTIME_FORWARD_CONTINUE_ON_ERROR", True),
            max_errors=_get_int_env("REALTIME_FORWARD_MAX_ERRORS", 3, minimum=1),
        )
        print(format_realtime_forward_summary(result, runner_config))
        if result.status in ("completed", "stopped"):
            return 0
        return 1
    except Exception as exc:
        print(format_realtime_forward_error(str(exc)))
        return 1


if __name__ == "__main__":
    sys.exit(main())
