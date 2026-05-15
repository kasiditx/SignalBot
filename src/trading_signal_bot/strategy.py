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
TRAFFIC_LOOKBACK = 40
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
    bullish_pullback = _is_trend_pullback_entry(SignalAction.BUY, candles, fast_now, slow_now, support, resistance, atr_now, rsi_now)
    bearish_pullback = _is_trend_pullback_entry(SignalAction.SELL, candles, fast_now, slow_now, support, resistance, atr_now, rsi_now)
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
        if not _has_price_action_edge(SignalAction.BUY, candles, latest.close, support, resistance, atr_now, config.risk_reward):
            return _wait_signal(
                config=config,
                latest=latest,
                support=support,
                resistance=resistance,
                fast_now=fast_now,
                slow_now=slow_now,
                rsi_now=rsi_now,
                atr_now=atr_now,
                market_structure=market_structure,
                setup_type="Breakout without clean price-action edge",
                trend_summary=trend_summary,
                trend_alignment=trend_alignment,
                wait_reason=(
                    "M5 breakout แล้ว แต่ยังไม่เห็น clean traffic, force flip, wick-fill target, "
                    "rejection หรือ fib context ที่ชัดพอ จึงรอเพื่อไม่ไล่ breakout เปล่า"
                ),
            )
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
    if bullish_pullback and directional_buy_ok and buy_trend_allowed:
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
            setup_type="Bullish pullback rejection continuation",
            trend_summary=trend_summary,
            trend_alignment=trend_alignment,
        )
    if bearish_pullback and directional_sell_ok and sell_trend_allowed:
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
            setup_type="Bearish pullback rejection continuation",
            trend_summary=trend_summary,
            trend_alignment=trend_alignment,
        )
    if bearish_breakdown and directional_sell_ok and sell_trend_allowed:
        if not _has_price_action_edge(SignalAction.SELL, candles, latest.close, support, resistance, atr_now, config.risk_reward):
            return _wait_signal(
                config=config,
                latest=latest,
                support=support,
                resistance=resistance,
                fast_now=fast_now,
                slow_now=slow_now,
                rsi_now=rsi_now,
                atr_now=atr_now,
                market_structure=market_structure,
                setup_type="Breakdown without clean price-action edge",
                trend_summary=trend_summary,
                trend_alignment=trend_alignment,
                wait_reason=(
                    "M5 breakdown แล้ว แต่ยังไม่เห็น clean traffic, force flip, wick-fill target, "
                    "rejection หรือ fib context ที่ชัดพอ จึงรอเพื่อไม่ไล่ breakdown เปล่า"
                ),
            )
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

    return _wait_signal(
        config=config,
        latest=latest,
        support=support,
        resistance=resistance,
        fast_now=fast_now,
        slow_now=slow_now,
        rsi_now=rsi_now,
        atr_now=atr_now,
        market_structure=market_structure,
        setup_type=setup_type,
        trend_summary=trend_summary,
        trend_alignment=trend_alignment,
        wait_reason=wait_reason,
    )


def _wait_signal(
    config: SignalConfig,
    latest: Candle,
    support: float,
    resistance: float,
    fast_now: float,
    slow_now: float,
    rsi_now: float,
    atr_now: float,
    market_structure: str,
    setup_type: str,
    trend_summary: str,
    trend_alignment: str,
    wait_reason: str,
) -> Signal:
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
        entry_condition = (
            "Buy หลัง body close เหนือแนวต้าน พร้อม price-action edge เช่น clean traffic, "
            "force flip, wick-fill target, rejection หรือ fib context"
        )
        invalidation = "ยกเลิกแผน Buy หากราคาปิดกลับใต้แนวต้านที่ breakout หรือหลุดปลาย wick protection"
        reason = (
            "เกิด body-close breakout ตามคู่มือกราฟเปล่า: รอให้โครงสร้างยืนยันก่อน "
            "แล้วเข้าเฉพาะเมื่อมีทางราคา/แรงเทียนสนับสนุน ไม่ใช่ blind breakout"
        )
    else:
        stop_loss = max(latest.high, previous.high) + wick_buffer
        risk_distance = stop_loss - entry
        take_profit = entry - (risk_distance * effective_risk_reward)
        entry_condition = (
            "Sell หลัง body close ใต้แนวรับ พร้อม price-action edge เช่น clean traffic, "
            "force flip, wick-fill target, rejection หรือ fib context"
        )
        invalidation = "ยกเลิกแผน Sell หากราคาปิดกลับเหนือแนวรับที่ breakdown หรือชน wick protection"
        reason = (
            "เกิด body-close breakdown ตามคู่มือกราฟเปล่า: รอให้โครงสร้างยืนยันก่อน "
            "แล้วเข้าเฉพาะเมื่อมีทางราคา/แรงเทียนสนับสนุน ไม่ใช่ blind breakdown"
        )

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


def _has_price_action_edge(
    action: SignalAction,
    candles: list[Candle],
    entry: float,
    support: float,
    resistance: float,
    atr_value: float,
    risk_reward: float,
) -> bool:
    latest = candles[-1]
    previous = candles[-2]
    risk_distance = _estimated_risk_distance(action, latest, previous, entry, atr_value)
    target = entry + (risk_distance * risk_reward) if action == SignalAction.BUY else entry - (risk_distance * risk_reward)

    confirmations = [
        _has_clean_traffic(action, candles, entry, target, atr_value),
        _is_force_flip(action, latest, previous),
        _has_wick_fill_target(action, candles, entry, target),
        _has_rejection(action, latest, support, resistance, atr_value),
        _has_fib_context(action, candles, entry),
    ]
    return any(confirmations)


def _is_trend_pullback_entry(
    action: SignalAction,
    candles: list[Candle],
    fast_ema_value: float,
    slow_ema_value: float,
    support: float,
    resistance: float,
    atr_value: float,
    rsi_value: float,
) -> bool:
    latest = candles[-1]
    previous = candles[-2]
    recent = candles[-6:]
    body = max(abs(latest.close - latest.open), atr_value * 0.05)
    lower_wick = min(latest.open, latest.close) - latest.low
    upper_wick = latest.high - max(latest.open, latest.close)
    near_fast_ema = abs(latest.close - fast_ema_value) <= atr_value * 0.85

    if _is_exhaustion(candles) and not _is_force_flip(action, latest, previous):
        return False

    if action == SignalAction.BUY:
        has_recent_pullback = min(candle.low for candle in recent[:-1]) <= fast_ema_value + (atr_value * 0.35)
        bullish_rejection = latest.close > latest.open and lower_wick >= body * 0.45
        bullish_continuation = latest.close > previous.high or _is_force_flip(action, latest, previous)
        not_overextended = rsi_value <= 68 and latest.close <= resistance + (atr_value * 0.35)
        return near_fast_ema and has_recent_pullback and bullish_rejection and bullish_continuation and not_overextended

    has_recent_pullback = max(candle.high for candle in recent[:-1]) >= fast_ema_value - (atr_value * 0.35)
    bearish_rejection = latest.close < latest.open and upper_wick >= body * 0.45
    bearish_continuation = latest.close < previous.low or _is_force_flip(action, latest, previous)
    not_overextended = rsi_value >= 28 and latest.close >= support - (atr_value * 0.35)
    return near_fast_ema and has_recent_pullback and bearish_rejection and bearish_continuation and not_overextended


def _estimated_risk_distance(
    action: SignalAction,
    latest: Candle,
    previous: Candle,
    entry: float,
    atr_value: float,
) -> float:
    wick_buffer = atr_value * WICK_BUFFER_ATR_RATIO
    if action == SignalAction.BUY:
        stop_loss = min(latest.low, previous.low) - wick_buffer
        return max(entry - stop_loss, atr_value * 0.1)
    stop_loss = max(latest.high, previous.high) + wick_buffer
    return max(stop_loss - entry, atr_value * 0.1)


def _has_clean_traffic(
    action: SignalAction,
    candles: list[Candle],
    entry: float,
    target: float,
    atr_value: float,
) -> bool:
    left_side = candles[-(TRAFFIC_LOOKBACK + 1) : -1]
    lower = min(entry, target)
    upper = max(entry, target)
    if upper <= lower:
        return False

    noisy_touches = 0
    tolerance = atr_value * 0.05
    for candle in left_side:
        overlaps_target_path = candle.high >= lower - tolerance and candle.low <= upper + tolerance
        if overlaps_target_path:
            noisy_touches += 1

    if action == SignalAction.BUY:
        made_road = any(candle.high >= target for candle in left_side)
    else:
        made_road = any(candle.low <= target for candle in left_side)
    return made_road and noisy_touches <= max(6, len(left_side) // 5)


def _is_force_flip(action: SignalAction, latest: Candle, previous: Candle) -> bool:
    latest_body_high = max(latest.open, latest.close)
    latest_body_low = min(latest.open, latest.close)
    previous_body_high = max(previous.open, previous.close)
    previous_body_low = min(previous.open, previous.close)
    engulfs_previous_body = latest_body_high >= previous_body_high and latest_body_low <= previous_body_low

    if action == SignalAction.BUY:
        return latest.close > latest.open and previous.close < previous.open and engulfs_previous_body
    return latest.close < latest.open and previous.close > previous.open and engulfs_previous_body


def _has_wick_fill_target(action: SignalAction, candles: list[Candle], entry: float, target: float) -> bool:
    left_side = candles[-(TRAFFIC_LOOKBACK + 1) : -1]
    if action == SignalAction.BUY:
        return any(candle.high >= target and candle.high > max(candle.open, candle.close) for candle in left_side)
    return any(candle.low <= target and candle.low < min(candle.open, candle.close) for candle in left_side)


def _has_rejection(
    action: SignalAction,
    latest: Candle,
    support: float,
    resistance: float,
    atr_value: float,
) -> bool:
    body = max(abs(latest.close - latest.open), atr_value * 0.05)
    upper_wick = latest.high - max(latest.open, latest.close)
    lower_wick = min(latest.open, latest.close) - latest.low

    if action == SignalAction.BUY:
        near_support = latest.low <= support + (atr_value * 0.25)
        return latest.close > latest.open and lower_wick >= body * 0.8 and (near_support or latest.close > resistance)

    near_resistance = latest.high >= resistance - (atr_value * 0.25)
    return latest.close < latest.open and upper_wick >= body * 0.8 and (near_resistance or latest.close < support)


def _has_fib_context(action: SignalAction, candles: list[Candle], entry: float) -> bool:
    swing = _recent_swing(candles)
    if swing is None:
        return False
    swing_low, swing_high = swing
    swing_range = swing_high - swing_low
    if swing_range <= 0:
        return False

    fib_382 = swing_high - (swing_range * 0.382)
    fib_618 = swing_high - (swing_range * 0.618)
    if action == SignalAction.BUY:
        return fib_618 <= entry <= swing_high

    bearish_382 = swing_low + (swing_range * 0.382)
    bearish_618 = swing_low + (swing_range * 0.618)
    return swing_low <= entry <= bearish_618 or bearish_382 <= entry <= bearish_618


def _recent_swing(candles: list[Candle], lookback: int = 40) -> tuple[float, float] | None:
    recent = candles[-lookback:]
    if len(recent) < 10:
        return None
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
