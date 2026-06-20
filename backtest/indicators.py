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

def pin_bar(bar: Bar, body_ratio_max: float = 0.4, wick_to_body: float = 2.0) -> Optional[Side]:
    """Pin Bar / 锤子线 / 吊人线：实体小、影线长，影线至少是实体的 wick_to_body 倍。

    上影线长 → 空头信号（short）
    下影线长 → 多头信号（long）
    """
    rng = bar.high - bar.low
    if rng <= 0:
        return None
    body = abs(bar.close - bar.open)
    if body / rng > body_ratio_max:
        return None
    upper_wick = bar.high - max(bar.close, bar.open)
    lower_wick = min(bar.close, bar.open) - bar.low
    if lower_wick > upper_wick and lower_wick >= body * wick_to_body:
        return "long"
    if upper_wick > lower_wick and upper_wick >= body * wick_to_body:
        return "short"
    return None


def engulfing(prev: Bar, cur: Bar) -> Optional[Side]:
    """吞没形态：当前 K 实体完全覆盖前一根实体。

    牛市吞没（cur 阳线吞 prev 阴线）→ long
    熊市吞没（cur 阴线吞 prev 阳线）→ short
    """
    prev_body = abs(prev.close - prev.open)
    cur_body = abs(cur.close - cur.open)
    if prev_body <= 0 or cur_body <= prev_body:
        return None
    prev_bull = prev.close > prev.open
    cur_bull = cur.close > cur.open
    if prev_bull and not cur_bull:
        return "short"
    if not prev_bull and cur_bull:
        return "long"
    return None


def harami(prev: Bar, cur: Bar) -> Optional[Side]:
    """孕线：当前 K 实体完全在前一根实体内。

    牛市孕线（阴 -> 阳/十字星，高位）→ 可能反转
    熊市孕线（阳 -> 阴/十字星，低位）→ 可能反转
    返回方向为可能的反转方向。
    """
    prev_body = abs(prev.close - prev.open)
    cur_body = abs(cur.close - cur.open)
    if prev_body <= 0 or cur_body >= prev_body:
        return None
    prev_top = max(prev.close, prev.open)
    prev_bot = min(prev.close, prev.open)
    cur_top = max(cur.close, cur.open)
    cur_bot = min(cur.close, cur.open)
    if cur_bot < prev_bot or cur_top > prev_top:
        return None  # 实体不完全包含
    prev_bull = prev.close > prev.open
    if prev_bull:
        return "short"  # 上涨后孕线 → 动能减弱，可能转空
    else:
        return "long"   # 下跌后孕线 → 动能减弱，可能转多


def inside_bar(outer: Bar, inner: Bar) -> bool:
    """Inside Bar：当前 K 的最高/最低完全在前一根范围内。"""
    return inner.high <= outer.high and inner.low >= outer.low


def fake_breakout(prior: Bar, breakout: Bar, confirm: Bar, side: Side) -> bool:
    """假突破检测：突破前一根高点/低点后立即收回。

    Args:
        prior: 突破前的参考 K
        breakout: 突破 K（影线突破但收盘收回）
        confirm: 确认 K（收盘反向）
        side: "long" = 假突破多头（突破前高后跌回），"short" = 假突破空头
    """
    if side == "long":
        if breakout.high <= prior.high:
            return False  # 根本没突破
        if breakout.close > prior.high:
            return False  # 收盘没收回来，是真突破
        return confirm.close < min(breakout.close, prior.high)
    else:
        if breakout.low >= prior.low:
            return False
        if breakout.close < prior.low:
            return False
        return confirm.close > max(breakout.close, prior.low)


def three_bar_push(a: Bar, b: Bar, c: Bar, side: Side) -> bool:
    """连续推力：三根同向 K，每根收盘都比前一根更远（延续确认）。"""
    if side == "long":
        return a.close > a.open and b.close > b.open and c.close > c.open \
            and b.close > a.close and c.close > b.close
    else:
        return a.close < a.open and b.close < b.open and c.close < c.open \
            and b.close < a.close and c.close < b.close


def doji(bar: Bar, body_max_ratio: float = 0.1) -> bool:
    """十字星：实体极小，开盘收盘几乎相等。"""
    rng = bar.high - bar.low
    if rng <= 0:
        return False
    return abs(bar.close - bar.open) / rng <= body_max_ratio


def marubozu(bar: Bar, body_min_ratio: float = 0.9) -> Optional[Side]:
    """光脚/光头阳线/阴线：实体几乎占满整根 K 线范围，上下影线极小。"""
    rng = bar.high - bar.low
    if rng <= 0:
        return None
    if abs(bar.close - bar.open) / rng < body_min_ratio:
        return None
    return "long" if bar.close > bar.open else "short"


# ---------------------------------------------------------------------------
# 多 K 组合信号
# ---------------------------------------------------------------------------

def bullish_reversal_setup(history: list[Bar], lookback: int = 5) -> Optional[str]:
    """检测多头反转形态组合（Pin Bar / 吞没 / 孕线）。"""
    if len(history) < 3:
        return None
    a, b = history[-3], history[-2]  # 前两根
    cur = history[-1]

    # 先看最近趋势方向：前几根是否偏空
    bearish_recent = all(
        history[-i].close < history[-i].open
        for i in range(3, min(lookback, len(history)) + 1)
    ) if len(history) > 3 else True

    # Pin Bar 在低点
    if pin_bar(cur) == "long" and cur.low <= min(a.low, b.low if b else float("inf")):
        return "pin_bar"

    # 吞没在低点：cur 阳线吞前一根阴线
    if engulfing(a, cur) == "long":
        # 确认前一根是阴线
        return "engulfing"

    # 孕线后阳线
    if harami(a, cur) == "long" and is_bull_bar(cur):
        return "harami"

    return None


def bearish_reversal_setup(history: list[Bar], lookback: int = 5) -> Optional[str]:
    """检测空头反转形态组合。"""
    if len(history) < 3:
        return None
    a, b = history[-3], history[-2]
    cur = history[-1]

    if pin_bar(cur) == "short" and cur.high >= max(a.high, b.high if b else float("-inf")):
        return "pin_bar"
    if engulfing(a, cur) == "short":
        return "engulfing"
    if harami(a, cur) == "short" and is_bear_bar(cur):
        return "harami"

    return None


def breakout_confirmation(history: list[Bar], lookback: int = 10) -> Optional[Side]:
    """突破确认：最近 bar 收盘突破 lookback 根内的显著 swing 高/低点。

    收盘站上最近 swing high → long
    收盘跌破最近 swing low → short
    None → 无确认突破
    """
    if len(history) < lookback + 1:
        return None
    swings = detect_swings(history, window=2)
    if not swings:
        return None
    cur = history[-1]
    # 取最近一个 swing
    last_swing = swings[-1]
    _, price, kind = last_swing
    # 再往前找前一个同类型的 swing 作为确认
    prev_same = None
    for i in range(len(swings) - 2, -1, -1):
        if swings[i][2] == kind:
            prev_same = swings[i]
            break

    if kind == "high":
        # 前一个 swing high 和当前之间
        prev_price = prev_same[1] if prev_same else price
        # 收盘突破最近的 swing high
        if cur.close > price:
            return "long"
        # 收盘跌破最近的 swing low → 空头
    else:
        if cur.close < price:
            return "short"

    return None


def squeeze_detection(history: list[Bar], lookback: int = 8) -> bool:
    """连续 Inside Bar 或窄幅 K 线 → 爆发前的蓄力。"""
    if len(history) < lookback:
        return False
    recent = history[-lookback:]
    inside_count = 0
    for i in range(1, len(recent)):
        if inside_bar(recent[i - 1], recent[i]):
            inside_count += 1
    return inside_count >= lookback * 0.5


def exhaustion_pattern(history: list[Bar], lookback: int = 5) -> Optional[Side]:
    """衰竭形态：连续加速 K 线 + 长上影或长下影（趋势动能耗尽）。"""
    if len(history) < lookback + 1:
        return None
    recent = history[-(lookback + 1):]
    # 检查最后 3 根是否连续同向
    a, b, c = recent[-3], recent[-2], recent[-1]

    # 多头衰竭：连续阳线，最后出现长上影或实体缩小
    if all(r.close > r.open for r in (a, b, c)):
        # 检查加速
        bodies = [abs(r.close - r.open) for r in (a, b, c)]
        if bodies[-1] < bodies[-2] or bodies[-2] < bodies[-3]:
            # 实体开始缩小，且最后一根有上影线
            if c.high - c.close > abs(c.close - c.open):
                return "short"
    # 空头衰竭：连续阴线，最后出现长下影或实体缩小
    elif all(r.close < r.open for r in (a, b, c)):
        bodies = [abs(r.close - r.open) for r in (a, b, c)]
        if bodies[-1] < bodies[-2] or bodies[-2] < bodies[-3]:
            if c.close - c.low > abs(c.close - c.open):
                return "long"
    return None
