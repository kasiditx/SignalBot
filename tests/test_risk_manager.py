from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from trading_signal_bot.models import SignalAction
from trading_signal_bot.risk_manager import (
    DailyRiskState,
    PositionSizingInput,
    RiskCheckInput,
    RiskLimits,
    calculate_position_size,
    evaluate_risk,
    update_state_after_result,
)


NOW = datetime(2026, 5, 18, 9, 0, tzinfo=UTC)


class RiskManagerTest(unittest.TestCase):
    def test_rejects_live_mode(self) -> None:
        decision = evaluate_risk(_check(mode="live"))

        self.assertRejected(decision.reasons, "Only paper/demo mode is allowed; live mode is rejected")

    def test_rejects_missing_entry(self) -> None:
        decision = evaluate_risk(_check(entry=None))

        self.assertRejected(decision.reasons, "Entry price is required")

    def test_rejects_missing_stop_loss(self) -> None:
        decision = evaluate_risk(_check(stop_loss=None))

        self.assertRejected(decision.reasons, "Stop loss is required")

    def test_rejects_missing_take_profit(self) -> None:
        decision = evaluate_risk(_check(take_profit=None))

        self.assertRejected(decision.reasons, "Take profit is required")

    def test_rejects_missing_risk_reward(self) -> None:
        decision = evaluate_risk(_check(risk_reward=None))

        self.assertRejected(decision.reasons, "Risk/reward is required")

    def test_rejects_zero_stop_loss_distance(self) -> None:
        decision = evaluate_risk(_check(entry=100.0, stop_loss=100.0))

        self.assertRejected(decision.reasons, "Stop loss distance must be greater than zero")

    def test_rejects_low_risk_reward(self) -> None:
        decision = evaluate_risk(_check(risk_reward=1.4))

        self.assertTrue(any("below minimum" in reason for reason in decision.reasons))

    def test_rejects_risk_percent_above_limit(self) -> None:
        decision = evaluate_risk(_check(sizing=_sizing(risk_percent=1.5), limits=RiskLimits(risk_per_trade=1.0)))

        self.assertRejected(decision.reasons, "Risk per trade exceeds configured limit")

    def test_rejects_max_daily_loss(self) -> None:
        state = _state(realized_loss_percent=3.0)

        decision = evaluate_risk(_check(state=state))

        self.assertRejected(decision.reasons, "Max daily loss limit reached")

    def test_rejects_max_trades_per_day(self) -> None:
        state = _state(trades_today=8)

        decision = evaluate_risk(_check(state=state))

        self.assertRejected(decision.reasons, "Max trades per day reached")

    def test_rejects_max_consecutive_losses(self) -> None:
        state = _state(consecutive_losses=3)

        decision = evaluate_risk(_check(state=state))

        self.assertRejected(decision.reasons, "Max consecutive losses reached")

    def test_rejects_cooldown(self) -> None:
        state = _state(cooldown_until=NOW + timedelta(minutes=10))

        decision = evaluate_risk(_check(state=state))

        self.assertRejected(decision.reasons, "Trading is in cooldown after consecutive losses")

    def test_rejects_same_direction_stack(self) -> None:
        state = _state(open_directions=(SignalAction.BUY,))

        decision = evaluate_risk(_check(state=state))

        self.assertRejected(decision.reasons, "Open position already exists in the same direction")

    def test_calculates_position_size(self) -> None:
        volume = calculate_position_size(_sizing(balance=1000, risk_percent=1.0, entry=100, stop_loss=99))

        self.assertEqual(volume, 0.1)

    def test_rejects_raw_volume_below_min_when_min_volume_not_allowed(self) -> None:
        with self.assertRaisesRegex(ValueError, "below broker minimum"):
            calculate_position_size(
                _sizing(balance=100, risk_percent=0.1, entry=100, stop_loss=99, min_volume=0.01)
            )

    def test_caps_volume_at_max_volume(self) -> None:
        volume = calculate_position_size(
            _sizing(balance=10000, risk_percent=2.0, entry=100, stop_loss=99, max_volume=0.5)
        )

        self.assertEqual(volume, 0.5)

    def test_approves_when_all_conditions_pass(self) -> None:
        decision = evaluate_risk(_check())

        self.assertTrue(decision.approved)
        self.assertEqual(decision.reasons, ())
        self.assertEqual(decision.volume, 0.1)

    def test_update_state_after_win_resets_consecutive_losses(self) -> None:
        state = _state(consecutive_losses=2, losses_today=2, cooldown_until=NOW + timedelta(minutes=5))

        updated = update_state_after_result(state, "WIN", now=NOW)

        self.assertEqual(updated.trades_today, 1)
        self.assertEqual(updated.losses_today, 2)
        self.assertEqual(updated.consecutive_losses, 0)
        self.assertIsNone(updated.cooldown_until)

    def test_update_state_after_loss_increments_loss_counters(self) -> None:
        state = _state(consecutive_losses=0, losses_today=0, realized_loss_percent=0.5)

        updated = update_state_after_result(state, "LOSS", loss_percent=1.0, now=NOW)

        self.assertEqual(updated.trades_today, 1)
        self.assertEqual(updated.losses_today, 1)
        self.assertEqual(updated.consecutive_losses, 1)
        self.assertEqual(updated.realized_loss_percent, 1.5)

    def test_update_state_after_second_loss_sets_cooldown(self) -> None:
        state = _state(consecutive_losses=1, losses_today=1)

        updated = update_state_after_result(state, "LOSS", loss_percent=1.0, now=NOW)

        self.assertEqual(updated.consecutive_losses, 2)
        self.assertEqual(updated.cooldown_until, NOW + timedelta(minutes=30))

    def assertRejected(self, reasons: tuple[str, ...], expected: str) -> None:
        self.assertIn(expected, reasons)


def _check(
    action: SignalAction = SignalAction.BUY,
    entry: float | None = 100.0,
    stop_loss: float | None = 99.0,
    take_profit: float | None = 101.5,
    risk_reward: float | None = 1.5,
    sizing: PositionSizingInput | None = None,
    limits: RiskLimits | None = None,
    state: DailyRiskState | None = None,
    mode: str = "paper",
) -> RiskCheckInput:
    return RiskCheckInput(
        action=action,
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_reward=risk_reward,
        sizing=sizing or _sizing(),
        limits=limits or RiskLimits(),
        state=state or _state(),
        now=NOW,
        mode=mode,
    )


def _sizing(
    balance: float = 1000.0,
    risk_percent: float = 1.0,
    entry: float = 100.0,
    stop_loss: float = 99.0,
    contract_size: float = 100.0,
    min_volume: float = 0.01,
    max_volume: float = 1.0,
    volume_step: float = 0.01,
    allow_min_volume: bool = False,
) -> PositionSizingInput:
    return PositionSizingInput(
        balance=balance,
        risk_percent=risk_percent,
        entry=entry,
        stop_loss=stop_loss,
        contract_size=contract_size,
        min_volume=min_volume,
        max_volume=max_volume,
        volume_step=volume_step,
        allow_min_volume=allow_min_volume,
    )


def _state(
    trades_today: int = 0,
    losses_today: int = 0,
    consecutive_losses: int = 0,
    realized_loss_percent: float = 0.0,
    cooldown_until: datetime | None = None,
    open_directions: tuple[SignalAction, ...] = (),
) -> DailyRiskState:
    return DailyRiskState(
        date="2026-05-18",
        trades_today=trades_today,
        losses_today=losses_today,
        consecutive_losses=consecutive_losses,
        realized_loss_percent=realized_loss_percent,
        cooldown_until=cooldown_until,
        open_directions=open_directions,
    )


if __name__ == "__main__":
    unittest.main()
