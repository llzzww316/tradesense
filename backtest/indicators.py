"""价格行为技术指标——各策略共享的辅助函数。"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from backtest.models import Bar, Side


def ema(values: list[float], span: int) -> float:
    if len(values) < span:
        return float("nan")
    return float(pd.Series(values).ewm(span=span, adjust=False).mean().iloc[-1])


def atr(bars: list[Bar], period: int) -> float:
    """Average True Range，用于动态止损。"""
    n = len(bars)
    if n < period + 1:
        return float("nan")
    tr_values = []
    for i in range(1, n):
        hl = bars[i].high - bars[i].low
        hc = abs(bars[i].high - bars[i - 1].close)
        lc = abs(bars[i].low - bars[i - 1].close)
        tr_values.append(max(hl, hc, lc))
    if len(tr_values) < period:
        return float("nan")
    return float(pd.Series(tr_values[-period:]).mean())


def trend_bar_side(
    bar: Bar, body_ratio_min: float = 0.5, close_extreme_ratio: float = 0.6
) -> Optional[Side]:
    """识别趋势 K：实体占比 >= body_ratio_min 且收盘接近一端极值。"""
    rng = bar.high - bar.low
    if rng <= 0:
        return None
    body = abs(bar.close - bar.open)
    if body / rng < body_ratio_min:
        return None
    if bar.close > bar.open:
        upper_zone = bar.low + rng * close_extreme_ratio
        if bar.close >= upper_zone:
            return "long"
    elif bar.close < bar.open:
        lower_zone = bar.high - rng * close_extreme_ratio
        if bar.close <= lower_zone:
            return "short"
    return None


def is_bull_bar(bar: Bar) -> bool:
    return bar.close > bar.open


def is_bear_bar(bar: Bar) -> bool:
    return bar.close < bar.open


def confirm_swing_low(history: list[Bar]) -> Optional[float]:
    """最近 3 根 K 中，中间那根 low 是最低 -> 确认摆动低。"""
    if len(history) < 3:
        return None
    a, b, c = history[-3], history[-2], history[-1]
    if b.low <= a.low and b.low <= c.low:
        return b.low
    return None


def confirm_swing_high(history: list[Bar]) -> Optional[float]:
    """最近 3 根 K 中，中间那根 high 是最高 -> 确认摆动高。"""
    if len(history) < 3:
        return None
    a, b, c = history[-3], history[-2], history[-1]
    if b.high >= a.high and b.high >= c.high:
        return b.high
    return None
