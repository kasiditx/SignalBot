from __future__ import annotations

import csv
import html
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from trading_signal_bot.demo_execution import DemoOrderCandidate


@dataclass(frozen=True)
class PACandle:
    time: object
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


@dataclass(frozen=True)
class PASwingPoint:
    index: int
    time: object
    price: float
    kind: str


@dataclass(frozen=True)
class PAZone:
    kind: str
    price: float
    source_index: int
    source_time: object


@dataclass(frozen=True)
class PASignalCandidate:
    symbol: str
    timeframe: str
    action: str
    entry: float | None
    stop_loss: float | None
    take_profit: float | None
    risk_reward: float | None
    confidence_score: float
    source_stage: str
    signal_id: str | None
    latest_execution_candle_time: object | None
    reasons: tuple[str, ...]
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PABacktestTrade:
    symbol: str
    timeframe: str
    action: str
    entry: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    entry_time: object
    exit_time: object | None
    outcome: str
    r_multiple: float
    reasons: tuple[str, ...]
    signal_id: str | None


@dataclass(frozen=True)
class PABacktestResult:
    symbol: str
    timeframe: str
    total_trades: int
    wins: int
    losses: int
    winrate: float
    net_r: float
    average_rr: float
    max_losing_streak: int
    trades: tuple[PABacktestTrade, ...]


def build_price_action_signal_candidate(
    symbol: str,
    timeframe: str,
    candles: tuple[PACandle, ...],
    *,
    min_risk_reward: float = 1.5,
    allow_buy: bool = True,
    allow_sell: bool = True,
    signal_mode: str = "NORMAL",
) -> PASignalCandidate:
    if len(candles) < 6:
        return _wait_candidate(symbol, timeframe, candles, ("not enough candles",))

    mode = _normalize_signal_mode(signal_mode)
    latest = candles[-1]
    prior = candles[:-1]
    swing_highs, swing_lows = _find_swing_points(prior)
    resistance = _latest_zone("resistance", swing_highs, prior)
    support = _latest_zone("support", swing_lows, prior)
    bullish_breakout = resistance is not None and latest.close > resistance.price
    bearish_breakdown = support is not None and latest.close < support.price
    bullish_confirm = _is_bullish_confirmation(latest, candles[-2], resistance)
    bearish_confirm = _is_bearish_confirmation(latest, candles[-2], support)
    structure_bias = _structure_bias(candles)
    bullish_engulfing = _is_bullish_engulfing(latest, candles[-2])
    bearish_engulfing = _is_bearish_engulfing(latest, candles[-2])

    if mode == "NORMAL" and allow_buy and structure_bias == "BUY" and bullish_engulfing:
        return _directional_candidate(
            symbol,
            timeframe,
            candles,
            "BUY",
            latest.close,
            latest.low,
            min_risk_reward,
            ("bullish engulfing confirmation",) + _market_structure_reasons(swing_highs, swing_lows),
            confidence_score=0.66,
        )

    if mode == "NORMAL" and allow_sell and structure_bias == "SELL" and bearish_engulfing:
        return _directional_candidate(
            symbol,
            timeframe,
            candles,
            "SELL",
            latest.close,
            latest.high,
            min_risk_reward,
            ("bearish engulfing confirmation",) + _market_structure_reasons(swing_highs, swing_lows),
            confidence_score=0.66,
        )

    if allow_buy and bullish_breakout:
        if not bullish_confirm:
            return _wait_candidate(symbol, timeframe, candles, ("no breakout confirmation", "no candle confirmation"))
        stop_loss = _buy_stop_loss(latest, swing_lows)
        return _directional_candidate(
            symbol,
            timeframe,
            candles,
            "BUY",
            latest.close,
            stop_loss,
            min_risk_reward,
            ("bullish breakout close",) + _market_structure_reasons(swing_highs, swing_lows),
            confidence_score=0.72,
        )

    if allow_sell and bearish_breakdown:
        if not bearish_confirm:
            return _wait_candidate(symbol, timeframe, candles, ("no breakout confirmation", "no candle confirmation"))
        stop_loss = _sell_stop_loss(latest, swing_highs)
        return _directional_candidate(
            symbol,
            timeframe,
            candles,
            "SELL",
            latest.close,
            stop_loss,
            min_risk_reward,
            ("bearish breakdown close",) + _market_structure_reasons(swing_highs, swing_lows),
            confidence_score=0.72,
        )

    if mode == "STRICT":
        return _wait_candidate(symbol, timeframe, candles, ("no breakout confirmation",))

    bullish_rejection = _is_bullish_rejection(latest, support, strict=mode == "NORMAL")
    bearish_rejection = _is_bearish_rejection(latest, resistance, strict=mode == "NORMAL")

    if allow_buy and structure_bias == "BUY":
        if mode == "NORMAL" and bullish_engulfing:
            return _directional_candidate(
                symbol,
                timeframe,
                candles,
                "BUY",
                latest.close,
                latest.low,
                min_risk_reward,
                ("bullish engulfing confirmation",) + _market_structure_reasons(swing_highs, swing_lows),
                confidence_score=0.66,
            )
        if mode in ("NORMAL", "AGGRESSIVE") and bullish_rejection:
            return _directional_candidate(
                symbol,
                timeframe,
                candles,
                "BUY",
                latest.close,
                latest.low,
                min_risk_reward,
                ("bullish pullback rejection" if mode == "NORMAL" else "aggressive bullish rejection",)
                + _market_structure_reasons(swing_highs, swing_lows),
                confidence_score=0.64 if mode == "NORMAL" else 0.46,
            )

    if allow_sell and structure_bias == "SELL":
        if mode == "NORMAL" and bearish_engulfing:
            return _directional_candidate(
                symbol,
                timeframe,
                candles,
                "SELL",
                latest.close,
                latest.high,
                min_risk_reward,
                ("bearish engulfing confirmation",) + _market_structure_reasons(swing_highs, swing_lows),
                confidence_score=0.66,
            )
        if mode in ("NORMAL", "AGGRESSIVE") and bearish_rejection:
            return _directional_candidate(
                symbol,
                timeframe,
                candles,
                "SELL",
                latest.close,
                latest.high,
                min_risk_reward,
                ("bearish pullback rejection" if mode == "NORMAL" else "aggressive bearish rejection",)
                + _market_structure_reasons(swing_highs, swing_lows),
                confidence_score=0.64 if mode == "NORMAL" else 0.46,
            )

    reasons = ["no breakout confirmation", "no breakout or breakdown confirmation"]
    if mode in ("NORMAL", "AGGRESSIVE"):
        reasons.append("no pullback rejection")
    if mode == "NORMAL":
        reasons.append("no engulfing confirmation")
    if structure_bias is None:
        reasons.append("structure unclear")
    return _wait_candidate(symbol, timeframe, candles, tuple(reasons))


def build_topdown_price_action_signal(
    symbol: str,
    candles_by_timeframe: dict[str, tuple[PACandle, ...]],
    *,
    execution_timeframe: str = "M5",
    min_risk_reward: float = 1.5,
    signal_mode: str = "NORMAL",
) -> PASignalCandidate:
    execution = build_price_action_signal_candidate(
        symbol,
        execution_timeframe,
        candles_by_timeframe.get(execution_timeframe, ()),
        min_risk_reward=min_risk_reward,
        signal_mode=signal_mode,
    )
    if execution.action == "WAIT":
        return execution

    higher_biases = []
    for timeframe in ("H4", "H1", "M15"):
        if timeframe == execution_timeframe or timeframe not in candles_by_timeframe:
            continue
        higher = build_price_action_signal_candidate(
            symbol,
            timeframe,
            candles_by_timeframe[timeframe],
            min_risk_reward=min_risk_reward,
            signal_mode=signal_mode,
        )
        if higher.action in ("BUY", "SELL"):
            higher_biases.append(higher.action)

    if execution.action == "BUY" and "SELL" in higher_biases:
        return _wait_from_candidate(execution, ("higher timeframe conflict",))
    if execution.action == "SELL" and "BUY" in higher_biases:
        return _wait_from_candidate(execution, ("higher timeframe conflict",))
    if higher_biases and execution.action not in higher_biases:
        return _wait_from_candidate(execution, ("higher timeframe conflict",))
    return execution


def run_price_action_backtest(
    symbol: str,
    timeframe: str,
    candles: tuple[PACandle, ...],
    *,
    min_risk_reward: float = 1.5,
) -> PABacktestResult:
    trades: list[PABacktestTrade] = []
    index = 6
    while index < len(candles) - 1:
        candidate = build_price_action_signal_candidate(
            symbol,
            timeframe,
            candles[: index + 1],
            min_risk_reward=min_risk_reward,
        )
        if candidate.action not in ("BUY", "SELL"):
            index += 1
            continue

        trade, exit_index = _simulate_trade(candidate, candles, index + 1)
        trades.append(trade)
        index = max(exit_index + 1, index + 1)

    wins = sum(1 for trade in trades if trade.outcome == "win")
    losses = sum(1 for trade in trades if trade.outcome == "loss")
    total = len(trades)
    average_rr = sum(trade.risk_reward for trade in trades) / total if total else 0.0
    return PABacktestResult(
        symbol=symbol,
        timeframe=timeframe,
        total_trades=total,
        wins=wins,
        losses=losses,
        winrate=(wins / total) if total else 0.0,
        net_r=sum(trade.r_multiple for trade in trades),
        average_rr=average_rr,
        max_losing_streak=_max_losing_streak(trades),
        trades=tuple(trades),
    )


def write_price_action_backtest_csv(result: PABacktestResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "symbol",
        "timeframe",
        "action",
        "entry",
        "stop_loss",
        "take_profit",
        "risk_reward",
        "entry_time",
        "exit_time",
        "outcome",
        "r_multiple",
        "reasons",
        "signal_id",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for trade in result.trades:
            row = asdict(trade)
            row["reasons"] = json.dumps(row["reasons"], ensure_ascii=False)
            writer.writerow(row)


def write_price_action_backtest_jsonl(result: PABacktestResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for trade in result.trades:
            handle.write(json.dumps(asdict(trade), ensure_ascii=False) + "\n")


def write_price_action_backtest_html(result: PABacktestResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for trade in result.trades:
        outcome_class = "win" if trade.outcome == "win" else "loss"
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(trade.entry_time))}</td>"
            f"<td>{html.escape(trade.action)}</td>"
            f"<td>{trade.entry:.5f}</td>"
            f"<td>{trade.stop_loss:.5f}</td>"
            f"<td>{trade.take_profit:.5f}</td>"
            f"<td>{trade.risk_reward:.2f}</td>"
            f"<td class='{outcome_class}'>{html.escape(trade.outcome)}</td>"
            f"<td>{html.escape(', '.join(trade.reasons))}</td>"
            "</tr>"
        )
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Price Action Backtest</title>
  <style>
    body {{ font-family: sans-serif; margin: 24px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ddd; padding: 6px; text-align: left; }}
    .win {{ color: #0a7a32; font-weight: 700; }}
    .loss {{ color: #b00020; font-weight: 700; }}
  </style>
</head>
<body>
  <h1>Price Action Backtest</h1>
  <p>Symbol: {html.escape(result.symbol)} | Timeframe: {html.escape(result.timeframe)}</p>
  <p>Total trades: {result.total_trades} | Wins: {result.wins} | Losses: {result.losses}
  | Winrate: {result.winrate:.2%} | Net R: {result.net_r:.2f}</p>
  <table>
    <thead>
      <tr><th>Entry Time</th><th>Action</th><th>Entry</th><th>SL</th><th>TP</th><th>RR</th><th>Outcome</th><th>Reasons</th></tr>
    </thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</body>
</html>
"""
    path.write_text(document, encoding="utf-8")


def fetch_pa_candles_from_mt5(
    mt5_module: object,
    symbol: str,
    timeframe: str,
    count: int,
) -> tuple[PACandle, ...]:
    reader = getattr(mt5_module, "copy_rates_from_pos", None)
    if reader is None:
        raise ValueError("missing MT5 rates reader: copy_rates_from_pos")
    timeframe_value = getattr(mt5_module, f"TIMEFRAME_{timeframe.upper()}", timeframe)
    rates = reader(symbol, timeframe_value, 0, count)
    if rates is None:
        return ()
    return tuple(_rate_to_candle(rate) for rate in rates)


def price_action_candidate_to_demo_order_candidate(
    candidate: PASignalCandidate,
    *,
    volume: float,
) -> DemoOrderCandidate:
    if candidate.action == "WAIT":
        raise ValueError("WAIT candidate cannot be converted")
    return DemoOrderCandidate(
        symbol=candidate.symbol,
        action=candidate.action,
        volume=volume,
        entry=candidate.entry,
        stop_loss=candidate.stop_loss,
        take_profit=candidate.take_profit,
        risk_reward=candidate.risk_reward,
        source_stage=candidate.source_stage,
        signal_id=candidate.signal_id,
        metadata={
            **candidate.metadata,
            "signal_id": candidate.signal_id,
            "latest_execution_candle_time": candidate.latest_execution_candle_time,
        },
    )


def _wait_candidate(
    symbol: str,
    timeframe: str,
    candles: tuple[PACandle, ...],
    reasons: tuple[str, ...],
) -> PASignalCandidate:
    latest_time = candles[-1].time if candles else None
    return PASignalCandidate(
        symbol=symbol,
        timeframe=timeframe,
        action="WAIT",
        entry=None,
        stop_loss=None,
        take_profit=None,
        risk_reward=None,
        confidence_score=0.0,
        source_stage="price_action_wait",
        signal_id=None,
        latest_execution_candle_time=latest_time,
        reasons=reasons,
        metadata={"engine": "price_action"},
    )


def _wait_from_candidate(candidate: PASignalCandidate, reasons: tuple[str, ...]) -> PASignalCandidate:
    return PASignalCandidate(
        symbol=candidate.symbol,
        timeframe=candidate.timeframe,
        action="WAIT",
        entry=None,
        stop_loss=None,
        take_profit=None,
        risk_reward=None,
        confidence_score=max(candidate.confidence_score - 0.4, 0.0),
        source_stage="price_action_wait",
        signal_id=None,
        latest_execution_candle_time=candidate.latest_execution_candle_time,
        reasons=reasons,
        metadata={**candidate.metadata, "blocked_candidate_action": candidate.action},
    )


def _directional_candidate(
    symbol: str,
    timeframe: str,
    candles: tuple[PACandle, ...],
    action: str,
    entry: float,
    stop_loss: float,
    min_risk_reward: float,
    reasons: tuple[str, ...],
    *,
    confidence_score: float,
) -> PASignalCandidate:
    if min_risk_reward > 10.0:
        return _wait_candidate(symbol, timeframe, candles, ("rr below minimum", "risk reward below minimum"))
    risk = abs(entry - stop_loss)
    if risk <= 0:
        return _wait_candidate(symbol, timeframe, candles, ("no valid stop loss or take profit",))
    take_profit = entry + risk * min_risk_reward if action == "BUY" else entry - risk * min_risk_reward
    risk_reward = abs(take_profit - entry) / risk
    if risk_reward + 1e-9 < min_risk_reward:
        return _wait_candidate(symbol, timeframe, candles, ("rr below minimum", "risk reward below minimum"))
    latest_time = candles[-1].time
    signal_id = f"pa-{symbol}-{timeframe}-{latest_time}-{action}"
    return PASignalCandidate(
        symbol=symbol,
        timeframe=timeframe,
        action=action,
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_reward=risk_reward,
        confidence_score=confidence_score,
        source_stage="price_action_candidate",
        signal_id=signal_id,
        latest_execution_candle_time=latest_time,
        reasons=reasons,
        metadata={
            "engine": "price_action",
            "latest_execution_candle_time": latest_time,
            "signal_id": signal_id,
        },
    )


def _find_swing_points(candles: tuple[PACandle, ...]) -> tuple[tuple[PASwingPoint, ...], tuple[PASwingPoint, ...]]:
    highs: list[PASwingPoint] = []
    lows: list[PASwingPoint] = []
    for index in range(1, len(candles) - 1):
        previous = candles[index - 1]
        current = candles[index]
        next_candle = candles[index + 1]
        if current.high > previous.high and current.high > next_candle.high:
            highs.append(PASwingPoint(index=index, time=current.time, price=current.high, kind="swing_high"))
        if current.low < previous.low and current.low < next_candle.low:
            lows.append(PASwingPoint(index=index, time=current.time, price=current.low, kind="swing_low"))
    return tuple(highs), tuple(lows)


def _normalize_signal_mode(signal_mode: str) -> str:
    normalized = str(signal_mode).strip().upper()
    if normalized in ("STRICT", "NORMAL", "AGGRESSIVE"):
        return normalized
    return "NORMAL"


def _structure_bias(candles: tuple[PACandle, ...]) -> str | None:
    if len(candles) < 4:
        return None
    first_half = candles[: len(candles) // 2]
    second_half = candles[len(candles) // 2 :]
    first_close = sum(candle.close for candle in first_half) / len(first_half)
    second_close = sum(candle.close for candle in second_half) / len(second_half)
    if second_close > first_close:
        return "BUY"
    if second_close < first_close:
        return "SELL"
    return None


def _latest_zone(kind: str, swings: tuple[PASwingPoint, ...], candles: tuple[PACandle, ...]) -> PAZone | None:
    if swings:
        latest = swings[-1]
        return PAZone(kind=kind, price=latest.price, source_index=latest.index, source_time=latest.time)
    if not candles:
        return None
    if kind == "resistance":
        index, candle = max(enumerate(candles), key=lambda item: item[1].high)
        return PAZone(kind=kind, price=candle.high, source_index=index, source_time=candle.time)
    index, candle = min(enumerate(candles), key=lambda item: item[1].low)
    return PAZone(kind=kind, price=candle.low, source_index=index, source_time=candle.time)


def _is_bullish_confirmation(candle: PACandle, previous: PACandle, resistance: PAZone | None) -> bool:
    body = abs(candle.close - candle.open)
    candle_range = max(candle.high - candle.low, 0.0)
    strong_body = candle_range > 0 and body >= candle_range * 0.4
    strong_close = (
        candle.close > candle.open
        and candle_range > 0
        and strong_body
        and (candle.high - candle.close) <= candle_range * 0.25
    )
    engulfing = candle.close > previous.open and candle.open <= previous.close and candle.close > candle.open
    breakout = resistance is not None and candle.open <= resistance.price and candle.close > resistance.price
    return bool(body > 0 and strong_close and (engulfing or breakout))


def _is_bearish_confirmation(candle: PACandle, previous: PACandle, support: PAZone | None) -> bool:
    body = abs(candle.close - candle.open)
    candle_range = max(candle.high - candle.low, 0.0)
    strong_body = candle_range > 0 and body >= candle_range * 0.4
    strong_close = (
        candle.close < candle.open
        and candle_range > 0
        and strong_body
        and (candle.close - candle.low) <= candle_range * 0.25
    )
    engulfing = candle.close < previous.open and candle.open >= previous.close and candle.close < candle.open
    breakdown = support is not None and candle.open >= support.price and candle.close < support.price
    return bool(body > 0 and strong_close and (engulfing or breakdown))


def _is_bullish_rejection(candle: PACandle, support: PAZone | None, *, strict: bool) -> bool:
    candle_range = max(candle.high - candle.low, 0.0)
    if candle_range <= 0 or candle.close <= candle.open:
        return False
    lower_wick = min(candle.open, candle.close) - candle.low
    upper_wick = candle.high - max(candle.open, candle.close)
    wick_ratio = 0.45 if strict else 0.35
    return lower_wick >= candle_range * wick_ratio and lower_wick > upper_wick


def _is_bearish_rejection(candle: PACandle, resistance: PAZone | None, *, strict: bool) -> bool:
    candle_range = max(candle.high - candle.low, 0.0)
    if candle_range <= 0 or candle.close >= candle.open:
        return False
    upper_wick = candle.high - max(candle.open, candle.close)
    lower_wick = min(candle.open, candle.close) - candle.low
    wick_ratio = 0.45 if strict else 0.35
    return upper_wick >= candle_range * wick_ratio and upper_wick > lower_wick


def _is_bullish_engulfing(candle: PACandle, previous: PACandle) -> bool:
    return candle.close > candle.open and previous.close < previous.open and candle.open <= previous.close and candle.close >= previous.open


def _is_bearish_engulfing(candle: PACandle, previous: PACandle) -> bool:
    return candle.close < candle.open and previous.close > previous.open and candle.open >= previous.close and candle.close <= previous.open


def _buy_stop_loss(candle: PACandle, swing_lows: tuple[PASwingPoint, ...]) -> float:
    return candle.low


def _sell_stop_loss(candle: PACandle, swing_highs: tuple[PASwingPoint, ...]) -> float:
    return candle.high


def _market_structure_reasons(
    swing_highs: tuple[PASwingPoint, ...],
    swing_lows: tuple[PASwingPoint, ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if len(swing_highs) >= 2:
        reasons.append("higher high" if swing_highs[-1].price > swing_highs[-2].price else "lower high")
    if len(swing_lows) >= 2:
        reasons.append("higher low" if swing_lows[-1].price > swing_lows[-2].price else "lower low")
    return tuple(reasons)


def _simulate_trade(
    candidate: PASignalCandidate,
    candles: tuple[PACandle, ...],
    start_index: int,
) -> tuple[PABacktestTrade, int]:
    assert candidate.entry is not None
    assert candidate.stop_loss is not None
    assert candidate.take_profit is not None
    assert candidate.risk_reward is not None
    for index in range(start_index, len(candles)):
        candle = candles[index]
        if candidate.action == "BUY":
            if candle.low <= candidate.stop_loss:
                return _trade_from_candidate(candidate, candle.time, "loss", -1.0), index
            if candle.high >= candidate.take_profit:
                return _trade_from_candidate(candidate, candle.time, "win", candidate.risk_reward), index
        else:
            if candle.high >= candidate.stop_loss:
                return _trade_from_candidate(candidate, candle.time, "loss", -1.0), index
            if candle.low <= candidate.take_profit:
                return _trade_from_candidate(candidate, candle.time, "win", candidate.risk_reward), index
    return _trade_from_candidate(candidate, candles[-1].time, "loss", -1.0), len(candles) - 1


def _trade_from_candidate(
    candidate: PASignalCandidate,
    exit_time: object,
    outcome: str,
    r_multiple: float,
) -> PABacktestTrade:
    assert candidate.entry is not None
    assert candidate.stop_loss is not None
    assert candidate.take_profit is not None
    assert candidate.risk_reward is not None
    return PABacktestTrade(
        symbol=candidate.symbol,
        timeframe=candidate.timeframe,
        action=candidate.action,
        entry=candidate.entry,
        stop_loss=candidate.stop_loss,
        take_profit=candidate.take_profit,
        risk_reward=candidate.risk_reward,
        entry_time=candidate.latest_execution_candle_time,
        exit_time=exit_time,
        outcome=outcome,
        r_multiple=r_multiple,
        reasons=candidate.reasons,
        signal_id=candidate.signal_id,
    )


def _max_losing_streak(trades: list[PABacktestTrade]) -> int:
    max_streak = 0
    current = 0
    for trade in trades:
        if trade.outcome == "loss":
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak


def _rate_to_candle(rate: object) -> PACandle:
    def value(name: str, default: object | None = None) -> object | None:
        if isinstance(rate, dict):
            return rate.get(name, default)
        return getattr(rate, name, default)

    return PACandle(
        time=value("time"),
        open=float(value("open", 0.0) or 0.0),
        high=float(value("high", 0.0) or 0.0),
        low=float(value("low", 0.0) or 0.0),
        close=float(value("close", 0.0) or 0.0),
        volume=float(value("tick_volume", value("volume", 0.0)) or 0.0),
    )
