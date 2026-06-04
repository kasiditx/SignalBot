from __future__ import annotations

import csv
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .demo_execution import (
    DemoExecutionConfig,
    DemoExecutionGuardResult,
    DemoExecutionState,
    DemoOrderCandidate,
    DemoOrderIntent,
    DemoOrderLifecycleRecord,
    validate_demo_execution_allowed,
)


@dataclass(frozen=True)
class DemoSendResult:
    attempted: bool
    accepted: bool
    lifecycle_record: DemoOrderLifecycleRecord
    request: dict[str, object] | None
    error_message: str | None = None


@dataclass(frozen=True)
class DemoOpenPosition:
    ticket: int | None
    symbol: str
    volume: float
    position_type: object | None
    action: str | None
    price_open: float | None
    stop_loss: float | None
    take_profit: float | None
    magic: int | None
    comment: str | None
    time: int | None


DEMO_ORDER_RECORD_FIELDS = (
    "timestamp",
    "symbol",
    "action",
    "volume",
    "price",
    "sl",
    "tp",
    "risk_reward",
    "retcode",
    "comment",
    "ticket",
    "accepted",
    "stage",
    "reasons",
    "account_mode",
    "metadata",
)
DEMO_ORDER_JSONL_ENCODING = "utf-8"
DEMO_ORDER_JSONL_READ_ENCODING = "utf-8-sig"
DEMO_ORDER_CSV_ENCODING = "utf-8-sig"


def _text_contains_demo(value: object) -> bool:
    return "demo" in str(value).lower()


def _text_contains_live_or_real(value: object) -> bool:
    text = str(value).lower()
    return "live" in text or "real" in text or "production" in text


def detect_mt5_account_mode(account_info: object | None) -> str | None:
    if account_info is None:
        return None

    text_fields = (
        _object_attr(account_info, "server", None),
        _object_attr(account_info, "company", None),
        _object_attr(account_info, "name", None),
    )
    if any(_text_contains_live_or_real(value) for value in text_fields if value is not None):
        return "live"
    if any(_text_contains_demo(value) for value in text_fields if value is not None):
        return "demo"

    trade_mode = _object_attr(account_info, "trade_mode", None)
    if _trade_mode_indicates_demo(trade_mode):
        return "demo"
    return None


def verify_mt5_demo_account(mt5_module: object) -> DemoExecutionGuardResult:
    try:
        account_info = mt5_module.account_info()
    except Exception:
        return DemoExecutionGuardResult(
            approved=False,
            reasons=("failed to read MT5 account info",),
        )

    mode = detect_mt5_account_mode(account_info)
    if mode == "demo":
        return DemoExecutionGuardResult(approved=True, reasons=())
    if mode == "live":
        return DemoExecutionGuardResult(
            approved=False,
            reasons=("live account is not allowed",),
        )
    return DemoExecutionGuardResult(
        approved=False,
        reasons=("demo account confirmation is required",),
    )


def _map_demo_intent_to_mt5_request(
    intent: DemoOrderIntent,
    *,
    deviation: int = 20,
) -> dict[str, object]:
    order_type = "BUY" if intent.order_type == "DEMO_BUY" else "SELL" if intent.order_type == "DEMO_SELL" else intent.action
    return {
        "symbol": intent.symbol,
        "type": order_type,
        "volume": intent.volume,
        "price": intent.price,
        "sl": intent.stop_loss,
        "tp": intent.take_profit,
        "deviation": deviation,
        "magic": intent.magic,
        "comment": intent.comment,
    }


def build_demo_request_lifecycle_record(
    stage: str,
    intent: DemoOrderIntent,
    approved: bool,
    reasons: tuple[str, ...] = (),
    account_mode: str | None = None,
    metadata: dict[str, object] | None = None,
) -> DemoOrderLifecycleRecord:
    return DemoOrderLifecycleRecord(
        timestamp=datetime.now(timezone.utc).isoformat(),
        stage=stage,
        symbol=intent.symbol,
        action=intent.action,
        volume=intent.volume,
        approved=approved,
        reasons=tuple(reasons),
        account_mode=account_mode,
        metadata=dict(metadata or {}),
    )


def build_demo_send_dry_run_result(
    intent: DemoOrderIntent,
    mt5_module: object,
    *,
    deviation: int = 20,
) -> DemoSendResult:
    guard = verify_mt5_demo_account(mt5_module)
    account_mode = "demo" if guard.approved else None
    if not guard.approved:
        return DemoSendResult(
            attempted=False,
            accepted=False,
            lifecycle_record=build_demo_request_lifecycle_record(
                "demo_account_rejected",
                intent,
                approved=False,
                reasons=guard.reasons,
                account_mode=account_mode,
            ),
            request=None,
            error_message=None,
        )

    request = _map_demo_intent_to_mt5_request(intent, deviation=deviation)
    return DemoSendResult(
        attempted=False,
        accepted=False,
        lifecycle_record=build_demo_request_lifecycle_record(
            "demo_request_built",
            intent,
            approved=True,
            account_mode=account_mode,
        ),
        request=request,
        error_message=None,
    )


def send_demo_order(
    intent: DemoOrderIntent,
    mt5_module: object,
    *,
    send_fn: Callable[[dict[str, object]], object],
    deviation: int = 20,
    success_retcodes: tuple[int, ...] = (10009, 10008),
) -> DemoSendResult:
    guard = verify_mt5_demo_account(mt5_module)
    if not guard.approved:
        return DemoSendResult(
            attempted=False,
            accepted=False,
            lifecycle_record=_build_demo_sender_lifecycle_record(
                "demo_account_rejected",
                intent,
                approved=False,
                reasons=guard.reasons,
                account_mode=None,
            ),
            request=None,
            error_message=None,
        )

    request = _map_demo_intent_to_mt5_request(intent, deviation=deviation)
    try:
        result = send_fn(request)
    except Exception as exc:
        return DemoSendResult(
            attempted=True,
            accepted=False,
            lifecycle_record=_build_demo_sender_lifecycle_record(
                "demo_order_failed",
                intent,
                approved=False,
                reasons=("demo sender failed",),
                metadata={"request": request},
            ),
            request=request,
            error_message=str(exc),
        )

    retcode_value = _result_attr(result, "retcode")
    comment_value = _result_attr(result, "comment")
    mt5_retcode = _optional_int(retcode_value)
    mt5_comment = None if comment_value is None else str(comment_value)
    ticket = _sender_ticket(result)
    if mt5_retcode is None:
        return DemoSendResult(
            attempted=True,
            accepted=False,
            lifecycle_record=_build_demo_sender_lifecycle_record(
                "demo_order_rejected",
                intent,
                approved=False,
                reasons=("missing sender retcode",),
                mt5_comment=mt5_comment,
                ticket=ticket,
                metadata={"request": request},
            ),
            request=request,
            error_message=None,
        )

    accepted = mt5_retcode in success_retcodes
    return DemoSendResult(
        attempted=True,
        accepted=accepted,
        lifecycle_record=_build_demo_sender_lifecycle_record(
            "demo_order_accepted" if accepted else "demo_order_rejected",
            intent,
            approved=accepted,
            reasons=() if accepted else ("demo sender rejected order",),
            mt5_retcode=mt5_retcode,
            mt5_comment=mt5_comment,
            ticket=ticket,
            metadata={"request": request},
        ),
        request=request,
        error_message=None,
    )


def build_mt5_demo_send_fn(
    mt5_module: object,
) -> Callable[[dict[str, object]], object]:
    sender_name = "order" + "_send"
    sender = getattr(mt5_module, sender_name, None)
    if sender is None:
        raise ValueError(f"missing MT5 sender: {sender_name}")

    def _send(demo_request: dict[str, object]) -> object:
        mt5_request = build_mt5_order_request_from_demo_request(demo_request, mt5_module)
        return sender(mt5_request)

    return _send


def _bool_env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"true", "1", "yes", "y", "on"}


def demo_execution_env_enabled(
    env_name: str = "DEMO_EXECUTION_ENABLED",
) -> DemoExecutionGuardResult:
    if _bool_env_enabled(env_name):
        return DemoExecutionGuardResult(approved=True, reasons=())
    return DemoExecutionGuardResult(
        approved=False,
        reasons=("demo execution is not enabled",),
    )


def demo_sender_stop_active(config: DemoExecutionConfig) -> DemoExecutionGuardResult:
    reasons: list[str] = []
    if config.stop_demo_execution_path.exists():
        reasons.append("demo execution stop file active")
    if config.stop_all_trading_path.exists():
        reasons.append("global trading stop file active")
    return DemoExecutionGuardResult(
        approved=not reasons,
        reasons=tuple(reasons),
    )


def validate_demo_sender_preconditions(
    intent: DemoOrderIntent,
    config: DemoExecutionConfig,
    state: DemoExecutionState,
    account_mode: str | None,
    spread_points: float | None,
    *,
    env_name: str = "DEMO_EXECUTION_ENABLED",
) -> DemoExecutionGuardResult:
    candidate = DemoOrderCandidate(
        symbol=intent.symbol,
        action=intent.action,
        volume=intent.volume,
        entry=intent.price,
        stop_loss=intent.stop_loss,
        take_profit=intent.take_profit,
        risk_reward=intent.risk_reward,
        source_stage=intent.source_stage,
        signal_id=intent.signal_id,
        metadata=dict(intent.metadata),
    )
    guards = (
        demo_execution_env_enabled(env_name),
        demo_sender_stop_active(config),
        validate_demo_execution_allowed(
            candidate,
            state,
            config,
            account_mode=account_mode,
            spread_points=spread_points,
        ),
    )
    reasons = tuple(reason for guard in guards for reason in guard.reasons)
    return DemoExecutionGuardResult(
        approved=not reasons,
        reasons=reasons,
    )


def build_demo_send_blocked_result(
    intent: DemoOrderIntent,
    reasons: tuple[str, ...],
    *,
    account_mode: str | None = None,
) -> DemoSendResult:
    return DemoSendResult(
        attempted=False,
        accepted=False,
        lifecycle_record=_build_demo_sender_lifecycle_record(
            "demo_send_blocked",
            intent,
            approved=False,
            reasons=tuple(reasons),
            account_mode=account_mode,
        ),
        request=None,
        error_message=None,
    )


def demo_order_record_from_result(
    intent: DemoOrderIntent,
    result: DemoSendResult,
) -> dict[str, object]:
    lifecycle = result.lifecycle_record
    metadata = {
        "intent": dict(intent.metadata),
        "lifecycle": dict(lifecycle.metadata),
    }
    if result.error_message:
        metadata["error_message"] = result.error_message
    return {
        "timestamp": lifecycle.timestamp,
        "symbol": intent.symbol,
        "action": intent.action,
        "volume": intent.volume,
        "price": intent.price,
        "sl": intent.stop_loss,
        "tp": intent.take_profit,
        "risk_reward": intent.risk_reward,
        "retcode": lifecycle.mt5_retcode,
        "comment": lifecycle.mt5_comment,
        "ticket": lifecycle.ticket,
        "accepted": result.accepted,
        "stage": lifecycle.stage,
        "reasons": list(lifecycle.reasons),
        "account_mode": lifecycle.account_mode,
        "metadata": metadata,
    }


def append_demo_order_record_jsonl(
    record: dict[str, object],
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding=DEMO_ORDER_JSONL_ENCODING) as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def append_demo_order_record_csv(
    record: dict[str, object],
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    row = {field: record.get(field) for field in DEMO_ORDER_RECORD_FIELDS}
    row["reasons"] = json.dumps(row["reasons"], ensure_ascii=False)
    row["metadata"] = json.dumps(row["metadata"], ensure_ascii=False)
    with path.open("a", newline="", encoding=DEMO_ORDER_CSV_ENCODING) as handle:
        writer = csv.DictWriter(handle, fieldnames=DEMO_ORDER_RECORD_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def write_demo_order_record(
    intent: DemoOrderIntent,
    result: DemoSendResult,
    *,
    csv_path: Path,
    jsonl_path: Path,
) -> dict[str, object]:
    record = demo_order_record_from_result(intent, result)
    append_demo_order_record_csv(record, csv_path)
    append_demo_order_record_jsonl(record, jsonl_path)
    return record


def load_demo_order_records_jsonl(
    path: Path,
) -> tuple[dict[str, object], ...]:
    if not path.exists():
        return ()
    records: list[dict[str, object]] = []
    with path.open("r", encoding=DEMO_ORDER_JSONL_READ_ENCODING) as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                loaded = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid demo order JSONL at line {line_number}") from exc
            if not isinstance(loaded, dict):
                raise ValueError(f"invalid demo order record at line {line_number}")
            records.append(loaded)
    return tuple(records)


def _position_value(position: object, name: str, default: object | None = None) -> object | None:
    if isinstance(position, dict):
        return position.get(name, default)
    return getattr(position, name, default)


def _position_action_from_type(position_type: object | None, mt5_module: object) -> str | None:
    if position_type is None:
        return None
    buy_type = getattr(mt5_module, "POSITION_TYPE_BUY", None)
    sell_type = getattr(mt5_module, "POSITION_TYPE_SELL", None)
    if buy_type is not None and position_type == buy_type:
        return "BUY"
    if sell_type is not None and position_type == sell_type:
        return "SELL"
    return None


def map_mt5_position_to_demo_position(
    position: object,
    mt5_module: object,
) -> DemoOpenPosition:
    position_type = _position_value(position, "type")
    return DemoOpenPosition(
        ticket=_optional_int(_position_value(position, "ticket")),
        symbol=str(_position_value(position, "symbol", "") or ""),
        volume=_optional_float(_position_value(position, "volume")) or 0.0,
        position_type=position_type,
        action=_position_action_from_type(position_type, mt5_module),
        price_open=_optional_float(_position_value(position, "price_open")),
        stop_loss=_optional_float(_position_value(position, "sl")),
        take_profit=_optional_float(_position_value(position, "tp")),
        magic=_optional_int(_position_value(position, "magic")),
        comment=_optional_text(_position_value(position, "comment")),
        time=_optional_int(_position_value(position, "time")),
    )


def fetch_demo_open_positions(
    mt5_module: object,
    *,
    symbol: str | None = None,
) -> tuple[DemoOpenPosition, ...]:
    reader_name = "positions" + "_get"
    reader = getattr(mt5_module, reader_name, None)
    if reader is None:
        raise ValueError(f"missing MT5 positions reader: {reader_name}")
    try:
        positions = reader(symbol=symbol) if symbol else reader()
    except Exception as exc:
        raise RuntimeError("failed to read MT5 open positions") from exc
    if positions is None:
        return ()
    return tuple(map_mt5_position_to_demo_position(position, mt5_module) for position in positions)


def validate_demo_position_limits(
    positions: tuple[DemoOpenPosition, ...],
    config: DemoExecutionConfig,
) -> DemoExecutionGuardResult:
    if len(positions) >= config.max_open_positions:
        return DemoExecutionGuardResult(
            approved=False,
            reasons=("max open positions reached",),
        )
    return DemoExecutionGuardResult(approved=True, reasons=())


def validate_demo_same_symbol_action_guard(
    intent: DemoOrderIntent,
    positions: tuple[DemoOpenPosition, ...],
    *,
    allow_pyramiding: bool = False,
    max_same_symbol_positions: int = 1,
    require_existing_position_profit: bool = True,
    require_scale_in_signal_id: bool = True,
    reject_opposite_action: bool = True,
) -> DemoExecutionGuardResult:
    if allow_pyramiding:
        return validate_demo_pyramiding_guard(
            intent,
            positions,
            (),
            allow_pyramiding=allow_pyramiding,
            max_same_symbol_positions=max_same_symbol_positions,
            reject_opposite_action=reject_opposite_action,
            require_existing_position_profit=require_existing_position_profit,
            require_scale_in_signal_id=require_scale_in_signal_id,
        )

    reasons: list[str] = []
    for position in positions:
        if position.symbol != intent.symbol:
            continue
        if position.action == intent.action:
            reasons.append("same symbol action position already open")
            break
        if reject_opposite_action:
            reasons.append("same symbol position already open")
            break
    return DemoExecutionGuardResult(
        approved=not reasons,
        reasons=tuple(reasons),
    )


def validate_demo_pyramiding_guard(
    intent: DemoOrderIntent,
    positions: tuple[DemoOpenPosition, ...],
    records: tuple[dict[str, object], ...],
    *,
    allow_pyramiding: bool,
    max_same_symbol_positions: int,
    reject_opposite_action: bool = True,
    require_existing_position_profit: bool = True,
    require_scale_in_signal_id: bool = True,
) -> DemoExecutionGuardResult:
    del require_existing_position_profit
    same_symbol_positions = tuple(position for position in positions if position.symbol == intent.symbol)
    same_action_positions = tuple(position for position in same_symbol_positions if position.action == intent.action)
    opposite_positions = tuple(
        position
        for position in same_symbol_positions
        if position.action is not None and position.action != intent.action
    )
    unknown_action_positions = tuple(position for position in same_symbol_positions if position.action is None)

    reasons: list[str] = []
    if reject_opposite_action and (opposite_positions or unknown_action_positions):
        reasons.append("opposite symbol position already open")

    if allow_pyramiding and len(same_symbol_positions) >= max_same_symbol_positions:
        reasons.append("max same symbol positions reached")

    if not allow_pyramiding and same_action_positions:
        reasons.append("same symbol action position already open")

    if allow_pyramiding and same_action_positions and require_scale_in_signal_id and intent.signal_id is None:
        reasons.append("signal id is required")

    if records:
        duplicate_guard = validate_demo_duplicate_guards(intent, records)
        reasons.extend(duplicate_guard.reasons)

    return DemoExecutionGuardResult(
        approved=not reasons,
        reasons=tuple(reasons),
    )


def validate_demo_open_position_guards(
    intent: DemoOrderIntent,
    positions: tuple[DemoOpenPosition, ...],
    config: DemoExecutionConfig,
    *,
    allow_pyramiding: bool = False,
    max_same_symbol_positions: int = 1,
    require_existing_position_profit: bool = True,
    require_scale_in_signal_id: bool = True,
    reject_opposite_action: bool = True,
) -> DemoExecutionGuardResult:
    symbol_guard = (
        validate_demo_pyramiding_guard(
            intent,
            positions,
            (),
            allow_pyramiding=allow_pyramiding,
            max_same_symbol_positions=max_same_symbol_positions,
            reject_opposite_action=reject_opposite_action,
            require_existing_position_profit=require_existing_position_profit,
            require_scale_in_signal_id=require_scale_in_signal_id,
        )
        if allow_pyramiding
        else validate_demo_same_symbol_action_guard(
            intent,
            positions,
            reject_opposite_action=reject_opposite_action,
        )
    )
    guards = (
        validate_demo_position_limits(positions, config),
        symbol_guard,
    )
    reasons = tuple(reason for guard in guards for reason in guard.reasons)
    return DemoExecutionGuardResult(
        approved=not reasons,
        reasons=reasons,
    )


def validate_demo_duplicate_signal_id(
    intent: DemoOrderIntent,
    records: tuple[dict[str, object], ...],
    *,
    require_signal_id: bool = True,
) -> DemoExecutionGuardResult:
    if intent.signal_id is None:
        if require_signal_id:
            return DemoExecutionGuardResult(
                approved=False,
                reasons=("signal id is required",),
            )
        return DemoExecutionGuardResult(approved=True, reasons=())

    for record in records:
        if _record_signal_id(record) == intent.signal_id:
            return DemoExecutionGuardResult(
                approved=False,
                reasons=("duplicate signal id",),
            )
    return DemoExecutionGuardResult(approved=True, reasons=())


def validate_demo_duplicate_candle(
    intent: DemoOrderIntent,
    records: tuple[dict[str, object], ...],
    *,
    require_candle_time: bool = True,
) -> DemoExecutionGuardResult:
    candle_time = _metadata_value(intent.metadata, "latest_execution_candle_time")
    if candle_time is None:
        if require_candle_time:
            return DemoExecutionGuardResult(
                approved=False,
                reasons=("execution candle time is required",),
            )
        return DemoExecutionGuardResult(approved=True, reasons=())

    for record in records:
        if record.get("symbol") != intent.symbol:
            continue
        if _record_metadata_value(record, "latest_execution_candle_time") == candle_time:
            return DemoExecutionGuardResult(
                approved=False,
                reasons=("duplicate execution candle",),
            )
    return DemoExecutionGuardResult(approved=True, reasons=())


def validate_demo_duplicate_guards(
    intent: DemoOrderIntent,
    records: tuple[dict[str, object], ...],
) -> DemoExecutionGuardResult:
    guards = (
        validate_demo_duplicate_signal_id(intent, records),
        validate_demo_duplicate_candle(intent, records),
    )
    reasons = tuple(reason for guard in guards for reason in guard.reasons)
    return DemoExecutionGuardResult(
        approved=not reasons,
        reasons=reasons,
    )


def validate_demo_execution_gate(
    intent: DemoOrderIntent,
    config: DemoExecutionConfig,
    state: DemoExecutionState,
    *,
    account_mode: str | None,
    spread_points: float | None,
    positions: tuple[DemoOpenPosition, ...],
    records: tuple[dict[str, object], ...],
    env_name: str = "DEMO_EXECUTION_ENABLED",
    allow_pyramiding: bool = False,
    max_same_symbol_positions: int = 1,
    require_existing_position_profit: bool = True,
    require_scale_in_signal_id: bool = True,
    reject_opposite_action: bool = True,
) -> DemoExecutionGuardResult:
    guards = (
        validate_demo_sender_preconditions(
            intent,
            config,
            state,
            account_mode=account_mode,
            spread_points=spread_points,
            env_name=env_name,
        ),
        validate_demo_open_position_guards(
            intent,
            positions,
            config,
            allow_pyramiding=allow_pyramiding,
            max_same_symbol_positions=max_same_symbol_positions,
            reject_opposite_action=reject_opposite_action,
            require_existing_position_profit=require_existing_position_profit,
            require_scale_in_signal_id=require_scale_in_signal_id,
        ),
        validate_demo_duplicate_guards(intent, records),
    )
    reasons = tuple(reason for guard in guards for reason in guard.reasons)
    return DemoExecutionGuardResult(
        approved=not reasons,
        reasons=reasons,
    )


def _required_mt5_constant(mt5_module: object, name: str) -> object:
    if not hasattr(mt5_module, name):
        raise ValueError(f"missing MT5 constant: {name}")
    return getattr(mt5_module, name)


def _optional_mt5_constant(mt5_module: object, name: str) -> object | None:
    return getattr(mt5_module, name, None)


def _mt5_order_type_from_demo_type(demo_type: object, mt5_module: object) -> object:
    normalized_type = str(demo_type).strip().upper()
    if normalized_type == "BUY":
        return _required_mt5_constant(mt5_module, "ORDER_TYPE_BUY")
    if normalized_type == "SELL":
        return _required_mt5_constant(mt5_module, "ORDER_TYPE_SELL")
    raise ValueError("demo request type must be BUY or SELL")


def _mt5_symbol_filling_type(mt5_module: object, symbol: str) -> object | None:
    symbol_info = getattr(mt5_module, "symbol_info", None)
    if symbol_info is None:
        return None
    try:
        info = symbol_info(symbol)
    except Exception:
        return None
    if info is None:
        return None

    filling_mode = getattr(info, "filling_mode", None)
    if filling_mode is None:
        return None
    try:
        filling_flags = int(filling_mode)
    except (TypeError, ValueError):
        return None

    if filling_flags & 1:
        return _required_mt5_constant(mt5_module, "ORDER_FILLING_FOK")
    if filling_flags & 2:
        return _required_mt5_constant(mt5_module, "ORDER_FILLING_IOC")
    if filling_flags & 4:
        return _optional_mt5_constant(mt5_module, "ORDER_FILLING_RETURN")
    return None


def _mt5_filling_type(mt5_module: object) -> object:
    for name in ("ORDER_FILLING_IOC", "ORDER_FILLING_FOK", "ORDER_FILLING_RETURN"):
        value = _optional_mt5_constant(mt5_module, name)
        if value is not None:
            return value
    raise ValueError("missing MT5 filling constant")


def build_mt5_order_request_from_demo_request(
    demo_request: dict[str, object],
    mt5_module: object,
) -> dict[str, object]:
    action_constant_name = "TRADE" + "_ACTION_DEAL"
    symbol = str(demo_request["symbol"])
    symbol_filling_type = _mt5_symbol_filling_type(mt5_module, symbol)
    return {
        "action": _required_mt5_constant(mt5_module, action_constant_name),
        "symbol": symbol,
        "type": _mt5_order_type_from_demo_type(demo_request.get("type"), mt5_module),
        "volume": demo_request["volume"],
        "price": demo_request["price"],
        "sl": demo_request["sl"],
        "tp": demo_request["tp"],
        "deviation": demo_request["deviation"],
        "magic": demo_request["magic"],
        "comment": demo_request["comment"],
        "type_time": _required_mt5_constant(mt5_module, "ORDER_TIME_GTC"),
        "type_filling": symbol_filling_type if symbol_filling_type is not None else _mt5_filling_type(mt5_module),
    }


def _result_attr(result: object, name: str, default: object | None = None) -> object | None:
    if isinstance(result, dict):
        return result.get(name, default)
    return getattr(result, name, default)


def _sender_ticket(result: object) -> int | None:
    for name in ("ticket", "order", "deal"):
        value = _result_attr(result, name)
        ticket = _optional_int(value)
        if ticket is not None:
            return ticket
    return None


def _build_demo_sender_lifecycle_record(
    stage: str,
    intent: DemoOrderIntent,
    *,
    approved: bool,
    reasons: tuple[str, ...] = (),
    account_mode: str | None = "demo",
    mt5_retcode: int | None = None,
    mt5_comment: str | None = None,
    ticket: int | None = None,
    metadata: dict[str, object] | None = None,
) -> DemoOrderLifecycleRecord:
    return DemoOrderLifecycleRecord(
        timestamp=datetime.now(timezone.utc).isoformat(),
        stage=stage,
        symbol=intent.symbol,
        action=intent.action,
        volume=intent.volume,
        approved=approved,
        reasons=tuple(reasons),
        account_mode=account_mode,
        mt5_retcode=mt5_retcode,
        mt5_comment=mt5_comment,
        ticket=ticket,
        metadata=dict(metadata or {}),
    )


def _object_attr(value: object, name: str, default: Any) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _record_signal_id(record: dict[str, object]) -> object | None:
    if record.get("signal_id") is not None:
        return record.get("signal_id")
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        return None
    if metadata.get("signal_id") is not None:
        return metadata.get("signal_id")
    intent_metadata = metadata.get("intent")
    if isinstance(intent_metadata, dict):
        return intent_metadata.get("signal_id")
    return None


def _metadata_value(metadata: dict[str, object], key: str) -> object | None:
    return metadata.get(key)


def _record_metadata_value(record: dict[str, object], key: str) -> object | None:
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        return None
    if metadata.get(key) is not None:
        return metadata.get(key)
    intent_metadata = metadata.get("intent")
    if isinstance(intent_metadata, dict) and intent_metadata.get(key) is not None:
        return intent_metadata.get(key)
    lifecycle_metadata = metadata.get("lifecycle")
    if isinstance(lifecycle_metadata, dict):
        return lifecycle_metadata.get(key)
    return None


def _trade_mode_indicates_demo(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        text = value.lower()
        return "demo" in text and not _text_contains_live_or_real(text)
    return False


def _optional_int(value: object | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: object | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    return str(value)
