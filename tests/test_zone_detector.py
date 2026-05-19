from __future__ import annotations

import unittest

from trading_signal_bot.models import Candle
from trading_signal_bot.zone_detector import (
    DEMAND,
    MID_ZONE,
    NEAR_DEMAND,
    NEAR_SUPPLY,
    OUTSIDE,
    SUPPLY,
    PriceZone,
    SupportResistance,
    classify_price_location,
    detect_demand_zones,
    detect_supply_zones,
    nearest_levels,
)


class ZoneDetectorTest(unittest.TestCase):
    def test_detects_demand_after_bullish_impulse(self) -> None:
        zones = detect_demand_zones(_demand_impulse_candles(), lookback=10, impulse_ratio=1.4, timeframe="M15")

        self.assertTrue(zones)
        self.assertEqual(zones[-1].kind, DEMAND)
        self.assertEqual(zones[-1].timeframe, "M15")

    def test_detects_supply_after_bearish_impulse(self) -> None:
        zones = detect_supply_zones(_supply_impulse_candles(), lookback=10, impulse_ratio=1.4, timeframe="M30")

        self.assertTrue(zones)
        self.assertEqual(zones[-1].kind, SUPPLY)
        self.assertEqual(zones[-1].timeframe, "M30")

    def test_short_candles_do_not_crash(self) -> None:
        candles = [_candle(0, 10, 11, 9, 10)]

        self.assertEqual(detect_demand_zones(candles), [])
        self.assertEqual(detect_supply_zones(candles), [])

    def test_nearest_levels_empty_candles_returns_none(self) -> None:
        levels = nearest_levels([])

        self.assertIsNone(levels.support)
        self.assertIsNone(levels.resistance)

    def test_classifies_price_near_demand(self) -> None:
        result = classify_price_location(
            price=100.2,
            zones=[PriceZone(DEMAND, 99.8, 100.5, "t0", 2.0)],
            support_resistance=SupportResistance(95.0, 110.0),
            proximity=0.3,
        )

        self.assertEqual(result.location, NEAR_DEMAND)

    def test_classifies_price_near_supply(self) -> None:
        result = classify_price_location(
            price=109.8,
            zones=[PriceZone(SUPPLY, 109.5, 110.2, "t0", 2.0)],
            support_resistance=SupportResistance(95.0, 110.0),
            proximity=0.3,
        )

        self.assertEqual(result.location, NEAR_SUPPLY)

    def test_classifies_price_in_middle_of_range(self) -> None:
        result = classify_price_location(
            price=102.0,
            zones=[],
            support_resistance=SupportResistance(100.0, 104.0),
            proximity=0.2,
        )

        self.assertEqual(result.location, MID_ZONE)
        self.assertTrue(result.is_mid_zone)

    def test_classifies_price_outside_when_no_conditions_match(self) -> None:
        result = classify_price_location(
            price=98.0,
            zones=[],
            support_resistance=SupportResistance(100.0, 110.0),
            proximity=0.3,
        )

        self.assertEqual(result.location, OUTSIDE)


def _demand_impulse_candles() -> list[Candle]:
    return [
        _candle(0, 100.0, 100.4, 99.8, 100.1),
        _candle(1, 100.1, 100.5, 99.9, 100.2),
        _candle(2, 100.2, 103.8, 100.0, 103.5),
        _candle(3, 103.5, 104.0, 103.0, 103.8),
    ]


def _supply_impulse_candles() -> list[Candle]:
    return [
        _candle(0, 110.0, 110.3, 109.8, 110.1),
        _candle(1, 110.1, 110.4, 109.9, 110.0),
        _candle(2, 110.0, 110.2, 106.0, 106.4),
        _candle(3, 106.4, 107.0, 106.1, 106.6),
    ]


def _candle(index: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(
        timestamp=f"2026-05-18 01:{index:02d}",
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000 + index,
    )


if __name__ == "__main__":
    unittest.main()
