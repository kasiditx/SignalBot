from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
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


DEFAULT_OUTPUT_DIR = Path("logs/month_to_current_backtest")


@dataclass(frozen=True)
class MonthToCurrentResult:
    months: int
    start: str
    end: str
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


def main() -> int:
    load_env_file()
    output_dir = Path(os.getenv("MONTH_BACKTEST_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR)))
    output_dir.mkdir(parents=True, exist_ok=True)

    config = load_signal_config()
    realism = build_realism_config_from_env()
    candles_by_timeframe = load_timeframe_candles(config)
    execution_candles = candles_by_timeframe.get(config.execution_timeframe)
    if not execution_candles:
        raise SystemExit(f"No candles for execution timeframe {config.execution_timeframe}")

    latest_time = parse_candle_timestamp(execution_candles[-1].timestamp)
    available_start = parse_candle_timestamp(execution_candles[0].timestamp)
    month_counts = _month_counts_from_env("MONTH_BACKTEST_COUNTS", (1, 2, 3, 6, 12))

    results: list[MonthToCurrentResult] = []
    for months in month_counts:
        requested_start = _start_of_months_back(latest_time, months)
        start = max(requested_start, available_start)
        report = run_enhanced_backtest_report_with_realism(
            candles_by_timeframe=candles_by_timeframe,
            config=config,
            realism=realism,
            backtest_range=BacktestRange(start=start, end=latest_time, label=f"{months}m_to_current"),
            money_config=None,
        )
        balance = summarize_balance_performance(report, realism.initial_balance)
        trades = summarize_trade_performance(report)
        costs = calculate_backtest_cost_summary(report.trades, realism)
        results.append(
            MonthToCurrentResult(
                months=months,
                start=start.isoformat(timespec="minutes"),
                end=latest_time.isoformat(timespec="minutes"),
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
            )
        )

    csv_path = output_dir / "month_to_current_backtest.csv"
    json_path = output_dir / "month_to_current_backtest.json"
    _write_csv(csv_path, results)
    _write_json(json_path, results, available_start, latest_time)
    print(_format_results(results, available_start, latest_time, csv_path, json_path))
    return 0


def _start_of_months_back(latest_time: datetime, months: int) -> datetime:
    if months < 1:
        raise ValueError("months must be greater than or equal to 1")
    year = latest_time.year
    month = latest_time.month - months
    while month <= 0:
        month += 12
        year -= 1
    return latest_time.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)


def _write_csv(path: Path, results: list[MonthToCurrentResult]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(MonthToCurrentResult.__dataclass_fields__))
        writer.writeheader()
        for result in results:
            writer.writerow(asdict(result))


def _write_json(
    path: Path,
    results: list[MonthToCurrentResult],
    available_start: datetime,
    latest_time: datetime,
) -> None:
    payload = {
        "available_data_start": available_start.isoformat(timespec="minutes"),
        "available_data_end": latest_time.isoformat(timespec="minutes"),
        "month_semantics": "N months means from the first day of the month N months before the latest candle through the latest candle.",
        "results": [asdict(result) for result in results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _format_results(
    results: list[MonthToCurrentResult],
    available_start: datetime,
    latest_time: datetime,
    csv_path: Path,
    json_path: Path,
) -> str:
    lines = [
        "Month-To-Current Backtest",
        f"Available data: {available_start.isoformat(timespec='minutes')} -> {latest_time.isoformat(timespec='minutes')}",
        "",
    ]
    for result in results:
        lines.append(
            "- "
            f"{result.months}m: {result.start} -> {result.end}, "
            f"trades={result.trades}, W/L={result.wins}/{result.losses}, "
            f"win_rate={result.win_rate:.2f}%, pnl={result.net_pnl:.2f}, "
            f"final={result.final_balance:.2f}, return={result.return_percent:.2f}%, "
            f"pf={result.profit_factor:.2f}"
        )
    lines.extend(["", f"CSV: {csv_path}", f"JSON: {json_path}"])
    return "\n".join(lines)


def _month_counts_from_env(name: str, default: tuple[int, ...]) -> tuple[int, ...]:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    values = tuple(int(part.strip()) for part in raw_value.split(",") if part.strip())
    return values or default


if __name__ == "__main__":
    raise SystemExit(main())
