"""突破形态识别：突破、突破回调。"""
from __future__ import annotations

from typing import Optional

from backtest.models import Bar
from backtest.indicators import atr


def _atr_from_bars(bars: list[Bar], period: int = 14) -> float:
    """从 Bar 列表计算 ATR（复用 indicators.atr 的逻辑，适配直接传 Bar list）。"""
    if len(bars) < period + 1:
        return float("nan")
    tr_values = []
    for i in range(len(bars) - period, len(bars)):
        hl = bars[i].high - bars[i].low
        hc = abs(bars[i].high - bars[i - 1].close)
        lc = abs(bars[i].low - bars[i - 1].close)
        tr_values.append(max(hl, hc, lc))
    return sum(tr_values) / len(tr_values)


# ---------------------------------------------------------------------------
# 突破 (Breakout)
# ---------------------------------------------------------------------------

def detect_breakout(
    bars: list[Bar],
    lookback: int = 20,
    volume_ratio: float = 1.5,
    body_atr_ratio: float = 0.5,
    upper_wick_ratio: float = 0.3,
) -> Optional[dict]:
    """检测突破形态。

    逻辑：
    1. 近 lookback 日最高价 = 阻力位
    2. 当前收盘价 > 阻力位
    3. 当日成交量 > 20日均量 * volume_ratio
    4. 突破K线为大阳线（body > ATR * body_atr_ratio）
    5. 上影线不能太长（排除假突破）

    返回 dict: detected, score, pattern, detail
    """
    if len(bars) < lookback + 5:
        return None

    cur = bars[-1]
    prior = bars[:-1]

    # 阻力位：前 lookback 根的最高价（不含最新一根）
    resistance = max(b.high for b in prior[-lookback:])
    if resistance <= 0:
        return None
    cur_close = cur.close

    # 必须收盘价突破
    if cur_close <= resistance:
        return None

    # 当日必须是阳线
    if cur_close <= cur.open:
        return None

    # ---- 量能放大 ----
    vol_bars = prior[-20:] if len(prior) >= 20 else prior
    avg_vol = sum(b.volume for b in vol_bars) / max(1, len(vol_bars))
    vol_ok = cur.volume > avg_vol * volume_ratio if avg_vol > 0 else False
    if not vol_ok:
        return None

    # ---- 实体足够大 ----
    cur_atr = _atr_from_bars(bars, period=14)
    body = cur_close - cur.open
    body_ok = body > cur_atr * body_atr_ratio if cur_atr > 0 else False
    if not body_ok:
        return None

    # ---- 上影线不能太长（假突破信号） ----
    upper_wick = cur.high - cur_close
    full_range = cur.high - cur.low
    upper_wick_pct = upper_wick / full_range if full_range > 0 else 0
    is_fake = upper_wick_pct > upper_wick_ratio

    if is_fake:
        return None  # 上影线太长，视为假突破
    score = 80  # 基础分

    # 突破幅度
    breakout_pct = (cur_close - resistance) / resistance * 100
    if breakout_pct > 3:
        score += 10
    elif breakout_pct > 2:
        score += 5

    # 放量
    vol_multiple = cur.volume / avg_vol if avg_vol > 0 else 0
    if vol_multiple > 2.5:
        score += 15
    elif vol_multiple > 2.0:
        score += 10
    elif vol_multiple > 1.5:
        score += 5

    # 大阳线
    score += 5

    # 上影线扣分
    if upper_wick_pct > 0.2:
        score -= 5

    score = max(0, min(100, score))

    return {
        "detected": True,
        "score": score,
        "pattern": "突破",
        "detail": (f"突破阻力{resistance:.2f}, "
                   f"突破幅度{breakout_pct:.1f}%, "
                   f"量比{cur.volume / avg_vol:.1f}x"),
    }


# ---------------------------------------------------------------------------
# 突破回调 (Breakout Pullback)
# ---------------------------------------------------------------------------

def detect_breakout_pullback(
    bars: list[Bar],
    lookback: int = 20,
    max_pullback_pct: float = 5.0,
    proximity_pct: float = 2.5,
    volume_shrink_ratio: float = 0.7,
) -> Optional[dict]:
    """检测突破回调形态。

    逻辑：
    1. 近 lookback 日内发生过突破（收盘 > 前期高点）
    2. 突破后出现回调：从高点回落 < max_pullback_pct
    3. 回调到突破位附近（距离 < proximity_pct）
    4. 回调期间量能萎缩
    5. 未跌破突破位

    返回 dict: detected, score, pattern, detail
    """
    if len(bars) < lookback + 5:
        return None

    # ---- 找突破事件 ----
    # 从最近的候选突破开始验证，避免较早的失效突破挡住后面的有效结构。
    cur_price = bars[-1].close
    search_start = max(0, len(bars) - lookback)

    for i in range(len(bars) - 2, search_start - 1, -1):
        # 这一天之前20日的最高价
        prev_bars = bars[max(0, i - 20):i]
        if len(prev_bars) < 10:
            continue
        prev_high = max(b.high for b in prev_bars)
        if prev_high <= 0:
            continue
        if bars[i].close <= prev_high or bars[i].close <= bars[i].open:
            continue

        breakout_day = i
        breakout_price = prev_high  # 前阻力位

        # ---- 突破后的K线 = 回调期 ----
        pullback_bars = bars[breakout_day + 1:]
        if len(pullback_bars) < 2:
            continue  # 还没开始回调

        # ---- 回调幅度 ----
        pullback_high = max(b.high for b in pullback_bars)
        pullback_from_high = (pullback_high - cur_price) / pullback_high * 100 if pullback_high > 0 else 0

        if pullback_from_high > max_pullback_pct:
            continue  # 回调太深

        # ---- 回调到突破位附近 ----
        dist_to_support = abs(cur_price - breakout_price) / breakout_price * 100
        if dist_to_support > proximity_pct:
            continue  # 距离支撑位太远

        # ---- 未跌破突破位 ----
        pullback_low = min(b.low for b in pullback_bars)
        if pullback_low < breakout_price * 0.98:  # 允许2%假破位
            continue

        # ---- 回调期间量能萎缩 ----
        breakout_vol = bars[breakout_day].volume
        pullback_avg_vol = sum(b.volume for b in pullback_bars) / len(pullback_bars)
        vol_shrink = pullback_avg_vol < breakout_vol * volume_shrink_ratio if breakout_vol > 0 else False

        # ---- 评分 ----
        score = 75  # 基础分

        # 回调到位加分
        if 0.5 <= dist_to_support <= 1.5:
            score += 15  # 最佳回调位置
        elif dist_to_support < 0.5:
            score += 10

        # 缩量加分
        if vol_shrink:
            score += 10

        # 回调幅度
        if pullback_from_high < 2:
            score += 5

        # 回调过深扣分
        if pullback_from_high > 4:
            score -= 15
        elif pullback_from_high > 3:
            score -= 10

        score = max(0, min(100, score))

        return {
            "detected": True,
            "score": score,
            "pattern": "突破回调",
            "detail": (f"突破位{breakout_price:.2f}, "
                       f"回调{pullback_from_high:.1f}%, "
                       f"距支撑{dist_to_support:.1f}%, "
                       f"{'缩量' if vol_shrink else '未缩量'}"),
        }

    return None
