from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SignalAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"


class Confidence(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class TrendDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    SIDEWAYS = "SIDEWAYS"


class TimeframeRole(str, Enum):
    HTF = "HTF"
    ZONE = "ZONE"
    MOMENTUM = "MOMENTUM"
    EXECUTION = "EXECUTION"


@dataclass(frozen=True)
class Candle:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class TimeframePlan:
    htf_timeframes: tuple[str, ...] = ("H4", "H1")
    zone_timeframes: tuple[str, ...] = ("M30", "M15")
    momentum_timeframe: str = "M5"
    execution_timeframe: str = "M1"
    timeframe_order: tuple[str, ...] = ("H4", "H1", "M30", "M15", "M5", "M1")


@dataclass(frozen=True)
class TimeframeContext:
    timeframe: str
    role: TimeframeRole
    direction: TrendDirection
    latest_timestamp: str | None = None


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade: float = 1.0
    max_daily_loss: float = 3.0
    max_trades_per_day: int = 8
    max_consecutive_losses: int = 3
    cooldown_minutes: int = 30


@dataclass(frozen=True)
class ExecutionPolicyConfig:
    max_spread_points: int = 500
    allowed_sessions: tuple[str, ...] = ("London", "NewYork")
    enable_news_filter: bool = False
    enable_break_even: bool = True
    enable_trailing_stop: bool = True
    enable_partial_close: bool = False


@dataclass(frozen=True)
class SignalConfig:
    symbol: str
    timeframe: str
    csv_path: str
    fast_ema_period: int
    slow_ema_period: int
    rsi_period: int
    atr_period: int
    atr_multiplier: float
    body_break_atr_ratio: float
    risk_reward: float
    min_candles: int
    max_candle_age_minutes: int
    multi_timeframe_enabled: bool
    timeframe_paths: dict[str, str]
    dry_run: bool
    send_wait: bool
    trade_mode: str = "high_winrate"
    execution_timeframe: str = "M1"
    momentum_timeframe: str = "M5"
    zone_timeframes: tuple[str, ...] = ("M30", "M15")
    htf_timeframes: tuple[str, ...] = ("H4", "H1")
    timeframe_order: tuple[str, ...] = ("H4", "H1", "M30", "M15", "M5", "M1")
    risk_config: RiskConfig = field(default_factory=RiskConfig)
    execution_policy_config: ExecutionPolicyConfig = field(default_factory=ExecutionPolicyConfig)

    def __post_init__(self) -> None:
        if self.risk_reward < 1.5:
            raise ValueError("SignalConfig.risk_reward must be at least 1.5")


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str | None
    chat_id: str | None


@dataclass(frozen=True)
class WebhookConfig:
    host: str
    port: int
    path: str
    secret: str
    dry_run: bool


@dataclass(frozen=True)
class AutoTradeConfig:
    enabled: bool
    mode: str
    order_file: str
    journal_file: str
    account_balance: float
    risk_percent: float
    contract_size: float
    min_volume: float
    max_volume: float
    volume_step: float
    allow_min_volume: bool
    magic_number: int
    comment: str


@dataclass(frozen=True)
class TradeLevels:
    entry: float | None
    stop_loss: float | None
    take_profit: float | None
    risk_reward: float | None


@dataclass(frozen=True)
class Signal:
    action: SignalAction
    symbol: str
    timeframe: str
    strategy_name: str
    market_structure: str
    setup_type: str
    trend_summary: str
    trend_alignment: str
    confidence: Confidence
    reason: str
    entry_condition: str
    invalidation: str
    no_trade_reason: str
    support: float
    resistance: float
    latest_close: float
    fast_ema: float
    slow_ema: float
    rsi: float
    atr: float
    levels: TradeLevels
