from __future__ import annotations

import json
import unittest

from trading_signal_bot.models import Confidence, SignalAction
from trading_signal_bot.tradingview import format_tradingview_message, parse_tradingview_payload


class TradingViewPayloadTest(unittest.TestCase):
    def test_parses_valid_payload(self) -> None:
        payload = {
            "secret": "expected",
            "action": "BUY",
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "price": 2365.25,
            "confidence": "Medium",
        }

        signal = parse_tradingview_payload(json.dumps(payload).encode("utf-8"), "expected")

        self.assertEqual(signal.action, SignalAction.BUY)
        self.assertEqual(signal.confidence, Confidence.MEDIUM)
        self.assertEqual(signal.symbol, "XAUUSD")

    def test_rejects_invalid_secret(self) -> None:
        payload = {
            "secret": "wrong",
            "action": "SELL",
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "price": 2365.25,
        }

        with self.assertRaises(PermissionError):
            parse_tradingview_payload(json.dumps(payload).encode("utf-8"), "expected")

    def test_rejects_invalid_action(self) -> None:
        payload = {
            "secret": "expected",
            "action": "MAYBE",
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "price": 2365.25,
        }

        with self.assertRaises(ValueError):
            parse_tradingview_payload(json.dumps(payload).encode("utf-8"), "expected")

    def test_formats_message_with_risk_warning(self) -> None:
        payload = {
            "secret": "expected",
            "action": "WAIT",
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "price": 2365.25,
        }
        signal = parse_tradingview_payload(json.dumps(payload).encode("utf-8"), "expected")

        message = format_tradingview_message(signal)

        self.assertIn("TradingView Signal: WAIT", message)
        self.assertIn("ไม่ใช่คำแนะนำทางการเงินส่วนบุคคล", message)


if __name__ == "__main__":
    unittest.main()
