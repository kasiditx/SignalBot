from __future__ import annotations

import hmac
import json
from dataclasses import dataclass

from .models import Confidence, SignalAction


@dataclass(frozen=True)
class TradingViewSignal:
    action: SignalAction
    symbol: str
    timeframe: str
    price: float
    entry: float | None
    stop_loss: float | None
    take_profit: float | None
    risk_reward: float | None
    confidence: Confidence
    reason: str
    invalidation: str
    support: float | None
    resistance: float | None
    rsi: float | None
    atr: float | None
    ema_fast: float | None
    ema_slow: float | None


def parse_tradingview_payload(raw_body: bytes, expected_secret: str) -> TradingViewSignal:
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Webhook body must be valid JSON") from exc

    if not isinstance(payload, dict):
        raise ValueError("Webhook JSON must be an object")

    received_secret = _required_string(payload, "secret")
    if not hmac.compare_digest(received_secret, expected_secret):
        raise PermissionError("Invalid webhook secret")

    action = _parse_action(_required_string(payload, "action"))
    confidence = _parse_confidence(str(payload.get("confidence", "Low")))

    return TradingViewSignal(
        action=action,
        symbol=_required_string(payload, "symbol"),
        timeframe=_required_string(payload, "timeframe"),
        price=_required_float(payload, "price"),
        entry=_optional_float(payload, "entry"),
        stop_loss=_optional_float(payload, "stop_loss"),
        take_profit=_optional_float(payload, "take_profit"),
        risk_reward=_optional_float(payload, "risk_reward"),
        confidence=confidence,
        reason=_optional_string(payload, "reason", "TradingView alert condition triggered"),
        invalidation=_optional_string(payload, "invalidation", "รอข้อมูล invalidation จาก alert payload"),
        support=_optional_float(payload, "support"),
        resistance=_optional_float(payload, "resistance"),
        rsi=_optional_float(payload, "rsi"),
        atr=_optional_float(payload, "atr"),
        ema_fast=_optional_float(payload, "ema_fast"),
        ema_slow=_optional_float(payload, "ema_slow"),
    )


def format_tradingview_message(signal: TradingViewSignal) -> str:
    return (
        f"TradingView Signal: {signal.action.value}\n"
        f"Asset: {signal.symbol}\n"
        f"Timeframe: {signal.timeframe}\n"
        f"Confidence: {signal.confidence.value}\n\n"
        f"ราคา Alert: {_format_number(signal.price)}\n"
        f"Entry: {_format_number(signal.entry)}\n"
        f"Stop Loss: {_format_number(signal.stop_loss)}\n"
        f"Take Profit: {_format_number(signal.take_profit)}\n"
        f"Risk/Reward: {_format_risk_reward(signal.risk_reward)}\n\n"
        f"Market Levels:\n"
        f"- Support: {_format_number(signal.support)}\n"
        f"- Resistance: {_format_number(signal.resistance)}\n\n"
        f"Indicator Confirmation:\n"
        f"- EMA Fast: {_format_number(signal.ema_fast)}\n"
        f"- EMA Slow: {_format_number(signal.ema_slow)}\n"
        f"- RSI: {_format_number(signal.rsi)}\n"
        f"- ATR: {_format_number(signal.atr)}\n\n"
        f"เหตุผล: {signal.reason}\n"
        f"เงื่อนไขยกเลิกแผน: {signal.invalidation}\n\n"
        "หมายเหตุ: นี่ไม่ใช่คำแนะนำทางการเงินส่วนบุคคล การลงทุนและการเทรดมีความเสี่ยง "
        "ผู้ใช้ต้องตัดสินใจด้วยตนเอง และควรทดสอบใน Demo/Paper ก่อนใช้เงินจริง"
    )


def _parse_action(value: str) -> SignalAction:
    try:
        return SignalAction(value.strip().upper())
    except ValueError as exc:
        raise ValueError("action must be BUY, SELL, or WAIT") from exc


def _parse_confidence(value: str) -> Confidence:
    normalized = value.strip().lower()
    for confidence in Confidence:
        if confidence.value.lower() == normalized:
            return confidence
    raise ValueError("confidence must be Low, Medium, or High")


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _optional_string(payload: dict[str, object], key: str, default: str) -> str:
    value = payload.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value.strip() or default


def _required_float(payload: dict[str, object], key: str) -> float:
    value = _optional_float(payload, key)
    if value is None:
        raise ValueError(f"{key} is required")
    return value


def _optional_float(payload: dict[str, object], key: str) -> float | None:
    value = payload.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{key} must be a number")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a number") from exc


def _format_number(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.5f}".rstrip("0").rstrip(".")


def _format_risk_reward(value: float | None) -> str:
    if value is None:
        return "-"
    return f"1:{value:.2f}"
