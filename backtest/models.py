"""回测框架数据类定义。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

Side = Literal["long", "short"]
Action = Literal["open_long", "open_short", "close"]


@dataclass
class Bar:
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Order:
    action: Action
    qty: int
    reason: str = ""


@dataclass
class Fill:
    time: str
    action: Action
    qty: int
    price: float
    fee: float
    reason: str = ""


@dataclass
class Position:
    side: Side
    qty: int
    avg_price: float
    opened_time: str
    margin: float


@dataclass
class Trade:
    open_time: str
    close_time: str
    side: Side
    qty: int
    open_price: float
    close_price: float
    fee: float
    pnl: float
    net_pnl: float
    holding_bars: int
    open_reason: str = ""
    close_reason: str = ""


@dataclass
class BacktestConfig:
    symbol: str
    contract: Optional[str]
    period: str
    start_date: Optional[str]
    end_date: Optional[str]
    initial_capital: float
    tick_size: float
    tick_value: float
    margin_rate: float
    fee_per_lot: float
    slippage_ticks: int
    intraday_only: bool
    strategy: str
    strategy_params: dict = field(default_factory=dict)


@dataclass
class EquityPoint:
    time: str
    equity: float
    drawdown: float


@dataclass
class BacktestResult:
    config: BacktestConfig
    bars: list[Bar]
    fills: list[Fill]
    trades: list[Trade]
    equity_curve: list[EquityPoint]
    metrics: dict
    liquidated: bool = False
    liquidated_at: Optional[str] = None
