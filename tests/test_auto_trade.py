from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from trading_signal_bot.auto_trade import process_auto_trade
from trading_signal_bot.models import AutoTradeConfig, Confidence, Signal, SignalAction, TradeLevels


class AutoTradeTest(unittest.TestCase):
    def test_paper_trade_writes_journal_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "journal.csv"
            config = _config(journal_file=str(journal))
            signal = _signal()

            first = process_auto_trade(signal, config)
            second = process_auto_trade(signal, config)

            self.assertEqual(first.status, "paper")
            self.assertEqual(second.status, "duplicate")
            self.assertTrue(journal.exists())
            self.assertEqual(len(journal.read_text(encoding="utf-8").splitlines()), 2)

    def test_wait_signal_is_skipped(self) -> None:
        config = _config()
        signal = _signal(action=SignalAction.WAIT, levels=TradeLevels(None, None, None, None))

        result = process_auto_trade(signal, config)

        self.assertEqual(result.status, "skipped")


def _config(journal_file: str = "logs/test_auto_trade_journal.csv") -> AutoTradeConfig:
    return AutoTradeConfig(
        enabled=True,
        mode="paper",
        order_file="logs/test_order.csv",
        journal_file=journal_file,
        account_balance=1000.0,
        risk_percent=0.5,
        contract_size=100.0,
        min_volume=0.01,
        max_volume=0.10,
        volume_step=0.01,
        allow_min_volume=False,
        magic_number=20260515,
        comment="TestBot",
    )


def _signal(
    action: SignalAction = SignalAction.BUY,
    levels: TradeLevels = TradeLevels(entry=100.0, stop_loss=99.0, take_profit=101.5, risk_reward=1.5),
) -> Signal:
    return Signal(
        action=action,
        symbol="XAUUSD",
        timeframe="M5",
        strategy_name="test",
        market_structure="test",
        setup_type="test",
        trend_summary="test",
        trend_alignment="test",
        confidence=Confidence.MEDIUM,
        reason="test",
        entry_condition="test",
        invalidation="test",
        no_trade_reason="test",
        support=99.0,
        resistance=101.0,
        latest_close=100.0,
        fast_ema=100.0,
        slow_ema=99.0,
        rsi=55.0,
        atr=1.0,
        levels=levels,
    )


if __name__ == "__main__":
    unittest.main()
