from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .candle_confirmation import CandleConfirmationResult, analyze_candle_confirmation
from .execution_policy import (
    ExecutionPlan,
    ExecutionPolicyDecision,
    ExecutionPolicyLimits,
    MarketConditionSnapshot,
    TradeExecutionCandidate,
    evaluate_execution_policy,
)
from .journal import (
    EVENT_ERROR,
    EVENT_EXECUTION_PLAN_APPROVED,
    EVENT_EXECUTION_POLICY_REJECT,
    EVENT_NO_TRADE,
    EVENT_PAPER_ORDER_INTENT,
    EVENT_RISK_MANAGER_REJECT,
    EVENT_SIGNAL_GENERATED,
    JournalWriteResult,
    JournalWriterConfig,
    create_journal_event,
    write_journal_event,
)
from .market_structure import MarketStructureResult, analyze_market_structure
from .models import Candle, SignalAction, TrendDirection
from .no_trade_filter import NoTradeDecision, TradeCandidateContext, evaluate_no_trade
from .risk_manager import (
    DailyRiskState,
    PositionSizingInput,
    RiskCheckInput,
    RiskDecision,
    RiskLimits,
    evaluate_risk,
)
from .zone_detector import (
    PriceZone,
    PriceLocationResult,
    SupportResistance,
    classify_price_location,
    detect_demand_zones,
    detect_supply_zones,
    nearest_levels,
)


PAPER_MODES = {"paper", "demo"}


@dataclass(frozen=True)
class DryRunTradeInput:
    symbol: str
    action: SignalAction | None
    mode: str
    entry: float | None = None
    stop_loss: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    risk_reward: float | None = None
    candle_closed: bool = True
    timeframe: str = "M1"


@dataclass(frozen=True)
class DryRunMarketInput:
    current_price: float
    spread_points: float | None
    atr_value: float | None
    average_atr: float | None
    session: str | None
    high_impact_news_nearby: bool = False


@dataclass(frozen=True)
class DryRunPipelineConfig:
    execution_timeframe: str = "M1"
    momentum_timeframe: str = "M5"
    zone_timeframes: tuple[str, ...] = ("M30", "M15")
    htf_timeframes: tuple[str, ...] = ("H4", "H1")
    minimum_risk_reward: float = 1.5
    structure_lookback: int = 2
    min_swings: int = 4
    zone_lookback: int = 80
    zone_impulse_ratio: float = 1.5
    zone_proximity: float = 0.0


@dataclass(frozen=True)
class DryRunPipelineResult:
    approved: bool
    stage: str
    reasons: tuple[str, ...]
    execution_plan: ExecutionPlan | None
    risk_decision: RiskDecision | None
    journal_results: tuple[JournalWriteResult, ...]


def run_dry_run_pipeline(
    candles_by_timeframe: dict[str, list[Candle]],
    trade_input: DryRunTradeInput,
    market_input: DryRunMarketInput,
    execution_limits: ExecutionPolicyLimits,
    risk_limits: RiskLimits,
    risk_state: DailyRiskState,
    sizing: PositionSizingInput,
    journal_config: JournalWriterConfig,
    pipeline_config: DryRunPipelineConfig = DryRunPipelineConfig(),
) -> DryRunPipelineResult:
    journal_results: list[JournalWriteResult] = []
    mode = trade_input.mode.strip().lower()

    if mode not in PAPER_MODES:
        reasons = ("Only paper/demo mode is allowed; live mode is rejected",)
        journal_results.append(
            write_pipeline_event(
                EVENT_ERROR,
                journal_config,
                trade_input=trade_input,
                approved=False,
                reasons=reasons,
                error_message=reasons[0],
                metadata={"stage": "mode_validation"},
            )
        )
        return _result(False, "mode_validation", reasons, None, None, journal_results)

    execution_candles = candles_by_timeframe.get(pipeline_config.execution_timeframe)
    if not execution_candles:
        reasons = (f"No candles for execution timeframe {pipeline_config.execution_timeframe}",)
        journal_results.append(
            write_pipeline_event(
                EVENT_ERROR,
                journal_config,
                trade_input=trade_input,
                approved=False,
                reasons=reasons,
                error_message=reasons[0],
                metadata={"stage": "market_data"},
            )
        )
        return _result(False, "market_data", reasons, None, None, journal_results)

    latest_execution = execution_candles[-1]
    htf_structure = _analyze_first_available_structure(
        candles_by_timeframe,
        pipeline_config.htf_timeframes,
        pipeline_config,
    )
    execution_structure = analyze_market_structure(
        execution_candles,
        lookback=pipeline_config.structure_lookback,
        min_swings=pipeline_config.min_swings,
    )
    momentum_structure = _analyze_optional_structure(
        candles_by_timeframe.get(pipeline_config.momentum_timeframe),
        pipeline_config,
    )
    support_resistance = _combined_support_resistance(candles_by_timeframe, pipeline_config)
    zones = _combined_zones(candles_by_timeframe, pipeline_config)
    proximity = _zone_proximity(pipeline_config, market_input)
    price_location = classify_price_location(
        price=market_input.current_price,
        zones=zones,
        support_resistance=support_resistance,
        proximity=proximity,
    )
    candle_confirmation = analyze_candle_confirmation(
        execution_candles,
        support=support_resistance.support,
        resistance=support_resistance.resistance,
        atr_value=market_input.atr_value,
    )

    journal_results.append(
        write_pipeline_event(
            EVENT_SIGNAL_GENERATED,
            journal_config,
            trade_input=trade_input,
            htf_bias=htf_structure.trend.value,
            execution_trend=execution_structure.trend.value,
            structure_label=execution_structure.structure_label,
            price_location=price_location.location,
            candle_confirmation_summary=candle_confirmation.summary,
            entry=trade_input.entry if trade_input.entry is not None else latest_execution.close,
            stop_loss=trade_input.stop_loss,
            tp1=trade_input.tp1,
            tp2=trade_input.tp2,
            risk_reward=trade_input.risk_reward,
            approved=None,
            metadata={
                "stage": "analysis",
                "momentum_trend": momentum_structure.trend.value if momentum_structure else None,
                "supply_demand_zones": len(zones),
            },
        )
    )

    candidate_context = build_trade_candidate_context(
        trade_input=trade_input,
        structure=execution_structure,
        price_location=price_location,
        candle_confirmation=candle_confirmation,
        htf_trend=htf_structure.trend,
        execution_trend=execution_structure.trend,
    )
    no_trade_decision = evaluate_no_trade(candidate_context, minimum_risk_reward=pipeline_config.minimum_risk_reward)
    if not no_trade_decision.should_trade:
        journal_results.append(
            write_pipeline_event(
                EVENT_NO_TRADE,
                journal_config,
                trade_input=trade_input,
                htf_bias=htf_structure.trend.value,
                execution_trend=execution_structure.trend.value,
                structure_label=execution_structure.structure_label,
                price_location=price_location.location,
                candle_confirmation_summary=candle_confirmation.summary,
                entry=trade_input.entry,
                stop_loss=trade_input.stop_loss,
                tp1=trade_input.tp1,
                tp2=trade_input.tp2,
                risk_reward=trade_input.risk_reward,
                approved=False,
                reasons=no_trade_decision.reasons,
                metadata={"stage": "no_trade_filter"},
            )
        )
        reasons = _with_journal_errors(no_trade_decision.reasons, journal_results)
        return _result(False, "no_trade_filter", reasons, None, None, journal_results)

    execution_candidate = build_execution_candidate(
        trade_input=trade_input,
        price_location=price_location,
        candle_confirmation=candle_confirmation,
    )
    execution_decision = evaluate_execution_policy(
        execution_candidate,
        MarketConditionSnapshot(
            current_price=market_input.current_price,
            spread_points=market_input.spread_points,
            atr_value=market_input.atr_value,
            average_atr=market_input.average_atr,
            session=market_input.session,
            high_impact_news_nearby=market_input.high_impact_news_nearby,
        ),
        execution_limits,
    )
    if not execution_decision.approved:
        journal_results.append(
            _execution_policy_event(
                execution_decision,
                journal_config,
                trade_input,
                htf_structure,
                execution_structure,
                price_location,
                candle_confirmation,
            )
        )
        reasons = _with_journal_errors(execution_decision.reasons, journal_results)
        return _result(False, "execution_policy", reasons, None, None, journal_results)

    risk_decision = evaluate_risk(
        RiskCheckInput(
            action=execution_candidate.action,
            entry=execution_candidate.entry,
            stop_loss=execution_candidate.stop_loss,
            take_profit=execution_candidate.tp2,
            risk_reward=execution_candidate.risk_reward,
            sizing=sizing,
            limits=risk_limits,
            state=risk_state,
            now=_journal_now(),
            mode=trade_input.mode,
        )
    )
    if not risk_decision.approved:
        journal_results.append(
            write_pipeline_event(
                EVENT_RISK_MANAGER_REJECT,
                journal_config,
                trade_input=trade_input,
                htf_bias=htf_structure.trend.value,
                execution_trend=execution_structure.trend.value,
                structure_label=execution_structure.structure_label,
                price_location=price_location.location,
                candle_confirmation_summary=candle_confirmation.summary,
                entry=execution_candidate.entry,
                stop_loss=execution_candidate.stop_loss,
                tp1=execution_candidate.tp1,
                tp2=execution_candidate.tp2,
                risk_reward=execution_candidate.risk_reward,
                volume=risk_decision.volume,
                approved=False,
                reasons=risk_decision.reasons,
                metadata={"stage": "risk_manager"},
            )
        )
        reasons = _with_journal_errors(risk_decision.reasons, journal_results)
        return _result(False, "risk_manager", reasons, execution_decision.plan, risk_decision, journal_results)

    journal_results.append(
        write_pipeline_event(
            EVENT_EXECUTION_PLAN_APPROVED,
            journal_config,
            trade_input=trade_input,
            htf_bias=htf_structure.trend.value,
            execution_trend=execution_structure.trend.value,
            structure_label=execution_structure.structure_label,
            price_location=price_location.location,
            candle_confirmation_summary=candle_confirmation.summary,
            entry=execution_decision.plan.entry if execution_decision.plan else None,
            stop_loss=execution_decision.plan.stop_loss if execution_decision.plan else None,
            tp1=execution_decision.plan.tp1 if execution_decision.plan else None,
            tp2=execution_decision.plan.tp2 if execution_decision.plan else None,
            risk_reward=execution_candidate.risk_reward,
            volume=risk_decision.volume,
            approved=True,
            metadata={"stage": "execution_policy"},
        )
    )
    journal_results.append(
        write_pipeline_event(
            EVENT_PAPER_ORDER_INTENT,
            journal_config,
            trade_input=trade_input,
            entry=execution_decision.plan.entry if execution_decision.plan else None,
            stop_loss=execution_decision.plan.stop_loss if execution_decision.plan else None,
            tp1=execution_decision.plan.tp1 if execution_decision.plan else None,
            tp2=execution_decision.plan.tp2 if execution_decision.plan else None,
            risk_reward=execution_candidate.risk_reward,
            volume=risk_decision.volume,
            approved=True,
            metadata={"stage": "dry_run_only", "order_sent": False, "order_intent_written": False},
        )
    )

    reasons = _with_journal_errors((), journal_results)
    return _result(
        approved=not reasons,
        stage="approved" if not reasons else "journal",
        reasons=reasons,
        execution_plan=execution_decision.plan,
        risk_decision=risk_decision,
        journal_results=journal_results,
    )


def build_trade_candidate_context(
    trade_input: DryRunTradeInput,
    structure: MarketStructureResult,
    price_location: PriceLocationResult,
    candle_confirmation: CandleConfirmationResult,
    htf_trend: TrendDirection,
    execution_trend: TrendDirection,
) -> TradeCandidateContext:
    return TradeCandidateContext(
        action=trade_input.action,
        structure=structure,
        price_location=price_location,
        candle_confirmation=candle_confirmation,
        risk_reward=trade_input.risk_reward,
        htf_trend=htf_trend,
        execution_trend=execution_trend,
    )


def build_execution_candidate(
    trade_input: DryRunTradeInput,
    price_location: PriceLocationResult,
    candle_confirmation: CandleConfirmationResult,
) -> TradeExecutionCandidate:
    if trade_input.action is None:
        raise ValueError("Trade action is required to build an execution candidate")
    return TradeExecutionCandidate(
        action=trade_input.action,
        entry=trade_input.entry,
        stop_loss=trade_input.stop_loss,
        tp1=trade_input.tp1,
        tp2=trade_input.tp2,
        risk_reward=trade_input.risk_reward,
        candle_closed=trade_input.candle_closed,
        price_location=price_location,
        candle_confirmation=candle_confirmation,
        mode=trade_input.mode,
    )


def write_pipeline_event(
    event_type: str,
    journal_config: JournalWriterConfig,
    *,
    trade_input: DryRunTradeInput,
    htf_bias: str | None = None,
    execution_trend: str | None = None,
    structure_label: str | None = None,
    price_location: str | None = None,
    candle_confirmation_summary: str | None = None,
    entry: float | None = None,
    stop_loss: float | None = None,
    tp1: float | None = None,
    tp2: float | None = None,
    risk_reward: float | None = None,
    volume: float | None = None,
    approved: bool | None = None,
    reasons: tuple[str, ...] = (),
    error_message: str | None = None,
    metadata: dict[str, object] | None = None,
) -> JournalWriteResult:
    event = create_journal_event(
        event_type,
        symbol=trade_input.symbol,
        timeframe=trade_input.timeframe,
        action=trade_input.action.value if trade_input.action else None,
        mode=trade_input.mode,
        htf_bias=htf_bias,
        execution_trend=execution_trend,
        structure_label=structure_label,
        price_location=price_location,
        candle_confirmation_summary=candle_confirmation_summary,
        entry=entry,
        stop_loss=stop_loss,
        tp1=tp1,
        tp2=tp2,
        risk_reward=risk_reward,
        volume=volume,
        approved=approved,
        reasons=reasons,
        error_message=error_message,
        metadata=metadata,
    )
    return write_journal_event(event, journal_config)


def _analyze_first_available_structure(
    candles_by_timeframe: dict[str, list[Candle]],
    timeframes: tuple[str, ...],
    config: DryRunPipelineConfig,
) -> MarketStructureResult:
    for timeframe in timeframes:
        candles = candles_by_timeframe.get(timeframe)
        if candles:
            return analyze_market_structure(candles, lookback=config.structure_lookback, min_swings=config.min_swings)
    return analyze_market_structure([], lookback=config.structure_lookback, min_swings=config.min_swings)


def _analyze_optional_structure(
    candles: list[Candle] | None,
    config: DryRunPipelineConfig,
) -> MarketStructureResult | None:
    if not candles:
        return None
    return analyze_market_structure(candles, lookback=config.structure_lookback, min_swings=config.min_swings)


def _combined_zones(
    candles_by_timeframe: dict[str, list[Candle]],
    config: DryRunPipelineConfig,
) -> list[PriceZone]:
    zones: list[PriceZone] = []
    for timeframe in config.zone_timeframes:
        candles = candles_by_timeframe.get(timeframe)
        if not candles:
            continue
        zones.extend(
            detect_supply_zones(
                candles,
                lookback=config.zone_lookback,
                impulse_ratio=config.zone_impulse_ratio,
                timeframe=timeframe,
            )
        )
        zones.extend(
            detect_demand_zones(
                candles,
                lookback=config.zone_lookback,
                impulse_ratio=config.zone_impulse_ratio,
                timeframe=timeframe,
            )
        )
    return zones


def _combined_support_resistance(
    candles_by_timeframe: dict[str, list[Candle]],
    config: DryRunPipelineConfig,
) -> SupportResistance:
    supports = []
    resistances = []
    for timeframe in config.zone_timeframes:
        candles = candles_by_timeframe.get(timeframe)
        if not candles:
            continue
        levels = nearest_levels(candles, lookback=min(config.zone_lookback, len(candles)))
        if levels.support is not None:
            supports.append(levels.support)
        if levels.resistance is not None:
            resistances.append(levels.resistance)
    return SupportResistance(
        support=max(supports) if supports else None,
        resistance=min(resistances) if resistances else None,
    )


def _zone_proximity(config: DryRunPipelineConfig, market_input: DryRunMarketInput) -> float:
    if config.zone_proximity > 0:
        return config.zone_proximity
    if market_input.atr_value is not None and market_input.atr_value > 0:
        return market_input.atr_value * 0.25
    return 0.0


def _execution_policy_event(
    execution_decision: ExecutionPolicyDecision,
    journal_config: JournalWriterConfig,
    trade_input: DryRunTradeInput,
    htf_structure: MarketStructureResult,
    execution_structure: MarketStructureResult,
    price_location: PriceLocationResult,
    candle_confirmation: CandleConfirmationResult,
) -> JournalWriteResult:
    return write_pipeline_event(
        EVENT_EXECUTION_POLICY_REJECT,
        journal_config,
        trade_input=trade_input,
        htf_bias=htf_structure.trend.value,
        execution_trend=execution_structure.trend.value,
        structure_label=execution_structure.structure_label,
        price_location=price_location.location,
        candle_confirmation_summary=candle_confirmation.summary,
        entry=trade_input.entry,
        stop_loss=trade_input.stop_loss,
        tp1=trade_input.tp1,
        tp2=trade_input.tp2,
        risk_reward=trade_input.risk_reward,
        approved=False,
        reasons=execution_decision.reasons,
        metadata={"stage": "execution_policy"},
    )


def _with_journal_errors(
    reasons: tuple[str, ...],
    journal_results: list[JournalWriteResult],
) -> tuple[str, ...]:
    journal_errors = tuple(
        f"Journal write failed: {result.error_message}"
        for result in journal_results
        if not result.success and result.error_message
    )
    return (*reasons, *journal_errors)


def _result(
    approved: bool,
    stage: str,
    reasons: tuple[str, ...],
    execution_plan: ExecutionPlan | None,
    risk_decision: RiskDecision | None,
    journal_results: list[JournalWriteResult],
) -> DryRunPipelineResult:
    return DryRunPipelineResult(
        approved=approved,
        stage=stage,
        reasons=reasons,
        execution_plan=execution_plan,
        risk_decision=risk_decision,
        journal_results=tuple(journal_results),
    )


def _journal_now() -> datetime:
    return datetime.now(tz=UTC)
