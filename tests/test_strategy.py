from __future__ import annotations

import unittest

from trading_signal_bot.models import Candle, SignalAction, SignalConfig
from trading_signal_bot.strategy import generate_signal


def _config() -> SignalConfig:
    return SignalConfig(
        symbol="TEST",
        timeframe="H1",
        csv_path="",
        fast_ema_period=5,
        slow_ema_period=10,
        rsi_period=5,
        atr_period=5,
        atr_multiplier=1.5,
        body_break_atr_ratio=0.20,
        risk_reward=2.0,
        min_candles=30,
        max_candle_age_minutes=180,
        multi_timeframe_enabled=False,
        timeframe_paths={},
        dry_run=True,
        send_wait=False,
    )


class StrategyTest(unittest.TestCase):
    def test_generates_buy_signal_for_orderly_uptrend(self) -> None:
        candles = _breakout_candles(direction="buy")

        signal = generate_signal(candles, _config())

        self.assertEqual(signal.action, SignalAction.BUY)
        self.assertEqual(signal.setup_type, "Bullish body-close breakout")
        self.assertIsNotNone(signal.levels.stop_loss)
        self.assertIsNotNone(signal.levels.take_profit)
        self.assertLess(signal.levels.stop_loss, signal.levels.entry)
        self.assertGreater(signal.levels.take_profit, signal.levels.entry)
        self.assertLessEqual(signal.levels.risk_reward, 1.5)

    def test_generates_sell_signal_for_orderly_downtrend(self) -> None:
        candles = _breakout_candles(direction="sell")

        signal = generate_signal(candles, _config())

        self.assertEqual(signal.action, SignalAction.SELL)
        self.assertEqual(signal.setup_type, "Bearish body-close breakdown")
        self.assertGreater(signal.levels.stop_loss, signal.levels.entry)
        self.assertLess(signal.levels.take_profit, signal.levels.entry)

    def test_waits_on_wick_sweep_instead_of_treating_it_as_breakout(self) -> None:
        candles = _range_candles(count=60)
        previous_resistance = max(candle.high for candle in candles[-20:])
        candles.append(
            Candle(
                timestamp="2026-05-03 12:00",
                open=101.0,
                high=previous_resistance + 1.5,
                low=100.8,
                close=previous_resistance - 0.2,
                volume=2000,
            )
        )

        signal = generate_signal(candles, _config())

        self.assertEqual(signal.action, SignalAction.WAIT)
        self.assertEqual(signal.setup_type, "Liquidity sweep above resistance")

    def test_rejects_insufficient_candles(self) -> None:
        with self.assertRaises(ValueError):
            generate_signal(_candles(start=100.0, step=0.1, count=5), _config())


def _candles(start: float, step: float, count: int) -> list[Candle]:
    candles: list[Candle] = []
    price = start
    for index in range(count):
        open_price = price
        direction = 1 if step >= 0 else -1
        pullback = direction * -0.18 if index % 5 == 0 else 0
        close = price + step + pullback
        high = max(open_price, close) + 0.25
        low = min(open_price, close) - 0.25
        candles.append(
            Candle(
                timestamp=f"2026-05-01 {index:02d}:00",
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=1000 + index,
            )
        )
        price = close
    return candles


def _range_candles(count: int) -> list[Candle]:
    candles: list[Candle] = []
    for index in range(count):
        open_price = 100.0 + (0.15 if index % 2 == 0 else -0.15)
        close = 100.1 if index % 2 == 0 else 99.9
        candles.append(
            Candle(
                timestamp=f"2026-05-01 {index:02d}:00",
                open=open_price,
                high=101.0,
                low=99.0,
                close=close,
                volume=1000 + index,
            )
        )
    return candles


def _breakout_candles(direction: str) -> list[Candle]:
    if direction == "buy":
        candles = _range_candles(59)
        candles.append(
            Candle(
                timestamp="2026-05-03 11:00",
                open=100.4,
                high=102.2,
                low=100.0,
                close=101.8,
                volume=2500,
            )
        )
        return candles

    candles: list[Candle] = []
    for index in range(59):
        open_price = 140.0 + (0.15 if index % 2 == 0 else -0.15)
        close = 140.1 if index % 2 == 0 else 139.9
        candles.append(
            Candle(
                timestamp=f"2026-05-01 {index:02d}:00",
                open=open_price,
                high=141.0,
                low=139.0,
                close=close,
                volume=1000 + index,
            )
        )
    candles.append(
        Candle(
            timestamp="2026-05-03 11:00",
            open=139.6,
            high=140.0,
            low=137.8,
            close=138.2,
            volume=2500,
        )
    )
    return candles


if __name__ == "__main__":
    unittest.main()
