from __future__ import annotations

import csv
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from trading_signal_bot.backtest import (
    BacktestRange,
    calculate_backtest_cost_summary,
    run_enhanced_backtest_report_with_realism,
    summarize_balance_performance,
    summarize_trade_performance,
)
from trading_signal_bot.config import load_env_file, load_signal_config
from trading_signal_bot.enhanced_backtest_main import build_realism_config_from_env
from trading_signal_bot.multitimeframe import load_timeframe_candles
from trading_signal_bot.time_utils import parse_candle_timestamp


DEFAULT_OUTPUT_DIR = Path("logs/reality_check")


@dataclass(frozen=True)
class RealityWindowResult:
    label: str
    start: str
    end: str
    days: int
    trades: int
    wins: int
    losses: int
    win_rate: float
    profit_factor: float
    net_pnl: float
    return_percent: float
    final_balance: float
    max_drawdown: float
    total_cost: float
    stopped_reason: str
    passed: bool


def main() -> int:
    load_env_file()
    output_dir = Path(os.getenv("REALITY_CHECK_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR)))
    output_dir.mkdir(parents=True, exist_ok=True)

    signal_config = load_signal_config()
    realism_config = build_realism_config_from_env()
    candles_by_timeframe = load_timeframe_candles(signal_config)
    execution_candles = candles_by_timeframe.get(signal_config.execution_timeframe)
    if not execution_candles:
        print(f"No candles for execution timeframe {signal_config.execution_timeframe}", file=sys.stderr)
        return 1

    execution_times = [parse_candle_timestamp(candle.timestamp) for candle in execution_candles]
    first_time = execution_times[0]
    last_time = execution_times[-1]
    window_days = _int_tuple_from_env("REALITY_CHECK_WINDOWS", (30, 60, 90, 180, 365))
    step_days = _int_from_env("REALITY_CHECK_STEP_DAYS", 7)
    pass_return_percent = _float_from_env("REALITY_CHECK_PASS_RETURN_PERCENT", 0.0)

    results: list[RealityWindowResult] = []
    for days in window_days:
        results.extend(
            _run_rolling_windows(
                candles_by_timeframe=candles_by_timeframe,
                signal_config=signal_config,
                realism_config=realism_config,
                first_time=first_time,
                last_time=last_time,
                days=days,
                step_days=step_days,
                pass_return_percent=pass_return_percent,
            )
        )

    monthly_results = _run_calendar_months(
        candles_by_timeframe=candles_by_timeframe,
        signal_config=signal_config,
        realism_config=realism_config,
        first_time=first_time,
        last_time=last_time,
        pass_return_percent=pass_return_percent,
    )
    all_results = [*results, *monthly_results]

    csv_path = output_dir / "reality_check_windows.csv"
    json_path = output_dir / "reality_check_summary.json"
    _write_results_csv(csv_path, all_results)
    _write_summary_json(json_path, all_results, first_time.isoformat(), last_time.isoformat())

    print(_format_console_summary(all_results, first_time.isoformat(), last_time.isoformat(), csv_path, json_path))
    return 0


def _run_rolling_windows(
    *,
    candles_by_timeframe: dict,
    signal_config,
    realism_config,
    first_time,
    last_time,
    days: int,
    step_days: int,
    pass_return_percent: float,
) -> list[RealityWindowResult]:
    if days <= 0 or step_days <= 0:
        raise ValueError("days and step_days must be greater than zero")

    window = timedelta(days=days)
    step = timedelta(days=step_days)
    if first_time + window > last_time:
        return []

    results: list[RealityWindowResult] = []
    start = first_time
    while start + window <= last_time:
        end = start + window
        results.append(
            _run_single_window(
                label=f"rolling_{days}d",
                start=start,
                end=end,
                days=days,
                candles_by_timeframe=candles_by_timeframe,
                signal_config=signal_config,
                realism_config=realism_config,
                pass_return_percent=pass_return_percent,
            )
        )
        start += step

    latest_start = last_time - window
    if results and latest_start > datetime.fromisoformat(results[-1].start):
        results.append(
            _run_single_window(
                label=f"rolling_{days}d_latest",
                start=latest_start,
                end=last_time,
                days=days,
                candles_by_timeframe=candles_by_timeframe,
                signal_config=signal_config,
                realism_config=realism_config,
                pass_return_percent=pass_return_percent,
            )
        )
    return results


def _run_calendar_months(
    *,
    candles_by_timeframe: dict,
    signal_config,
    realism_config,
    first_time,
    last_time,
    pass_return_percent: float,
) -> list[RealityWindowResult]:
    results: list[RealityWindowResult] = []
    month_start = first_time.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while month_start < last_time:
        next_month = _next_month(month_start)
        start = max(month_start, first_time)
        end = min(next_month, last_time)
        if start < end:
            results.append(
                _run_single_window(
                    label="calendar_month",
                    start=start,
                    end=end,
                    days=max(1, (end - start).days),
                    candles_by_timeframe=candles_by_timeframe,
                    signal_config=signal_config,
                    realism_config=realism_config,
                    pass_return_percent=pass_return_percent,
                )
            )
        month_start = next_month
    return results


def _run_single_window(
    *,
    label: str,
    start,
    end,
    days: int,
    candles_by_timeframe: dict,
    signal_config,
    realism_config,
    pass_return_percent: float,
) -> RealityWindowResult:
    report = run_enhanced_backtest_report_with_realism(
        candles_by_timeframe=candles_by_timeframe,
        config=signal_config,
        realism=realism_config,
        backtest_range=BacktestRange(start=start, end=end, label=label),
        money_config=None,
    )
    balance = summarize_balance_performance(report, realism_config.initial_balance)
    trades = summarize_trade_performance(report)
    costs = calculate_backtest_cost_summary(report.trades, realism_config)
    return RealityWindowResult(
        label=label,
        start=start.isoformat(timespec="minutes"),
        end=end.isoformat(timespec="minutes"),
        days=days,
        trades=int(trades["total_trades"]),
        wins=int(trades["wins"]),
        losses=int(trades["losses"]),
        win_rate=float(trades["win_rate"]),
        profit_factor=float(trades["profit_factor"]),
        net_pnl=float(balance["net_pnl"]),
        return_percent=float(balance["return_percent"]),
        final_balance=float(balance["final_balance"]),
        max_drawdown=float(balance["max_drawdown"]),
        total_cost=float(costs["total_cost"]),
        stopped_reason=report.stopped_reason or "",
        passed=float(balance["return_percent"]) > pass_return_percent,
    )


def _write_results_csv(path: Path, results: list[RealityWindowResult]) -> None:
    fieldnames = list(RealityWindowResult.__dataclass_fields__)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            writer.writerow(asdict(result))


def _write_summary_json(path: Path, results: list[RealityWindowResult], first_time: str, last_time: str) -> None:
    grouped: dict[str, list[RealityWindowResult]] = {}
    for result in results:
        grouped.setdefault(result.label, []).append(result)

    payload = {
        "data_start": first_time,
        "data_end": last_time,
        "groups": {
            label: _group_summary(group_results)
            for label, group_results in sorted(grouped.items())
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _group_summary(results: list[RealityWindowResult]) -> dict[str, float | int | str]:
    if not results:
        return {}
    returns = [result.return_percent for result in results]
    balances = [result.final_balance for result in results]
    trade_counts = [result.trades for result in results]
    worst = min(results, key=lambda result: result.return_percent)
    best = max(results, key=lambda result: result.return_percent)
    pass_count = sum(1 for result in results if result.passed)
    return {
        "windows": len(results),
        "pass_count": pass_count,
        "pass_rate": pass_count / len(results) * 100.0,
        "average_return_percent": sum(returns) / len(returns),
        "worst_return_percent": worst.return_percent,
        "worst_window": f"{worst.start} -> {worst.end}",
        "best_return_percent": best.return_percent,
        "best_window": f"{best.start} -> {best.end}",
        "min_final_balance": min(balances),
        "max_final_balance": max(balances),
        "average_trades": sum(trade_counts) / len(trade_counts),
    }


def _format_console_summary(
    results: list[RealityWindowResult],
    first_time: str,
    last_time: str,
    csv_path: Path,
    json_path: Path,
) -> str:
    grouped: dict[str, list[RealityWindowResult]] = {}
    for result in results:
        grouped.setdefault(result.label, []).append(result)

    lines = [
        "Reality Check Backtest",
        f"Data: {first_time} -> {last_time}",
        "",
        "Groups:",
    ]
    for label, group_results in sorted(grouped.items()):
        summary = _group_summary(group_results)
        lines.append(
            "- "
            f"{label}: windows={summary['windows']}, "
            f"pass_rate={summary['pass_rate']:.2f}%, "
            f"avg_return={summary['average_return_percent']:.2f}%, "
            f"worst={summary['worst_return_percent']:.2f}% ({summary['worst_window']}), "
            f"best={summary['best_return_percent']:.2f}% ({summary['best_window']})"
        )
    lines.extend(["", f"CSV: {csv_path}", f"JSON: {json_path}"])
    return "\n".join(lines)


def _next_month(value):
    if value.month == 12:
        return value.replace(year=value.year + 1, month=1)
    return value.replace(month=value.month + 1)


def _int_tuple_from_env(name: str, default: tuple[int, ...]) -> tuple[int, ...]:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    values = tuple(int(part.strip()) for part in raw_value.split(",") if part.strip())
    return values or default


def _int_from_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    return int(raw_value)


def _float_from_env(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    return float(raw_value)


if __name__ == "__main__":
    raise SystemExit(main())
