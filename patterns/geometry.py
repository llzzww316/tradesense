"""几何形态识别：牛旗、上升三角形。"""
from __future__ import annotations

import math
from typing import Optional

from backtest.models import Bar
from backtest.indicators import detect_swings, atr, squeeze_detection


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _bars_to_dicts(bars: list[Bar]) -> list[dict]:
    """将 Bar dataclass 列表转为 dict 列表，方便后续处理。"""
    return [{"open": b.open, "high": b.high, "low": b.low,
             "close": b.close, "volume": b.volume, "time": b.time}
            for b in bars]


def _find_local_extremes(bars: list[dict], lookback: int = 3) -> tuple[list[dict], list[dict]]:
    """在最近 lookback*2+1 根 K 线中找局部高低点序列。"""
    highs = []
    lows = []
    for i in range(lookback, len(bars) - lookback):
        is_high = all(bars[i]["high"] >= bars[j]["high"]
                      for j in range(i - lookback, i + lookback + 1) if j != i)
        is_low = all(bars[i]["low"] <= bars[j]["low"]
                     for j in range(i - lookback, i + lookback + 1) if j != i)
        if is_high:
            highs.append({"index": i, "price": bars[i]["high"]})
        if is_low:
            lows.append({"index": i, "price": bars[i]["low"]})
    return highs, lows


# ---------------------------------------------------------------------------
# 牛旗 (Bull Flag)
# ---------------------------------------------------------------------------

def detect_bull_flag(
    bars: list[Bar],
    flagpole_gain_pct: float = 15.0,
    flag_max_pullback_ratio: float = 0.4,
    flag_max_bars: int = 25,
    proximity_pct: float = 6.0,
) -> Optional[dict]:
    """检测牛旗形态。

    逻辑：
    1. 找旗杆：近 N 日内从低点到高点的明显上涨（>15%）
    2. 找旗面：之后的回调/横盘，幅度 < 旗杆的 1/3
    3. 旗面期间波动收窄
    4. 当前价格接近旗面上沿

    返回 dict:
        detected: bool
        score: int (0-100)
        detail: str
    """
    if len(bars) < 30:
        return None

    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]

    best_flagpole = None

    # 在过去 60-90 根内寻找旗杆
    # pole_end 从 len-3 开始（给旗面留至少 3 根）到 search_start 结束
    search_start = max(10, len(bars) - 90)
    for pole_end in range(len(bars) - 3, search_start, -1):
        # 从 pole_end 往前找旗杆起点（最近的显著低点）
        pole_low_idx = pole_end
        for i in range(pole_end, max(search_start - 1, 0), -1):
            if lows[i] < lows[pole_low_idx]:
                pole_low_idx = i

        pole_low = lows[pole_low_idx]
        pole_high_idx = max(range(pole_low_idx, pole_end + 1), key=lambda idx: highs[idx])
        pole_high = highs[pole_high_idx]
        if pole_low <= 0:
            continue
        gain_pct = (pole_high - pole_low) / pole_low * 100
        if gain_pct >= flagpole_gain_pct:
            if best_flagpole is None or pole_high > best_flagpole["high"]:
                best_flagpole = {
                    "start_idx": pole_low_idx,
                    "end_idx": pole_high_idx,
                    "low": pole_low,
                    "high": pole_high,
                    "gain_pct": gain_pct,
                }

    if best_flagpole is None:
        return None

    pole_high = best_flagpole["high"]
    pole_low = best_flagpole["low"]
    pole_gain = pole_high - pole_low
    flag_start = best_flagpole["end_idx"] + 1

    # 旗面：从旗杆高点之后到最近的 K 线
    flag_bars = bars[flag_start:]
    if len(flag_bars) < 3 or len(flag_bars) > flag_max_bars:
        return None

    flag_highs = [b.high for b in flag_bars]
    flag_lows = [b.low for b in flag_bars]
    flag_closes = [b.close for b in flag_bars]

    # 旗面最高点不应大幅超过旗杆高点（否则已突破）
    flag_max = max(flag_highs)
    if flag_max > pole_high * 1.03:
        return None

    # ---- 旗面上沿水平度检查（线性回归法）----
    # 牛旗的上沿应该大致水平。
    # 对旗面高点做线性回归，如果斜率显著为负（R²>0.5），说明高点在趋势性下移，是回调不是旗形。
    flag_upper_slope_pct = 3.0  # 7根K线内累计下移不超过3%视为水平
    flag_upper_r2_min = 0.5    # 线性拟合度阈值，超过说明下移是趋势而非噪音
    n_flag = len(flag_highs)
    if n_flag >= 5:
        x_vals = list(range(n_flag))
        x_mean = sum(x_vals) / n_flag
        y_mean = sum(flag_highs) / n_flag
        num = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, flag_highs))
        den = sum((x - x_mean) ** 2 for x in x_vals)
        if den > 0:
            slope = num / den
            # R²
            y_pred = [slope * x + (y_mean - slope * x_mean) for x in x_vals]
            ss_res = sum((y - yp) ** 2 for y, yp in zip(flag_highs, y_pred))
            ss_tot = sum((y - y_mean) ** 2 for y in flag_highs)
            r_sq = 1 - ss_res / ss_tot if ss_tot > 0 else 0

            total_trend_pct = slope / y_mean * 100 * (n_flag - 1)
            if slope < 0 and r_sq > flag_upper_r2_min and total_trend_pct < -flag_upper_slope_pct:
                # 高点趋势性下移，这是回调不是旗形
                return None

    # 旗面回调幅度 = 旗杆高点 - 旗面最低点
    flag_min_low = min(flag_lows)
    pullback = pole_high - flag_min_low
    pullback_ratio = pullback / pole_gain if pole_gain > 0 else 1.0
    if pullback_ratio > flag_max_pullback_ratio:
        return None

    # 旗面低点不跌破旗杆起点
    if flag_min_low < pole_low:
        return None

    # 评分
    score = 70  # 基础分

    # 旗杆涨幅加分
    if best_flagpole["gain_pct"] > 25:
        score += 10
    elif best_flagpole["gain_pct"] > 20:
        score += 5

    # 旗面缩量（如有 volume）
    if hasattr(bars[0], 'volume') and bars[0].volume is not None:
        pole_avg_vol = sum(b.volume for b in bars[flag_start - 5:flag_start]) / max(1, min(5, flag_start))
        flag_avg_vol = sum(b.volume for b in flag_bars) / len(flag_bars)
        if pole_avg_vol > 0 and flag_avg_vol < pole_avg_vol * 0.6:
            score += 10  # 明显缩量

    # 旗面收窄
    if squeeze_detection(flag_bars, lookback=min(8, len(flag_bars))):
        score += 5

    # 旗面过长扣分
    if len(flag_bars) > 20:
        score -= 10

    # 当前价格接近旗面上沿（允许略微超出，极端强拉可能微破）
    cur_price = bars[-1].close
    upper_edge = pole_high  # 旗面上沿 ≈ 旗杆高点
    dist_pct = abs(cur_price - upper_edge) / upper_edge * 100
    if dist_pct > proximity_pct:
        return None  # 距离太远，形态未成熟

    score = max(0, min(100, score))

    return {
        "detected": True,
        "score": score,
        "pattern": "牛旗",
        "detail": (f"旗杆涨幅{best_flagpole['gain_pct']:.1f}%, "
                   f"回调{pullback_ratio:.1%}, "
                   f"旗面{len(flag_bars)}根K线, "
                   f"距上沿{dist_pct:.1f}%"),
    }


# ---------------------------------------------------------------------------
# 上升三角形 (Ascending Triangle)
# ---------------------------------------------------------------------------

def detect_asc_triangle(
    bars: list[Bar],
    min_swing_points: int = 3,
    resistance_tolerance_pct: float = 3.0,
    max_upper_slope_down_pct: float = 1.0,
    proximity_pct: float = 3.0,
    max_narrowing_ratio: float = 0.9,
) -> Optional[dict]:
    """检测上升三角形形态。

    逻辑：
    1. 用 detect_swings 找高低点序列
    2. 上沿：略微下倾或水平（累计斜率绝对值 < max_upper_slope_down_pct 或向上）
    3. 低点逐步抬高（至少3个低点递增）
    4. 波动收窄（后半段振幅 < 前半段）
    5. 当前价格接近上沿

    返回 dict:
        detected: bool
        score: int (0-100)
        detail: str
    """
    if len(bars) < 30:
        return None

    swings = detect_swings(bars, window=3)
    if len(swings) < 5:
        return None

    highs = [(idx, price) for idx, price, kind in swings if kind == "high"]
    lows = [(idx, price) for idx, price, kind in swings if kind == "low"]

    if len(highs) < 2 or len(lows) < min_swing_points:
        return None

    # ---- 上沿判定：允许水平或略下倾 ----
    recent_highs = [(idx, p) for idx, p in highs[-4:]]
    n_high = len(recent_highs)

    # 用线性回归算上沿斜率
    x_h = [i for i in range(n_high)]
    y_h = [p for _, p in recent_highs]
    x_h_mean = sum(x_h) / n_high
    y_h_mean = sum(y_h) / n_high
    denom_h = sum((x - x_h_mean) ** 2 for x in x_h)
    if denom_h == 0:
        return None
    slope_h = sum((x - x_h_mean) * (y - y_h_mean) for x, y in zip(x_h, y_h)) / denom_h
    # 累计斜率占第一个高点价格的百分比
    total_slope_pct = (slope_h * (n_high - 1)) / y_h_mean * 100 if y_h_mean > 0 else 0

    # 上沿向上倾斜太多 → 那是上升通道，不是三角形
    if total_slope_pct > resistance_tolerance_pct:
        return None
    # 上沿下倾太陡 → 不是上升三角形
    if total_slope_pct < -max_upper_slope_down_pct:
        return None

    # ---- 低点上升趋势 ----
    recent_lows = [p for _, p in lows[-min_swing_points - 1:]]
    if len(recent_lows) < min_swing_points:
        return None

    # 检查低点是否递增（允许小幅波动）
    ascending_count = 0
    for i in range(1, len(recent_lows)):
        if recent_lows[i] > recent_lows[i - 1] * 0.99:  # 允许1%容差
            ascending_count += 1
    ascending_ratio = ascending_count / (len(recent_lows) - 1)
    if ascending_ratio < 0.6:
        return None  # 低点上升不够明显

    # ---- 波动收窄 ----
    # 三角形前半段 vs 后半段的振幅应该缩小
    swing_start_idx = highs[-4][0] if len(highs) >= 4 else highs[0][0]
    triangle_bars = bars[swing_start_idx:]
    if len(triangle_bars) < 10:
        return None

    mid = len(triangle_bars) // 2
    first_half_range = max(b.high for b in triangle_bars[:mid]) - min(b.low for b in triangle_bars[:mid])
    second_half_range = max(b.high for b in triangle_bars[mid:]) - min(b.low for b in triangle_bars[mid:])
    if first_half_range <= 0:
        return None
    narrowing_ratio = second_half_range / first_half_range
    if narrowing_ratio > max_narrowing_ratio:
        return None

    # ---- 当前价格接近上沿 ----
    cur_price = bars[-1].close
    # 用线性回归预测最新高点位置作为"阻力位"
    upper_at_end = slope_h * (n_high - 1) + (y_h_mean - slope_h * x_h_mean)
    dist_to_upper = abs(cur_price - upper_at_end) / upper_at_end * 100

    if dist_to_upper > proximity_pct:
        return None  # 距上沿太远

    # ---- 评分 ----
    score = 75  # 基础分

    # 上沿越平越好（正斜率也不错，但别扣分）
    if total_slope_pct < -0.5:
        score -= 10  # 下倾太多扣分
    elif total_slope_pct < 0:
        score -= 5  # 轻微下倾微扣

    # 低点递增越明显越好
    if ascending_ratio >= 0.8:
        score += 10
    elif ascending_ratio >= 0.6:
        score += 5

    # 波动收窄加分
    if narrowing_ratio < 0.5:
        score += 10
    elif narrowing_ratio < 0.7:
        score += 5

    # 上沿斜率太正扣分（通道不是三角形）
    if total_slope_pct > 2.0:
        score -= 10

    score = max(0, min(100, score))

    return {
        "detected": True,
        "score": score,
        "pattern": "上升三角形",
        "detail": (f"上沿斜率{total_slope_pct:.1f}%, "
                   f"低点递增{ascending_ratio:.0%}, "
                   f"收窄比{narrowing_ratio:.2f}, "
                   f"距上沿{dist_to_upper:.1f}%"),
    }
