from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

from .backtest import (
    BacktestRange,
    BacktestRealismConfig,
    BacktestReport,
    calculate_backtest_cost_summary,
    calculate_session_pnl_summary,
    export_backtest_report,
    run_enhanced_backtest_report,
    run_enhanced_backtest_report_with_realism,
    run_enhanced_backtest_report_with_simulation,
    summarize_balance_performance,
    summarize_risk_skips,
    summarize_trade_performance,
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
    if mode not in {"decision", "simulation", "realism"}:
        raise ValueError("ENHANCED_BACKTEST_MODE must be decision, simulation, or realism")
    return mode


def _get_float_env(name: str, default: float, minimum: float | None = None) -> float:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        value = default
    else:
        try:
            value = float(raw_value)
        except ValueError as exc:
            raise ValueError(f"{name} must be a number") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be greater than or equal to {minimum}")
    return value


def _get_int_env(name: str, default: int, minimum: int | None = None) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        value = default
    else:
        try:
            value = int(raw_value)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be greater than or equal to {minimum}")
    return value


def _get_bool_env(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    value = raw_value.strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def build_realism_config_from_env() -> BacktestRealismConfig:
    min_volume = _get_float_env("BACKTEST_MIN_VOLUME", 0.01, 0.00000001)
    max_volume = _get_float_env("BACKTEST_MAX_VOLUME", 10.0, 0.00000001)
    if min_volume > max_volume:
        raise ValueError("BACKTEST_MIN_VOLUME must be lower than or equal to BACKTEST_MAX_VOLUME")

    return BacktestRealismConfig(
        initial_balance=_get_float_env("BACKTEST_INITIAL_BALANCE", 10000.0, 0.00000001),
        risk_percent=_get_float_env("BACKTEST_RISK_PERCENT", 1.0, 0.00000001),
        contract_size=_get_float_env("BACKTEST_CONTRACT_SIZE", 100.0, 0.00000001),
        min_volume=min_volume,
        max_volume=max_volume,
        volume_step=_get_float_env("BACKTEST_VOLUME_STEP", 0.01, 0.00000001),
        allow_min_volume=_get_bool_env("BACKTEST_ALLOW_MIN_VOLUME", True),
        spread_points=_get_float_env("BACKTEST_SPREAD_POINTS", 20.0, 0.0),
        point_value=_get_float_env("BACKTEST_POINT_VALUE", 0.01, 0.00000001),
        slippage_points=_get_float_env("BACKTEST_SLIPPAGE_POINTS", 5.0, 0.0),
        commission_per_lot=_get_float_env("BACKTEST_COMMISSION_PER_LOT", 7.0, 0.0),
        max_daily_loss_percent=_get_float_env("BACKTEST_MAX_DAILY_LOSS_PERCENT", 3.0, 0.00000001),
        max_consecutive_losses=_get_int_env("BACKTEST_MAX_CONSECUTIVE_LOSSES", 3, 1),
        cooldown_minutes=_get_int_env("BACKTEST_COOLDOWN_MINUTES", 30, 0),
    )


def _format_decision_section(report: BacktestReport) -> list[str]:
    return [
        "Decisions:",
        f"- Total decisions: {len(report.decisions)}",
        f"- Approved: {report.metrics.approved_trades}",
        f"- Rejected: {report.metrics.rejected_trades}",
        f"- Skipped: {report.metrics.skipped_trades}",
    ]


def _format_trade_section(report: BacktestReport) -> list[str]:
    summary = summarize_trade_performance(report)
    return [
        "Trades:",
        f"- Trades simulated: {summary['total_trades']}",
        f"- Wins: {summary['wins']}",
        f"- Losses: {summary['losses']}",
        f"- Open at end: {summary['open_at_end']}",
        f"- Loss both hit: {summary['loss_both_hit']}",
        f"- Win rate: {summary['win_rate']:.2f}%",
        f"- Profit factor: {summary['profit_factor']:.2f}",
        f"- Net R: {summary['net_r']:.2f}R",
        f"- Average win: {summary['average_win']:.2f}",
        f"- Average loss: {summary['average_loss']:.2f}",
        f"- Average RR: {summary['average_rr']:.2f}",
    ]


def _format_balance_section(
    report: BacktestReport,
    realism: BacktestRealismConfig | None,
) -> list[str]:
    if realism is None:
        return []

    summary = summarize_balance_performance(report, realism.initial_balance)
    return [
        "Balance:",
        f"- Initial balance: {summary['initial_balance']:.2f}",
        f"- Final balance: {summary['final_balance']:.2f}",
        f"- Net PnL: {summary['net_pnl']:.2f}",
        f"- Return %: {summary['return_percent']:.2f}%",
        f"- Max drawdown: {summary['max_drawdown']:.2f}",
    ]


def _format_cost_section(
    report: BacktestReport,
    realism: BacktestRealismConfig | None,
) -> list[str]:
    if realism is None:
        return []

    summary = calculate_backtest_cost_summary(report.trades, realism)
    return [
        "Costs:",
        f"- Commission: {summary['total_commission']:.2f}",
        f"- Spread cost: {summary['total_spread_cost']:.2f}",
        f"- Slippage cost: {summary['total_slippage_cost']:.2f}",
        f"- Total cost: {summary['total_cost']:.2f}",
    ]


def _format_risk_skip_section(report: BacktestReport) -> list[str]:
    summary = summarize_risk_skips(report)
    lines = ["Risk skips:"]
    if not summary:
        lines.append("- None")
        return lines

    for reason in (
        "cooldown active",
        "daily risk stopped for day",
        "max daily loss reached",
        "max consecutive losses reached",
    ):
        lines.append(f"- {reason}: {summary.get(reason, 0)}")
    return lines


def _format_session_pnl_section(report: BacktestReport) -> list[str]:
    summary = calculate_session_pnl_summary(report.trades)
    lines = ["Session PnL:"]
    for session in ("Asia", "London", "NewYork", "Other"):
        values = summary[session]
        lines.append(
            "- "
            f"{session}: trades={values['trades']}, "
            f"pnl={values['net_pnl']:.2f}, "
            f"win_rate={values['win_rate']:.2f}%, "
            f"net_r={values['net_r']:.2f}R"
        )
    return lines


def format_enhanced_backtest_summary(
    report: BacktestReport,
    output_dir: Path,
    mode: str = "decision",
    realism: BacktestRealismConfig | None = None,
) -> str:
    mode_labels = {
        "decision": "decision-only offline report",
        "simulation": "offline simulation report",
        "realism": "offline realism report",
    }
    mode_label = mode_labels.get(mode, "decision-only offline report")
    lines = [
        "Enhanced Backtest Summary",
        f"Mode: {mode_label}",
    ]
    lines.extend(["", *_format_decision_section(report)])

    if mode in {"simulation", "realism"}:
        lines.extend(["", *_format_trade_section(report)])

    if mode == "realism":
        balance_lines = _format_balance_section(report, realism)
        if balance_lines:
            lines.extend(["", *balance_lines])

        cost_lines = _format_cost_section(report, realism)
        if cost_lines:
            lines.extend(["", *cost_lines])

        lines.extend(["", *_format_risk_skip_section(report)])
        lines.extend(["", *_format_session_pnl_section(report)])
    else:
        risk_skip_lines = _format_risk_skip_section(report)
        if len(risk_skip_lines) > 1 and risk_skip_lines[1] != "- None":
            lines.extend(["", *risk_skip_lines])

    lines.extend(["", f"Output directory: {output_dir}"])
    if report.stopped_reason:
        lines.append(f"Stopped: {report.stopped_reason}")
    if not report.decisions:
        lines.append("Warning: No decisions were captured. Check candles/timeframe/backtest range.")
    lines.extend(
        [
            "No order was sent.",
            "No MT5 order intent was written.",
            "Offline backtest only.",
        ]
    )
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
        realism_config: BacktestRealismConfig | None = None
        if mode == "realism":
            realism_config = build_realism_config_from_env()
            report = run_enhanced_backtest_report_with_realism(
                candles_by_timeframe=candles_by_timeframe,
                config=signal_config,
                realism=realism_config,
                backtest_range=backtest_range,
                money_config=None,
            )
        elif mode == "simulation":
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
        print(format_enhanced_backtest_summary(report, output_dir, mode, realism_config))
        return 0
    except Exception as exc:
        print("Enhanced backtest failed")
        print(f"error={exc}")
        print(NO_ORDER_TEXT)
        return 1


if __name__ == "__main__":
    sys.exit(main())
