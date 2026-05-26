"""回测框架数据类定义。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

Side = Literal["long", "short"]
Action = Literal["open_long", "open_short", "close"]
InstrumentType = Literal["futures", "stock"]


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
    trigger_price: float = 0.0


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

    # 品种类型
    instrument_type: InstrumentType = "futures"

    # 股票专用参数（期货模式下不生效）
    commission_rate: float = 0.00025      # 佣金费率（如万分之2.5 = 0.00025）
    stamp_tax_rate: float = 0.001          # 印花税率（卖出千分之1）
    transfer_fee_rate: float = 0.00001     # 过户费率（万分之0.1）
    lot_size: int = 100                    # 每手股数


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
    bars_df: Optional[object] = None  # 原始 DataFrame，供序列化提效
