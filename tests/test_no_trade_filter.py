from __future__ import annotations

import unittest

from trading_signal_bot.candle_confirmation import CandleConfirmationResult
from trading_signal_bot.market_structure import InvalidationLevels, MarketStructureResult
from trading_signal_bot.models import SignalAction, TrendDirection
from trading_signal_bot.no_trade_filter import NoTradeDecision, TradeCandidateContext, evaluate_no_trade
from trading_signal_bot.zone_detector import MID_ZONE, NEAR_DEMAND, NEAR_SUPPLY, OUTSIDE, PriceLocationResult


class NoTradeFilterTest(unittest.TestCase):
    def test_rejects_missing_action(self) -> None:
        decision = evaluate_no_trade(_candidate(action=None))

        self.assertRejected(decision, "No trade action candidate")

    def test_rejects_missing_risk_reward(self) -> None:
        decision = evaluate_no_trade(_candidate(risk_reward=None))

        self.assertRejected(decision, "Risk/reward is missing")

    def test_rejects_low_risk_reward(self) -> None:
        decision = evaluate_no_trade(_candidate(risk_reward=1.2))

        self.assertFalse(decision.should_trade)
        self.assertTrue(any("below minimum" in reason for reason in decision.reasons))

    def test_rejects_mid_zone(self) -> None:
        decision = evaluate_no_trade(_candidate(location=MID_ZONE, is_mid_zone=True))

        self.assertRejected(decision, "Price is in the middle of the zone")

    def test_rejects_fakeout(self) -> None:
        decision = evaluate_no_trade(_candidate(candle_confirmation=_confirmation(fakeout=True)))

        self.assertRejected(decision, "Candle shows wick fakeout instead of confirmed body close")

    def test_rejects_sideways_structure(self) -> None:
        decision = evaluate_no_trade(_candidate(structure_trend=TrendDirection.SIDEWAYS))

        self.assertRejected(decision, "Market structure is sideway or unclear")

    def test_rejects_buy_against_bearish_htf(self) -> None:
        decision = evaluate_no_trade(_candidate(action=SignalAction.BUY, htf_trend=TrendDirection.BEARISH))

        self.assertRejected(decision, "HTF trend conflicts with trade direction")

    def test_rejects_sell_against_bullish_htf(self) -> None:
        decision = evaluate_no_trade(_candidate(action=SignalAction.SELL, htf_trend=TrendDirection.BULLISH))

        self.assertRejected(decision, "HTF trend conflicts with trade direction")

    def test_rejects_buy_near_supply_without_body_breakout(self) -> None:
        decision = evaluate_no_trade(
            _candidate(
                action=SignalAction.BUY,
                location=NEAR_SUPPLY,
                candle_confirmation=_confirmation(body_breakout=False, strong_close=True),
            )
        )

        self.assertRejected(decision, "Price location conflicts with action without body breakout confirmation")

    def test_rejects_sell_near_demand_without_body_breakout(self) -> None:
        decision = evaluate_no_trade(
            _candidate(
                action=SignalAction.SELL,
                location=NEAR_DEMAND,
                candle_confirmation=_confirmation(body_breakout=False, strong_close=True, direction=SignalAction.SELL),
                htf_trend=TrendDirection.BEARISH,
                execution_trend=TrendDirection.BEARISH,
                structure_trend=TrendDirection.BEARISH,
            )
        )

        self.assertRejected(decision, "Price location conflicts with action without body breakout confirmation")

    def test_all_conditions_aligned_allows_candidate(self) -> None:
        decision = evaluate_no_trade(_candidate())

        self.assertTrue(decision.should_trade)
        self.assertEqual(decision.reasons, ())

    def assertRejected(self, decision: NoTradeDecision, reason: str) -> None:
        self.assertFalse(decision.should_trade)
        self.assertIn(reason, decision.reasons)


def _candidate(
    action: SignalAction | None = SignalAction.BUY,
    location: str = OUTSIDE,
    is_mid_zone: bool = False,
    candle_confirmation: CandleConfirmationResult | None = None,
    risk_reward: float | None = 1.5,
    htf_trend: TrendDirection = TrendDirection.BULLISH,
    execution_trend: TrendDirection = TrendDirection.BULLISH,
    structure_trend: TrendDirection = TrendDirection.BULLISH,
) -> TradeCandidateContext:
    return TradeCandidateContext(
        action=action,
        structure=_structure(structure_trend),
        price_location=PriceLocationResult(
            location=location,
            nearest_zone=None,
            distance_to_zone=None,
            is_mid_zone=is_mid_zone,
        ),
        candle_confirmation=candle_confirmation or _confirmation(),
        risk_reward=risk_reward,
        htf_trend=htf_trend,
        execution_trend=execution_trend,
    )


def _structure(trend: TrendDirection) -> MarketStructureResult:
    return MarketStructureResult(
        trend=trend,
        structure_label="test",
        swings=(),
        latest_swing_high=None,
        latest_swing_low=None,
        has_bos=False,
        has_choch=False,
        bos_direction=None,
        choch_direction=None,
        invalidation=InvalidationLevels(buy=None, sell=None),
    )


def _confirmation(
    fakeout: bool = False,
    body_breakout: bool = True,
    strong_close: bool = True,
    direction: SignalAction | None = SignalAction.BUY,
) -> CandleConfirmationResult:
    return CandleConfirmationResult(
        bullish_engulfing=False,
        bearish_engulfing=False,
        pin_bar=False,
        rejection_wick=False,
        strong_close=strong_close,
        body_breakout=body_breakout,
        fakeout=fakeout,
        direction=direction,
        summary="test",
    )


if __name__ == "__main__":
    unittest.main()
