"""价格行为技术指标——各策略共享的辅助函数。"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from backtest.models import Bar, Side


def ema(values: list[float], span: int) -> float:
    """一次性 EMA 计算（用于初始播种或小数据量场景）。增量场景请用 ema_inc。"""
    if len(values) < span:
        return float("nan")
    return float(pd.Series(values).ewm(span=span, adjust=False).mean().iloc[-1])


def ema_inc(state: dict, key: str, close: float, span: int) -> float:
    """增量 EMA，O(1)。首次调用以 close 为种子（与 ewm(adjust=False) 行为一致）。

    用法：每根 bar 调用一次，state 中缓存上一根 EMA 值。
    ema_now = ema_inc(ctx.state, "ema_20", bar.close, 20)
    """
    last = state.get(key)
    if last is None or last != last:  # None 或 NaN
        state[key] = close
        return close
    alpha = 2.0 / (span + 1)
    val = alpha * close + (1.0 - alpha) * last
    state[key] = val
    return val


def atr(bars: list[Bar], period: int) -> float:
    """Average True Range（SMA of TR），仅计算最近 period 根 bar，用于动态止损。"""
    n = len(bars)
    if n < period + 1:
        return float("nan")
    tr_values = []
    for i in range(max(1, n - period), n):
        hl = bars[i].high - bars[i].low
        hc = abs(bars[i].high - bars[i - 1].close)
        lc = abs(bars[i].low - bars[i - 1].close)
        tr_values.append(max(hl, hc, lc))
    return float(pd.Series(tr_values).mean())


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


def detect_swings(
    history: list[Bar],
    window: int = 2,
) -> list[tuple[int, float, str]]:
    """检测交替的摆动高点和低点。

    Returns:
        list of (bar_index, price, 'high'|'low') — 交替排列
    """
    n = len(history)
    if n < window * 2 + 1:
        return []

    swings: list[tuple[int, float, str]] = []
    for i in range(window, n - window):
        bar = history[i]
        neighbors = list(range(i - window, i)) + list(range(i + 1, i + window + 1))

        is_swing_high = all(bar.high >= history[j].high for j in neighbors)
        is_swing_low = all(bar.low <= history[j].low for j in neighbors)

        if is_swing_high and is_swing_low:
            continue

        if is_swing_high and (not swings or swings[-1][2] != "high"):
            swings.append((i, bar.high, "high"))
        elif is_swing_low and (not swings or swings[-1][2] != "low"):
            swings.append((i, bar.low, "low"))

    return swings


def is_trading_range(
    history: list[Bar],
    lookback: int = 20,
    overlap_threshold: float = 0.6,
) -> bool:
    """K 线重叠率检测交易区间。

    相邻 K 线重叠部分占联合区间 > overlap_threshold 的比例超过 60% → 视为 TR。
    """
    n = len(history)
    if n < lookback:
        return False

    recent = history[-lookback:]
    overlap_count = 0
    pair_count = len(recent) - 1

    for i in range(1, len(recent)):
        prev, cur = recent[i - 1], recent[i]
        joint_range = max(prev.high, cur.high) - min(prev.low, cur.low)
        if joint_range <= 0:
            overlap_count += 1
            continue
        overlap = min(prev.high, cur.high) - max(prev.low, cur.low)
        if overlap > 0 and overlap / joint_range >= overlap_threshold:
            overlap_count += 1

    if pair_count <= 0:
        return False
    return overlap_count / pair_count > 0.6
