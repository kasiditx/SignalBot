from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .dry_run_pipeline import (
    DryRunMarketInput,
    DryRunPipelineConfig,
    DryRunPipelineResult,
    DryRunTradeInput,
    run_dry_run_pipeline,
)
from .execution_policy import ExecutionPolicyLimits
from .journal import JournalWriterConfig
from .models import AutoTradeConfig, Candle, SignalAction, SignalConfig
from .risk_manager import DailyRiskState, PositionSizingInput, RiskLimits


JOURNAL_CSV_PATH = Path("logs/audit_journal.csv")
JOURNAL_JSONL_PATH = Path("logs/audit_journal.jsonl")
NO_ORDER_MESSAGE = "No order was sent. No MT5 order intent was written."


@dataclass(frozen=True)
class DryRunAdapterInput:
    action: SignalAction | None
    entry: float | None
    stop_loss: float | None
    tp1: float | None
    tp2: float | None
    risk_reward: float | None
    mode: str = "paper"


@dataclass(frozen=True)
class DryRunAdapterResult:
    pipeline_result: DryRunPipelineResult
    message: str


def build_pipeline_config(signal_config: SignalConfig) -> DryRunPipelineConfig:
    return DryRunPipelineConfig(
        execution_timeframe=signal_config.execution_timeframe,
        momentum_timeframe=signal_config.momentum_timeframe,
        zone_timeframes=signal_config.zone_timeframes,
        htf_timeframes=signal_config.htf_timeframes,
        minimum_risk_reward=signal_config.risk_reward,
    )


def build_execution_limits(signal_config: SignalConfig) -> ExecutionPolicyLimits:
    policy_config = signal_config.execution_policy_config
    return ExecutionPolicyLimits(
        max_spread_points=policy_config.max_spread_points,
        allowed_sessions=policy_config.allowed_sessions,
        enable_news_filter=policy_config.enable_news_filter,
        enable_break_even=policy_config.enable_break_even,
        enable_trailing_stop=policy_config.enable_trailing_stop,
        enable_partial_close=policy_config.enable_partial_close,
        max_entry_deviation=0.0,
        abnormal_atr_multiplier=2.5,
    )


def build_risk_limits(signal_config: SignalConfig) -> RiskLimits:
    risk_config = signal_config.risk_config
    return RiskLimits(
        risk_per_trade=risk_config.risk_per_trade,
        max_daily_loss=risk_config.max_daily_loss,
        max_trades_per_day=risk_config.max_trades_per_day,
        max_consecutive_losses=risk_config.max_consecutive_losses,
        cooldown_minutes=risk_config.cooldown_minutes,
        minimum_risk_reward=signal_config.risk_reward,
    )


def build_position_sizing_input(
    auto_trade_config: AutoTradeConfig,
    adapter_input: DryRunAdapterInput,
) -> PositionSizingInput:
    return _build_position_sizing_input(
        auto_trade_config=auto_trade_config,
        adapter_input=adapter_input,
        risk_percent=auto_trade_config.risk_percent,
    )


def build_daily_risk_state(today: date | None = None) -> DailyRiskState:
    risk_date = today or date.today()
    return DailyRiskState(
        date=risk_date.isoformat(),
        trades_today=0,
        losses_today=0,
        consecutive_losses=0,
        realized_loss_percent=0.0,
        open_directions=(),
    )


def build_journal_config() -> JournalWriterConfig:
    return JournalWriterConfig(
        csv_path=JOURNAL_CSV_PATH,
        jsonl_path=JOURNAL_JSONL_PATH,
        write_csv=True,
        write_jsonl=True,
    )


def run_pipeline_from_configs(
    candles_by_timeframe: dict[str, list[Candle]],
    signal_config: SignalConfig,
    auto_trade_config: AutoTradeConfig,
    adapter_input: DryRunAdapterInput,
    market_input: DryRunMarketInput,
) -> DryRunAdapterResult:
    pipeline_result = run_dry_run_pipeline(
        candles_by_timeframe=candles_by_timeframe,
        trade_input=DryRunTradeInput(
            symbol=signal_config.symbol,
            action=adapter_input.action,
            mode=adapter_input.mode,
            entry=adapter_input.entry,
            stop_loss=adapter_input.stop_loss,
            tp1=adapter_input.tp1,
            tp2=adapter_input.tp2,
            risk_reward=adapter_input.risk_reward,
            timeframe=signal_config.execution_timeframe,
        ),
        market_input=market_input,
        execution_limits=build_execution_limits(signal_config),
        risk_limits=build_risk_limits(signal_config),
        risk_state=build_daily_risk_state(),
        sizing=_build_position_sizing_input(
            auto_trade_config=auto_trade_config,
            adapter_input=adapter_input,
            risk_percent=min(
                auto_trade_config.risk_percent,
                signal_config.risk_config.risk_per_trade,
            ),
        ),
        journal_config=build_journal_config(),
        pipeline_config=build_pipeline_config(signal_config),
    )
    return DryRunAdapterResult(
        pipeline_result=pipeline_result,
        message=_build_adapter_message(adapter_input.mode, pipeline_result),
    )


def _build_position_sizing_input(
    auto_trade_config: AutoTradeConfig,
    adapter_input: DryRunAdapterInput,
    risk_percent: float,
) -> PositionSizingInput:
    if adapter_input.entry is None:
        raise ValueError("Entry price is required to build position sizing input")
    if adapter_input.stop_loss is None:
        raise ValueError("Stop loss is required to build position sizing input")

    return PositionSizingInput(
        balance=auto_trade_config.account_balance,
        risk_percent=risk_percent,
        entry=adapter_input.entry,
        stop_loss=adapter_input.stop_loss,
        contract_size=auto_trade_config.contract_size,
        min_volume=auto_trade_config.min_volume,
        max_volume=auto_trade_config.max_volume,
        volume_step=auto_trade_config.volume_step,
        allow_min_volume=auto_trade_config.allow_min_volume,
    )


def _build_adapter_message(
    mode: str,
    pipeline_result: DryRunPipelineResult,
) -> str:
    safety_message = NO_ORDER_MESSAGE
    if mode.strip().lower() == "live":
        safety_message = f"Live mode is not allowed for dry-run adapter. {safety_message}"
    status = "approved" if pipeline_result.approved else f"rejected at {pipeline_result.stage}"
    return f"Dry-run pipeline {status}. {safety_message}"
