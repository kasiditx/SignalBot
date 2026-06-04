from __future__ import annotations

import importlib
import os
from dataclasses import replace
from pathlib import Path

from trading_signal_bot.demo_execution import (
    DemoExecutionGuardResult,
    DemoExecutionState,
    DemoOrderCandidate,
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


OUTPUT_DIR = Path("logs/forward_validation")
DEMO_ORDER_CSV_PATH = OUTPUT_DIR / "demo_order_records.csv"
DEMO_ORDER_JSONL_PATH = OUTPUT_DIR / "demo_order_records.jsonl"
MAX_SMOKE_VOLUME = 0.01
DEFAULT_SMOKE_COMMENT = "SB demo smoke"
MAX_MT5_COMMENT_LENGTH = 31
SAFETY_FOOTER = (
    "Demo smoke test only.\n"
    "Live account is not allowed.\n"
    "Real-money trading is blocked."
)


def _text_env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def _required_text_env(name: str) -> str:
    value = _text_env(name)
    if value is None:
        raise ValueError(f"{name} is required")
    return value


def _float_env(name: str, *, default: float | None = None) -> float:
    value = _text_env(name)
    if value is None:
        if default is None:
            raise ValueError(f"{name} is required")
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def _int_env(name: str, *, default: int | None = None) -> int:
    value = _text_env(name)
    if value is None:
        if default is None:
            raise ValueError(f"{name} is required")
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _bool_env_true(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() == "true"


def sanitize_mt5_comment(comment: str | None) -> str:
    text = (comment or DEFAULT_SMOKE_COMMENT).strip()
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = "".join(character for character in text if 32 <= ord(character) < 127)
    text = " ".join(text.split())
    if not text:
        text = DEFAULT_SMOKE_COMMENT
    return text[:MAX_MT5_COMMENT_LENGTH]


def _load_mt5_module() -> object:
    try:
        return importlib.import_module("MetaTrader5")
    except ImportError as exc:
        raise RuntimeError("MetaTrader5 package is required for manual demo smoke test") from exc


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


def _build_candidate() -> DemoOrderCandidate:
    action = _required_text_env("DEMO_SMOKE_ACTION").upper()
    if action not in ("BUY", "SELL"):
        raise ValueError("DEMO_SMOKE_ACTION must be BUY or SELL")

    volume = _float_env("DEMO_SMOKE_VOLUME", default=MAX_SMOKE_VOLUME)
    if volume > MAX_SMOKE_VOLUME:
        raise ValueError("DEMO_SMOKE_VOLUME must be <= 0.01 for smoke test")

    signal_id = _text_env("DEMO_SMOKE_SIGNAL_ID") or "manual-demo-smoke-001"
    candle_time = _text_env("DEMO_SMOKE_CANDLE_TIME") or signal_id
    return DemoOrderCandidate(
        symbol=_required_text_env("DEMO_SMOKE_SYMBOL"),
        action=action,
        volume=volume,
        entry=_float_env("DEMO_SMOKE_ENTRY"),
        stop_loss=_float_env("DEMO_SMOKE_STOP_LOSS"),
        take_profit=_float_env("DEMO_SMOKE_TAKE_PROFIT"),
        risk_reward=_float_env("DEMO_SMOKE_RISK_REWARD"),
        source_stage="manual_demo_smoke",
        signal_id=signal_id,
        metadata={
            "source": "manual_demo_smoke_test",
            "signal_id": signal_id,
            "latest_execution_candle_time": candle_time,
        },
    )


def main() -> int:
    print(SAFETY_FOOTER)
    if not _bool_env_true("DEMO_EXECUTION_ENABLED"):
        print("Rejected: DEMO_EXECUTION_ENABLED must be true")
        return 1

    try:
        candidate = _build_candidate()
        config = replace(
            default_demo_execution_config(OUTPUT_DIR),
            max_spread_points=_float_env("DEMO_SMOKE_MAX_SPREAD_POINTS", default=30.0),
        )
        allow_pyramiding = _bool_env_true("DEMO_ALLOW_PYRAMIDING")
        max_same_symbol_positions = _int_env("DEMO_MAX_SAME_SYMBOL_POSITIONS", default=1)
        if max_same_symbol_positions < 1:
            raise ValueError("DEMO_MAX_SAME_SYMBOL_POSITIONS must be >= 1")
        comment = sanitize_mt5_comment(_text_env("DEMO_SMOKE_COMMENT"))
        intent = build_demo_order_intent(
            candidate,
            DemoExecutionGuardResult(approved=True, reasons=()),
            comment=comment,
        )
        print(f"comment={comment}")
        print(f"comment_length={len(comment)}")
        print(f"allow_pyramiding={allow_pyramiding}")
        print(f"max_same_symbol_positions={max_same_symbol_positions}")

        mt5 = _load_mt5_module()
        _initialize_mt5(mt5)
        try:
            account_guard = verify_mt5_demo_account(mt5)
            if not account_guard.approved:
                print(f"Rejected: {', '.join(account_guard.reasons)}")
                return 1

            spread_points = _spread_points(mt5, candidate.symbol)
            positions = fetch_demo_open_positions(mt5, symbol=candidate.symbol)
            records = load_demo_order_records_jsonl(DEMO_ORDER_JSONL_PATH)
            print(f"open_positions_count={len(positions)}")
            print(f"demo_order_records_count={len(records)}")
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
            print(f"lifecycle_stage={record.stage}")
            print(f"lifecycle_reasons={record.reasons}")
            print(f"retcode={record.mt5_retcode}")
            print(f"comment={record.mt5_comment}")
            print(f"ticket={record.ticket}")
            print(f"lifecycle_mt5_retcode={record.mt5_retcode}")
            print(f"lifecycle_mt5_comment={record.mt5_comment}")
            print(f"lifecycle_ticket={record.ticket}")
            print(f"lifecycle_metadata={record.metadata}")
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
