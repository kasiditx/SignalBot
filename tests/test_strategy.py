from __future__ import annotations

import unittest

from trading_signal_bot.models import Candle, SignalAction, SignalConfig
from trading_signal_bot.strategy import generate_signal


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

    def test_asian_breakout_generates_buy_after_london_open(self) -> None:
        candles = _asian_breakout_candles("buy")

        signal = generate_signal(candles, _config(trade_mode="asian_breakout", timeframe="M5"))

        self.assertEqual(signal.action, SignalAction.BUY)
        self.assertEqual(signal.strategy_name, "Asian Range Breakout XAUUSD")
        self.assertEqual(signal.setup_type, "Asian range bullish breakout")
        self.assertEqual(signal.levels.risk_reward, 2.0)
        self.assertLess(signal.levels.stop_loss, signal.levels.entry)
        self.assertGreater(signal.levels.take_profit, signal.levels.entry)

    def test_asian_breakout_generates_sell_after_london_open(self) -> None:
        candles = _asian_breakout_candles("sell")

        signal = generate_signal(candles, _config(trade_mode="asian_breakout", timeframe="M5"))

        self.assertEqual(signal.action, SignalAction.SELL)
        self.assertEqual(signal.strategy_name, "Asian Range Breakout XAUUSD")
        self.assertEqual(signal.setup_type, "Asian range bearish breakout")
        self.assertGreater(signal.levels.stop_loss, signal.levels.entry)
        self.assertLess(signal.levels.take_profit, signal.levels.entry)

    def test_asian_breakout_waits_before_london_window(self) -> None:
        candles = _asian_range_only_candles()

        signal = generate_signal(candles, _config(trade_mode="asian_breakout", timeframe="M5"))

        self.assertEqual(signal.action, SignalAction.WAIT)
        self.assertEqual(signal.setup_type, "Building Asian range")


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


def _config(trade_mode: str = "high_winrate", timeframe: str = "H1") -> SignalConfig:
    return SignalConfig(
        symbol="TEST",
        timeframe=timeframe,
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
        trade_mode=trade_mode,
    )


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


def _asian_range_only_candles() -> list[Candle]:
    candles: list[Candle] = []
    for index in range(90):
        hour = index // 12
        minute = (index % 12) * 5
        open_price = 100.0 + (0.08 if index % 2 == 0 else -0.08)
        close = 100.05 if index % 2 == 0 else 99.95
        candles.append(
            Candle(
                timestamp=f"2026-05-01 {hour:02d}:{minute:02d}",
                open=open_price,
                high=100.4,
                low=99.6,
                close=close,
                volume=1000 + index,
            )
        )
    return candles


def _asian_breakout_candles(direction: str) -> list[Candle]:
    candles = _asian_range_only_candles()
    candles.extend(
        [
            Candle(
                timestamp="2026-05-01 07:30",
                open=100.0,
                high=100.4,
                low=99.6,
                close=100.0,
                volume=2000,
            ),
            Candle(
                timestamp="2026-05-01 07:35",
                open=100.0,
                high=100.4,
                low=99.6,
                close=100.0,
                volume=2001,
            ),
            Candle(
                timestamp="2026-05-01 07:40",
                open=100.0,
                high=100.4,
                low=99.6,
                close=100.0,
                volume=2002,
            ),
            Candle(
                timestamp="2026-05-01 07:45",
                open=100.0,
                high=100.4,
                low=99.6,
                close=100.0,
                volume=2003,
            ),
            Candle(
                timestamp="2026-05-01 07:50",
                open=100.0,
                high=100.4,
                low=99.6,
                close=100.0,
                volume=2004,
            ),
            Candle(
                timestamp="2026-05-01 07:55",
                open=100.0,
                high=100.4,
                low=99.6,
                close=100.0,
                volume=2005,
            ),
            Candle(
                timestamp="2026-05-01 08:00",
                open=100.0,
                high=100.5,
                low=99.5,
                close=100.0,
                volume=2100,
            ),
        ]
    )

    if direction == "buy":
        candles.append(
            Candle(
                timestamp="2026-05-01 08:05",
                open=100.2,
                high=103.2,
                low=100.0,
                close=102.4,
                volume=3000,
            )
        )
        return candles

    candles.append(
        Candle(
            timestamp="2026-05-01 08:05",
            open=99.8,
            high=100.0,
            low=96.8,
            close=97.6,
            volume=3000,
        )
    )
    return candles


if __name__ == "__main__":
    unittest.main()
