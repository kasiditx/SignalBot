from __future__ import annotations

import unittest

from trading_signal_bot.candle_confirmation import CandleConfirmationResult
from trading_signal_bot.execution_policy import (
    ExecutionPolicyLimits,
    MarketConditionSnapshot,
    TradeExecutionCandidate,
    evaluate_execution_policy,
)
from trading_signal_bot.models import SignalAction
from trading_signal_bot.zone_detector import MID_ZONE, OUTSIDE, PriceLocationResult


class ExecutionPolicyTest(unittest.TestCase):
    def test_rejects_live_mode(self) -> None:
        decision = evaluate_execution_policy(_candidate(mode="live"), _market(), _limits())

        self.assertRejected(decision.reasons, "Only paper/demo mode is allowed; live mode is rejected")

    def test_rejects_missing_entry(self) -> None:
        decision = evaluate_execution_policy(_candidate(entry=None), _market(), _limits())

        self.assertRejected(decision.reasons, "Entry price is required")

    def test_rejects_missing_stop_loss(self) -> None:
        decision = evaluate_execution_policy(_candidate(stop_loss=None), _market(), _limits())

        self.assertRejected(decision.reasons, "Stop loss is required")

    def test_rejects_missing_tp1(self) -> None:
        decision = evaluate_execution_policy(_candidate(tp1=None), _market(), _limits())

        self.assertRejected(decision.reasons, "TP1 is required")

    def test_rejects_missing_tp2(self) -> None:
        decision = evaluate_execution_policy(_candidate(tp2=None), _market(), _limits())

        self.assertRejected(decision.reasons, "TP2 is required")

    def test_rejects_open_execution_candle(self) -> None:
        decision = evaluate_execution_policy(_candidate(candle_closed=False), _market(), _limits())

        self.assertRejected(decision.reasons, "Execution candle must be closed")

    def test_rejects_mid_zone_location(self) -> None:
        decision = evaluate_execution_policy(
            _candidate(price_location=PriceLocationResult(MID_ZONE, None, None, True)),
            _market(),
            _limits(),
        )

        self.assertRejected(decision.reasons, "Price is in the middle of the zone")

    def test_rejects_buy_chased_above_entry(self) -> None:
        decision = evaluate_execution_policy(
            _candidate(action=SignalAction.BUY, entry=100.0),
            _market(current_price=100.6),
            _limits(max_entry_deviation=0.5),
        )

        self.assertRejected(decision.reasons, "Current price moved too far from planned entry")

    def test_rejects_sell_chased_below_entry(self) -> None:
        decision = evaluate_execution_policy(
            _candidate(action=SignalAction.SELL, entry=100.0),
            _market(current_price=99.4),
            _limits(max_entry_deviation=0.5),
        )

        self.assertRejected(decision.reasons, "Current price moved too far from planned entry")

    def test_rejects_high_spread(self) -> None:
        decision = evaluate_execution_policy(_candidate(), _market(spread_points=501), _limits(max_spread_points=500))

        self.assertRejected(decision.reasons, "Spread is above maximum allowed")

    def test_rejects_disallowed_session(self) -> None:
        decision = evaluate_execution_policy(_candidate(), _market(session="Asia"), _limits())

        self.assertRejected(decision.reasons, "Current session is not allowed")

    def test_rejects_high_impact_news_when_filter_enabled(self) -> None:
        decision = evaluate_execution_policy(
            _candidate(),
            _market(high_impact_news_nearby=True),
            _limits(enable_news_filter=True),
        )

        self.assertRejected(decision.reasons, "High-impact news is nearby")

    def test_rejects_abnormal_atr(self) -> None:
        decision = evaluate_execution_policy(
            _candidate(),
            _market(atr_value=3.0, average_atr=1.0),
            _limits(abnormal_atr_multiplier=2.5),
        )

        self.assertRejected(decision.reasons, "ATR is abnormally high")

    def test_rejects_fakeout(self) -> None:
        decision = evaluate_execution_policy(
            _candidate(candle_confirmation=_confirmation(fakeout=True)),
            _market(),
            _limits(),
        )

        self.assertRejected(decision.reasons, "Candle shows wick fakeout instead of confirmed body close")

    def test_rejects_breakout_candidate_without_body_breakout(self) -> None:
        decision = evaluate_execution_policy(
            _candidate(candle_confirmation=_confirmation(body_breakout=False, summary="breakout candidate")),
            _market(),
            _limits(),
        )

        self.assertRejected(decision.reasons, "Breakout candidate requires body close confirmation")

    def test_rejects_missing_candle_confirmation(self) -> None:
        decision = evaluate_execution_policy(_candidate(candle_confirmation=_empty_confirmation()), _market(), _limits())

        self.assertRejected(decision.reasons, "No clear execution candle confirmation")

    def test_approved_case_returns_execution_plan(self) -> None:
        decision = evaluate_execution_policy(_candidate(), _market(), _limits())

        self.assertTrue(decision.approved)
        self.assertIsNotNone(decision.plan)
        self.assertEqual(decision.plan.entry, 100.0)
        self.assertEqual(decision.plan.stop_loss, 99.0)
        self.assertEqual(decision.plan.tp1, 101.0)
        self.assertEqual(decision.plan.tp2, 102.0)

    def test_break_even_trigger_uses_tp1_when_enabled(self) -> None:
        decision = evaluate_execution_policy(_candidate(), _market(), _limits(enable_break_even=True))

        self.assertEqual(decision.plan.break_even_trigger, 101.0)

    def test_break_even_trigger_is_none_when_disabled(self) -> None:
        decision = evaluate_execution_policy(_candidate(), _market(), _limits(enable_break_even=False))

        self.assertIsNone(decision.plan.break_even_trigger)

    def test_trailing_stop_flag_follows_config(self) -> None:
        decision = evaluate_execution_policy(_candidate(), _market(), _limits(enable_trailing_stop=False))

        self.assertFalse(decision.plan.trailing_stop_enabled)

    def test_partial_close_flag_follows_config(self) -> None:
        decision = evaluate_execution_policy(_candidate(), _market(), _limits(enable_partial_close=True))

        self.assertTrue(decision.plan.partial_close_enabled)

    def assertRejected(self, reasons: tuple[str, ...], expected: str) -> None:
        self.assertIn(expected, reasons)


def _candidate(
    action: SignalAction = SignalAction.BUY,
    entry: float | None = 100.0,
    stop_loss: float | None = 99.0,
    tp1: float | None = 101.0,
    tp2: float | None = 102.0,
    risk_reward: float | None = 1.5,
    candle_closed: bool = True,
    price_location: PriceLocationResult | None = None,
    candle_confirmation: CandleConfirmationResult | None = None,
    mode: str = "paper",
) -> TradeExecutionCandidate:
    return TradeExecutionCandidate(
        action=action,
        entry=entry,
        stop_loss=stop_loss,
        tp1=tp1,
        tp2=tp2,
        risk_reward=risk_reward,
        candle_closed=candle_closed,
        price_location=price_location or PriceLocationResult(OUTSIDE, None, None, False),
        candle_confirmation=candle_confirmation or _confirmation(),
        mode=mode,
    )


def _market(
    current_price: float = 100.0,
    spread_points: float | None = 100.0,
    atr_value: float | None = 1.0,
    average_atr: float | None = 1.0,
    session: str | None = "London",
    high_impact_news_nearby: bool = False,
) -> MarketConditionSnapshot:
    return MarketConditionSnapshot(
        current_price=current_price,
        spread_points=spread_points,
        atr_value=atr_value,
        average_atr=average_atr,
        session=session,
        high_impact_news_nearby=high_impact_news_nearby,
    )


def _limits(
    max_spread_points: float = 500.0,
    allowed_sessions: tuple[str, ...] = ("London", "NewYork"),
    enable_news_filter: bool = False,
    enable_break_even: bool = True,
    enable_trailing_stop: bool = True,
    enable_partial_close: bool = False,
    max_entry_deviation: float = 0.5,
    abnormal_atr_multiplier: float = 2.5,
) -> ExecutionPolicyLimits:
    return ExecutionPolicyLimits(
        max_spread_points=max_spread_points,
        allowed_sessions=allowed_sessions,
        enable_news_filter=enable_news_filter,
        enable_break_even=enable_break_even,
        enable_trailing_stop=enable_trailing_stop,
        enable_partial_close=enable_partial_close,
        max_entry_deviation=max_entry_deviation,
        abnormal_atr_multiplier=abnormal_atr_multiplier,
    )


def _confirmation(
    fakeout: bool = False,
    body_breakout: bool = True,
    summary: str = "strong close",
) -> CandleConfirmationResult:
    return CandleConfirmationResult(
        bullish_engulfing=False,
        bearish_engulfing=False,
        pin_bar=False,
        rejection_wick=False,
        strong_close=True,
        body_breakout=body_breakout,
        fakeout=fakeout,
        direction=SignalAction.BUY,
        summary=summary,
    )


def _empty_confirmation() -> CandleConfirmationResult:
    return CandleConfirmationResult(
        bullish_engulfing=False,
        bearish_engulfing=False,
        pin_bar=False,
        rejection_wick=False,
        strong_close=False,
        body_breakout=False,
        fakeout=False,
        direction=None,
        summary="No clear candle confirmation",
    )


if __name__ == "__main__":
    unittest.main()
