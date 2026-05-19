from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .models import SignalAction


PAPER_MODES = {"paper", "demo"}
LOSS_RESULTS = {"LOSS", "SL", "STOP_LOSS", "LOSS_BOTH_HIT"}


@dataclass(frozen=True)
class PositionSizingInput:
    balance: float
    risk_percent: float
    entry: float
    stop_loss: float
    contract_size: float
    min_volume: float
    max_volume: float
    volume_step: float
    allow_min_volume: bool = False


@dataclass(frozen=True)
class RiskLimits:
    risk_per_trade: float = 1.0
    max_daily_loss: float = 3.0
    max_trades_per_day: int = 8
    max_consecutive_losses: int = 3
    cooldown_minutes: int = 30
    minimum_risk_reward: float = 1.5


@dataclass(frozen=True)
class DailyRiskState:
    date: str
    trades_today: int
    losses_today: int
    consecutive_losses: int
    realized_loss_percent: float
    cooldown_until: datetime | None = None
    open_directions: tuple[SignalAction, ...] = ()


@dataclass(frozen=True)
class RiskCheckInput:
    action: SignalAction
    entry: float | None
    stop_loss: float | None
    take_profit: float | None
    risk_reward: float | None
    sizing: PositionSizingInput
    limits: RiskLimits
    state: DailyRiskState
    now: datetime
    mode: str = "paper"


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reasons: tuple[str, ...]
    volume: float | None
    money_at_risk: float | None
    risk_percent: float | None


def calculate_position_size(sizing: PositionSizingInput) -> float:
    _validate_sizing_input(sizing)
    risk_distance = abs(sizing.entry - sizing.stop_loss)
    money_at_risk = sizing.balance * (sizing.risk_percent / 100.0)
    raw_volume = money_at_risk / (risk_distance * sizing.contract_size)

    if raw_volume < sizing.min_volume and not sizing.allow_min_volume:
        raise ValueError(
            "Calculated volume is below broker minimum volume; "
            "using minimum volume would exceed configured risk"
        )

    stepped_volume = math.floor(raw_volume / sizing.volume_step) * sizing.volume_step
    volume = sizing.min_volume if stepped_volume < sizing.min_volume else stepped_volume
    capped_volume = round(min(volume, sizing.max_volume), 8)
    if capped_volume <= 0:
        raise ValueError("Calculated volume must be greater than zero")
    return capped_volume


def evaluate_risk(check: RiskCheckInput) -> RiskDecision:
    reasons: list[str] = []
    mode = check.mode.strip().lower()

    if mode not in PAPER_MODES:
        reasons.append("Only paper/demo mode is allowed; live mode is rejected")
    if check.entry is None:
        reasons.append("Entry price is required")
    if check.stop_loss is None:
        reasons.append("Stop loss is required")
    if check.take_profit is None:
        reasons.append("Take profit is required")
    if check.risk_reward is None:
        reasons.append("Risk/reward is required")
    elif check.risk_reward < check.limits.minimum_risk_reward:
        reasons.append(f"Risk/reward is below minimum 1:{check.limits.minimum_risk_reward:.2f}")
    if check.sizing.risk_percent > check.limits.risk_per_trade:
        reasons.append("Risk per trade exceeds configured limit")
    if check.state.realized_loss_percent >= check.limits.max_daily_loss:
        reasons.append("Max daily loss limit reached")
    if check.state.trades_today >= check.limits.max_trades_per_day:
        reasons.append("Max trades per day reached")
    if check.state.consecutive_losses >= check.limits.max_consecutive_losses:
        reasons.append("Max consecutive losses reached")
    if is_in_cooldown(check.state, check.now):
        reasons.append("Trading is in cooldown after consecutive losses")
    if would_stack_same_direction(check.action, check.state):
        reasons.append("Open position already exists in the same direction")

    if check.entry is not None and check.stop_loss is not None:
        if abs(check.entry - check.stop_loss) <= 0:
            reasons.append("Stop loss distance must be greater than zero")

    volume: float | None = None
    money_at_risk: float | None = None
    if not reasons:
        try:
            sizing = PositionSizingInput(
                balance=check.sizing.balance,
                risk_percent=check.sizing.risk_percent,
                entry=float(check.entry),
                stop_loss=float(check.stop_loss),
                contract_size=check.sizing.contract_size,
                min_volume=check.sizing.min_volume,
                max_volume=check.sizing.max_volume,
                volume_step=check.sizing.volume_step,
                allow_min_volume=check.sizing.allow_min_volume,
            )
            volume = calculate_position_size(sizing)
            money_at_risk = sizing.balance * (sizing.risk_percent / 100.0)
        except ValueError as exc:
            reasons.append(str(exc))

    return RiskDecision(
        approved=not reasons,
        reasons=tuple(reasons),
        volume=volume if not reasons else None,
        money_at_risk=money_at_risk if not reasons else None,
        risk_percent=check.sizing.risk_percent if not reasons else None,
    )


def is_in_cooldown(state: DailyRiskState, now: datetime) -> bool:
    return state.cooldown_until is not None and now < state.cooldown_until


def would_stack_same_direction(action: SignalAction, state: DailyRiskState) -> bool:
    return action in state.open_directions


def update_state_after_result(
    state: DailyRiskState,
    result: str,
    loss_percent: float = 0.0,
    now: datetime | None = None,
) -> DailyRiskState:
    event_time = now or datetime.now(tz=UTC)
    normalized_result = result.strip().upper()
    is_loss = normalized_result in LOSS_RESULTS or loss_percent > 0
    consecutive_losses = state.consecutive_losses + 1 if is_loss else 0
    losses_today = state.losses_today + 1 if is_loss else state.losses_today
    cooldown_until = state.cooldown_until

    if is_loss and consecutive_losses >= 2:
        cooldown_until = event_time + timedelta(minutes=max(0, 30))
    if not is_loss:
        cooldown_until = None

    return DailyRiskState(
        date=state.date,
        trades_today=state.trades_today + 1,
        losses_today=losses_today,
        consecutive_losses=consecutive_losses,
        realized_loss_percent=state.realized_loss_percent + max(0.0, loss_percent),
        cooldown_until=cooldown_until,
        open_directions=state.open_directions,
    )


def _validate_sizing_input(sizing: PositionSizingInput) -> None:
    if sizing.balance <= 0:
        raise ValueError("Balance must be greater than zero")
    if sizing.risk_percent <= 0:
        raise ValueError("Risk percent must be greater than zero")
    if sizing.contract_size <= 0:
        raise ValueError("Contract size must be greater than zero")
    if sizing.min_volume < 0:
        raise ValueError("Minimum volume cannot be negative")
    if sizing.max_volume <= 0:
        raise ValueError("Maximum volume must be greater than zero")
    if sizing.min_volume > sizing.max_volume:
        raise ValueError("Minimum volume must be lower than or equal to maximum volume")
    if sizing.volume_step <= 0:
        raise ValueError("Volume step must be greater than zero")
    if abs(sizing.entry - sizing.stop_loss) <= 0:
        raise ValueError("Stop loss distance must be greater than zero")
