from __future__ import annotations

from dataclasses import dataclass

from .candle_confirmation import CandleConfirmationResult
from .models import SignalAction
from .zone_detector import MID_ZONE, PriceLocationResult


PAPER_MODES = {"paper", "demo"}


@dataclass(frozen=True)
class MarketConditionSnapshot:
    current_price: float
    spread_points: float | None
    atr_value: float | None
    average_atr: float | None
    session: str | None
    high_impact_news_nearby: bool = False


@dataclass(frozen=True)
class ExecutionPolicyLimits:
    max_spread_points: float = 500.0
    allowed_sessions: tuple[str, ...] = ("London", "NewYork")
    enable_news_filter: bool = False
    enable_break_even: bool = True
    enable_trailing_stop: bool = True
    enable_partial_close: bool = False
    max_entry_deviation: float = 0.0
    abnormal_atr_multiplier: float = 2.5


@dataclass(frozen=True)
class TradeExecutionCandidate:
    action: SignalAction
    entry: float | None
    stop_loss: float | None
    tp1: float | None
    tp2: float | None
    risk_reward: float | None
    candle_closed: bool
    price_location: PriceLocationResult
    candle_confirmation: CandleConfirmationResult
    mode: str = "paper"


@dataclass(frozen=True)
class ExecutionPlan:
    action: SignalAction
    entry: float
    stop_loss: float
    tp1: float
    tp2: float
    break_even_trigger: float | None
    trailing_stop_enabled: bool
    partial_close_enabled: bool


@dataclass(frozen=True)
class ExecutionPolicyDecision:
    approved: bool
    reasons: tuple[str, ...]
    plan: ExecutionPlan | None


def evaluate_execution_policy(
    candidate: TradeExecutionCandidate,
    market: MarketConditionSnapshot,
    limits: ExecutionPolicyLimits,
) -> ExecutionPolicyDecision:
    reasons: list[str] = []
    mode = candidate.mode.strip().lower()

    if mode not in PAPER_MODES:
        reasons.append("Only paper/demo mode is allowed; live mode is rejected")
    if candidate.entry is None:
        reasons.append("Entry price is required")
    if candidate.stop_loss is None:
        reasons.append("Stop loss is required")
    if candidate.tp1 is None:
        reasons.append("TP1 is required")
    if candidate.tp2 is None:
        reasons.append("TP2 is required")
    if not candidate.candle_closed:
        reasons.append("Execution candle must be closed")
    if candidate.price_location.location == MID_ZONE or candidate.price_location.is_mid_zone:
        reasons.append("Price is in the middle of the zone")
    if candidate.entry is not None and price_chased_too_far(
        candidate.action,
        market.current_price,
        candidate.entry,
        limits.max_entry_deviation,
    ):
        reasons.append("Current price moved too far from planned entry")
    if market.spread_points is not None and market.spread_points > limits.max_spread_points:
        reasons.append("Spread is above maximum allowed")
    if not is_session_allowed(market.session, limits.allowed_sessions):
        reasons.append("Current session is not allowed")
    if limits.enable_news_filter and market.high_impact_news_nearby:
        reasons.append("High-impact news is nearby")
    if is_abnormal_atr(market.atr_value, market.average_atr, limits.abnormal_atr_multiplier):
        reasons.append("ATR is abnormally high")
    if candidate.candle_confirmation.fakeout:
        reasons.append("Candle shows wick fakeout instead of confirmed body close")
    if _requires_body_breakout(candidate) and not candidate.candle_confirmation.body_breakout:
        reasons.append("Breakout candidate requires body close confirmation")
    if not _has_clear_candle_confirmation(candidate.candle_confirmation):
        reasons.append("No clear execution candle confirmation")

    plan = None if reasons else build_execution_plan(candidate, limits)
    return ExecutionPolicyDecision(approved=not reasons, reasons=tuple(reasons), plan=plan)


def build_execution_plan(
    candidate: TradeExecutionCandidate,
    limits: ExecutionPolicyLimits,
) -> ExecutionPlan:
    if candidate.entry is None or candidate.stop_loss is None or candidate.tp1 is None or candidate.tp2 is None:
        raise ValueError("Entry, stop loss, TP1, and TP2 are required to build an execution plan")

    return ExecutionPlan(
        action=candidate.action,
        entry=candidate.entry,
        stop_loss=candidate.stop_loss,
        tp1=candidate.tp1,
        tp2=candidate.tp2,
        break_even_trigger=candidate.tp1 if limits.enable_break_even else None,
        trailing_stop_enabled=limits.enable_trailing_stop,
        partial_close_enabled=limits.enable_partial_close,
    )


def price_chased_too_far(
    action: SignalAction,
    current_price: float,
    entry: float,
    max_entry_deviation: float,
) -> bool:
    if max_entry_deviation < 0:
        raise ValueError("max_entry_deviation must be greater than or equal to 0")
    if action == SignalAction.BUY:
        return current_price > entry + max_entry_deviation
    if action == SignalAction.SELL:
        return current_price < entry - max_entry_deviation
    return False


def is_abnormal_atr(
    atr_value: float | None,
    average_atr: float | None,
    abnormal_atr_multiplier: float,
) -> bool:
    if abnormal_atr_multiplier <= 0:
        raise ValueError("abnormal_atr_multiplier must be greater than 0")
    if atr_value is None or average_atr is None or average_atr <= 0:
        return False
    return atr_value > average_atr * abnormal_atr_multiplier


def is_session_allowed(
    session: str | None,
    allowed_sessions: tuple[str, ...],
) -> bool:
    if not allowed_sessions:
        return True
    if session is None or not session.strip():
        return False
    normalized_session = session.strip().lower()
    return normalized_session in {allowed.strip().lower() for allowed in allowed_sessions}


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


def _requires_body_breakout(candidate: TradeExecutionCandidate) -> bool:
    return "breakout" in candidate.candle_confirmation.summary.lower()
