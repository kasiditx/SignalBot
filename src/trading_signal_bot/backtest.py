from __future__ import annotations

import csv
import json
import logging
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from collections.abc import Iterable
from pathlib import Path

from .config import load_env_file, load_signal_config
from .models import Candle, SignalAction, SignalConfig
from .multitimeframe import EXECUTION_TIMEFRAME, load_timeframe_candles
from .strategy import generate_signal
from .time_utils import parse_candle_timestamp


LOGGER = logging.getLogger(__name__)
_RISK_SKIP_REASONS = (
    "cooldown active",
    "daily risk stopped for day",
    "max daily loss reached",
    "max consecutive losses reached",
)


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


@dataclass(frozen=True)
class BacktestDecision:
    timestamp: str
    session: str
    symbol: str
    timeframe: str
    action: str | None
    stage: str
    approved: bool
    reasons: tuple[str, ...]
    htf_bias: str | None
    execution_trend: str | None
    price_location: str | None
    candle_confirmation_summary: str | None
    risk_reward: float | None


@dataclass(frozen=True)
class BacktestTradeResult:
    action: SignalAction
    session: str
    entry_time: str
    exit_time: str
    entry: float
    stop_loss: float
    tp1: float | None
    tp2: float | None
    result: str
    r_multiple: float
    risk_reward: float | None
    volume: float | None
    pnl: float | None
    balance_after: float | None
    loss_reason: str | None
    reject_reasons_before_entry: tuple[str, ...] = ()


@dataclass(frozen=True)
class BacktestMetrics:
    total_trades: int
    approved_trades: int
    rejected_trades: int
    skipped_trades: int
    win_rate: float
    loss_rate: float
    profit_factor: float
    max_drawdown: float
    average_win: float
    average_loss: float
    average_rr: float
    max_consecutive_losses: int
    net_r: float


@dataclass(frozen=True)
class BacktestReport:
    trades: tuple[BacktestTradeResult, ...]
    decisions: tuple[BacktestDecision, ...]
    metrics: BacktestMetrics
    session_metrics: dict[str, BacktestMetrics]
    reject_reason_summary: dict[str, int]
    skip_reason_summary: dict[str, int]
    stopped_reason: str | None


@dataclass(frozen=True)
class BacktestCandidate:
    decision: BacktestDecision
    action: SignalAction
    entry: float
    stop_loss: float
    tp1: float | None
    tp2: float | None
    risk_reward: float | None
    signal_index: int


@dataclass(frozen=True)
class BacktestRealismConfig:
    initial_balance: float
    risk_percent: float
    contract_size: float
    min_volume: float
    max_volume: float
    volume_step: float
    allow_min_volume: bool
    spread_points: float
    point_value: float
    slippage_points: float
    commission_per_lot: float
    max_daily_loss_percent: float
    max_consecutive_losses: int
    cooldown_minutes: int


@dataclass(frozen=True)
class BacktestDailyRiskState:
    date: str
    trades_today: int
    losses_today: int
    consecutive_losses: int
    realized_loss_percent: float
    cooldown_until: datetime | None
    stopped_for_day: bool


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


def execution_timeframe_for_backtest(config: object) -> str:
    return getattr(config, "execution_timeframe", EXECUTION_TIMEFRAME)


def has_required_snapshot_candles(
    snapshot: dict[str, list[Candle]],
    config: object,
) -> bool:
    execution_timeframe = execution_timeframe_for_backtest(config)
    required_candles = getattr(config, "min_candles", 60)
    return len(snapshot.get(execution_timeframe, [])) >= required_candles


def backtest_decision_from_signal(
    signal: object,
    config: SignalConfig,
    current_time: datetime,
    stage: str,
    approved: bool,
    reasons: tuple[str, ...],
) -> BacktestDecision:
    execution_timeframe = execution_timeframe_for_backtest(config)
    action = getattr(signal, "action", None)
    levels = getattr(signal, "levels", None)
    return BacktestDecision(
        timestamp=current_time.isoformat(),
        session=classify_session(current_time),
        symbol=getattr(signal, "symbol", config.symbol),
        timeframe=execution_timeframe,
        action=action.value if isinstance(action, SignalAction) else None,
        stage=stage,
        approved=approved,
        reasons=reasons,
        htf_bias=_signal_text_attr(signal, "trend_alignment"),
        execution_trend=_signal_text_attr(signal, "trend_summary"),
        price_location=_signal_text_attr(signal, "setup_type"),
        candle_confirmation_summary=_signal_text_attr(signal, "reason"),
        risk_reward=getattr(levels, "risk_reward", None) if levels is not None else None,
    )


def backtest_candidate_from_signal(
    signal: object,
    decision: BacktestDecision,
    signal_index: int,
) -> BacktestCandidate:
    action = getattr(signal, "action", None)
    if action not in {SignalAction.BUY, SignalAction.SELL}:
        raise ValueError("Backtest candidate requires BUY or SELL signal action")

    levels = getattr(signal, "levels", None)
    if levels is None:
        raise ValueError("Signal levels are required to build a backtest candidate")
    if levels.entry is None:
        raise ValueError("Signal entry is required to build a backtest candidate")
    if levels.stop_loss is None:
        raise ValueError("Signal stop loss is required to build a backtest candidate")
    if levels.take_profit is None:
        raise ValueError("Signal take profit is required to build a backtest candidate")

    return BacktestCandidate(
        decision=decision,
        action=action,
        entry=float(levels.entry),
        stop_loss=float(levels.stop_loss),
        tp1=None,
        tp2=float(levels.take_profit),
        risk_reward=getattr(levels, "risk_reward", None) or decision.risk_reward,
        signal_index=signal_index,
    )


def simulate_enhanced_trade(
    candidate: BacktestCandidate,
    execution_candles: list[Candle],
) -> BacktestTradeResult:
    if not execution_candles:
        raise ValueError("Execution candles are required to simulate enhanced trade")

    target = candidate.tp2 if candidate.tp2 is not None else candidate.tp1
    if target is None:
        raise ValueError("Take profit target is required to simulate enhanced trade")

    risk_distance = abs(candidate.entry - candidate.stop_loss)
    if risk_distance <= 0:
        raise ValueError("Risk distance must be greater than zero")

    entry_candle = execution_candles[min(candidate.signal_index, len(execution_candles) - 1)]
    for candle in execution_candles[candidate.signal_index + 1 :]:
        stopped, target_hit = _enhanced_exit_hits(candidate, candle, target)
        if stopped and target_hit:
            return _enhanced_trade_result(
                candidate=candidate,
                entry_time=entry_candle.timestamp,
                exit_time=candle.timestamp,
                target=target,
                result="LOSS_BOTH_HIT",
                r_multiple=-1.0,
                loss_reason="both_tp_sl_hit",
            )
        if stopped:
            return _enhanced_trade_result(
                candidate=candidate,
                entry_time=entry_candle.timestamp,
                exit_time=candle.timestamp,
                target=target,
                result="LOSS",
                r_multiple=-1.0,
                loss_reason="stop_loss_hit",
            )
        if target_hit:
            return _enhanced_trade_result(
                candidate=candidate,
                entry_time=entry_candle.timestamp,
                exit_time=candle.timestamp,
                target=target,
                result="WIN",
                r_multiple=abs(target - candidate.entry) / risk_distance,
                loss_reason=None,
            )

    last = execution_candles[-1]
    if candidate.action == SignalAction.BUY:
        r_multiple = (last.close - candidate.entry) / risk_distance
    else:
        r_multiple = (candidate.entry - last.close) / risk_distance
    return _enhanced_trade_result(
        candidate=candidate,
        entry_time=entry_candle.timestamp,
        exit_time=last.timestamp,
        target=target,
        result="OPEN_AT_END",
        r_multiple=r_multiple,
        loss_reason="open_at_end",
    )


def calculate_backtest_position_size(
    balance: float,
    entry: float,
    stop_loss: float,
    realism: BacktestRealismConfig,
) -> float:
    if balance <= 0:
        raise ValueError("Balance must be greater than zero")
    if realism.risk_percent <= 0:
        raise ValueError("Risk percent must be greater than zero")
    if realism.contract_size <= 0:
        raise ValueError("Contract size must be greater than zero")
    if realism.min_volume <= 0:
        raise ValueError("Minimum volume must be greater than zero")
    if realism.max_volume <= 0:
        raise ValueError("Maximum volume must be greater than zero")
    if realism.volume_step <= 0:
        raise ValueError("Volume step must be greater than zero")
    if realism.min_volume > realism.max_volume:
        raise ValueError("Minimum volume must be lower than or equal to maximum volume")

    risk_distance = abs(entry - stop_loss)
    if risk_distance <= 0:
        raise ValueError("Risk distance must be greater than zero")

    money_at_risk = balance * (realism.risk_percent / 100.0)
    raw_volume = money_at_risk / (risk_distance * realism.contract_size)
    step_count = math.floor((raw_volume / realism.volume_step) + 1e-12)
    stepped_volume = step_count * realism.volume_step

    if stepped_volume < realism.min_volume:
        if not realism.allow_min_volume:
            raise ValueError("Calculated volume is below minimum volume")
        volume = realism.min_volume
    else:
        volume = stepped_volume

    return round(min(volume, realism.max_volume), 8)


def calculate_backtest_trade_costs(
    volume: float,
    realism: BacktestRealismConfig,
) -> dict[str, float]:
    if volume <= 0:
        raise ValueError("Volume must be greater than zero")
    if realism.contract_size <= 0:
        raise ValueError("Contract size must be greater than zero")
    if realism.point_value <= 0:
        raise ValueError("Point value must be greater than zero")
    if realism.spread_points < 0:
        raise ValueError("Spread points must be greater than or equal to zero")
    if realism.slippage_points < 0:
        raise ValueError("Slippage points must be greater than or equal to zero")
    if realism.commission_per_lot < 0:
        raise ValueError("Commission per lot must be greater than or equal to zero")

    commission = realism.commission_per_lot * volume
    spread_cost = realism.spread_points * realism.point_value * realism.contract_size * volume
    slippage_cost = realism.slippage_points * realism.point_value * realism.contract_size * volume
    total_cost = commission + spread_cost + slippage_cost
    return {
        "commission": commission,
        "spread_cost": spread_cost,
        "slippage_cost": slippage_cost,
        "total_cost": total_cost,
    }


def apply_backtest_money_result(
    trade: BacktestTradeResult,
    balance: float,
    realism: BacktestRealismConfig,
) -> BacktestTradeResult:
    if balance <= 0:
        raise ValueError("Balance must be greater than zero")

    volume = calculate_backtest_position_size(
        balance=balance,
        entry=trade.entry,
        stop_loss=trade.stop_loss,
        realism=realism,
    )
    costs = calculate_backtest_trade_costs(volume, realism)
    risk_distance = abs(trade.entry - trade.stop_loss)
    if risk_distance <= 0:
        raise ValueError("Risk distance must be greater than zero")

    risk_amount = risk_distance * realism.contract_size * volume
    gross_pnl = trade.r_multiple * risk_amount
    net_pnl = gross_pnl - costs["total_cost"]
    balance_after = balance + net_pnl
    return BacktestTradeResult(
        action=trade.action,
        session=trade.session,
        entry_time=trade.entry_time,
        exit_time=trade.exit_time,
        entry=trade.entry,
        stop_loss=trade.stop_loss,
        tp1=trade.tp1,
        tp2=trade.tp2,
        result=trade.result,
        r_multiple=trade.r_multiple,
        risk_reward=trade.risk_reward,
        volume=volume,
        pnl=net_pnl,
        balance_after=balance_after,
        loss_reason=trade.loss_reason,
        reject_reasons_before_entry=trade.reject_reasons_before_entry,
    )


def reset_backtest_daily_risk_state(
    current_time: datetime,
) -> BacktestDailyRiskState:
    return BacktestDailyRiskState(
        date=current_time.date().isoformat(),
        trades_today=0,
        losses_today=0,
        consecutive_losses=0,
        realized_loss_percent=0.0,
        cooldown_until=None,
        stopped_for_day=False,
    )


def reset_daily_risk_state_if_new_day(
    state: BacktestDailyRiskState,
    current_time: datetime,
) -> BacktestDailyRiskState:
    if current_time.date().isoformat() != state.date:
        return reset_backtest_daily_risk_state(current_time)
    return state


def evaluate_backtest_daily_risk_state(
    state: BacktestDailyRiskState,
    current_time: datetime,
    realism: BacktestRealismConfig,
) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    if state.stopped_for_day:
        reasons.append("daily risk stopped for day")
    if state.realized_loss_percent >= realism.max_daily_loss_percent:
        reasons.append("max daily loss reached")
    if state.consecutive_losses >= realism.max_consecutive_losses:
        reasons.append("max consecutive losses reached")
    if state.cooldown_until is not None and current_time < state.cooldown_until:
        reasons.append("cooldown active")
    return bool(reasons), tuple(reasons)


def update_backtest_daily_risk_state_after_trade(
    state: BacktestDailyRiskState,
    trade: BacktestTradeResult,
    current_time: datetime,
    realism: BacktestRealismConfig,
) -> BacktestDailyRiskState:
    if trade.pnl is None:
        raise ValueError("Trade pnl is required to update daily risk state")

    trades_today = state.trades_today + 1
    losses_today = state.losses_today
    consecutive_losses = state.consecutive_losses
    realized_loss_percent = state.realized_loss_percent
    cooldown_until = state.cooldown_until
    is_loss = trade.pnl < 0 or trade.result.startswith("LOSS")

    if is_loss:
        losses_today += 1
        consecutive_losses += 1
        realized_loss_percent += abs(trade.pnl) / realism.initial_balance * 100.0
        cooldown_until = (
            current_time + timedelta(minutes=realism.cooldown_minutes)
            if realism.cooldown_minutes > 0
            else None
        )
    else:
        consecutive_losses = 0
        cooldown_until = None

    stopped_for_day = (
        state.stopped_for_day
        or realized_loss_percent >= realism.max_daily_loss_percent
        or consecutive_losses >= realism.max_consecutive_losses
    )
    return BacktestDailyRiskState(
        date=state.date,
        trades_today=trades_today,
        losses_today=losses_today,
        consecutive_losses=consecutive_losses,
        realized_loss_percent=realized_loss_percent,
        cooldown_until=cooldown_until,
        stopped_for_day=stopped_for_day,
    )


def backtest_risk_skip_decision(
    config: SignalConfig,
    current_time: datetime,
    reasons: tuple[str, ...],
) -> BacktestDecision:
    return BacktestDecision(
        timestamp=current_time.isoformat(),
        session=classify_session(current_time),
        symbol=getattr(config, "symbol", "UNKNOWN"),
        timeframe=execution_timeframe_for_backtest(config),
        action=None,
        stage="risk_skip",
        approved=False,
        reasons=reasons,
        htf_bias=None,
        execution_trend=None,
        price_location=None,
        candle_confirmation_summary=None,
        risk_reward=None,
    )


def capture_backtest_decision(
    snapshot: dict[str, list[Candle]],
    config: SignalConfig,
    current_time: datetime,
    money_config: BacktestMoneyConfig | None = None,
    balance: float = 0.0,
) -> BacktestDecision:
    del money_config, balance
    execution_timeframe = execution_timeframe_for_backtest(config)
    if execution_timeframe not in snapshot:
        return _backtest_decision_without_signal(
            config=config,
            current_time=current_time,
            stage="market_data",
            approved=False,
            reasons=("missing execution timeframe candles",),
        )
    if not has_required_snapshot_candles(snapshot, config):
        return _backtest_decision_without_signal(
            config=config,
            current_time=current_time,
            stage="insufficient_candles",
            approved=False,
            reasons=("insufficient candles",),
        )

    try:
        signal = generate_signal(snapshot[execution_timeframe], config, snapshot)
    except ValueError as exc:
        return _backtest_decision_without_signal(
            config=config,
            current_time=current_time,
            stage="signal_error",
            approved=False,
            reasons=(str(exc),),
        )

    if signal.action == SignalAction.WAIT:
        reason = getattr(signal, "no_trade_reason", None) or "signal action is WAIT"
        return backtest_decision_from_signal(
            signal=signal,
            config=config,
            current_time=current_time,
            stage="skip",
            approved=False,
            reasons=(reason,),
        )
    if not _has_complete_trade_levels(signal):
        return backtest_decision_from_signal(
            signal=signal,
            config=config,
            current_time=current_time,
            stage="skip",
            approved=False,
            reasons=("missing trade levels",),
        )
    return backtest_decision_from_signal(
        signal=signal,
        config=config,
        current_time=current_time,
        stage="signal_candidate",
        approved=True,
        reasons=(),
    )


def run_backtest_decision_capture(
    candles_by_timeframe: dict[str, list[Candle]],
    config: SignalConfig,
    backtest_range: BacktestRange | None = None,
    money_config: BacktestMoneyConfig | None = None,
) -> tuple[BacktestDecision, ...]:
    execution_timeframe = execution_timeframe_for_backtest(config)
    execution = candles_by_timeframe.get(execution_timeframe)
    if not execution:
        return (
            _backtest_decision_without_signal(
                config=config,
                current_time=datetime.now(),
                stage="market_data",
                approved=False,
                reasons=("missing execution timeframe candles",),
            ),
        )

    execution_timestamps = [parse_candle_timestamp(candle.timestamp) for candle in execution]
    cursors = _build_cursors(candles_by_timeframe)
    decisions: list[BacktestDecision] = []
    index = max(0, getattr(config, "min_candles", 60) - 1)
    snapshot_max_bars = max(getattr(config, "min_candles", 60) + 40, 160)
    balance = money_config.initial_balance if money_config else 0.0

    while index < len(execution):
        current_time = execution_timestamps[index]
        if backtest_range and not _is_in_backtest_range(current_time, backtest_range):
            index += 1
            continue
        snapshot = _snapshot(cursors, current_time, snapshot_max_bars)
        decisions.append(
            capture_backtest_decision(
                snapshot=snapshot,
                config=config,
                current_time=current_time,
                money_config=money_config,
                balance=balance,
            )
        )
        index += 1
    return tuple(decisions)


def build_backtest_report_from_decisions(
    decisions: tuple[BacktestDecision, ...],
    trades: tuple[BacktestTradeResult, ...] = (),
    stopped_reason: str | None = None,
) -> BacktestReport:
    trade_list = list(trades)
    decision_list = list(decisions)
    return BacktestReport(
        trades=trades,
        decisions=decisions,
        metrics=calculate_backtest_metrics(trade_list, decision_list),
        session_metrics=calculate_session_metrics(trade_list, decision_list),
        reject_reason_summary=summarize_reject_reasons(decision_list),
        skip_reason_summary=summarize_skip_reasons(decision_list),
        stopped_reason=stopped_reason,
    )


def run_enhanced_backtest_report(
    candles_by_timeframe: dict[str, list[Candle]],
    config: SignalConfig,
    backtest_range: BacktestRange | None = None,
    money_config: BacktestMoneyConfig | None = None,
) -> BacktestReport:
    decisions = run_backtest_decision_capture(
        candles_by_timeframe=candles_by_timeframe,
        config=config,
        backtest_range=backtest_range,
        money_config=money_config,
    )
    return build_backtest_report_from_decisions(decisions)


def capture_backtest_decision_and_candidate(
    snapshot: dict[str, list[Candle]],
    config: SignalConfig,
    current_time: datetime,
    signal_index: int,
) -> tuple[BacktestDecision, BacktestCandidate | None]:
    execution_timeframe = execution_timeframe_for_backtest(config)
    if execution_timeframe not in snapshot:
        return (
            _backtest_decision_without_signal(
                config=config,
                current_time=current_time,
                stage="market_data",
                approved=False,
                reasons=("missing execution timeframe candles",),
            ),
            None,
        )
    if not has_required_snapshot_candles(snapshot, config):
        return (
            _backtest_decision_without_signal(
                config=config,
                current_time=current_time,
                stage="insufficient_candles",
                approved=False,
                reasons=("insufficient candles",),
            ),
            None,
        )

    try:
        signal = generate_signal(snapshot[execution_timeframe], config, snapshot)
    except ValueError as exc:
        return (
            _backtest_decision_without_signal(
                config=config,
                current_time=current_time,
                stage="signal_error",
                approved=False,
                reasons=(str(exc),),
            ),
            None,
        )

    if signal.action == SignalAction.WAIT:
        reason = getattr(signal, "no_trade_reason", None) or "signal action is WAIT"
        return (
            backtest_decision_from_signal(
                signal=signal,
                config=config,
                current_time=current_time,
                stage="skip",
                approved=False,
                reasons=(reason,),
            ),
            None,
        )
    if signal.action not in {SignalAction.BUY, SignalAction.SELL}:
        return (
            backtest_decision_from_signal(
                signal=signal,
                config=config,
                current_time=current_time,
                stage="skip",
                approved=False,
                reasons=("unsupported signal action",),
            ),
            None,
        )
    if not _has_complete_trade_levels(signal):
        return (
            backtest_decision_from_signal(
                signal=signal,
                config=config,
                current_time=current_time,
                stage="skip",
                approved=False,
                reasons=("missing trade levels",),
            ),
            None,
        )

    decision = backtest_decision_from_signal(
        signal=signal,
        config=config,
        current_time=current_time,
        stage="signal_candidate",
        approved=True,
        reasons=(),
    )
    return decision, backtest_candidate_from_signal(signal, decision, signal_index)


def run_enhanced_backtest_report_with_simulation(
    candles_by_timeframe: dict[str, list[Candle]],
    config: SignalConfig,
    backtest_range: BacktestRange | None = None,
    money_config: BacktestMoneyConfig | None = None,
) -> BacktestReport:
    del money_config
    execution_timeframe = execution_timeframe_for_backtest(config)
    execution = candles_by_timeframe.get(execution_timeframe)
    if not execution:
        return build_backtest_report_from_decisions(
            (
                _backtest_decision_without_signal(
                    config=config,
                    current_time=datetime.now(),
                    stage="market_data",
                    approved=False,
                    reasons=("missing execution timeframe candles",),
                ),
            )
        )

    execution_timestamps = [parse_candle_timestamp(candle.timestamp) for candle in execution]
    cursors = _build_cursors(candles_by_timeframe)
    decisions: list[BacktestDecision] = []
    trades: list[BacktestTradeResult] = []
    index = max(0, getattr(config, "min_candles", 60) - 1)
    snapshot_max_bars = max(getattr(config, "min_candles", 60) + 40, 160)

    while index < len(execution):
        current_time = execution_timestamps[index]
        if backtest_range and not _is_in_backtest_range(current_time, backtest_range):
            index += 1
            continue

        snapshot = _snapshot(cursors, current_time, snapshot_max_bars)
        decision, candidate = capture_backtest_decision_and_candidate(
            snapshot=snapshot,
            config=config,
            current_time=current_time,
            signal_index=index,
        )
        decisions.append(decision)

        if candidate is None:
            index += 1
            continue

        try:
            trade = simulate_enhanced_trade(candidate, execution)
        except ValueError as exc:
            decisions.append(
                _backtest_decision_without_signal(
                    config=config,
                    current_time=current_time,
                    stage="simulation_error",
                    approved=False,
                    reasons=(str(exc),),
                )
            )
            index += 1
            continue

        trades.append(trade)
        index = _index_after_time(execution, trade.exit_time, index + 1)

    return build_backtest_report_from_decisions(tuple(decisions), tuple(trades))


def run_enhanced_backtest_report_with_realism(
    candles_by_timeframe: dict[str, list[Candle]],
    config: SignalConfig,
    realism: BacktestRealismConfig,
    backtest_range: BacktestRange | None = None,
    money_config: BacktestMoneyConfig | None = None,
) -> BacktestReport:
    del money_config
    execution_timeframe = execution_timeframe_for_backtest(config)
    execution = candles_by_timeframe.get(execution_timeframe)
    if not execution:
        return build_backtest_report_from_decisions(
            (
                _backtest_decision_without_signal(
                    config=config,
                    current_time=datetime.now(),
                    stage="market_data",
                    approved=False,
                    reasons=("missing execution timeframe candles",),
                ),
            )
        )

    execution_timestamps = [parse_candle_timestamp(candle.timestamp) for candle in execution]
    cursors = _build_cursors(candles_by_timeframe)
    decisions: list[BacktestDecision] = []
    trades: list[BacktestTradeResult] = []
    balance = realism.initial_balance
    index = max(0, getattr(config, "min_candles", 60) - 1)
    risk_state = reset_backtest_daily_risk_state(execution_timestamps[index])
    snapshot_max_bars = max(getattr(config, "min_candles", 60) + 40, 160)

    while index < len(execution):
        current_time = execution_timestamps[index]
        if backtest_range and not _is_in_backtest_range(current_time, backtest_range):
            index += 1
            continue

        risk_state = reset_daily_risk_state_if_new_day(risk_state, current_time)
        should_skip, risk_reasons = evaluate_backtest_daily_risk_state(
            risk_state,
            current_time,
            realism,
        )
        if should_skip:
            decisions.append(
                backtest_risk_skip_decision(
                    config=config,
                    current_time=current_time,
                    reasons=risk_reasons,
                )
            )
            index += 1
            continue

        snapshot = _snapshot(cursors, current_time, snapshot_max_bars)
        decision, candidate = capture_backtest_decision_and_candidate(
            snapshot=snapshot,
            config=config,
            current_time=current_time,
            signal_index=index,
        )
        decisions.append(decision)

        if candidate is None:
            index += 1
            continue

        try:
            raw_trade = simulate_enhanced_trade(candidate, execution)
        except ValueError as exc:
            decisions.append(
                _backtest_decision_without_signal(
                    config=config,
                    current_time=current_time,
                    stage="simulation_error",
                    approved=False,
                    reasons=(str(exc),),
                )
            )
            index += 1
            continue

        try:
            money_trade = apply_backtest_money_result(raw_trade, balance, realism)
            if money_trade.balance_after is None:
                raise ValueError("Realism trade result is missing balance_after")
        except ValueError as exc:
            decisions.append(
                _backtest_decision_without_signal(
                    config=config,
                    current_time=current_time,
                    stage="realism_error",
                    approved=False,
                    reasons=(str(exc),),
                )
            )
            index += 1
            continue

        trades.append(money_trade)
        balance = money_trade.balance_after
        exit_time = parse_candle_timestamp(money_trade.exit_time)
        risk_state = update_backtest_daily_risk_state_after_trade(
            state=risk_state,
            trade=money_trade,
            current_time=exit_time,
            realism=realism,
        )
        index = _index_after_time(execution, money_trade.exit_time, index + 1)

    return build_backtest_report_from_decisions(tuple(decisions), tuple(trades))


def classify_session(timestamp: datetime) -> str:
    hour = timestamp.hour
    if 0 <= hour < 7:
        return "Asia"
    if 7 <= hour < 13:
        return "London"
    if 13 <= hour < 21:
        return "NewYork"
    return "Other"


def calculate_backtest_metrics(
    trades: list[BacktestTradeResult],
    decisions: list[BacktestDecision],
) -> BacktestMetrics:
    wins = [trade for trade in trades if trade.result == "WIN"]
    losses = [trade for trade in trades if trade.result.startswith("LOSS")]
    closed = wins + losses
    gross_win = sum(max(0.0, trade.r_multiple) for trade in wins)
    gross_loss = abs(sum(min(0.0, trade.r_multiple) for trade in losses))
    net_r = sum(trade.r_multiple for trade in trades)
    average_win = _average([trade.r_multiple for trade in wins])
    average_loss = _average([trade.r_multiple for trade in losses])
    average_rr = _average([trade.risk_reward for trade in trades if trade.risk_reward is not None])

    return BacktestMetrics(
        total_trades=len(trades),
        approved_trades=sum(1 for decision in decisions if decision.approved),
        rejected_trades=sum(1 for decision in decisions if _is_reject_decision(decision)),
        skipped_trades=sum(1 for decision in decisions if _is_skip_decision(decision)),
        win_rate=(len(wins) / len(closed) * 100.0) if closed else 0.0,
        loss_rate=(len(losses) / len(closed) * 100.0) if closed else 0.0,
        profit_factor=_profit_factor(gross_win, gross_loss),
        max_drawdown=_max_enhanced_drawdown(trades),
        average_win=average_win,
        average_loss=average_loss,
        average_rr=average_rr,
        max_consecutive_losses=_max_consecutive_losses(trades),
        net_r=net_r,
    )


def summarize_reject_reasons(
    decisions: list[BacktestDecision],
) -> dict[str, int]:
    return _summarize_decision_reasons(decision for decision in decisions if _is_reject_decision(decision))


def summarize_skip_reasons(
    decisions: list[BacktestDecision],
) -> dict[str, int]:
    return _summarize_decision_reasons(decision for decision in decisions if _is_skip_decision(decision))


def calculate_session_metrics(
    trades: list[BacktestTradeResult],
    decisions: list[BacktestDecision],
) -> dict[str, BacktestMetrics]:
    sessions = ("Asia", "London", "NewYork", "Other")
    return {
        session: calculate_backtest_metrics(
            [trade for trade in trades if trade.session == session],
            [decision for decision in decisions if decision.session == session],
        )
        for session in sessions
    }


def summarize_balance_performance(
    report: BacktestReport,
    initial_balance: float,
) -> dict[str, float]:
    if initial_balance <= 0:
        raise ValueError("Initial balance must be greater than zero")

    balances = [trade.balance_after for trade in report.trades if trade.balance_after is not None]
    final_balance = balances[-1] if balances else initial_balance
    net_pnl = final_balance - initial_balance
    return {
        "initial_balance": initial_balance,
        "final_balance": final_balance,
        "net_pnl": net_pnl,
        "return_percent": (net_pnl / initial_balance) * 100.0,
        "max_drawdown": report.metrics.max_drawdown,
    }


def summarize_trade_performance(
    report: BacktestReport,
) -> dict[str, float | int]:
    wins = [trade for trade in report.trades if trade.result == "WIN"]
    losses = [trade for trade in report.trades if trade.result == "LOSS"]
    loss_both_hit = [trade for trade in report.trades if trade.result == "LOSS_BOTH_HIT"]
    open_at_end = [trade for trade in report.trades if trade.result == "OPEN_AT_END"]
    closed_losses = losses + loss_both_hit
    closed = wins + closed_losses
    return {
        "total_trades": len(report.trades),
        "wins": len(wins),
        "losses": len(losses),
        "open_at_end": len(open_at_end),
        "loss_both_hit": len(loss_both_hit),
        "win_rate": (len(wins) / len(closed) * 100.0) if closed else 0.0,
        "profit_factor": report.metrics.profit_factor,
        "net_r": report.metrics.net_r,
        "average_win": report.metrics.average_win,
        "average_loss": report.metrics.average_loss,
        "average_rr": report.metrics.average_rr,
    }


def summarize_risk_skips(
    report: BacktestReport,
) -> dict[str, int]:
    risk_reasons = {
        "cooldown active",
        "daily risk stopped for day",
        "max daily loss reached",
        "max consecutive losses reached",
    }
    return {
        reason: count
        for reason, count in report.skip_reason_summary.items()
        if reason in risk_reasons
    }


def calculate_session_pnl_summary(
    trades: tuple[BacktestTradeResult, ...],
) -> dict[str, dict[str, float | int]]:
    summary: dict[str, dict[str, float | int]] = {}
    for session in ("Asia", "London", "NewYork", "Other"):
        session_trades = [trade for trade in trades if trade.session == session]
        wins = [trade for trade in session_trades if trade.result == "WIN"]
        losses = [trade for trade in session_trades if trade.result.startswith("LOSS")]
        closed = wins + losses
        pnl_values = [trade.pnl for trade in session_trades if trade.pnl is not None]
        summary[session] = {
            "trades": len(session_trades),
            "wins": len(wins),
            "losses": len(losses),
            "net_pnl": sum(pnl_values),
            "average_pnl": _average(pnl_values),
            "win_rate": (len(wins) / len(closed) * 100.0) if closed else 0.0,
            "net_r": sum(trade.r_multiple for trade in session_trades),
        }
    return summary


def calculate_backtest_cost_summary(
    trades: tuple[BacktestTradeResult, ...],
    realism: BacktestRealismConfig,
) -> dict[str, float]:
    total_commission = 0.0
    total_spread_cost = 0.0
    total_slippage_cost = 0.0
    total_cost = 0.0
    for trade in trades:
        if trade.volume is None:
            continue
        costs = calculate_backtest_trade_costs(trade.volume, realism)
        total_commission += costs["commission"]
        total_spread_cost += costs["spread_cost"]
        total_slippage_cost += costs["slippage_cost"]
        total_cost += costs["total_cost"]
    return {
        "total_commission": total_commission,
        "total_spread_cost": total_spread_cost,
        "total_slippage_cost": total_slippage_cost,
        "total_cost": total_cost,
    }


def export_backtest_trades_csv(
    report: BacktestReport,
    path: Path,
) -> None:
    fieldnames = [
        "entry_time",
        "exit_time",
        "session",
        "action",
        "entry",
        "stop_loss",
        "tp1",
        "tp2",
        "result",
        "r_multiple",
        "risk_reward",
        "volume",
        "pnl",
        "balance_after",
        "loss_reason",
        "reject_reasons_before_entry",
    ]
    _write_csv_rows(
        path,
        fieldnames,
        [
            {
                "entry_time": trade.entry_time,
                "exit_time": trade.exit_time,
                "session": trade.session,
                "action": trade.action.value,
                "entry": trade.entry,
                "stop_loss": trade.stop_loss,
                "tp1": trade.tp1,
                "tp2": trade.tp2,
                "result": trade.result,
                "r_multiple": trade.r_multiple,
                "risk_reward": trade.risk_reward,
                "volume": trade.volume,
                "pnl": trade.pnl,
                "balance_after": trade.balance_after,
                "loss_reason": trade.loss_reason,
                "reject_reasons_before_entry": _format_reasons(trade.reject_reasons_before_entry),
            }
            for trade in report.trades
        ],
    )


def export_backtest_decisions_csv(
    report: BacktestReport,
    path: Path,
) -> None:
    fieldnames = [
        "timestamp",
        "session",
        "symbol",
        "timeframe",
        "action",
        "stage",
        "approved",
        "reasons",
        "htf_bias",
        "execution_trend",
        "price_location",
        "candle_confirmation_summary",
        "risk_reward",
    ]
    _write_csv_rows(
        path,
        fieldnames,
        [
            {
                "timestamp": decision.timestamp,
                "session": decision.session,
                "symbol": decision.symbol,
                "timeframe": decision.timeframe,
                "action": decision.action,
                "stage": decision.stage,
                "approved": decision.approved,
                "reasons": _format_reasons(decision.reasons),
                "htf_bias": decision.htf_bias,
                "execution_trend": decision.execution_trend,
                "price_location": decision.price_location,
                "candle_confirmation_summary": decision.candle_confirmation_summary,
                "risk_reward": decision.risk_reward,
            }
            for decision in report.decisions
        ],
    )


def export_backtest_session_summary_csv(
    report: BacktestReport,
    path: Path,
) -> None:
    fieldnames = [
        "session",
        "total_trades",
        "approved_trades",
        "rejected_trades",
        "skipped_trades",
        "win_rate",
        "loss_rate",
        "profit_factor",
        "max_drawdown",
        "average_win",
        "average_loss",
        "average_rr",
        "max_consecutive_losses",
        "net_r",
    ]
    _write_csv_rows(
        path,
        fieldnames,
        [
            {"session": session, **_metrics_to_dict(metrics)}
            for session, metrics in report.session_metrics.items()
        ],
    )


def export_backtest_summary_json(
    report: BacktestReport,
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "metrics": _metrics_to_dict(report.metrics),
        "session_metrics": {
            session: _metrics_to_dict(metrics)
            for session, metrics in report.session_metrics.items()
        },
        "reject_reason_summary": report.reject_reason_summary,
        "skip_reason_summary": report.skip_reason_summary,
        "stopped_reason": report.stopped_reason,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def export_backtest_report(
    report: BacktestReport,
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    export_backtest_trades_csv(report, output_dir / "backtest_trades.csv")
    export_backtest_decisions_csv(report, output_dir / "backtest_decisions.csv")
    export_backtest_session_summary_csv(report, output_dir / "backtest_session_summary.csv")
    export_backtest_summary_json(report, output_dir / "backtest_summary.json")


def export_backtest_realism_summary_csv(
    report: BacktestReport,
    path: Path,
    realism: BacktestRealismConfig,
) -> None:
    fieldnames = [
        "initial_balance",
        "final_balance",
        "net_pnl",
        "return_percent",
        "max_drawdown",
        "total_trades",
        "wins",
        "losses",
        "open_at_end",
        "loss_both_hit",
        "win_rate",
        "profit_factor",
        "net_r",
        "average_win",
        "average_loss",
        "average_rr",
    ]
    balance_summary = summarize_balance_performance(report, realism.initial_balance)
    trade_summary = summarize_trade_performance(report)
    _write_csv_rows(
        path,
        fieldnames,
        [{**balance_summary, **trade_summary}],
    )


def export_backtest_risk_skip_summary_csv(
    report: BacktestReport,
    path: Path,
) -> None:
    fieldnames = ["reason", "count"]
    risk_summary = summarize_risk_skips(report)
    _write_csv_rows(
        path,
        fieldnames,
        [
            {"reason": reason, "count": risk_summary.get(reason, 0)}
            for reason in _RISK_SKIP_REASONS
        ],
    )


def export_backtest_cost_summary_csv(
    report: BacktestReport,
    path: Path,
    realism: BacktestRealismConfig,
) -> None:
    fieldnames = [
        "total_commission",
        "total_spread_cost",
        "total_slippage_cost",
        "total_cost",
    ]
    _write_csv_rows(
        path,
        fieldnames,
        [calculate_backtest_cost_summary(report.trades, realism)],
    )


def export_backtest_session_pnl_summary_csv(
    report: BacktestReport,
    path: Path,
) -> None:
    fieldnames = [
        "session",
        "trades",
        "wins",
        "losses",
        "net_pnl",
        "average_pnl",
        "win_rate",
        "net_r",
    ]
    session_summary = calculate_session_pnl_summary(report.trades)
    _write_csv_rows(
        path,
        fieldnames,
        [
            {"session": session, **session_summary[session]}
            for session in ("Asia", "London", "NewYork", "Other")
        ],
    )


def export_enhanced_backtest_summary_json(
    report: BacktestReport,
    path: Path,
    realism: BacktestRealismConfig | None = None,
    mode: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "metrics": _metrics_to_dict(report.metrics),
        "session_metrics": {
            session: _metrics_to_dict(metrics)
            for session, metrics in report.session_metrics.items()
        },
        "reject_reason_summary": report.reject_reason_summary,
        "skip_reason_summary": report.skip_reason_summary,
        "stopped_reason": report.stopped_reason,
        "mode": mode,
        "trade_performance": summarize_trade_performance(report),
        "risk_skip_summary": summarize_risk_skips(report),
        "session_pnl_summary": calculate_session_pnl_summary(report.trades),
    }
    if realism is not None:
        payload["balance_performance"] = summarize_balance_performance(
            report,
            realism.initial_balance,
        )
        payload["cost_summary"] = calculate_backtest_cost_summary(report.trades, realism)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def export_enhanced_backtest_summary_files(
    report: BacktestReport,
    output_dir: Path,
    realism: BacktestRealismConfig | None = None,
    mode: str | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    export_enhanced_backtest_summary_json(
        report,
        output_dir / "enhanced_backtest_summary.json",
        realism=realism,
        mode=mode,
    )
    export_backtest_risk_skip_summary_csv(
        report,
        output_dir / "backtest_risk_skip_summary.csv",
    )
    export_backtest_session_pnl_summary_csv(
        report,
        output_dir / "backtest_session_pnl_summary.csv",
    )
    if realism is None:
        return
    export_backtest_realism_summary_csv(
        report,
        output_dir / "backtest_realism_summary.csv",
        realism,
    )
    export_backtest_cost_summary_csv(
        report,
        output_dir / "backtest_cost_summary.csv",
        realism,
    )


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
    print(f"- Average volume: {average_volume:.3f} lot")


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


def _write_csv_rows(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _metrics_to_dict(metrics: BacktestMetrics) -> dict[str, object]:
    return {
        "total_trades": metrics.total_trades,
        "approved_trades": metrics.approved_trades,
        "rejected_trades": metrics.rejected_trades,
        "skipped_trades": metrics.skipped_trades,
        "win_rate": metrics.win_rate,
        "loss_rate": metrics.loss_rate,
        "profit_factor": metrics.profit_factor,
        "max_drawdown": metrics.max_drawdown,
        "average_win": metrics.average_win,
        "average_loss": metrics.average_loss,
        "average_rr": metrics.average_rr,
        "max_consecutive_losses": metrics.max_consecutive_losses,
        "net_r": metrics.net_r,
    }


def _format_reasons(reasons: tuple[str, ...]) -> str:
    return " | ".join(reasons)


def _backtest_decision_without_signal(
    config: SignalConfig,
    current_time: datetime,
    stage: str,
    approved: bool,
    reasons: tuple[str, ...],
) -> BacktestDecision:
    return BacktestDecision(
        timestamp=current_time.isoformat(),
        session=classify_session(current_time),
        symbol=config.symbol,
        timeframe=execution_timeframe_for_backtest(config),
        action=None,
        stage=stage,
        approved=approved,
        reasons=reasons,
        htf_bias=None,
        execution_trend=None,
        price_location=None,
        candle_confirmation_summary=None,
        risk_reward=None,
    )


def _signal_text_attr(signal: object, name: str) -> str | None:
    value = getattr(signal, name, None)
    return value if isinstance(value, str) and value else None


def _enhanced_exit_hits(
    candidate: BacktestCandidate,
    candle: Candle,
    target: float,
) -> tuple[bool, bool]:
    if candidate.action == SignalAction.BUY:
        return candle.low <= candidate.stop_loss, candle.high >= target
    return candle.high >= candidate.stop_loss, candle.low <= target


def _enhanced_trade_result(
    candidate: BacktestCandidate,
    entry_time: str,
    exit_time: str,
    target: float,
    result: str,
    r_multiple: float,
    loss_reason: str | None,
) -> BacktestTradeResult:
    return BacktestTradeResult(
        action=candidate.action,
        session=candidate.decision.session,
        entry_time=entry_time,
        exit_time=exit_time,
        entry=candidate.entry,
        stop_loss=candidate.stop_loss,
        tp1=candidate.tp1,
        tp2=target,
        result=result,
        r_multiple=r_multiple,
        risk_reward=candidate.risk_reward,
        volume=None,
        pnl=None,
        balance_after=None,
        loss_reason=loss_reason,
        reject_reasons_before_entry=candidate.decision.reasons,
    )


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _profit_factor(gross_win: float, gross_loss: float) -> float:
    if gross_loss == 0:
        return gross_win if gross_win > 0 else 0.0
    return gross_win / gross_loss


def _max_enhanced_drawdown(trades: list[BacktestTradeResult]) -> float:
    balances = [trade.balance_after for trade in trades if trade.balance_after is not None]
    if balances:
        peak = balances[0]
        max_drawdown = 0.0
        for balance in balances:
            peak = max(peak, balance)
            max_drawdown = max(max_drawdown, peak - balance)
        return max_drawdown

    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for trade in trades:
        equity += trade.r_multiple
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return max_drawdown


def _max_consecutive_losses(trades: list[BacktestTradeResult]) -> int:
    max_losses = 0
    current_losses = 0
    for trade in trades:
        if trade.result.startswith("LOSS"):
            current_losses += 1
            max_losses = max(max_losses, current_losses)
        elif trade.result == "WIN":
            current_losses = 0
    return max_losses


def _is_reject_decision(decision: BacktestDecision) -> bool:
    if decision.approved:
        return False
    return decision.stage not in {"skip", "skipped", "insufficient_candles"}


def _is_skip_decision(decision: BacktestDecision) -> bool:
    if decision.approved:
        return False
    return decision.stage in {"skip", "skipped", "insufficient_candles", "risk_skip"}


def _summarize_decision_reasons(decisions: Iterable[BacktestDecision]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for decision in decisions:
        reasons = decision.reasons or ("unspecified",)
        for reason in reasons:
            summary[reason] = summary.get(reason, 0) + 1
    return summary


if __name__ == "__main__":
    sys.exit(main())
