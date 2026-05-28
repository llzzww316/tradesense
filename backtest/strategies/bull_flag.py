"""上升旗型策略 —— 经典趋势延续形态。

形态结构:
    旗杆 (Pole):  一段陡峭的上涨，幅度 ≥ pole_min_pct，回撤 ≤ pole_max_retrace_pct
    旗帜 (Flag):  旗杆后的窄幅收敛整理，上轨下行，下轨走平或微升，ATR 显著收缩
    突破 (Breakout): 价格收盘站上旗帜上轨 → 入场做多
    止盈:         等幅测量 (旗杆高度从突破点向上投射)
    止损:         旗帜最低点下方 N 倍旗杆 ATR
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from backtest.indicators import ema_inc, is_bull_bar
from backtest.models import Bar
from backtest.registry import register_strategy


# ============================================================================
# 线性回归辅助
# ============================================================================

def _linreg(x: list[float], y: list[float]) -> tuple[float, float, float]:
    """简单线性回归 → (斜率, 截距, R²)。使用 pd.Series 批量计算。"""
    n = len(x)
    if n < 2:
        return 0.0, y[0] if y else 0.0, 0.0
    xs = pd.Series(x, dtype=float)
    ys = pd.Series(y, dtype=float)
    xm, ym = xs.mean(), ys.mean()
    num = ((xs - xm) * (ys - ym)).sum()
    den = ((xs - xm) ** 2).sum()
    if den == 0:
        return 0.0, ym, 0.0
    slope = float(num / den)
    intercept = float(ym - slope * xm)
    ss_res = ((ys - (slope * xs + intercept)) ** 2).sum()
    ss_tot = ((ys - ym) ** 2).sum()
    r2 = float(1.0 - (ss_res / ss_tot)) if ss_tot > 0 else (1.0 if ss_res == 0 else 0.0)
    return slope, intercept, r2


# ============================================================================
# 旗杆识别
# ============================================================================

def _calc_atr(bars: list[Bar]) -> float:
    """计算指定 bars 的 ATR（SMA of True Range）。"""
    if len(bars) < 2:
        return 0.0
    trs = []
    for i in range(1, len(bars)):
        hl = bars[i].high - bars[i].low
        hc = abs(bars[i].high - bars[i - 1].close)
        lc = abs(bars[i].low - bars[i - 1].close)
        trs.append(max(hl, hc, lc))
    return float(pd.Series(trs, dtype=float).mean()) if trs else 0.0


def _find_bull_pole(
    history: list[Bar],
    pole_lookback: int,
    pole_min_pct: float,
    pole_max_retrace_pct: float,
    min_gap: int = 0,
) -> Optional[tuple[int, int, float, float, float]]:
    """在 lookback 窗口内找到最近的 bull pole。

    min_gap: pole high 必须距末尾至少 min_gap 根 K（为旗帜/突破留空间）。
    返回 (pole_start_idx, pole_end_idx, pole_low, pole_high, pole_atr) 或 None。
    """
    n = len(history)
    start = max(0, n - pole_lookback)
    # 搜索窗口排除末尾 min_gap 根 bar
    search_end = max(start + 1, n - min_gap)
    if search_end <= start:
        return None
    recent = history[start:search_end]
    if not recent:
        return None

    highs = [b.high for b in recent]
    pole_high = max(highs)
    pole_high_local_idx = max(i for i, h in enumerate(highs) if h == pole_high)
    pole_high_idx = start + pole_high_local_idx

    pre_pole = history[start : pole_high_idx + 1]
    lows = [b.low for b in pre_pole]
    pole_low = min(lows)
    pole_low_local_idx = max(i for i, l in enumerate(lows) if l == pole_low)
    pole_low_idx = start + pole_low_local_idx

    pole_height = pole_high - pole_low
    if pole_low <= 0 or pole_height <= 0:
        return None
    if (pole_height / pole_low * 100) < pole_min_pct:
        return None

    after_peak = history[pole_high_idx + 1:]
    if not after_peak:
        return None
    max_retrace = max((pole_high - b.low) / pole_height for b in after_peak)
    if max_retrace > pole_max_retrace_pct / 100.0:
        return None

    pole_bars = history[pole_low_idx : pole_high_idx + 1]
    pole_atr_val = _calc_atr(pole_bars)
    if pole_atr_val <= 0:
        return None

    return (pole_low_idx, pole_high_idx, pole_low, pole_high, pole_atr_val)


# ============================================================================
# 旗型形态检测
# ============================================================================

def detect_bull_flag(
    history: list[Bar],
    *,
    pole_lookback: int = 20,
    pole_min_pct: float = 5.0,
    pole_max_retrace_pct: float = 30.0,
    flag_min_bars: int = 5,
    flag_atr_shrink_ratio: float = 0.6,
    flag_max_height_ratio: float = 0.5,
    pole_high_min_bull_bars: int = 2,
) -> Optional[dict]:
    """检测上升旗型形态。

    检测逻辑:
      1. 在最近 pole_lookback 根 K 线中找旗杆 (陡峭上涨)
      2. 旗杆之后 flag_min_bars 根 K 线形成旗帜 (窄幅收敛)
      3. 返回突破价，由调用方确认是否突破

    Returns:
        dict: {
            pole_low, pole_high, pole_height, pole_atr,
            flag_low, flag_upper_slope, flag_upper_intercept,
            breakout_price,
        }
        None: 未检测到形态
    """
    n = len(history)
    if n < flag_min_bars + 4:
        return None

    # ---- 1. 旗杆（排除末尾 flag_min_bars+1 根 bar 作为旗帜/突破预留） ----
    pole = _find_bull_pole(
        history, pole_lookback, pole_min_pct, pole_max_retrace_pct,
        min_gap=flag_min_bars + 1,
    )
    if pole is None:
        return None

    pole_start_idx, pole_end_idx, pole_low, pole_high, pole_atr_val = pole
    pole_height = pole_high - pole_low

    # 旗杆中至少有 N 根阳线
    pole_bars = history[pole_start_idx : pole_end_idx + 1]
    bull_count = sum(1 for b in pole_bars if is_bull_bar(b))
    if bull_count < pole_high_min_bull_bars:
        return None

    # 旗杆之后至少 flag_min_bars+1 根 K 线
    if n - pole_end_idx - 1 < flag_min_bars + 1:
        return None

    # ---- 2. 旗帜 ----
    flag_bars = history[pole_end_idx + 1:]   # 旗杆后的所有 bar（含当前）
    flag_body = flag_bars[:-1]                # 不含当前 bar

    if len(flag_body) < flag_min_bars:
        return None

    # 旗帜最低点
    flag_low = min(b.low for b in flag_body)

    # 旗帜高度约束
    if pole_high - flag_low > pole_height * flag_max_height_ratio:
        return None

    # ATR 收缩
    # 只统计旗帜本体波动，不把突破 bar 混入整理区间 ATR。
    flag_atr_val = _calc_atr(flag_body)
    if flag_atr_val <= 0 or flag_atr_val > pole_atr_val * flag_atr_shrink_ratio:
        return None

    # ---- 3. 上轨拟合（必须下行） ----
    xs = list(range(1, len(flag_body) + 1))
    highs = [b.high for b in flag_body]
    u_slope, u_intercept, u_r2 = _linreg(xs, highs)

    avg_high = sum(highs) / len(highs)
    if avg_high <= 0:
        return None
    # 用相对斜率做品种无关阈值（每 bar 跌幅至少 0.1%）
    if (u_slope / avg_high) >= -0.001:
        return None

    # ---- 4. 下轨拟合（不能明显下行） ----
    lows = [b.low for b in flag_body]
    l_slope, _, _ = _linreg(xs, lows)

    avg_low = sum(lows) / len(lows)
    if avg_low <= 0:
        return None
    if (l_slope / avg_low) < -0.001:
        return None

    # ---- 5. 当前 bar 对应的上轨突破价 ----
    current_x = len(flag_body) + 1
    breakout_price = u_slope * current_x + u_intercept

    if breakout_price < flag_low:
        return None

    return {
        "pole_low": pole_low,
        "pole_high": pole_high,
        "pole_height": pole_height,
        "pole_atr": pole_atr_val,
        "pole_start_idx": pole_start_idx,
        "pole_end_idx": pole_end_idx,
        "flag_low": flag_low,
        "flag_upper_slope": u_slope,
        "flag_upper_r2": u_r2,
        "flag_upper_intercept": u_intercept,
        "breakout_price": breakout_price,
    }


# ============================================================================
# 策略入口
# ============================================================================

@register_strategy("bull_flag")
def on_bar(
    bar: Bar,
    ctx,
    *,
    # 旗杆参数
    pole_lookback: int = 15,
    pole_min_pct: float = 1.5,
    pole_max_retrace_pct: float = 60.0,
    pole_high_min_bull_bars: int = 2,
    # 旗帜参数
    flag_min_bars: int = 3,
    flag_atr_shrink_ratio: float = 1.0,
    flag_max_height_ratio: float = 0.7,
    # 风控参数
    atr_stop_mult: float = 1.5,
    fixed_qty: int = 100,
    # 趋势过滤
    trend_filter: bool = True,
    trend_ema_fast: int = 50,
    trend_ema_slow: int = 150,
    # R² 自适应止损
    r2_filter: bool = False,
    r2_min: float = 0.30,
    r2_stop_tight: float = 0.5,
    r2_stop_loose: float = 2.5,
) -> None:
    """上升旗型策略 — on_bar 回调。

    每根 K 线调用一次：
      - 持仓时：检测止损/止盈
      - 空仓时：检测旗型形态 → 突破确认 → 开多
    """
    # ---- 持仓管理 ----
    if ctx.position_side == "short":
        return

    if ctx.position_side == "long":
        _manage_long(ctx, bar)
        return

    # ---- 已有挂单则跳过 ----
    if ctx.pending_orders:
        return

    # ---- 检测旗型 ----
    info = detect_bull_flag(
        ctx.history,
        pole_lookback=pole_lookback,
        pole_min_pct=pole_min_pct,
        pole_max_retrace_pct=pole_max_retrace_pct,
        flag_min_bars=flag_min_bars,
        flag_atr_shrink_ratio=flag_atr_shrink_ratio,
        flag_max_height_ratio=flag_max_height_ratio,
        pole_high_min_bull_bars=pole_high_min_bull_bars,
    )

    if info is None:
        return

    # ---- 突破确认（收盘价站上上轨） ----
    if bar.close <= info["breakout_price"]:
        return

    # ---- EMA 趋势过滤 ----
    if trend_filter:
        fast_key = f"bf_ema_fast_{trend_ema_fast}"
        slow_key = f"bf_ema_slow_{trend_ema_slow}"
        init_key = f"bf_ema_init_{trend_ema_fast}_{trend_ema_slow}"

        if len(ctx.closes) < max(trend_ema_fast, trend_ema_slow):
            return

        if not ctx.state.get(init_key):
            for c in ctx.closes:
                ema_inc(ctx.state, fast_key, float(c), trend_ema_fast)
                ema_inc(ctx.state, slow_key, float(c), trend_ema_slow)
            ctx.state[init_key] = True
        else:
            close_val = float(bar.close)
            ema_inc(ctx.state, fast_key, close_val, trend_ema_fast)
            ema_inc(ctx.state, slow_key, close_val, trend_ema_slow)

        fast_val = float(ctx.state[fast_key])
        slow_val = float(ctx.state[slow_key])
        if fast_val <= slow_val:
            return
    # ---- R² 自适应止损 ----
    if r2_filter:
        u_r2 = info["flag_upper_r2"]
        if u_r2 < r2_min:
            return
        ratio = (1.0 - u_r2) / max(1.0 - r2_min, 0.001)
        eff_mult = r2_stop_loose - ratio * (r2_stop_loose - r2_stop_tight)
    else:
        eff_mult = atr_stop_mult

    # ---- 入场 ----
    pole_height = info["pole_height"]
    pole_atr_val = info["pole_atr"]
    flag_low = info["flag_low"]

    stop_price = flag_low - eff_mult * pole_atr_val
    target_price = bar.close + pole_height

    ctx.state["bf_entry"] = bar.close
    ctx.state["bf_stop"] = stop_price
    ctx.state["bf_target"] = target_price
    ctx.state["bf_pole_height"] = pole_height
    ctx.state["bf_entry_filled"] = False
    ctx.state["bf_pole_high"] = info["pole_high"]
    ctx.state["bf_flag_low"] = flag_low

    ctx.buy(fixed_qty, reason=f"BullFlag bk@{info['breakout_price']:.2f}")


def _manage_long(ctx, bar: Bar) -> None:
    """管理多头持仓：止损 / 止盈。"""
    if not ctx.state.get("bf_entry_filled"):
        fill_price = float(ctx.position_avg_price or 0.0)
        pole_height = float(ctx.state.get("bf_pole_height", 0.0))
        if fill_price > 0 and pole_height > 0:
            ctx.state["bf_entry"] = fill_price
            ctx.state["bf_target"] = fill_price + pole_height
            ctx.state["bf_entry_filled"] = True

    stop = ctx.state.get("bf_stop", 0.0)
    target = ctx.state.get("bf_target", float("inf"))

    if bar.low <= stop:
        ctx.close(reason=f"SL@{stop:.2f}")
        return

    if bar.high >= target:
        ctx.close(reason=f"TP@{target:.2f}")
