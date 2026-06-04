from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from .dry_run_pipeline import DryRunMarketInput
from .forward_validation import (
    ForwardValidationConfig,
    ForwardValidationInput,
    ForwardValidationResult,
    load_forward_records_jsonl,
    run_forward_validation,
    write_forward_summaries,
)
from .models import AutoTradeConfig, SignalAction, SignalConfig
from .pipeline_adapter import DryRunAdapterInput, run_pipeline_from_configs
from .realtime_market_data import (
    RealtimeMarketSnapshot,
    fetch_realtime_market_snapshot,
    normalize_realtime_timeframe,
)
from .strategy import generate_signal


DEFAULT_REALTIME_TIMEFRAMES = ("H4", "H1", "M30", "M15", "M5", "M1")


@dataclass(frozen=True)
class RealtimeForwardRunnerConfig:
    symbol: str
    timeframes: tuple[str, ...]
    execution_timeframe: str
    candle_count: int
    interval_seconds: int
    output_dir: Path
    state_path: Path
    stop_file_path: Path
    mode: str = "paper"
    max_iterations: int | None = None
    session: str | None = None
    high_impact_news_nearby: bool = False


@dataclass(frozen=True)
class RealtimeForwardState:
    last_processed_candle_time: str | None = None
    last_run_timestamp: str | None = None
    last_stage: str | None = None
    last_approved: bool | None = None
    last_reasons: tuple[str, ...] = ()
    last_record_written: bool = False


@dataclass(frozen=True)
class RealtimeForwardDecision:
    should_process: bool
    reason: str | None
    latest_candle_time: str | None


@dataclass(frozen=True)
class RealtimeForwardRunResult:
    status: str
    processed: bool
    reason: str | None
    latest_candle_time: str | None
    state: RealtimeForwardState
    validation_result: ForwardValidationResult | None
    snapshot: RealtimeMarketSnapshot | None
    error_message: str | None = None


@dataclass(frozen=True)
class RealtimeForwardLoopResult:
    status: str
    iterations: int
    processed_count: int
    skipped_count: int
    error_count: int
    stopped: bool
    last_result: RealtimeForwardRunResult | None


def default_realtime_forward_config(
    symbol: str,
    output_dir: Path,
    execution_timeframe: str = "M1",
) -> RealtimeForwardRunnerConfig:
    return RealtimeForwardRunnerConfig(
        symbol=symbol,
        timeframes=DEFAULT_REALTIME_TIMEFRAMES,
        execution_timeframe=normalize_realtime_timeframe(execution_timeframe),
        candle_count=300,
        interval_seconds=60,
        output_dir=output_dir,
        state_path=output_dir / "realtime_state.json",
        stop_file_path=output_dir / "STOP_REALTIME_FORWARD",
        mode="paper",
    )


def load_realtime_forward_state(path: Path) -> RealtimeForwardState:
    if not path.exists():
        return RealtimeForwardState()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid realtime forward state JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Realtime forward state must be a JSON object")

    return RealtimeForwardState(
        last_processed_candle_time=_optional_string(payload.get("last_processed_candle_time")),
        last_run_timestamp=_optional_string(payload.get("last_run_timestamp")),
        last_stage=_optional_string(payload.get("last_stage")),
        last_approved=_optional_bool(payload.get("last_approved")),
        last_reasons=_normalize_reasons(payload.get("last_reasons")),
        last_record_written=bool(payload.get("last_record_written", False)),
    )


def write_realtime_forward_state(
    state: RealtimeForwardState,
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_processed_candle_time": state.last_processed_candle_time,
        "last_run_timestamp": state.last_run_timestamp,
        "last_stage": state.last_stage,
        "last_approved": state.last_approved,
        "last_reasons": list(state.last_reasons),
        "last_record_written": state.last_record_written,
    }
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def latest_execution_candle_time(
    snapshot: RealtimeMarketSnapshot,
    execution_timeframe: str,
) -> str | None:
    normalized = normalize_realtime_timeframe(execution_timeframe)
    candles = snapshot.candles_by_timeframe.get(normalized)
    if not candles:
        return None
    return candles[-1].timestamp


def should_process_snapshot(
    snapshot: RealtimeMarketSnapshot,
    state: RealtimeForwardState,
    execution_timeframe: str,
) -> RealtimeForwardDecision:
    latest_candle_time = latest_execution_candle_time(snapshot, execution_timeframe)
    if latest_candle_time is None:
        return RealtimeForwardDecision(
            should_process=False,
            reason="missing execution candle",
            latest_candle_time=None,
        )
    if latest_candle_time == state.last_processed_candle_time:
        return RealtimeForwardDecision(
            should_process=False,
            reason="duplicate execution candle",
            latest_candle_time=latest_candle_time,
        )
    return RealtimeForwardDecision(
        should_process=True,
        reason=None,
        latest_candle_time=latest_candle_time,
    )


def snapshot_to_market_metadata(
    snapshot: RealtimeMarketSnapshot,
    latest_candle_time: str | None = None,
) -> dict[str, object]:
    return {
        "bid": snapshot.bid,
        "ask": snapshot.ask,
        "current_price": snapshot.current_price,
        "spread_points": snapshot.spread_points,
        "snapshot_timestamp": snapshot.timestamp.isoformat(),
        "market_open": snapshot.market_open,
        "snapshot_errors": list(snapshot.errors),
        "latest_execution_candle_time": latest_candle_time,
    }


def build_realtime_forward_validation_input(
    snapshot: RealtimeMarketSnapshot,
    config: RealtimeForwardRunnerConfig,
    latest_candle_time: str | None = None,
) -> ForwardValidationInput:
    return ForwardValidationInput(
        symbol=snapshot.symbol,
        mode=config.mode,
        action=None,
        entry=None,
        stop_loss=None,
        tp1=None,
        tp2=None,
        risk_reward=None,
        current_price=snapshot.current_price,
        spread_points=snapshot.spread_points,
        session=config.session,
        metadata=snapshot_to_market_metadata(snapshot, latest_candle_time),
    )


def build_realtime_signal_candidate_input(
    snapshot: RealtimeMarketSnapshot,
    config: RealtimeForwardRunnerConfig,
    signal_config: SignalConfig,
    latest_candle_time: str | None = None,
    generate_signal_fn=generate_signal,
) -> ForwardValidationInput:
    metadata = snapshot_to_market_metadata(snapshot, latest_candle_time)
    execution_timeframe = normalize_realtime_timeframe(config.execution_timeframe)
    execution_candles = snapshot.candles_by_timeframe.get(execution_timeframe)
    if not execution_candles:
        metadata["missing_trade_level_reasons"] = ["missing execution candles"]
        return ForwardValidationInput(
            symbol=snapshot.symbol,
            mode=config.mode,
            action=None,
            entry=None,
            stop_loss=None,
            tp1=None,
            tp2=None,
            risk_reward=None,
            current_price=snapshot.current_price,
            spread_points=snapshot.spread_points,
            session=config.session,
            metadata=metadata,
        )

    signal = generate_signal_fn(execution_candles, signal_config, snapshot.candles_by_timeframe)
    levels = getattr(signal, "levels", None)
    action = getattr(signal, "action", None)
    entry = getattr(levels, "entry", None) if levels is not None else None
    stop_loss = getattr(levels, "stop_loss", None) if levels is not None else None
    tp1 = getattr(levels, "take_profit", None) if levels is not None else None
    risk_reward = getattr(levels, "risk_reward", None) if levels is not None else None
    metadata["signal_action"] = _action_value(action)
    signal_reason = getattr(signal, "reason", None)
    no_trade_reason = getattr(signal, "no_trade_reason", None)
    if signal_reason is not None:
        metadata["signal_reason"] = str(signal_reason)
    if no_trade_reason is not None:
        metadata["no_trade_reason"] = str(no_trade_reason)

    missing_reasons = _missing_realtime_trade_level_reasons(
        action=action,
        entry=entry,
        stop_loss=stop_loss,
        tp1=tp1,
        tp2=None,
        risk_reward=risk_reward,
    )
    if missing_reasons:
        metadata["missing_trade_level_reasons"] = list(missing_reasons)

    return ForwardValidationInput(
        symbol=snapshot.symbol,
        mode=config.mode,
        action=action,
        entry=entry,
        stop_loss=stop_loss,
        tp1=tp1,
        tp2=None,
        risk_reward=risk_reward,
        current_price=snapshot.current_price,
        spread_points=snapshot.spread_points,
        session=config.session,
        metadata=metadata,
    )


def has_realtime_trade_levels(validation_input: ForwardValidationInput) -> bool:
    if validation_input.action is None:
        return False
    if validation_input.entry is None or validation_input.stop_loss is None:
        return False
    if validation_input.tp1 is None and validation_input.tp2 is None:
        return False
    if validation_input.risk_reward is None:
        return False
    return True


def missing_realtime_trade_level_reasons(
    validation_input: ForwardValidationInput,
) -> tuple[str, ...]:
    return _missing_realtime_trade_level_reasons(
        action=validation_input.action,
        entry=validation_input.entry,
        stop_loss=validation_input.stop_loss,
        tp1=validation_input.tp1,
        tp2=validation_input.tp2,
        risk_reward=validation_input.risk_reward,
    )


def build_realtime_preflight_pipeline_result(
    stage: str,
    reasons: tuple[str, ...],
) -> object:
    return SimpleNamespace(
        stage=stage,
        approved=False,
        reasons=tuple(reasons),
        execution_plan=None,
        risk_decision=None,
        journal_results=(),
    )


def build_realtime_signal_pipeline_result(
    stage: str,
    reasons: tuple[str, ...],
) -> object:
    return SimpleNamespace(
        stage=stage,
        approved=False,
        reasons=tuple(reasons),
        execution_plan=None,
        risk_decision=None,
        journal_results=(),
    )


def build_realtime_dry_run_market_input(
    snapshot: RealtimeMarketSnapshot,
    config: RealtimeForwardRunnerConfig,
) -> DryRunMarketInput:
    return DryRunMarketInput(
        current_price=snapshot.current_price,
        spread_points=snapshot.spread_points,
        atr_value=None,
        average_atr=None,
        session=config.session,
        high_impact_news_nearby=config.high_impact_news_nearby,
    )


def build_realtime_forward_state_after_result(
    latest_candle_time: str,
    stage: str,
    approved: bool,
    reasons: tuple[str, ...],
    record_written: bool,
    timestamp: str | None = None,
) -> RealtimeForwardState:
    return RealtimeForwardState(
        last_processed_candle_time=latest_candle_time,
        last_run_timestamp=timestamp or datetime.now(timezone.utc).isoformat(),
        last_stage=stage,
        last_approved=approved,
        last_reasons=tuple(reasons),
        last_record_written=record_written,
    )


def stop_file_exists(path: Path) -> bool:
    return path.exists()


def run_realtime_forward_once(
    config: RealtimeForwardRunnerConfig,
    signal_config: SignalConfig,
    sizing_config: AutoTradeConfig,
    validation_config: ForwardValidationConfig,
    *,
    fetch_snapshot=fetch_realtime_market_snapshot,
    run_pipeline=run_pipeline_from_configs,
    run_validation=run_forward_validation,
    load_state=load_realtime_forward_state,
    write_state=write_realtime_forward_state,
    load_records=load_forward_records_jsonl,
    write_summaries=write_forward_summaries,
    build_validation_input=build_realtime_signal_candidate_input,
) -> RealtimeForwardRunResult:
    if stop_file_exists(config.stop_file_path):
        state = _load_state_or_blank(load_state, config.state_path)
        return RealtimeForwardRunResult(
            status="stopped",
            processed=False,
            reason="stop file detected",
            latest_candle_time=None,
            state=state,
            validation_result=None,
            snapshot=None,
        )

    try:
        state = load_state(config.state_path)
        snapshot = fetch_snapshot(
            config.symbol,
            config.timeframes,
            config.candle_count,
        )
        decision = should_process_snapshot(snapshot, state, config.execution_timeframe)
        if not decision.should_process:
            return RealtimeForwardRunResult(
                status="skipped",
                processed=False,
                reason=decision.reason,
                latest_candle_time=decision.latest_candle_time,
                state=state,
                validation_result=None,
                snapshot=snapshot,
            )

        latest_candle_time = decision.latest_candle_time
        validation_input = build_validation_input(
            snapshot,
            config,
            signal_config,
            latest_candle_time,
        )
        if _is_wait_action(validation_input.action):
            signal_result = build_realtime_signal_pipeline_result(
                stage="realtime_signal",
                reasons=_wait_signal_reasons(validation_input),
            )
            validation_result = run_validation(
                validation_input,
                signal_result,
                validation_config,
            )
            return _finalize_realtime_forward_result(
                latest_candle_time=latest_candle_time,
                state=state,
                validation_result=validation_result,
                snapshot=snapshot,
                validation_config=validation_config,
                load_records=load_records,
                write_summaries=write_summaries,
                write_state=write_state,
                state_path=config.state_path,
            )

        missing_reasons = missing_realtime_trade_level_reasons(validation_input)
        if missing_reasons:
            invalid_result = build_realtime_signal_pipeline_result(
                stage="realtime_signal_invalid",
                reasons=missing_reasons,
            )
            validation_result = run_validation(
                validation_input,
                invalid_result,
                validation_config,
            )
            return _finalize_realtime_forward_result(
                latest_candle_time=latest_candle_time,
                state=state,
                validation_result=validation_result,
                snapshot=snapshot,
                validation_config=validation_config,
                load_records=load_records,
                write_summaries=write_summaries,
                write_state=write_state,
                state_path=config.state_path,
            )

        if not has_realtime_trade_levels(validation_input):
            preflight_result = build_realtime_preflight_pipeline_result(
                stage="realtime_preflight",
                reasons=("missing realtime trade levels",),
            )
            validation_result = run_validation(
                validation_input,
                preflight_result,
                validation_config,
            )
            return _finalize_realtime_forward_result(
                latest_candle_time=latest_candle_time,
                state=state,
                validation_result=validation_result,
                snapshot=snapshot,
                validation_config=validation_config,
                load_records=load_records,
                write_summaries=write_summaries,
                write_state=write_state,
                state_path=config.state_path,
            )

        adapter_input = _adapter_input_from_validation_input(validation_input)
        market_input = build_realtime_dry_run_market_input(snapshot, config)
        adapter_result = run_pipeline(
            snapshot.candles_by_timeframe,
            signal_config,
            sizing_config,
            adapter_input,
            market_input,
        )
        pipeline_result = getattr(adapter_result, "pipeline_result", adapter_result)
        validation_result = run_validation(
            validation_input,
            pipeline_result,
            validation_config,
        )
        return _finalize_realtime_forward_result(
            latest_candle_time=latest_candle_time,
            state=state,
            validation_result=validation_result,
            snapshot=snapshot,
            validation_config=validation_config,
            load_records=load_records,
            write_summaries=write_summaries,
            write_state=write_state,
            state_path=config.state_path,
        )
    except Exception as exc:
        current_state = locals().get("state")
        return RealtimeForwardRunResult(
            status="error",
            processed=False,
            reason=None,
            latest_candle_time=None,
            state=current_state if isinstance(current_state, RealtimeForwardState) else RealtimeForwardState(),
            validation_result=None,
            snapshot=locals().get("snapshot") if isinstance(locals().get("snapshot"), RealtimeMarketSnapshot) else None,
            error_message=str(exc),
        )


def run_realtime_forward_loop(
    config: RealtimeForwardRunnerConfig,
    signal_config: SignalConfig,
    sizing_config: AutoTradeConfig,
    validation_config: ForwardValidationConfig,
    *,
    run_once=run_realtime_forward_once,
    sleep_fn=time.sleep,
    stop_check=stop_file_exists,
    continue_on_error: bool = True,
    max_errors: int = 3,
) -> RealtimeForwardLoopResult:
    if config.interval_seconds <= 0:
        return _loop_result("invalid_config", 0, 0, 0, 0, False, None)
    if config.max_iterations is not None and config.max_iterations <= 0:
        return _loop_result("invalid_config", 0, 0, 0, 0, False, None)
    if max_errors <= 0:
        return _loop_result("invalid_config", 0, 0, 0, 0, False, None)

    iterations = 0
    processed_count = 0
    skipped_count = 0
    error_count = 0
    last_result: RealtimeForwardRunResult | None = None

    while True:
        if stop_check(config.stop_file_path):
            return _loop_result(
                "stopped",
                iterations,
                processed_count,
                skipped_count,
                error_count,
                True,
                last_result,
            )

        result = run_once(
            config,
            signal_config,
            sizing_config,
            validation_config,
        )
        last_result = result
        iterations += 1

        if result.status == "processed":
            processed_count += 1
        elif result.status == "skipped":
            skipped_count += 1
        elif result.status in ("error", "record_failed", "summary_failed"):
            error_count += 1

        if result.status == "stopped":
            return _loop_result(
                "stopped",
                iterations,
                processed_count,
                skipped_count,
                error_count,
                True,
                last_result,
            )

        if result.status in ("error", "record_failed", "summary_failed"):
            if not continue_on_error or error_count >= max_errors:
                return _loop_result(
                    "error_limit_reached",
                    iterations,
                    processed_count,
                    skipped_count,
                    error_count,
                    False,
                    last_result,
                )

        if config.max_iterations is not None and iterations >= config.max_iterations:
            return _loop_result(
                "completed",
                iterations,
                processed_count,
                skipped_count,
                error_count,
                False,
                last_result,
            )

        sleep_fn(config.interval_seconds)


def _normalize_reasons(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return (str(value),)


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _action_value(action: object) -> str | None:
    if action is None:
        return None
    value = getattr(action, "value", action)
    return str(value)


def _is_wait_action(action: object) -> bool:
    return action == SignalAction.WAIT or _action_value(action) == SignalAction.WAIT.value


def _is_directional_action(action: object) -> bool:
    return action in (SignalAction.BUY, SignalAction.SELL) or _action_value(action) in (
        SignalAction.BUY.value,
        SignalAction.SELL.value,
    )


def _missing_realtime_trade_level_reasons(
    *,
    action: object,
    entry: float | None,
    stop_loss: float | None,
    tp1: float | None,
    tp2: float | None,
    risk_reward: float | None,
) -> tuple[str, ...]:
    if action is None or _is_wait_action(action) or not _is_directional_action(action):
        return ()

    reasons: list[str] = []
    if entry is None:
        reasons.append("missing entry")
    if stop_loss is None:
        reasons.append("missing stop loss")
    if tp1 is None and tp2 is None:
        reasons.append("missing take profit")
    if risk_reward is None:
        reasons.append("missing risk reward")
    return tuple(reasons)


def _wait_signal_reasons(validation_input: ForwardValidationInput) -> tuple[str, ...]:
    no_trade_reason = validation_input.metadata.get("no_trade_reason")
    signal_reason = validation_input.metadata.get("signal_reason")
    if no_trade_reason:
        return (str(no_trade_reason),)
    if signal_reason:
        return (str(signal_reason),)
    return ("signal action is WAIT",)


def _load_state_or_blank(load_state, path: Path) -> RealtimeForwardState:
    try:
        return load_state(path)
    except Exception:
        return RealtimeForwardState()


def _adapter_input_from_validation_input(
    validation_input: ForwardValidationInput,
) -> DryRunAdapterInput:
    return DryRunAdapterInput(
        action=validation_input.action,
        entry=validation_input.entry,
        stop_loss=validation_input.stop_loss,
        tp1=validation_input.tp1,
        tp2=validation_input.tp2,
        risk_reward=validation_input.risk_reward,
        mode=validation_input.mode,
    )


def _finalize_realtime_forward_result(
    latest_candle_time: str | None,
    state: RealtimeForwardState,
    validation_result: ForwardValidationResult,
    snapshot: RealtimeMarketSnapshot,
    validation_config: ForwardValidationConfig,
    load_records,
    write_summaries,
    write_state,
    state_path: Path,
) -> RealtimeForwardRunResult:
    if not validation_result.write_success:
        return RealtimeForwardRunResult(
            status="record_failed",
            processed=False,
            reason="forward validation record write failed",
            latest_candle_time=latest_candle_time,
            state=state,
            validation_result=validation_result,
            snapshot=snapshot,
        )

    try:
        records = load_records(validation_config.record_jsonl_path)
        write_summaries(records, validation_config)
    except Exception as exc:
        return RealtimeForwardRunResult(
            status="summary_failed",
            processed=False,
            reason="forward summary update failed",
            latest_candle_time=latest_candle_time,
            state=state,
            validation_result=validation_result,
            snapshot=snapshot,
            error_message=str(exc),
        )

    new_state = build_realtime_forward_state_after_result(
        latest_candle_time=latest_candle_time or "",
        stage=validation_result.record.stage,
        approved=validation_result.record.approved,
        reasons=validation_result.record.reasons,
        record_written=validation_result.write_success,
    )
    write_state(new_state, state_path)
    return RealtimeForwardRunResult(
        status="processed",
        processed=True,
        reason=None,
        latest_candle_time=latest_candle_time,
        state=new_state,
        validation_result=validation_result,
        snapshot=snapshot,
    )


def _loop_result(
    status: str,
    iterations: int,
    processed_count: int,
    skipped_count: int,
    error_count: int,
    stopped: bool,
    last_result: RealtimeForwardRunResult | None,
) -> RealtimeForwardLoopResult:
    return RealtimeForwardLoopResult(
        status=status,
        iterations=iterations,
        processed_count=processed_count,
        skipped_count=skipped_count,
        error_count=error_count,
        stopped=stopped,
        last_result=last_result,
    )
