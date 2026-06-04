from __future__ import annotations

import importlib
import os

from trading_signal_bot.price_action_signal_engine import (
    build_price_action_signal_candidate,
    fetch_pa_candles_from_mt5,
)


def _text_env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


def _int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value and value.strip() else default


def _float_env(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value and value.strip() else default


def _load_mt5_module() -> object:
    try:
        return importlib.import_module("MetaTrader5")
    except ImportError as exc:
        raise RuntimeError("MetaTrader5 package is required for signal preview") from exc


def main() -> int:
    symbol = _text_env("PA_SIGNAL_SYMBOL", "XAUUSD.iux")
    timeframe = _text_env("PA_SIGNAL_EXECUTION_TIMEFRAME", "M5")
    count = _int_env("PA_SIGNAL_CANDLE_COUNT", 300)
    min_rr = _float_env("PA_SIGNAL_MIN_RR", 1.5)
    signal_mode = _text_env("PA_SIGNAL_MODE", "NORMAL").upper()

    mt5 = _load_mt5_module()
    initialize = getattr(mt5, "initialize", None)
    if initialize is not None and not initialize():
        last_error = getattr(mt5, "last_error", lambda: None)
        raise RuntimeError(f"MT5 initialize failed: {last_error()}")
    try:
        candles = fetch_pa_candles_from_mt5(mt5, symbol, timeframe, count)
        candidate = build_price_action_signal_candidate(
            symbol,
            timeframe,
            candles,
            min_risk_reward=min_rr,
            signal_mode=signal_mode,
        )
        print(f"signal_mode={signal_mode}")
        print(f"action={candidate.action}")
        print(f"entry={candidate.entry}")
        print(f"stop_loss={candidate.stop_loss}")
        print(f"take_profit={candidate.take_profit}")
        print(f"risk_reward={candidate.risk_reward}")
        print(f"confidence_score={candidate.confidence_score}")
        print(f"reasons={candidate.reasons}")
        print(f"signal_id={candidate.signal_id}")
        print(f"latest_execution_candle_time={candidate.latest_execution_candle_time}")
        return 0
    finally:
        shutdown = getattr(mt5, "shutdown", None)
        if shutdown is not None:
            shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
