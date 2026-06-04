from __future__ import annotations

import importlib
import os
from pathlib import Path

from trading_signal_bot.price_action_signal_engine import (
    fetch_pa_candles_from_mt5,
    run_price_action_backtest,
    write_price_action_backtest_csv,
    write_price_action_backtest_html,
    write_price_action_backtest_jsonl,
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
        raise RuntimeError("MetaTrader5 package is required for price action backtest") from exc


def main() -> int:
    symbol = _text_env("PA_BACKTEST_SYMBOL", "XAUUSD.iux")
    timeframe = _text_env("PA_BACKTEST_TIMEFRAME", "M5")
    count = _int_env("PA_BACKTEST_CANDLE_COUNT", 1000)
    output_dir = Path(_text_env("PA_BACKTEST_OUTPUT_DIR", "logs/price_action_backtest"))
    min_rr = _float_env("PA_BACKTEST_MIN_RR", 1.5)

    mt5 = _load_mt5_module()
    initialize = getattr(mt5, "initialize", None)
    if initialize is not None and not initialize():
        last_error = getattr(mt5, "last_error", lambda: None)
        raise RuntimeError(f"MT5 initialize failed: {last_error()}")
    try:
        candles = fetch_pa_candles_from_mt5(mt5, symbol, timeframe, count)
        result = run_price_action_backtest(symbol, timeframe, candles, min_risk_reward=min_rr)
        csv_path = output_dir / "backtest_trades.csv"
        jsonl_path = output_dir / "backtest_trades.jsonl"
        html_path = output_dir / "backtest_report.html"
        write_price_action_backtest_csv(result, csv_path)
        write_price_action_backtest_jsonl(result, jsonl_path)
        write_price_action_backtest_html(result, html_path)
        print(f"symbol={symbol}")
        print(f"timeframe={timeframe}")
        print(f"candles={len(candles)}")
        print(f"total_trades={result.total_trades}")
        print(f"wins={result.wins}")
        print(f"losses={result.losses}")
        print(f"winrate={result.winrate:.2%}")
        print(f"net_r={result.net_r:.2f}")
        print(f"csv={csv_path}")
        print(f"jsonl={jsonl_path}")
        print(f"html={html_path}")
        return 0
    finally:
        shutdown = getattr(mt5, "shutdown", None)
        if shutdown is not None:
            shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
