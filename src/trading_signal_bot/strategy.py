from __future__ import annotations

from .indicators import atr, ema, rsi
from .models import Candle, Confidence, Signal, SignalAction, SignalConfig, TradeLevels, TrendDirection
from .multitimeframe import (
    confirmation_bias,
    dominant_bias,
    format_trend_summary,
    trend_map,
)

STRATEGY_NAME = "Pro MTF Price Action Structure"
STRUCTURE_LOOKBACK = 20
RECENT_MOMENTUM_CANDLES = 7
DEFAULT_BODY_BREAK_ATR_RATIO = 0.20
EXHAUSTION_BODY_RATIO = 0.45
WICK_BUFFER_ATR_RATIO = 0.10
MAX_MANUAL_RISK_REWARD = 1.5
HIGHER_TIMEFRAMES = ("D1", "H4", "H1")
CONFIRMATION_TIMEFRAMES = ("M30", "M15")


def generate_signal(
    candles: list[Candle],
    config: SignalConfig,
    timeframe_candles: dict[str, list[Candle]] | None = None,
) -> Signal:
    required_candles = max(
        config.min_candles,
        config.slow_ema_period + 2,
        config.rsi_period + 2,
        config.atr_period + 2,
    )
    if len(candles) < required_candles:
        raise ValueError(f"Need at least {required_candles} candles, got {len(candles)}")

    closes = [candle.close for candle in candles]
    fast_ema_values = ema(closes, config.fast_ema_period)
    slow_ema_values = ema(closes, config.slow_ema_period)
    rsi_values = rsi(closes, config.rsi_period)
    atr_values = atr(candles, config.atr_period)

    latest = candles[-1]
    support, resistance = _support_resistance(candles)
    fast_now = fast_ema_values[-1]
    slow_now = slow_ema_values[-1]
    rsi_now = rsi_values[-1]
    atr_now = atr_values[-1]

    market_structure = _market_structure(candles)
    trends = trend_map(timeframe_candles or {config.timeframe: candles})
    htf_bias = dominant_bias(trends) if config.multi_timeframe_enabled else _single_timeframe_bias(candles)
    confirmation = confirmation_bias(trends) if config.multi_timeframe_enabled else htf_bias
    trend_summary = format_trend_summary(trends) if config.multi_timeframe_enabled else f"{config.timeframe}:{htf_bias.value}"
    trend_alignment = _trend_alignment_text(htf_bias, confirmation)
    body_break_atr_ratio = config.body_break_atr_ratio or DEFAULT_BODY_BREAK_ATR_RATIO
    setup_type, wait_reason = _setup_context(candles, support, resistance, atr_now, body_break_atr_ratio)
    bullish_breakout = setup_type == "Bullish body-close breakout"
    bearish_breakdown = setup_type == "Bearish body-close breakdown"
    directional_buy_ok = fast_now >= slow_now or latest.close > slow_now
    directional_sell_ok = fast_now <= slow_now or latest.close < slow_now

    buy_trend_allowed = not config.multi_timeframe_enabled or _trend_allows(
        SignalAction.BUY,
        htf_bias,
        confirmation,
        trends,
    )
    sell_trend_allowed = not config.multi_timeframe_enabled or _trend_allows(
        SignalAction.SELL,
        htf_bias,
        confirmation,
        trends,
    )

    if bullish_breakout and directional_buy_ok and buy_trend_allowed:
        return _build_directional_signal(
            action=SignalAction.BUY,
            candles=candles,
            config=config,
            support=support,
            resistance=resistance,
            fast_ema_value=fast_now,
            slow_ema_value=slow_now,
            rsi_value=rsi_now,
            atr_value=atr_now,
            market_structure=market_structure,
            setup_type=setup_type,
            trend_summary=trend_summary,
            trend_alignment=trend_alignment,
        )
    if bearish_breakdown and directional_sell_ok and sell_trend_allowed:
        return _build_directional_signal(
            action=SignalAction.SELL,
            candles=candles,
            config=config,
            support=support,
            resistance=resistance,
            fast_ema_value=fast_now,
            slow_ema_value=slow_now,
            rsi_value=rsi_now,
            atr_value=atr_now,
            market_structure=market_structure,
            setup_type=setup_type,
            trend_summary=trend_summary,
            trend_alignment=trend_alignment,
        )
    if config.multi_timeframe_enabled and (bullish_breakout or bearish_breakdown):
        wait_reason = (
            "M5 มี breakout/breakdown แต่ multi-timeframe filter ยังไม่ผ่าน: "
            "ต้องให้ 30M และ 15M ไปทางเดียวกับสัญญาณทั้งคู่ และ 1D/4H/1H ต้องไม่มี timeframe ที่สวนทาง"
        )

    return Signal(
        action=SignalAction.WAIT,
        symbol=config.symbol,
        timeframe=config.timeframe,
        strategy_name=STRATEGY_NAME,
        market_structure=market_structure,
        setup_type=setup_type,
        trend_summary=trend_summary,
        trend_alignment=trend_alignment,
        confidence=Confidence.LOW,
        reason=wait_reason,
        entry_condition="รอ body close ข้ามแนวรับ/แนวต้านสำคัญ พร้อม momentum และไม่ใช่ wick sweep",
        invalidation="ไม่มีแผนเข้า จึงยังไม่มีจุด invalidation ของ trade",
        no_trade_reason=wait_reason,
        support=support,
        resistance=resistance,
        latest_close=latest.close,
        fast_ema=fast_now,
        slow_ema=slow_now,
        rsi=rsi_now,
        atr=atr_now,
        levels=TradeLevels(entry=None, stop_loss=None, take_profit=None, risk_reward=None),
    )


def _build_directional_signal(
    action: SignalAction,
    candles: list[Candle],
    config: SignalConfig,
    support: float,
    resistance: float,
    fast_ema_value: float,
    slow_ema_value: float,
    rsi_value: float,
    atr_value: float,
    market_structure: str,
    setup_type: str,
    trend_summary: str,
    trend_alignment: str,
) -> Signal:
    latest = candles[-1]
    previous = candles[-2]
    entry = latest.close
    effective_risk_reward = min(config.risk_reward, MAX_MANUAL_RISK_REWARD)
    wick_buffer = atr_value * WICK_BUFFER_ATR_RATIO
    if action == SignalAction.BUY:
        stop_loss = min(latest.low, previous.low) - wick_buffer
        risk_distance = entry - stop_loss
        take_profit = entry + (risk_distance * effective_risk_reward)
        entry_condition = "Buy เฉพาะหลังเนื้อเทียนปิดเหนือแนวต้านเดิม ยืนยันว่าเป็น MSB/Breakout ไม่ใช่แค่ wick sweep"
        invalidation = "ยกเลิกแผน Buy หากราคาปิดกลับใต้แนวต้านที่ breakout หรือหลุดปลาย wick protection"
        reason = "เกิด body-close breakout ตามคู่มือ: รอให้กำแพงแตกก่อน แล้วเก็บ residual momentum ในโซนที่มี clean traffic"
    else:
        stop_loss = max(latest.high, previous.high) + wick_buffer
        risk_distance = stop_loss - entry
        take_profit = entry - (risk_distance * effective_risk_reward)
        entry_condition = "Sell เฉพาะหลังเนื้อเทียนปิดใต้แนวรับเดิม ยืนยันว่าเป็น MSB/Breakdown ไม่ใช่แค่ wick sweep"
        invalidation = "ยกเลิกแผน Sell หากราคาปิดกลับเหนือแนวรับที่ breakdown หรือชน wick protection"
        reason = "เกิด body-close breakdown ตามคู่มือ: รอให้กำแพงแตกก่อน แล้วเก็บ residual momentum ในโซนที่มี clean traffic"

    confidence = _confidence(candles, fast_ema_value, slow_ema_value, atr_value, rsi_value)

    return Signal(
        action=action,
        symbol=config.symbol,
        timeframe=config.timeframe,
        strategy_name=STRATEGY_NAME,
        market_structure=market_structure,
        setup_type=setup_type,
        trend_summary=trend_summary,
        trend_alignment=trend_alignment,
        confidence=confidence,
        reason=reason,
        entry_condition=entry_condition,
        invalidation=invalidation,
        no_trade_reason=_no_trade_reason(action, rsi_value),
        support=support,
        resistance=resistance,
        latest_close=latest.close,
        fast_ema=fast_ema_value,
        slow_ema=slow_ema_value,
        rsi=rsi_value,
        atr=atr_value,
        levels=TradeLevels(
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward=effective_risk_reward,
        ),
    )


def _support_resistance(candles: list[Candle], lookback: int = 20) -> tuple[float, float]:
    recent = candles[-(lookback + 1) : -1]
    return min(candle.low for candle in recent), max(candle.high for candle in recent)


def _confidence(
    candles: list[Candle],
    fast_ema_value: float,
    slow_ema_value: float,
    atr_value: float,
    rsi_value: float,
) -> Confidence:
    if rsi_value >= 75 or rsi_value <= 25:
        return Confidence.LOW
    if _is_exhaustion(candles) or _has_no_continuation_wick(candles[-1]):
        return Confidence.LOW
    ema_gap = abs(fast_ema_value - slow_ema_value)
    if ema_gap >= atr_value * 0.35 and 40 <= rsi_value <= 60:
        return Confidence.HIGH
    if ema_gap >= atr_value * 0.15 and 35 <= rsi_value <= 70:
        return Confidence.MEDIUM
    return Confidence.LOW


def _no_trade_reason(action: SignalAction, rsi_value: float) -> str:
    if action == SignalAction.BUY and rsi_value >= 75:
        return "ห้ามไล่ Buy หาก RSI อยู่สูงมากหรือราคาแท่งล่าสุดยืดจาก EMA มากเกินไป ควรรอย่อหรือรอ breakout ยืนยัน"
    if action == SignalAction.SELL and rsi_value <= 25:
        return "ห้ามไล่ Sell หาก RSI อยู่ต่ำมากหรือราคาแท่งล่าสุดยืดจาก EMA มากเกินไป ควรรอเด้งหรือรอ breakdown ยืนยัน"
    return "ห้ามเข้าเพิ่มหากราคาไล่ไปไกลจาก entry หรือ spread/slippage สูงผิดปกติ"


def _setup_context(
    candles: list[Candle],
    support: float,
    resistance: float,
    atr_value: float,
    body_break_atr_ratio: float,
) -> tuple[str, str]:
    latest = candles[-1]
    body_size = abs(latest.close - latest.open)
    clean_body_break = body_size >= atr_value * body_break_atr_ratio
    upper_sweep = latest.high > resistance and latest.close <= resistance
    lower_sweep = latest.low < support and latest.close >= support

    if upper_sweep:
        return (
            "Liquidity sweep above resistance",
            "ราคากวาดเหนือแนวต้านแต่ปิดกลับเข้ากรอบ จัดเป็น wick sweep ไม่ใช่ breakout ที่ยืนยันแล้ว",
        )
    if lower_sweep:
        return (
            "Liquidity sweep below support",
            "ราคากวาดใต้แนวรับแต่ปิดกลับเข้ากรอบ จัดเป็น wick sweep ไม่ใช่ breakdown ที่ยืนยันแล้ว",
        )
    if latest.close > resistance and clean_body_break and not _is_exhaustion(candles) and not _has_no_top_wick(latest):
        return (
            "Bullish body-close breakout",
            "เนื้อเทียนปิดเหนือแนวต้านด้วย body ที่ชัดเจน",
        )
    if latest.close < support and clean_body_break and not _is_exhaustion(candles) and not _has_no_bottom_wick(latest):
        return (
            "Bearish body-close breakdown",
            "เนื้อเทียนปิดใต้แนวรับด้วย body ที่ชัดเจน",
        )
    if _is_exhaustion(candles):
        return (
            "Momentum exhaustion",
            "แท่งล่าสุดมีลักษณะ large-medium-small หรือแรงเริ่มถดถอย จึงควรรอ confirmation ใหม่",
        )
    if _has_no_continuation_wick(latest):
        return (
            "No continuation wick warning",
            "แท่งล่าสุดปิดชิดปลายทางมากเกินไป มีความเสี่ยงพักตัวหรือกลับตัว ห้ามไล่ราคา",
        )
    return (
        "No confirmed structure break",
        "ยังไม่มี body close ข้ามแนวรับ/แนวต้านสำคัญ จึงรอให้กำแพงแตกก่อน",
    )


def _market_structure(candles: list[Candle]) -> str:
    recent = candles[-RECENT_MOMENTUM_CANDLES:]
    first_close = recent[0].close
    last_close = recent[-1].close
    recent_high = max(candle.high for candle in recent)
    recent_low = min(candle.low for candle in recent)
    previous = candles[-(RECENT_MOMENTUM_CANDLES * 2) : -RECENT_MOMENTUM_CANDLES]
    previous_high = max(candle.high for candle in previous)
    previous_low = min(candle.low for candle in previous)

    if recent_high > previous_high and recent_low > previous_low and last_close > first_close:
        return "Bullish structure: higher high / higher low ในแท่งล่าสุด"
    if recent_high < previous_high and recent_low < previous_low and last_close < first_close:
        return "Bearish structure: lower high / lower low ในแท่งล่าสุด"
    return "Sideways or mixed structure: โครงสร้างยังไม่ชัด"


def _single_timeframe_bias(candles: list[Candle]) -> TrendDirection:
    structure = _market_structure(candles)
    if structure.startswith("Bullish"):
        return TrendDirection.BULLISH
    if structure.startswith("Bearish"):
        return TrendDirection.BEARISH
    return TrendDirection.SIDEWAYS


def _trend_allows(
    action: SignalAction,
    htf_bias: TrendDirection,
    confirmation: TrendDirection,
    trends: dict[str, TrendDirection],
) -> bool:
    if action == SignalAction.BUY:
        return (
            htf_bias == TrendDirection.BULLISH
            and confirmation == TrendDirection.BULLISH
            and _timeframes_are_aligned(action, trends, CONFIRMATION_TIMEFRAMES)
            and not _has_opposite_higher_timeframe(action, trends)
        )
    if action == SignalAction.SELL:
        return (
            htf_bias == TrendDirection.BEARISH
            and confirmation == TrendDirection.BEARISH
            and _timeframes_are_aligned(action, trends, CONFIRMATION_TIMEFRAMES)
            and not _has_opposite_higher_timeframe(action, trends)
        )
    return False


def _timeframes_are_aligned(
    action: SignalAction,
    trends: dict[str, TrendDirection],
    timeframes: tuple[str, ...],
) -> bool:
    expected = TrendDirection.BULLISH if action == SignalAction.BUY else TrendDirection.BEARISH
    return all(trends.get(timeframe) == expected for timeframe in timeframes)


def _has_opposite_higher_timeframe(action: SignalAction, trends: dict[str, TrendDirection]) -> bool:
    opposite = TrendDirection.BEARISH if action == SignalAction.BUY else TrendDirection.BULLISH
    return any(trends.get(timeframe) == opposite for timeframe in HIGHER_TIMEFRAMES)


def _trend_alignment_text(htf_bias: TrendDirection, confirmation: TrendDirection) -> str:
    if htf_bias == TrendDirection.SIDEWAYS:
        return "HTF ยังไม่ชัด: 1D/4H/1H ไม่ได้ให้ bias ฝั่งเดียวกันพอ"
    if confirmation == TrendDirection.SIDEWAYS:
        return f"HTF {htf_bias.value}, 30M/15M ยังพัก/รอ confirmation"
    if confirmation == htf_bias:
        return f"Aligned: HTF {htf_bias.value} และ 30M/15M สนับสนุน"
    return f"Conflict: HTF {htf_bias.value} แต่ 30M/15M เป็น {confirmation.value}"


def _is_exhaustion(candles: list[Candle]) -> bool:
    recent = candles[-3:]
    bodies = [abs(candle.close - candle.open) for candle in recent]
    return bodies[0] > bodies[1] > bodies[2] and bodies[2] <= bodies[0] * EXHAUSTION_BODY_RATIO


def _has_no_continuation_wick(candle: Candle) -> bool:
    if candle.close > candle.open:
        return _has_no_top_wick(candle)
    if candle.close < candle.open:
        return _has_no_bottom_wick(candle)
    return False


def _has_no_top_wick(candle: Candle) -> bool:
    candle_range = candle.high - candle.low
    if candle_range <= 0:
        return False
    return (candle.high - max(candle.open, candle.close)) <= candle_range * 0.05


def _has_no_bottom_wick(candle: Candle) -> bool:
    candle_range = candle.high - candle.low
    if candle_range <= 0:
        return False
    return (min(candle.open, candle.close) - candle.low) <= candle_range * 0.05
