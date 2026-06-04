from __future__ import annotations

import importlib
import os
from dataclasses import replace
from pathlib import Path

from trading_signal_bot.demo_execution import (
    DemoExecutionGuardResult,
    DemoExecutionState,
    build_demo_order_intent,
    default_demo_execution_config,
)
from trading_signal_bot.demo_mt5_sender import (
    build_mt5_demo_send_fn,
    fetch_demo_open_positions,
    load_demo_order_records_jsonl,
    send_demo_order,
    validate_demo_execution_gate,
    verify_mt5_demo_account,
    write_demo_order_record,
)
from trading_signal_bot.price_action_signal_engine import (
    build_price_action_signal_candidate,
    fetch_pa_candles_from_mt5,
    price_action_candidate_to_demo_order_candidate,
)


OUTPUT_DIR = Path("logs/forward_validation")
DEMO_ORDER_CSV_PATH = OUTPUT_DIR / "demo_order_records.csv"
DEMO_ORDER_JSONL_PATH = OUTPUT_DIR / "demo_order_records.jsonl"
MAX_DEMO_VOLUME = 0.01
DEFAULT_COMMENT = "SB PA demo"
MAX_MT5_COMMENT_LENGTH = 31
SAFETY_FOOTER = (
    "Demo PA order only.\n"
    "Live account is not allowed.\n"
    "Real-money trading is blocked."
)


def _text_env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _float_env(name: str, default: float) -> float:
    value = _text_env(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def _int_env(name: str, default: int) -> int:
    value = _text_env(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _bool_env_true(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() == "true"


def _sanitize_comment(comment: str | None) -> str:
    text = (comment or DEFAULT_COMMENT).strip()
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = "".join(character for character in text if 32 <= ord(character) < 127)
    text = " ".join(text.split())
    if not text:
        text = DEFAULT_COMMENT
    return text[:MAX_MT5_COMMENT_LENGTH]


def _load_mt5_module() -> object:
    try:
        return importlib.import_module("MetaTrader5")
    except ImportError as exc:
        raise RuntimeError("MetaTrader5 package is required for PA demo order") from exc


def _initialize_mt5(mt5_module: object) -> None:
    initialize = getattr(mt5_module, "initialize", None)
    if initialize is not None and not initialize():
        last_error = getattr(mt5_module, "last_error", lambda: None)
        raise RuntimeError(f"MT5 initialize failed: {last_error()}")


def _shutdown_mt5(mt5_module: object) -> None:
    shutdown = getattr(mt5_module, "shutdown", None)
    if shutdown is not None:
        shutdown()


def _spread_points(mt5_module: object, symbol: str) -> float:
    symbol_info_tick = getattr(mt5_module, "symbol_info_tick", None)
    symbol_info = getattr(mt5_module, "symbol_info", None)
    if symbol_info_tick is None or symbol_info is None:
        raise RuntimeError("MT5 tick and symbol info functions are required")

    tick = symbol_info_tick(symbol)
    info = symbol_info(symbol)
    if tick is None:
        raise RuntimeError(f"missing MT5 tick for {symbol}")
    if info is None:
        raise RuntimeError(f"missing MT5 symbol info for {symbol}")

    bid = getattr(tick, "bid", None)
    ask = getattr(tick, "ask", None)
    point = getattr(info, "point", None)
    if bid is None or ask is None or point in (None, 0):
        raise RuntimeError("bid, ask, and point are required to calculate spread")
    return abs(float(ask) - float(bid)) / float(point)


def main() -> int:
    print(SAFETY_FOOTER)
    try:
        symbol = _text_env("PA_SIGNAL_SYMBOL", "XAUUSD.iux") or "XAUUSD.iux"
        timeframe = _text_env("PA_SIGNAL_EXECUTION_TIMEFRAME", "M5") or "M5"
        candle_count = _int_env("PA_SIGNAL_CANDLE_COUNT", 300)
        min_rr = _float_env("PA_SIGNAL_MIN_RR", 1.5)
        signal_mode = (_text_env("PA_SIGNAL_MODE", "NORMAL") or "NORMAL").upper()
        volume = _float_env("DEMO_SMOKE_VOLUME", MAX_DEMO_VOLUME)
        if volume > MAX_DEMO_VOLUME:
            raise ValueError("DEMO_SMOKE_VOLUME must be <= 0.01 for demo PA order")

        config = replace(
            default_demo_execution_config(OUTPUT_DIR),
            max_spread_points=_float_env("DEMO_SMOKE_MAX_SPREAD_POINTS", 50.0),
        )
        allow_pyramiding = _bool_env_true("DEMO_ALLOW_PYRAMIDING")
        max_same_symbol_positions = _int_env("DEMO_MAX_SAME_SYMBOL_POSITIONS", 1)
        if max_same_symbol_positions < 1:
            raise ValueError("DEMO_MAX_SAME_SYMBOL_POSITIONS must be >= 1")
        comment = _sanitize_comment(_text_env("DEMO_SMOKE_COMMENT", DEFAULT_COMMENT))

        mt5 = _load_mt5_module()
        _initialize_mt5(mt5)
        try:
            candles = fetch_pa_candles_from_mt5(mt5, symbol, timeframe, candle_count)
            pa_candidate = build_price_action_signal_candidate(
                symbol,
                timeframe,
                candles,
                min_risk_reward=min_rr,
                signal_mode=signal_mode,
            )
            print(f"signal_mode={signal_mode}")
            print(f"action={pa_candidate.action}")
            print(f"entry={pa_candidate.entry}")
            print(f"stop_loss={pa_candidate.stop_loss}")
            print(f"take_profit={pa_candidate.take_profit}")
            print(f"risk_reward={pa_candidate.risk_reward}")
            print(f"confidence_score={pa_candidate.confidence_score}")
            print(f"reasons={pa_candidate.reasons}")
            print(f"signal_id={pa_candidate.signal_id}")
            print(f"latest_execution_candle_time={pa_candidate.latest_execution_candle_time}")
            if pa_candidate.action == "WAIT":
                print("status=pa_signal_wait")
                print(SAFETY_FOOTER)
                return 0

            account_guard = verify_mt5_demo_account(mt5)
            if not account_guard.approved:
                print(f"Rejected: {', '.join(account_guard.reasons)}")
                return 1

            demo_candidate = price_action_candidate_to_demo_order_candidate(pa_candidate, volume=volume)
            intent = build_demo_order_intent(
                demo_candidate,
                DemoExecutionGuardResult(approved=True, reasons=()),
                comment=comment,
            )

            spread_points = _spread_points(mt5, symbol)
            positions = fetch_demo_open_positions(mt5, symbol=symbol)
            records = load_demo_order_records_jsonl(DEMO_ORDER_JSONL_PATH)
            print(f"spread_points={spread_points}")
            print(f"open_positions_count={len(positions)}")
            print(f"demo_order_records_count={len(records)}")
            print(f"allow_pyramiding={allow_pyramiding}")
            print(f"max_same_symbol_positions={max_same_symbol_positions}")

            gate = validate_demo_execution_gate(
                intent,
                config,
                DemoExecutionState(),
                account_mode="demo",
                spread_points=spread_points,
                positions=positions,
                records=records,
                allow_pyramiding=allow_pyramiding,
                max_same_symbol_positions=max_same_symbol_positions,
                reject_opposite_action=True,
            )
            if not gate.approved:
                print("status=demo_send_blocked")
                print(f"reasons={gate.reasons}")
                print(SAFETY_FOOTER)
                return 1

            result = send_demo_order(
                intent,
                mt5,
                send_fn=build_mt5_demo_send_fn(mt5),
            )
            try:
                write_demo_order_record(
                    intent,
                    result,
                    csv_path=DEMO_ORDER_CSV_PATH,
                    jsonl_path=DEMO_ORDER_JSONL_PATH,
                )
                print(f"demo_order_csv_log={DEMO_ORDER_CSV_PATH}")
                print(f"demo_order_jsonl_log={DEMO_ORDER_JSONL_PATH}")
            except Exception as log_exc:
                print(f"Warning: failed to write demo order lifecycle log: {log_exc}")

            record = result.lifecycle_record
            print(f"status={record.stage}")
            print(f"attempted={result.attempted}")
            print(f"accepted={result.accepted}")
            print(f"error_message={result.error_message}")
            print(f"retcode={record.mt5_retcode}")
            print(f"mt5_comment={record.mt5_comment}")
            print(f"ticket={record.ticket}")
            last_error = getattr(mt5, "last_error", None)
            if last_error is not None:
                print(f"mt5_last_error={last_error()}")
            if not result.accepted:
                print("Do not retry until retcode/comment/last_error is reviewed.")
            print(SAFETY_FOOTER)
            return 0 if result.accepted else 1
        finally:
            _shutdown_mt5(mt5)
    except Exception as exc:
        print(f"Rejected: {exc}")
        print(SAFETY_FOOTER)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
