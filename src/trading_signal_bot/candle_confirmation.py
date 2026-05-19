from __future__ import annotations

from dataclasses import dataclass

from .models import Candle, SignalAction


@dataclass(frozen=True)
class CandleConfirmationResult:
    bullish_engulfing: bool
    bearish_engulfing: bool
    pin_bar: bool
    rejection_wick: bool
    strong_close: bool
    body_breakout: bool
    fakeout: bool
    direction: SignalAction | None
    summary: str


def analyze_candle_confirmation(
    candles: list[Candle],
    support: float | None = None,
    resistance: float | None = None,
    atr_value: float | None = None,
) -> CandleConfirmationResult:
    if not candles:
        return _empty_result("No candle data")

    latest = candles[-1]
    previous = candles[-2] if len(candles) >= 2 else None
    bullish_engulfing = bool(previous and _is_bullish_engulfing(previous, latest))
    bearish_engulfing = bool(previous and _is_bearish_engulfing(previous, latest))
    pin_bar = _is_pin_bar(latest, atr_value)
    rejection_wick = _has_rejection_wick(latest)
    strong_close = _has_strong_close(latest)
    body_breakout = _has_body_breakout(latest, support, resistance)
    fakeout = is_fakeout(latest, support, resistance)
    direction = _confirmation_direction(
        latest=latest,
        bullish_engulfing=bullish_engulfing,
        bearish_engulfing=bearish_engulfing,
        pin_bar=pin_bar,
        rejection_wick=rejection_wick,
        strong_close=strong_close,
        body_breakout=body_breakout,
        fakeout=fakeout,
        support=support,
        resistance=resistance,
    )

    return CandleConfirmationResult(
        bullish_engulfing=bullish_engulfing,
        bearish_engulfing=bearish_engulfing,
        pin_bar=pin_bar,
        rejection_wick=rejection_wick,
        strong_close=strong_close,
        body_breakout=body_breakout,
        fakeout=fakeout,
        direction=direction,
        summary=_summary(
            bullish_engulfing=bullish_engulfing,
            bearish_engulfing=bearish_engulfing,
            pin_bar=pin_bar,
            rejection_wick=rejection_wick,
            strong_close=strong_close,
            body_breakout=body_breakout,
            fakeout=fakeout,
            direction=direction,
        ),
    )


def is_body_breakout(
    candle: Candle,
    level: float,
    direction: SignalAction,
    min_body_ratio: float = 0.5,
) -> bool:
    if min_body_ratio < 0 or min_body_ratio > 1:
        raise ValueError("min_body_ratio must be between 0 and 1")

    candle_range = candle.high - candle.low
    if candle_range <= 0:
        return False

    body_size = abs(candle.close - candle.open)
    if body_size < candle_range * min_body_ratio:
        return False

    if direction == SignalAction.BUY:
        return min(candle.open, candle.close) > level and candle.close > candle.open
    if direction == SignalAction.SELL:
        return max(candle.open, candle.close) < level and candle.close < candle.open
    return False


def is_fakeout(
    candle: Candle,
    support: float | None,
    resistance: float | None,
) -> bool:
    if resistance is not None and candle.high > resistance and candle.close <= resistance:
        return True
    if support is not None and candle.low < support and candle.close >= support:
        return True
    return False


def _empty_result(summary: str) -> CandleConfirmationResult:
    return CandleConfirmationResult(
        bullish_engulfing=False,
        bearish_engulfing=False,
        pin_bar=False,
        rejection_wick=False,
        strong_close=False,
        body_breakout=False,
        fakeout=False,
        direction=None,
        summary=summary,
    )


def _is_bullish_engulfing(previous: Candle, latest: Candle) -> bool:
    previous_body_low = min(previous.open, previous.close)
    previous_body_high = max(previous.open, previous.close)
    latest_body_low = min(latest.open, latest.close)
    latest_body_high = max(latest.open, latest.close)
    return (
        previous.close < previous.open
        and latest.close > latest.open
        and latest_body_low <= previous_body_low
        and latest_body_high >= previous_body_high
    )


def _is_bearish_engulfing(previous: Candle, latest: Candle) -> bool:
    previous_body_low = min(previous.open, previous.close)
    previous_body_high = max(previous.open, previous.close)
    latest_body_low = min(latest.open, latest.close)
    latest_body_high = max(latest.open, latest.close)
    return (
        previous.close > previous.open
        and latest.close < latest.open
        and latest_body_low <= previous_body_low
        and latest_body_high >= previous_body_high
    )


def _is_pin_bar(candle: Candle, atr_value: float | None) -> bool:
    candle_range = candle.high - candle.low
    if candle_range <= 0:
        return False
    if atr_value is not None and atr_value > 0 and candle_range < atr_value * 0.25:
        return False

    body_size = abs(candle.close - candle.open)
    upper_wick = candle.high - max(candle.open, candle.close)
    lower_wick = min(candle.open, candle.close) - candle.low
    return body_size <= candle_range * 0.35 and max(upper_wick, lower_wick) >= candle_range * 0.55


def _has_rejection_wick(candle: Candle) -> bool:
    candle_range = candle.high - candle.low
    if candle_range <= 0:
        return False

    upper_wick = candle.high - max(candle.open, candle.close)
    lower_wick = min(candle.open, candle.close) - candle.low
    return max(upper_wick, lower_wick) >= candle_range * 0.45


def _has_strong_close(candle: Candle) -> bool:
    candle_range = candle.high - candle.low
    if candle_range <= 0:
        return False

    close_position = (candle.close - candle.low) / candle_range
    if candle.close > candle.open:
        return close_position >= 0.75
    if candle.close < candle.open:
        return close_position <= 0.25
    return False


def _has_body_breakout(
    candle: Candle,
    support: float | None,
    resistance: float | None,
) -> bool:
    bullish_breakout = resistance is not None and is_body_breakout(candle, resistance, SignalAction.BUY)
    bearish_breakout = support is not None and is_body_breakout(candle, support, SignalAction.SELL)
    return bullish_breakout or bearish_breakout


def _confirmation_direction(
    latest: Candle,
    bullish_engulfing: bool,
    bearish_engulfing: bool,
    pin_bar: bool,
    rejection_wick: bool,
    strong_close: bool,
    body_breakout: bool,
    fakeout: bool,
    support: float | None,
    resistance: float | None,
) -> SignalAction | None:
    if fakeout:
        if resistance is not None and latest.high > resistance and latest.close <= resistance:
            return SignalAction.SELL
        if support is not None and latest.low < support and latest.close >= support:
            return SignalAction.BUY

    if bullish_engulfing or (latest.close > latest.open and (strong_close or body_breakout)):
        return SignalAction.BUY
    if bearish_engulfing or (latest.close < latest.open and (strong_close or body_breakout)):
        return SignalAction.SELL
    if pin_bar or rejection_wick:
        return _wick_direction(latest)
    return None


def _wick_direction(candle: Candle) -> SignalAction | None:
    upper_wick = candle.high - max(candle.open, candle.close)
    lower_wick = min(candle.open, candle.close) - candle.low
    if lower_wick > upper_wick:
        return SignalAction.BUY
    if upper_wick > lower_wick:
        return SignalAction.SELL
    return None


def _summary(
    bullish_engulfing: bool,
    bearish_engulfing: bool,
    pin_bar: bool,
    rejection_wick: bool,
    strong_close: bool,
    body_breakout: bool,
    fakeout: bool,
    direction: SignalAction | None,
) -> str:
    signals: list[str] = []
    if bullish_engulfing:
        signals.append("bullish engulfing")
    if bearish_engulfing:
        signals.append("bearish engulfing")
    if pin_bar:
        signals.append("pin bar")
    if rejection_wick:
        signals.append("rejection wick")
    if strong_close:
        signals.append("strong close")
    if body_breakout:
        signals.append("body breakout")
    if fakeout:
        signals.append("wick fakeout")
    if not signals:
        return "No clear candle confirmation"

    direction_text = direction.value if direction else "None"
    return f"Detected {', '.join(signals)}; direction={direction_text}"
