from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


DEMO_MODES = ("demo", "paper_demo", "mt5_demo")
BLOCKED_ACCOUNT_MODES = ("live", "real", "production")


@dataclass(frozen=True)
class DemoExecutionConfig:
    mode: str
    allowed_symbols: tuple[str, ...]
    max_lot: float
    max_trades_per_day: int
    max_open_positions: int
    max_daily_loss_percent: float
    max_spread_points: float
    require_stop_loss: bool
    cooldown_minutes: int
    max_consecutive_losses: int
    stop_demo_execution_path: Path
    stop_all_trading_path: Path


@dataclass(frozen=True)
class DemoOrderCandidate:
    symbol: str
    action: str
    volume: float
    entry: float | None
    stop_loss: float | None
    take_profit: float | None
    risk_reward: float | None
    source_stage: str | None
    signal_id: str | None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class DemoOrderIntent:
    symbol: str
    action: str
    order_type: str
    volume: float
    price: float | None
    stop_loss: float
    take_profit: float
    risk_reward: float
    comment: str
    magic: int
    signal_id: str | None
    source_stage: str | None
    metadata: dict[str, object]


@dataclass(frozen=True)
class DemoExecutionGuardResult:
    approved: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class DemoExecutionState:
    trades_today: int = 0
    open_positions: int = 0
    daily_loss_percent: float = 0.0
    consecutive_losses: int = 0
    last_signal_id: str | None = None
    cooldown_until: str | None = None


@dataclass(frozen=True)
class DemoOrderLifecycleRecord:
    timestamp: str
    stage: str
    symbol: str
    action: str | None
    volume: float | None
    approved: bool
    reasons: tuple[str, ...]
    account_mode: str | None
    mt5_retcode: int | None = None
    mt5_comment: str | None = None
    ticket: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)


def default_demo_execution_config(output_dir: Path) -> DemoExecutionConfig:
    return DemoExecutionConfig(
        mode="demo",
        allowed_symbols=(),
        max_lot=0.01,
        max_trades_per_day=3,
        max_open_positions=1,
        max_daily_loss_percent=2.0,
        max_spread_points=30.0,
        require_stop_loss=True,
        cooldown_minutes=30,
        max_consecutive_losses=2,
        stop_demo_execution_path=output_dir / "STOP_DEMO_EXECUTION",
        stop_all_trading_path=output_dir / "STOP_ALL_TRADING",
    )


def is_demo_mode(mode: str) -> bool:
    return _normalized_text(mode) in DEMO_MODES


def demo_stop_active(config: DemoExecutionConfig) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    if config.stop_demo_execution_path.exists():
        reasons.append("demo execution stop file active")
    if config.stop_all_trading_path.exists():
        reasons.append("global trading stop file active")
    return bool(reasons), tuple(reasons)


def validate_demo_order_candidate(
    candidate: DemoOrderCandidate,
    config: DemoExecutionConfig,
) -> DemoExecutionGuardResult:
    reasons: list[str] = []
    if config.allowed_symbols and candidate.symbol not in config.allowed_symbols:
        reasons.append("symbol is not allowed")
    if candidate.action not in ("BUY", "SELL"):
        reasons.append("action must be BUY or SELL")
    if candidate.volume <= 0:
        reasons.append("volume must be greater than zero")
    if candidate.volume > config.max_lot:
        reasons.append("volume exceeds max lot")
    if config.require_stop_loss and candidate.stop_loss is None:
        reasons.append("stop loss is required")
    if candidate.take_profit is None:
        reasons.append("take profit is required")
    if candidate.risk_reward is None or candidate.risk_reward <= 0:
        reasons.append("risk reward must be greater than zero")
    return _guard_result(reasons)


def validate_demo_risk_state(
    state: DemoExecutionState,
    config: DemoExecutionConfig,
) -> DemoExecutionGuardResult:
    reasons: list[str] = []
    if state.trades_today >= config.max_trades_per_day:
        reasons.append("max trades per day reached")
    if state.open_positions >= config.max_open_positions:
        reasons.append("max open positions reached")
    if state.daily_loss_percent >= config.max_daily_loss_percent:
        reasons.append("max daily loss reached")
    if state.consecutive_losses >= config.max_consecutive_losses:
        reasons.append("max consecutive losses reached")
    if _cooldown_active(state.cooldown_until):
        reasons.append("cooldown active")
    return _guard_result(reasons)


def validate_demo_execution_allowed(
    candidate: DemoOrderCandidate,
    state: DemoExecutionState,
    config: DemoExecutionConfig,
    account_mode: str | None,
    spread_points: float | None,
) -> DemoExecutionGuardResult:
    reasons: list[str] = []
    if not is_demo_mode(config.mode):
        reasons.append("demo execution mode is required")
    if account_mode is None:
        reasons.append("demo account confirmation is required")
    else:
        normalized_account_mode = _normalized_text(account_mode)
        if normalized_account_mode in BLOCKED_ACCOUNT_MODES:
            reasons.append("live account is not allowed")
        elif not is_demo_mode(account_mode):
            reasons.append("account mode is not demo")

    _, stop_reasons = demo_stop_active(config)
    reasons.extend(stop_reasons)

    if spread_points is None:
        reasons.append("spread points are required")
    elif spread_points > config.max_spread_points:
        reasons.append("spread exceeds max spread")

    reasons.extend(validate_demo_order_candidate(candidate, config).reasons)
    reasons.extend(validate_demo_risk_state(state, config).reasons)
    return _guard_result(reasons)


def build_demo_lifecycle_record(
    stage: str,
    candidate: DemoOrderCandidate,
    guard_result: DemoExecutionGuardResult,
    account_mode: str | None = None,
    metadata: dict[str, object] | None = None,
) -> DemoOrderLifecycleRecord:
    return DemoOrderLifecycleRecord(
        timestamp=datetime.now(timezone.utc).isoformat(),
        stage=stage,
        symbol=candidate.symbol,
        action=candidate.action,
        volume=candidate.volume,
        approved=guard_result.approved,
        reasons=guard_result.reasons,
        account_mode=account_mode,
        metadata=dict(metadata or {}),
    )


def build_demo_order_intent(
    candidate: DemoOrderCandidate,
    guard_result: DemoExecutionGuardResult,
    *,
    comment: str = "SignalBot demo execution",
    magic: int = 21001,
) -> DemoOrderIntent:
    if not guard_result.approved:
        raise ValueError("demo guard must be approved before building intent")
    order_type = _demo_order_type(candidate.action)
    if candidate.stop_loss is None:
        raise ValueError("stop loss is required")
    if candidate.take_profit is None:
        raise ValueError("take profit is required")
    if candidate.risk_reward is None or candidate.risk_reward <= 0:
        raise ValueError("risk reward is required")
    return DemoOrderIntent(
        symbol=candidate.symbol,
        action=candidate.action,
        order_type=order_type,
        volume=candidate.volume,
        price=candidate.entry,
        stop_loss=candidate.stop_loss,
        take_profit=candidate.take_profit,
        risk_reward=candidate.risk_reward,
        comment=comment,
        magic=magic,
        signal_id=candidate.signal_id,
        source_stage=candidate.source_stage,
        metadata=dict(candidate.metadata),
    )


def _demo_order_type(action: str) -> str:
    if action == "BUY":
        return "DEMO_BUY"
    if action == "SELL":
        return "DEMO_SELL"
    raise ValueError("action must be BUY or SELL")


def _guard_result(reasons: list[str]) -> DemoExecutionGuardResult:
    return DemoExecutionGuardResult(
        approved=not reasons,
        reasons=tuple(reasons),
    )


def _cooldown_active(cooldown_until: str | None) -> bool:
    if not cooldown_until:
        return False
    try:
        return datetime.now(timezone.utc) < datetime.fromisoformat(cooldown_until)
    except ValueError:
        return True


def _normalized_text(value: str) -> str:
    return value.strip().lower()
