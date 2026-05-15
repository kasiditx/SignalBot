from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class Candle:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float


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
