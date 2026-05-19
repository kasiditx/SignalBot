from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

from .backtest import (
    BacktestRange,
    BacktestReport,
    export_backtest_report,
    run_enhanced_backtest_report,
    run_enhanced_backtest_report_with_simulation,
)
from .config import load_env_file, load_signal_config
from .models import Candle, SignalConfig
from .multitimeframe import load_timeframe_candles
from .time_utils import parse_candle_timestamp


DEFAULT_OUTPUT_DIR = Path("logs/enhanced_backtest")
NO_ORDER_TEXT = "No order was sent. No MT5 order intent was written. Offline backtest only."


def build_backtest_range_from_env(
    candles_by_timeframe: dict[str, list[Candle]],
    config: SignalConfig,
) -> BacktestRange | None:
    raw_lookback_days = os.getenv("ENHANCED_BACKTEST_LOOKBACK_DAYS")
    if raw_lookback_days is None or not raw_lookback_days.strip():
        return None

    try:
        lookback_days = int(raw_lookback_days)
    except ValueError as exc:
        raise ValueError("ENHANCED_BACKTEST_LOOKBACK_DAYS must be an integer") from exc
    if lookback_days < 1:
        raise ValueError("ENHANCED_BACKTEST_LOOKBACK_DAYS must be greater than or equal to 1")

    execution_candles = candles_by_timeframe.get(config.execution_timeframe)
    if not execution_candles:
        raise ValueError(f"No candles for execution timeframe {config.execution_timeframe}")

    end = parse_candle_timestamp(execution_candles[-1].timestamp)
    start = end - timedelta(days=lookback_days)
    return BacktestRange(start=start, end=end, label=f"last {lookback_days} days")


def enhanced_backtest_output_dir_from_env() -> Path:
    raw_path = os.getenv("ENHANCED_BACKTEST_OUTPUT_DIR")
    if raw_path is None or not raw_path.strip():
        return DEFAULT_OUTPUT_DIR
    return Path(raw_path.strip())


def enhanced_backtest_mode_from_env() -> str:
    raw_mode = os.getenv("ENHANCED_BACKTEST_MODE")
    if raw_mode is None or not raw_mode.strip():
        return "decision"

    mode = raw_mode.strip().lower()
    if mode not in {"decision", "simulation"}:
        raise ValueError("ENHANCED_BACKTEST_MODE must be decision or simulation")
    return mode


def format_enhanced_backtest_summary(
    report: BacktestReport,
    output_dir: Path,
    mode: str = "decision",
) -> str:
    mode_label = "offline simulation report" if mode == "simulation" else "decision-only offline report"
    lines = [
        "Enhanced Backtest Summary",
        f"Mode: {mode_label}",
        f"Legacy mode label: {'offline decision report only' if mode == 'decision' else 'offline simulation report'}",
        f"Decisions: {len(report.decisions)}",
        f"Approved decisions: {report.metrics.approved_trades}",
        f"Rejected decisions: {report.metrics.rejected_trades}",
        f"Skipped decisions: {report.metrics.skipped_trades}",
        f"Trades simulated: {len(report.trades)}",
        f"Output directory: {output_dir}",
    ]
    if report.stopped_reason:
        lines.append(f"Stopped: {report.stopped_reason}")
    if not report.decisions:
        lines.append("Warning: No decisions were captured. Check candles/timeframe/backtest range.")
    lines.append(NO_ORDER_TEXT)
    return "\n".join(lines)


def load_enhanced_backtest_inputs() -> tuple[
    SignalConfig,
    dict[str, list[Candle]],
    BacktestRange | None,
]:
    load_env_file()
    signal_config = load_signal_config()
    candles_by_timeframe = load_timeframe_candles(signal_config)
    backtest_range = build_backtest_range_from_env(candles_by_timeframe, signal_config)
    return signal_config, candles_by_timeframe, backtest_range


def main() -> int:
    try:
        signal_config, candles_by_timeframe, backtest_range = load_enhanced_backtest_inputs()
        output_dir = enhanced_backtest_output_dir_from_env()
        mode = enhanced_backtest_mode_from_env()
        if mode == "simulation":
            report = run_enhanced_backtest_report_with_simulation(
                candles_by_timeframe=candles_by_timeframe,
                config=signal_config,
                backtest_range=backtest_range,
                money_config=None,
            )
        else:
            report = run_enhanced_backtest_report(
                candles_by_timeframe=candles_by_timeframe,
                config=signal_config,
                backtest_range=backtest_range,
                money_config=None,
            )
        export_backtest_report(report, output_dir)
        print(format_enhanced_backtest_summary(report, output_dir, mode))
        return 0
    except Exception as exc:
        print("Enhanced backtest failed")
        print(f"error={exc}")
        print(NO_ORDER_TEXT)
        return 1


if __name__ == "__main__":
    sys.exit(main())
