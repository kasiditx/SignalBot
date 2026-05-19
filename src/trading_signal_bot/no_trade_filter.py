from __future__ import annotations

from dataclasses import dataclass

from .candle_confirmation import CandleConfirmationResult
from .market_structure import MarketStructureResult
from .models import SignalAction, TrendDirection
from .zone_detector import MID_ZONE, NEAR_DEMAND, NEAR_SUPPLY, PriceLocationResult


@dataclass(frozen=True)
class TradeCandidateContext:
    action: SignalAction | None
    structure: MarketStructureResult
    price_location: PriceLocationResult
    candle_confirmation: CandleConfirmationResult
    risk_reward: float | None
    htf_trend: TrendDirection
    execution_trend: TrendDirection


@dataclass(frozen=True)
class NoTradeDecision:
    should_trade: bool
    reasons: tuple[str, ...]


def evaluate_no_trade(
    candidate: TradeCandidateContext,
    minimum_risk_reward: float = 1.5,
) -> NoTradeDecision:
    if minimum_risk_reward <= 0:
        raise ValueError("minimum_risk_reward must be greater than 0")

    reasons: list[str] = []
    action = candidate.action

    if action is None:
        reasons.append("No trade action candidate")
    if candidate.price_location.location == MID_ZONE or candidate.price_location.is_mid_zone:
        reasons.append("Price is in the middle of the zone")
    if not _has_clear_candle_confirmation(candidate.candle_confirmation):
        reasons.append("No clear candle confirmation")
    if candidate.candle_confirmation.fakeout:
        reasons.append("Candle shows wick fakeout instead of confirmed body close")
    if candidate.risk_reward is None:
        reasons.append("Risk/reward is missing")
    elif candidate.risk_reward < minimum_risk_reward:
        reasons.append(f"Risk/reward is below minimum 1:{minimum_risk_reward:.2f}")
    if candidate.structure.trend == TrendDirection.SIDEWAYS:
        reasons.append("Market structure is sideway or unclear")

    if action is not None:
        if _trend_conflicts(action, candidate.htf_trend):
            reasons.append("HTF trend conflicts with trade direction")
        if _trend_conflicts(action, candidate.execution_trend):
            reasons.append("Execution trend conflicts with trade direction")
        if _location_conflicts_without_breakout(action, candidate):
            reasons.append("Price location conflicts with action without body breakout confirmation")

    return NoTradeDecision(should_trade=not reasons, reasons=tuple(reasons))


def _has_clear_candle_confirmation(confirmation: CandleConfirmationResult) -> bool:
    return any(
        (
            confirmation.bullish_engulfing,
            confirmation.bearish_engulfing,
            confirmation.pin_bar,
            confirmation.rejection_wick,
            confirmation.strong_close,
            confirmation.body_breakout,
        )
    )


def _trend_conflicts(action: SignalAction, trend: TrendDirection) -> bool:
    if action == SignalAction.BUY:
        return trend == TrendDirection.BEARISH
    if action == SignalAction.SELL:
        return trend == TrendDirection.BULLISH
    return False


def _location_conflicts_without_breakout(
    action: SignalAction,
    candidate: TradeCandidateContext,
) -> bool:
    location = candidate.price_location.location
    confirmation = candidate.candle_confirmation
    if action == SignalAction.BUY and location == NEAR_SUPPLY:
        return not confirmation.body_breakout
    if action == SignalAction.SELL and location == NEAR_DEMAND:
        return not confirmation.body_breakout
    return False
