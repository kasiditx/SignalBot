from __future__ import annotations

from .indicators import atr, ema, rsi
from .models import Candle, Confidence, Signal, SignalAction, SignalConfig, TradeLevels, TrendDirection
from .multitimeframe import (
    confirmation_bias,
    dominant_bias,
    format_trend_summary,
    trend_map,
)
from .time_utils import parse_candle_timestamp

STRATEGY_NAME = "Pro MTF Price Action Structure"
ASIAN_BREAKOUT_STRATEGY_NAME = "Asian Range Breakout XAUUSD"
H4_BREAKOUT_RETEST_STRATEGY_NAME = "H4 Zone Breakout Retest XAUUSD"
STRUCTURE_LOOKBACK = 20
TRAFFIC_LOOKBACK = 40
RECENT_MOMENTUM_CANDLES = 7
DEFAULT_BODY_BREAK_ATR_RATIO = 0.20
EXHAUSTION_BODY_RATIO = 0.45
WICK_BUFFER_ATR_RATIO = 0.10
MAX_MANUAL_RISK_REWARD = 1.5
HIGHER_TIMEFRAMES = ("D1", "H4", "H1")
CONFIRMATION_TIMEFRAMES = ("M30", "M15")
ASIAN_SESSION_START_HOUR = 0
ASIAN_SESSION_END_HOUR = 8
LONDON_TRADE_END_HOUR = 15
ASIAN_BREAKOUT_BUFFER = 1.5
ASIAN_RANGE_MAX_ATR_MULTIPLIER = 2.5
ASIAN_STOP_ATR_MULTIPLIER = 2.0
ASIAN_TAKE_PROFIT_ATR_MULTIPLIER = 4.0
ASIAN_RISK_REWARD = ASIAN_TAKE_PROFIT_ATR_MULTIPLIER / ASIAN_STOP_ATR_MULTIPLIER


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

    if config.trade_mode == "asian_breakout":
        return _asian_range_breakout_signal(
            candles=candles,
            config=config,
            fast_now=fast_now,
            slow_now=slow_now,
            rsi_now=rsi_now,
            atr_now=atr_now,
            timeframe_candles=timeframe_candles,
        )
    if config.trade_mode == "h4_breakout_retest":
        return _h4_breakout_retest_signal(
            candles=candles,
            config=config,
            fast_now=fast_now,
            slow_now=slow_now,
            rsi_now=rsi_now,
            atr_now=atr_now,
            timeframe_candles=timeframe_candles,
        )

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
        config.trade_mode,
    )
    sell_trend_allowed = not config.multi_timeframe_enabled or _trend_allows(
        SignalAction.SELL,
        htf_bias,
        confirmation,
        trends,
        config.trade_mode,
    )

    if bullish_breakout and directional_buy_ok and buy_trend_allowed:
        if not _trade_mode_allows_setup(SignalAction.BUY, setup_type, config.trade_mode, config.multi_timeframe_enabled):
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
                setup_type="High-win-rate filter rejected bullish breakout",
                trend_summary=trend_summary,
                trend_alignment=trend_alignment,
                wait_reason=(
                    "โหมดคัดสัญญาณคุณภาพสูงไม่เข้า Buy breakout ทันที เพราะ backtest ล่าสุดให้ win rate ต่ำ "
                    "จึงรอ Buy เฉพาะ pullback/rejection continuation ที่มีโครงสร้างรองรับ"
                ),
            )
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


def _asian_range_breakout_signal(
    candles: list[Candle],
    config: SignalConfig,
    fast_now: float,
    slow_now: float,
    rsi_now: float,
    atr_now: float,
    timeframe_candles: dict[str, list[Candle]] | None,
) -> Signal:
    latest = candles[-1]
    previous = candles[-2]
    current_time = parse_candle_timestamp(latest.timestamp)
    current_hour = current_time.hour
    asian_candles = _asian_session_candles(candles, latest.timestamp)
    session_atr = _asian_session_atr(timeframe_candles, atr_now)
    fallback_support, fallback_resistance = _support_resistance(candles)
    trends = trend_map(timeframe_candles or {config.timeframe: candles})
    trend_summary = format_trend_summary(trends) if config.multi_timeframe_enabled else f"{config.timeframe}:{_single_timeframe_bias(candles).value}"

    if current_hour < ASIAN_SESSION_END_HOUR:
        return _asian_wait_signal(
            config=config,
            latest=latest,
            support=fallback_support,
            resistance=fallback_resistance,
            fast_now=fast_now,
            slow_now=slow_now,
            rsi_now=rsi_now,
            atr_now=atr_now,
            trend_summary=trend_summary,
            setup_type="Building Asian range",
            wait_reason="ยังอยู่ในช่วงเก็บกรอบเอเชีย 00:00-08:00 UTC จึงยังไม่เข้าเทรด",
        )

    if current_hour >= LONDON_TRADE_END_HOUR:
        return _asian_wait_signal(
            config=config,
            latest=latest,
            support=fallback_support,
            resistance=fallback_resistance,
            fast_now=fast_now,
            slow_now=slow_now,
            rsi_now=rsi_now,
            atr_now=atr_now,
            trend_summary=trend_summary,
            setup_type="London window closed",
            wait_reason="หมดช่วงเทรด London breakout 08:00-15:00 UTC แล้ว รอสร้างกรอบเอเชียวันถัดไป",
        )

    if len(asian_candles) < 12:
        return _asian_wait_signal(
            config=config,
            latest=latest,
            support=fallback_support,
            resistance=fallback_resistance,
            fast_now=fast_now,
            slow_now=slow_now,
            rsi_now=rsi_now,
            atr_now=atr_now,
            trend_summary=trend_summary,
            setup_type="Incomplete Asian range",
            wait_reason="ข้อมูลแท่งในช่วงเอเชียยังไม่พอสำหรับคำนวณกรอบ breakout",
        )

    asian_high = max(candle.high for candle in asian_candles)
    asian_low = min(candle.low for candle in asian_candles)
    asian_range = asian_high - asian_low
    if session_atr <= 0:
        wait_reason = "ATR ไม่พร้อมหรือเป็นศูนย์ จึงไม่คำนวณ SL/TP"
    elif asian_range > session_atr * ASIAN_RANGE_MAX_ATR_MULTIPLIER:
        wait_reason = (
            "กรอบเอเชียกว้างเกินไปเมื่อเทียบกับ ATR "
            f"range={asian_range:.2f}, ATR={session_atr:.2f}; ไม่ไล่ breakout วันที่ทองวิ่งไปมากแล้ว"
        )
    else:
        wait_reason = ""

    if wait_reason:
        return _asian_wait_signal(
            config=config,
            latest=latest,
            support=asian_low,
            resistance=asian_high,
            fast_now=fast_now,
            slow_now=slow_now,
            rsi_now=rsi_now,
            atr_now=session_atr,
            trend_summary=trend_summary,
            setup_type="Asian range filter rejected",
            wait_reason=wait_reason,
        )

    buy_level = asian_high + ASIAN_BREAKOUT_BUFFER
    sell_level = asian_low - ASIAN_BREAKOUT_BUFFER
    buy_breakout = previous.close <= buy_level < latest.close and latest.close > latest.open
    sell_breakout = previous.close >= sell_level > latest.close and latest.close < latest.open

    if buy_breakout:
        return _build_asian_breakout_signal(
            action=SignalAction.BUY,
            candles=candles,
            config=config,
            asian_low=asian_low,
            asian_high=asian_high,
            fast_now=fast_now,
            slow_now=slow_now,
            rsi_now=rsi_now,
            atr_now=session_atr,
            trend_summary=trend_summary,
        )
    if sell_breakout:
        return _build_asian_breakout_signal(
            action=SignalAction.SELL,
            candles=candles,
            config=config,
            asian_low=asian_low,
            asian_high=asian_high,
            fast_now=fast_now,
            slow_now=slow_now,
            rsi_now=rsi_now,
            atr_now=session_atr,
            trend_summary=trend_summary,
        )

    return _asian_wait_signal(
        config=config,
        latest=latest,
        support=asian_low,
        resistance=asian_high,
        fast_now=fast_now,
        slow_now=slow_now,
        rsi_now=rsi_now,
        atr_now=session_atr,
        trend_summary=trend_summary,
        setup_type="Waiting for Asian range breakout",
        wait_reason="รอแท่ง M5 ปิดทะลุกรอบเอเชียพร้อม buffer เพื่อเลี่ยง wick sweep/whipsaw",
    )


def _asian_session_atr(timeframe_candles: dict[str, list[Candle]] | None, fallback_atr: float) -> float:
    if not timeframe_candles:
        return fallback_atr
    h1_candles = timeframe_candles.get("H1")
    if not h1_candles or len(h1_candles) < 15:
        return fallback_atr
    return atr(h1_candles, 14)[-1]


def _h4_breakout_retest_signal(
    candles: list[Candle],
    config: SignalConfig,
    fast_now: float,
    slow_now: float,
    rsi_now: float,
    atr_now: float,
    timeframe_candles: dict[str, list[Candle]] | None,
) -> Signal:
    latest = candles[-1]
    latest_time = parse_candle_timestamp(latest.timestamp)
    fallback_support, fallback_resistance = _support_resistance(candles)
    trends = trend_map(timeframe_candles or {config.timeframe: candles})
    trend_summary = format_trend_summary(trends) if config.multi_timeframe_enabled else f"{config.timeframe}:{_single_timeframe_bias(candles).value}"
    h4_zone = _first_h4_zone_for_day(timeframe_candles, latest_time)
    if h4_zone is None:
        return _h4_wait_signal(
            config,
            latest,
            fallback_support,
            fallback_resistance,
            fast_now,
            slow_now,
            rsi_now,
            atr_now,
            trend_summary,
            "Waiting for H4 opening zone",
            "ยังไม่มี H4 แท่งแรกของวันสำหรับสร้างกรอบ breakout/retest",
        )

    zone_high, zone_low = h4_zone
    breakout = _latest_h4_zone_breakout(candles, zone_high, zone_low, config.h4_retest_buffer)
    if breakout is None:
        return _h4_wait_signal(
            config,
            latest,
            zone_low,
            zone_high,
            fast_now,
            slow_now,
            rsi_now,
            atr_now,
            trend_summary,
            "Waiting for H4 zone breakout",
            "รอ M5 ปิดทะลุ H4 opening range ก่อน จากนั้นค่อยรอ retest ไม่ไล่ราคา",
        )

    action, breakout_time = breakout
    if latest_time.date() != breakout_time.date() or (latest_time - breakout_time).total_seconds() > config.h4_retest_max_wait_seconds:
        return _h4_wait_signal(
            config,
            latest,
            zone_low,
            zone_high,
            fast_now,
            slow_now,
            rsi_now,
            atr_now,
            trend_summary,
            "H4 retest signal expired",
            "breakout เกิดนานเกิน MaxWaitSeconds หรือข้ามวันแล้ว จึงยกเลิกสิทธิ์รอ retest",
        )

    if not _is_h4_retest_confirmation(action, latest, zone_high, zone_low, config.h4_retest_tolerance):
        return _h4_wait_signal(
            config,
            latest,
            zone_low,
            zone_high,
            fast_now,
            slow_now,
            rsi_now,
            atr_now,
            trend_summary,
            "Waiting for H4 breakout retest",
            "ราคา breakout แล้ว แต่ยังไม่กลับมา retest โซนเดิมพร้อมปิดยืนยันใน M5",
        )

    if not _passes_pivot_momentum_filter(action, candles, latest, timeframe_candles, config, atr_now, rsi_now, zone_high, zone_low):
        return _h4_wait_signal(
            config,
            latest,
            zone_low,
            zone_high,
            fast_now,
            slow_now,
            rsi_now,
            atr_now,
            trend_summary,
            "H4 retest filter rejected",
            "retest เกิดแล้ว แต่ยังไม่ผ่าน Pivot + Momentum filter จึงกันสัญญาณหลอก",
        )

    return _build_h4_retest_signal(
        action=action,
        candles=candles,
        config=config,
        zone_low=zone_low,
        zone_high=zone_high,
        fast_now=fast_now,
        slow_now=slow_now,
        rsi_now=rsi_now,
        atr_now=atr_now,
        trend_summary=trend_summary,
    )


def _first_h4_zone_for_day(
    timeframe_candles: dict[str, list[Candle]] | None,
    current_time,
) -> tuple[float, float] | None:
    if not timeframe_candles:
        return None
    h4_candles = timeframe_candles.get("H4")
    if not h4_candles:
        return None
    current_date = current_time.date()
    same_day = [candle for candle in h4_candles if parse_candle_timestamp(candle.timestamp).date() == current_date]
    if not same_day:
        return None
    opening_candle = min(same_day, key=lambda candle: parse_candle_timestamp(candle.timestamp))
    return opening_candle.high, opening_candle.low


def _latest_h4_zone_breakout(
    candles: list[Candle],
    zone_high: float,
    zone_low: float,
    buffer: float,
) -> tuple[SignalAction, object] | None:
    buy_level = zone_high + buffer
    sell_level = zone_low - buffer
    for candle in reversed(candles[:-1]):
        candle_time = parse_candle_timestamp(candle.timestamp)
        if candle.close > buy_level:
            return SignalAction.BUY, candle_time
        if candle.close < sell_level:
            return SignalAction.SELL, candle_time
    return None


def _is_h4_retest_confirmation(
    action: SignalAction,
    latest: Candle,
    zone_high: float,
    zone_low: float,
    tolerance: float,
) -> bool:
    if action == SignalAction.BUY:
        touched_zone = latest.low <= zone_high + tolerance
        reclaimed_zone = latest.close > zone_high and latest.close > latest.open
        return touched_zone and reclaimed_zone
    touched_zone = latest.high >= zone_low - tolerance
    reclaimed_zone = latest.close < zone_low and latest.close < latest.open
    return touched_zone and reclaimed_zone


def _passes_pivot_momentum_filter(
    action: SignalAction,
    candles: list[Candle],
    latest: Candle,
    timeframe_candles: dict[str, list[Candle]] | None,
    config: SignalConfig,
    atr_now: float,
    rsi_now: float,
    zone_high: float,
    zone_low: float,
) -> bool:
    near_significant_zone = (
        abs(latest.close - zone_high) <= config.h4_retest_pivot_tolerance
        or abs(latest.close - zone_low) <= config.h4_retest_pivot_tolerance
        or _is_near_daily_or_weekly_pivot(latest.close, timeframe_candles, config.h4_retest_pivot_tolerance)
    )
    if not near_significant_zone:
        return False
    recent = candles[-21:-1]
    if not recent:
        return False
    average_volume = sum(candle.volume for candle in recent) / len(recent)
    volume_expansion = latest.volume >= average_volume * config.h4_retest_momentum_volume_multiplier
    body = abs(latest.close - latest.open)
    body_momentum = atr_now > 0 and body >= atr_now * 0.35
    directional_close = latest.close > latest.open if action == SignalAction.BUY else latest.close < latest.open
    rsi_momentum = rsi_now > 50 if action == SignalAction.BUY else rsi_now < 50
    return directional_close and volume_expansion and body_momentum and rsi_momentum


def _is_near_daily_or_weekly_pivot(
    price: float,
    timeframe_candles: dict[str, list[Candle]] | None,
    tolerance: float,
) -> bool:
    if not timeframe_candles:
        return False
    pivots = _daily_weekly_pivots(timeframe_candles.get("D1") or [])
    return any(abs(price - pivot) <= tolerance for pivot in pivots)


def _daily_weekly_pivots(d1_candles: list[Candle]) -> tuple[float, ...]:
    if len(d1_candles) < 2:
        return ()
    previous_day = d1_candles[-2]
    daily_pivot = (previous_day.high + previous_day.low + previous_day.close) / 3.0
    pivots = [daily_pivot]
    previous_week = d1_candles[-7:-2]
    if previous_week:
        weekly_high = max(candle.high for candle in previous_week)
        weekly_low = min(candle.low for candle in previous_week)
        weekly_close = previous_week[-1].close
        pivots.append((weekly_high + weekly_low + weekly_close) / 3.0)
    return tuple(pivots)


def _h4_wait_signal(
    config: SignalConfig,
    latest: Candle,
    support: float,
    resistance: float,
    fast_now: float,
    slow_now: float,
    rsi_now: float,
    atr_now: float,
    trend_summary: str,
    setup_type: str,
    wait_reason: str,
) -> Signal:
    return Signal(
        action=SignalAction.WAIT,
        symbol=config.symbol,
        timeframe=config.timeframe,
        strategy_name=H4_BREAKOUT_RETEST_STRATEGY_NAME,
        market_structure="H4 opening range breakout with M5 retest confirmation",
        setup_type=setup_type,
        trend_summary=trend_summary,
        trend_alignment="H4 zone first, M5 retest second, Pivot + Momentum filter required",
        confidence=Confidence.LOW,
        reason=wait_reason,
        entry_condition="รอ breakout จาก H4 opening range แล้วรอ M5 retest/confirm ไม่ไล่ราคา",
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


def _build_h4_retest_signal(
    action: SignalAction,
    candles: list[Candle],
    config: SignalConfig,
    zone_low: float,
    zone_high: float,
    fast_now: float,
    slow_now: float,
    rsi_now: float,
    atr_now: float,
    trend_summary: str,
) -> Signal:
    latest = candles[-1]
    entry = latest.close
    stop_buffer = max(config.h4_retest_stop_buffer, atr_now * 0.2)
    if action == SignalAction.BUY:
        stop_loss = min(latest.low, zone_high) - stop_buffer
        risk_distance = entry - stop_loss
        take_profit = entry + risk_distance * config.risk_reward
        setup_type = "H4 bullish breakout retest"
        reason = "ราคาทะลุ H4 opening range แล้วกลับมา retest กรอบบน พร้อม Pivot + Momentum confirmation"
        entry_condition = "Buy เมื่อ M5 retest H4 high เดิมแล้วปิดกลับเหนือโซนด้วย momentum"
        invalidation = "ยกเลิก/คัทหากราคากลับลงใต้โซน retest และชน SL"
    else:
        stop_loss = max(latest.high, zone_low) + stop_buffer
        risk_distance = stop_loss - entry
        take_profit = entry - risk_distance * config.risk_reward
        setup_type = "H4 bearish breakout retest"
        reason = "ราคาทะลุ H4 opening range ลง แล้วกลับมา retest กรอบล่าง พร้อม Pivot + Momentum confirmation"
        entry_condition = "Sell เมื่อ M5 retest H4 low เดิมแล้วปิดกลับใต้โซนด้วย momentum"
        invalidation = "ยกเลิก/คัทหากราคากลับขึ้นเหนือโซน retest และชน SL"

    return Signal(
        action=action,
        symbol=config.symbol,
        timeframe=config.timeframe,
        strategy_name=H4_BREAKOUT_RETEST_STRATEGY_NAME,
        market_structure="H4 opening range breakout with M5 retest confirmation",
        setup_type=setup_type,
        trend_summary=trend_summary,
        trend_alignment="H4 breakout happened first; M5 retest confirmed; Pivot + Momentum filter passed",
        confidence=Confidence.MEDIUM,
        reason=reason,
        entry_condition=entry_condition,
        invalidation=invalidation,
        no_trade_reason="ห้ามเข้าเพิ่มถ้าเลย MaxWaitSeconds, ไม่ผ่าน pivot/momentum, หรือ daily drawdown pause ทำงาน",
        support=zone_low,
        resistance=zone_high,
        latest_close=latest.close,
        fast_ema=fast_now,
        slow_ema=slow_now,
        rsi=rsi_now,
        atr=atr_now,
        levels=TradeLevels(entry=entry, stop_loss=stop_loss, take_profit=take_profit, risk_reward=config.risk_reward),
        breakeven_trigger_r=config.h4_retest_breakeven_trigger_r,
    )


def _asian_session_candles(candles: list[Candle], latest_timestamp: str) -> list[Candle]:
    latest_time = parse_candle_timestamp(latest_timestamp)
    latest_date = latest_time.date()
    return [
        candle
        for candle in candles
        if parse_candle_timestamp(candle.timestamp).date() == latest_date
        and ASIAN_SESSION_START_HOUR <= parse_candle_timestamp(candle.timestamp).hour < ASIAN_SESSION_END_HOUR
    ]


def _asian_wait_signal(
    config: SignalConfig,
    latest: Candle,
    support: float,
    resistance: float,
    fast_now: float,
    slow_now: float,
    rsi_now: float,
    atr_now: float,
    trend_summary: str,
    setup_type: str,
    wait_reason: str,
) -> Signal:
    return Signal(
        action=SignalAction.WAIT,
        symbol=config.symbol,
        timeframe=config.timeframe,
        strategy_name=ASIAN_BREAKOUT_STRATEGY_NAME,
        market_structure="Session momentum: Asian range then London breakout",
        setup_type=setup_type,
        trend_summary=trend_summary,
        trend_alignment="Session filter: trade only 08:00-15:00 UTC after Asian range completes",
        confidence=Confidence.LOW,
        reason=wait_reason,
        entry_condition="รอ M5 body close ทะลุ Asian high/low + buffer ในช่วง London",
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


def _build_asian_breakout_signal(
    action: SignalAction,
    candles: list[Candle],
    config: SignalConfig,
    asian_low: float,
    asian_high: float,
    fast_now: float,
    slow_now: float,
    rsi_now: float,
    atr_now: float,
    trend_summary: str,
) -> Signal:
    latest = candles[-1]
    entry = latest.close
    risk_distance = max(atr_now * ASIAN_STOP_ATR_MULTIPLIER, ASIAN_BREAKOUT_BUFFER)
    if config.asian_max_stop_distance > 0:
        risk_distance = min(risk_distance, config.asian_max_stop_distance)
    if action == SignalAction.BUY:
        stop_loss = entry - risk_distance
        take_profit = entry + (risk_distance * ASIAN_RISK_REWARD)
        setup_type = "Asian range bullish breakout"
        entry_condition = "Buy เมื่อ M5 ปิดเหนือ Asian high + buffer ในช่วง London"
        invalidation = "ยกเลิก/คัทหากราคากลับลงมาชน SL ใต้ entry ตาม ATR stop"
        reason = "London session ดันราคาปิดทะลุกรอบบนของเอเชีย เป็น session momentum breakout"
    else:
        stop_loss = entry + risk_distance
        take_profit = entry - (risk_distance * ASIAN_RISK_REWARD)
        setup_type = "Asian range bearish breakout"
        entry_condition = "Sell เมื่อ M5 ปิดใต้ Asian low - buffer ในช่วง London"
        invalidation = "ยกเลิก/คัทหากราคากลับขึ้นมาชน SL เหนือ entry ตาม ATR stop"
        reason = "London session กดราคาปิดทะลุกรอบล่างของเอเชีย เป็น session momentum breakdown"

    confidence = Confidence.MEDIUM
    if atr_now > 0 and (asian_high - asian_low) <= atr_now * 1.5:
        confidence = Confidence.HIGH

    return Signal(
        action=action,
        symbol=config.symbol,
        timeframe=config.timeframe,
        strategy_name=ASIAN_BREAKOUT_STRATEGY_NAME,
        market_structure="Session momentum: Asian range compression to London expansion",
        setup_type=setup_type,
        trend_summary=trend_summary,
        trend_alignment="Asian range complete; London window breakout confirmed by M5 body close",
        confidence=confidence,
        reason=reason,
        entry_condition=entry_condition,
        invalidation=invalidation,
        no_trade_reason="หยุดหลัง 1 order ต่อวัน และห้ามเข้าเพิ่มหาก actual risk เกินเพดาน small-account mode",
        support=asian_low,
        resistance=asian_high,
        latest_close=latest.close,
        fast_ema=fast_now,
        slow_ema=slow_now,
        rsi=rsi_now,
        atr=atr_now,
        levels=TradeLevels(
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward=ASIAN_RISK_REWARD,
        ),
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
    trade_mode: str,
) -> bool:
    if action == SignalAction.BUY:
        return (
            htf_bias == TrendDirection.BULLISH
            and confirmation == TrendDirection.BULLISH
            and _timeframes_are_aligned(action, trends, CONFIRMATION_TIMEFRAMES)
            and _trade_mode_allows_execution_timeframe(action, trends, trade_mode)
            and not _has_opposite_higher_timeframe(action, trends)
        )
    if action == SignalAction.SELL:
        return (
            htf_bias == TrendDirection.BEARISH
            and confirmation == TrendDirection.BEARISH
            and _timeframes_are_aligned(action, trends, CONFIRMATION_TIMEFRAMES)
            and _trade_mode_allows_execution_timeframe(action, trends, trade_mode)
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


def _execution_timeframe_is_aligned(action: SignalAction, trends: dict[str, TrendDirection]) -> bool:
    expected = TrendDirection.BULLISH if action == SignalAction.BUY else TrendDirection.BEARISH
    return trends.get("M5") == expected


def _trade_mode_allows_execution_timeframe(
    action: SignalAction,
    trends: dict[str, TrendDirection],
    trade_mode: str,
) -> bool:
    if trade_mode == "active":
        return True
    return _execution_timeframe_is_aligned(action, trends)


def _trade_mode_allows_setup(
    action: SignalAction,
    setup_type: str,
    trade_mode: str,
    multi_timeframe_enabled: bool,
) -> bool:
    if not multi_timeframe_enabled:
        return True
    if trade_mode == "active":
        return True
    return not (action == SignalAction.BUY and setup_type == "Bullish body-close breakout")


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
