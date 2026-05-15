from __future__ import annotations

import csv
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import load_env_file, load_signal_config
from .models import Candle, SignalAction
from .multitimeframe import EXECUTION_TIMEFRAME, load_timeframe_candles
from .strategy import generate_signal
from .time_utils import parse_candle_timestamp


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class BacktestTrade:
    action: SignalAction
    entry_time: str
    exit_time: str
    entry: float
    stop_loss: float
    take_profit: float
    result: str
    r_multiple: float
    setup_type: str
    trend_summary: str
    volume: float | None = None
    risk_amount: float | None = None
    pnl: float | None = None
    balance_after: float | None = None


@dataclass(frozen=True)
class BacktestMoneyConfig:
    initial_balance: float
    risk_percent: float
    contract_size: float
    min_volume: float
    max_volume: float
    volume_step: float
    compound: bool
    allow_min_volume: bool
    stop_on_zero_balance: bool


@dataclass(frozen=True)
class BacktestRunResult:
    trades: list[BacktestTrade]
    skipped_signals: int
    stopped_reason: str | None


@dataclass
class TimeframeCursor:
    candles: list[Candle]
    timestamps: list[datetime]
    end_index: int = 0

    def advance_to(self, current_time: datetime) -> None:
        while self.end_index < len(self.timestamps) and self.timestamps[self.end_index] <= current_time:
            self.end_index += 1

    def window(self, max_bars: int) -> list[Candle]:
        start = max(0, self.end_index - max_bars)
        return self.candles[start:self.end_index]


def main() -> int:
    load_env_file()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        config = load_signal_config()
        if not config.multi_timeframe_enabled:
            raise ValueError("Backtest requires SIGNAL_MULTI_TIMEFRAME=true")

        candles_by_timeframe = load_timeframe_candles(config)
        backtest_range = _load_backtest_range(candles_by_timeframe[EXECUTION_TIMEFRAME])
        money_config = _load_money_config()
        result = run_backtest_with_stats(candles_by_timeframe, config, backtest_range, money_config)
        _print_report(result, backtest_range, money_config)
        _write_trades_csv(result.trades, Path("logs/backtest_trades.csv"))
        return 0
    except Exception as exc:
        LOGGER.error("Backtest failed: %s", exc)
        return 1


@dataclass(frozen=True)
class BacktestRange:
    start: datetime | None
    end: datetime | None
    label: str


def run_backtest(
    candles_by_timeframe: dict[str, list[Candle]],
    config: object,
    backtest_range: BacktestRange | None = None,
    money_config: BacktestMoneyConfig | None = None,
) -> list[BacktestTrade]:
    return run_backtest_with_stats(candles_by_timeframe, config, backtest_range, money_config).trades


def run_backtest_with_stats(
    candles_by_timeframe: dict[str, list[Candle]],
    config: object,
    backtest_range: BacktestRange | None = None,
    money_config: BacktestMoneyConfig | None = None,
) -> BacktestRunResult:
    execution = candles_by_timeframe[EXECUTION_TIMEFRAME]
    execution_timestamps = [parse_candle_timestamp(candle.timestamp) for candle in execution]
    cursors = _build_cursors(candles_by_timeframe)
    trades: list[BacktestTrade] = []
    skipped_signals = 0
    stopped_reason: str | None = None
    balance = money_config.initial_balance if money_config else 0.0
    index = max(60, getattr(config, "min_candles", 60))
    snapshot_max_bars = max(getattr(config, "min_candles", 60) + 40, 160)

    while index < len(execution) - 2:
        current_time = execution_timestamps[index]
        snapshot = _snapshot(cursors, current_time, snapshot_max_bars)
        if backtest_range and not _is_in_backtest_range(current_time, backtest_range):
            index += 1
            continue
        if len(snapshot[EXECUTION_TIMEFRAME]) < getattr(config, "min_candles", 60):
            index += 1
            continue

        try:
            signal = generate_signal(snapshot[EXECUTION_TIMEFRAME], config, snapshot)
        except ValueError:
            index += 1
            continue

        if signal.action == SignalAction.WAIT or not _has_complete_trade_levels(signal):
            index += 1
            continue

        trade = _simulate_trade(signal, execution, index)
        if money_config:
            try:
                trade = _with_money_result(trade, money_config, balance)
            except ValueError as exc:
                LOGGER.info("Skipped signal at %s: %s", execution[index].timestamp, exc)
                skipped_signals += 1
                index += 1
                continue
            if trade.balance_after is not None:
                balance = trade.balance_after
            if money_config.stop_on_zero_balance and balance <= 0:
                stopped_reason = "balance reached zero or below"
                trades.append(trade)
                break
        trades.append(trade)
        index = _index_after_time(execution, trade.exit_time, index + 1)

    return BacktestRunResult(trades=trades, skipped_signals=skipped_signals, stopped_reason=stopped_reason)


def _load_backtest_range(execution: list[Candle]) -> BacktestRange:
    lookback_days = _get_optional_int_env("BACKTEST_LOOKBACK_DAYS", minimum=1)
    if lookback_days is None:
        return BacktestRange(start=None, end=None, label="all available data")
    if not execution:
        raise ValueError("Cannot calculate BACKTEST_LOOKBACK_DAYS because M5 data is empty")

    end = parse_candle_timestamp(execution[-1].timestamp)
    start = end - timedelta(days=lookback_days)
    return BacktestRange(start=start, end=end, label=f"last {lookback_days} days")


def _load_money_config() -> BacktestMoneyConfig:
    initial_balance = _get_float_env("BACKTEST_INITIAL_BALANCE", 30.0, 0.01)
    risk_percent = _get_float_env("BACKTEST_RISK_PERCENT", 0.5, 0.01)
    if risk_percent > 2.0:
        raise ValueError("BACKTEST_RISK_PERCENT should not exceed 2.0 without a reviewed risk plan")

    min_volume = _get_float_env("BACKTEST_MIN_VOLUME", 0.01, 0.0)
    max_volume = _get_float_env("BACKTEST_MAX_VOLUME", 0.01, 0.0)
    if min_volume > max_volume:
        raise ValueError("BACKTEST_MIN_VOLUME must be lower than or equal to BACKTEST_MAX_VOLUME")

    return BacktestMoneyConfig(
        initial_balance=initial_balance,
        risk_percent=risk_percent,
        contract_size=_get_float_env("BACKTEST_CONTRACT_SIZE", 100.0, 0.00000001),
        min_volume=min_volume,
        max_volume=max_volume,
        volume_step=_get_float_env("BACKTEST_VOLUME_STEP", 0.01, 0.00000001),
        compound=_get_bool_env("BACKTEST_COMPOUND", True),
        allow_min_volume=_get_bool_env("BACKTEST_ALLOW_MIN_VOLUME", True),
        stop_on_zero_balance=_get_bool_env("BACKTEST_STOP_ON_ZERO_BALANCE", True),
    )


def _is_in_backtest_range(current_time: datetime, backtest_range: BacktestRange) -> bool:
    if backtest_range.start is not None and current_time < backtest_range.start:
        return False
    if backtest_range.end is not None and current_time > backtest_range.end:
        return False
    return True


def _build_cursors(candles_by_timeframe: dict[str, list[Candle]]) -> dict[str, TimeframeCursor]:
    return {
        timeframe: TimeframeCursor(
            candles=candles,
            timestamps=[parse_candle_timestamp(candle.timestamp) for candle in candles],
        )
        for timeframe, candles in candles_by_timeframe.items()
    }


def _snapshot(cursors: dict[str, TimeframeCursor], current_time: datetime, max_bars: int) -> dict[str, list[Candle]]:
    snapshot: dict[str, list[Candle]] = {}
    for timeframe, cursor in cursors.items():
        cursor.advance_to(current_time)
        snapshot[timeframe] = cursor.window(max_bars)
    return snapshot


def _simulate_trade(signal: object, execution: list[Candle], signal_index: int) -> BacktestTrade:
    action = signal.action
    if not _has_complete_trade_levels(signal):
        raise ValueError("Signal is missing entry, stop loss, or take profit")

    entry = float(signal.levels.entry)
    stop_loss = float(signal.levels.stop_loss)
    take_profit = float(signal.levels.take_profit)
    risk = abs(entry - stop_loss)
    if risk <= 0:
        raise ValueError("Invalid trade risk distance")

    for candle in execution[signal_index + 1:]:
        if action == SignalAction.BUY:
            stopped = candle.low <= stop_loss
            target_hit = candle.high >= take_profit
        else:
            stopped = candle.high >= stop_loss
            target_hit = candle.low <= take_profit

        if stopped and target_hit:
            return _trade(signal, execution[signal_index].timestamp, candle.timestamp, "LOSS_BOTH_HIT", -1.0)
        if stopped:
            return _trade(signal, execution[signal_index].timestamp, candle.timestamp, "LOSS", -1.0)
        if target_hit:
            return _trade(signal, execution[signal_index].timestamp, candle.timestamp, "WIN", float(signal.levels.risk_reward or 0))

    last = execution[-1]
    if action == SignalAction.BUY:
        r_multiple = (last.close - entry) / risk
    else:
        r_multiple = (entry - last.close) / risk
    return _trade(signal, execution[signal_index].timestamp, last.timestamp, "OPEN_AT_END", r_multiple)


def _has_complete_trade_levels(signal: object) -> bool:
    levels = getattr(signal, "levels", None)
    if levels is None:
        return False
    return levels.entry is not None and levels.stop_loss is not None and levels.take_profit is not None


def _trade(signal: object, entry_time: str, exit_time: str, result: str, r_multiple: float) -> BacktestTrade:
    return BacktestTrade(
        action=signal.action,
        entry_time=entry_time,
        exit_time=exit_time,
        entry=float(signal.levels.entry),
        stop_loss=float(signal.levels.stop_loss),
        take_profit=float(signal.levels.take_profit),
        result=result,
        r_multiple=r_multiple,
        setup_type=signal.setup_type,
        trend_summary=signal.trend_summary,
    )


def _with_money_result(
    trade: BacktestTrade,
    money_config: BacktestMoneyConfig,
    current_balance: float,
) -> BacktestTrade:
    sizing_balance = current_balance if money_config.compound else money_config.initial_balance
    volume = _position_size(
        balance=sizing_balance,
        risk_percent=money_config.risk_percent,
        entry=trade.entry,
        stop_loss=trade.stop_loss,
        contract_size=money_config.contract_size,
        min_volume=money_config.min_volume,
        max_volume=money_config.max_volume,
        volume_step=money_config.volume_step,
        allow_min_volume=money_config.allow_min_volume,
    )
    risk_amount = abs(trade.entry - trade.stop_loss) * money_config.contract_size * volume
    pnl = trade.r_multiple * risk_amount
    balance_after = current_balance + pnl
    return BacktestTrade(
        action=trade.action,
        entry_time=trade.entry_time,
        exit_time=trade.exit_time,
        entry=trade.entry,
        stop_loss=trade.stop_loss,
        take_profit=trade.take_profit,
        result=trade.result,
        r_multiple=trade.r_multiple,
        setup_type=trade.setup_type,
        trend_summary=trade.trend_summary,
        volume=volume,
        risk_amount=risk_amount,
        pnl=pnl,
        balance_after=balance_after,
    )


def _position_size(
    balance: float,
    risk_percent: float,
    entry: float,
    stop_loss: float,
    contract_size: float,
    min_volume: float,
    max_volume: float,
    volume_step: float,
    allow_min_volume: bool,
) -> float:
    risk_distance = abs(entry - stop_loss)
    if risk_distance <= 0:
        raise ValueError("Stop loss distance must be greater than zero")

    money_at_risk = balance * (risk_percent / 100.0)
    raw_volume = money_at_risk / (risk_distance * contract_size)
    if raw_volume < min_volume and not allow_min_volume:
        raise ValueError("calculated volume is below broker minimum volume")
    stepped_volume = (raw_volume // volume_step) * volume_step
    if stepped_volume < min_volume:
        volume = min_volume
    else:
        volume = min(stepped_volume, max_volume)
    if volume <= 0:
        raise ValueError("Calculated volume must be greater than zero")
    return round(volume, 8)


def _index_after_time(candles: list[Candle], timestamp: str, fallback: int) -> int:
    for index, candle in enumerate(candles[fallback:], start=fallback):
        if candle.timestamp == timestamp:
            return index + 1
    return fallback + 1


def _print_report(
    result: BacktestRunResult,
    backtest_range: BacktestRange,
    money_config: BacktestMoneyConfig,
) -> None:
    trades = result.trades
    wins = [trade for trade in trades if trade.result == "WIN"]
    losses = [trade for trade in trades if trade.result.startswith("LOSS")]
    open_at_end = [trade for trade in trades if trade.result == "OPEN_AT_END"]
    closed = wins + losses
    gross_win = sum(trade.r_multiple for trade in wins)
    gross_loss = abs(sum(trade.r_multiple for trade in losses))
    net_r = sum(trade.r_multiple for trade in trades)
    profit_factor = gross_win / gross_loss if gross_loss else 0.0
    max_drawdown = _max_drawdown(trades)
    win_rate = (len(wins) / len(closed) * 100) if closed else 0.0

    print("Backtest Summary")
    print(f"- Range: {backtest_range.label}")
    if backtest_range.start and backtest_range.end:
        print(f"- From: {backtest_range.start:%Y-%m-%d %H:%M}")
        print(f"- To: {backtest_range.end:%Y-%m-%d %H:%M}")
    print(f"- Trades: {len(trades)}")
    print(f"- Wins: {len(wins)}")
    print(f"- Losses: {len(losses)}")
    print(f"- Open at end: {len(open_at_end)}")
    print(f"- Closed-trade win rate: {win_rate:.2f}%")
    print(f"- Net R: {net_r:.2f}R")
    print(f"- Profit factor: {profit_factor:.2f}")
    print(f"- Max drawdown: {max_drawdown:.2f}R")
    print(f"- Skipped signals: {result.skipped_signals}")
    if result.stopped_reason:
        print(f"- Stopped: {result.stopped_reason}")
    _print_money_report(trades, money_config)
    print("- Output: logs/backtest_trades.csv")


def _print_money_report(trades: list[BacktestTrade], money_config: BacktestMoneyConfig) -> None:
    if not trades:
        print(f"- Initial balance: ${money_config.initial_balance:.2f}")
        print(f"- Final balance: ${money_config.initial_balance:.2f}")
        return

    final_balance = trades[-1].balance_after or money_config.initial_balance
    net_profit = final_balance - money_config.initial_balance
    max_drawdown = _max_money_drawdown(trades, money_config.initial_balance)
    lowest_balance = min(
        [money_config.initial_balance] + [trade.balance_after for trade in trades if trade.balance_after is not None]
    )
    max_risk_amount = max((trade.risk_amount or 0.0) for trade in trades)
    max_risk_percent = (max_risk_amount / money_config.initial_balance) * 100
    average_volume = sum((trade.volume or 0.0) for trade in trades) / len(trades)
    return_percent = (net_profit / money_config.initial_balance) * 100
    max_drawdown_percent = (max_drawdown / money_config.initial_balance) * 100

    print(f"- Initial balance: ${money_config.initial_balance:.2f}")
    print(f"- Final balance: ${final_balance:.2f}")
    print(f"- Net profit: ${net_profit:.2f} ({return_percent:.2f}%)")
    print(f"- Lowest balance: ${lowest_balance:.2f}")
    print(f"- Max money drawdown: ${max_drawdown:.2f} ({max_drawdown_percent:.2f}%)")
    print(f"- Risk per trade setting: {money_config.risk_percent:.2f}%")
    print(f"- Max actual risk per trade: ${max_risk_amount:.2f} ({max_risk_percent:.2f}% of initial balance)")
    print(f"- Average volume: {average_volume:.2f} lot")


def _get_optional_int_env(name: str, minimum: int) -> int | None:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return None
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be greater than or equal to {minimum}")
    return value


def _get_float_env(name: str, default: float, minimum: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value < minimum:
        raise ValueError(f"{name} must be greater than or equal to {minimum}")
    return value


def _get_bool_env(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _max_drawdown(trades: list[BacktestTrade]) -> float:
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for trade in trades:
        equity += trade.r_multiple
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return max_drawdown


def _max_money_drawdown(trades: list[BacktestTrade], initial_balance: float) -> float:
    peak = initial_balance
    max_drawdown = 0.0
    for trade in trades:
        balance = trade.balance_after
        if balance is None:
            continue
        peak = max(peak, balance)
        max_drawdown = max(max_drawdown, peak - balance)
    return max_drawdown


def _write_trades_csv(trades: list[BacktestTrade], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(BacktestTrade.__dataclass_fields__.keys()))
        writer.writeheader()
        for trade in trades:
            row = {
                "action": trade.action.value,
                "entry_time": trade.entry_time,
                "exit_time": trade.exit_time,
                "entry": trade.entry,
                "stop_loss": trade.stop_loss,
                "take_profit": trade.take_profit,
                "result": trade.result,
                "r_multiple": trade.r_multiple,
                "setup_type": trade.setup_type,
                "trend_summary": trade.trend_summary,
                "volume": trade.volume,
                "risk_amount": trade.risk_amount,
                "pnl": trade.pnl,
                "balance_after": trade.balance_after,
            }
            writer.writerow(row)


if __name__ == "__main__":
    sys.exit(main())
