"""菲阿里改良版 v2.1 — Mode A：OHLC4 关键位回调策略。

基于 OHLC4 计算日内关键位，配合斐波那契 0.382 回调：
  - 前一日高低价 → OHLC4 均价作为锚点
  - 当日开盘价 vs 锚点 → 判定多空方向
  - 跳空检测：开盘偏离锚点过大时改用斐波那契位
  - v2.1 新增：日线趋势三取二投票过滤（K线计数/结构/MA5）

────────────────────────────────────────────────────────────────
回测记录 (螺纹钢2610, 5m, 2026-03-01 ~ 2026-06-03)
────────────────────────────────────────────────────────────────
配置: initial_capital=100000, tick_size=1, tick_value=10,
      margin_rate=0.10, fee_per_lot=3, slippage=1, intraday_only=True

结果:
  最终权益:   99,722.00
  总收益率:       -0.28%
  最大回撤:     -278.00 (-0.28%)
  总交易数:      13 (胜2 / 负11)
  胜率:          15.38%
  盈亏比:         0.33
  平均盈利:      69.00
  平均亏损:      37.82
  Sharpe:        -8.18

已修复bug:
  _is_new_trading_day 原条件 "prev_t >= 15*60 and cur_t >= 21*60"
  会在夜盘内每根K线重复触发（21:05→21:10 也满足 >=15:00 且 >=21:00），
  导致 day_idx 虚高、range 计算错误、方向永远为 None。
  修复: 增加 "prev_t < 21*60" 限制，仅在午后→夜盘切换时触发。

问题分析:
  1. 尾盘入场：大量交易在 22:05-23:00 入场后立即被 eod_force 强平。
     建议: 22:00 之后停止开仓（当前限制 22:30，实际 eod_force 从 22:30
     开始但入场可能太晚来不及盈利）。
  2. 交易频率低: 3个月仅13笔，大量交易日 range < 25 tick 导致
     fa_direction = None，无入场信号。
  3. 盈亏比不足: 平均亏损 37.82 > 平均盈利 69.00 的比例不合理，
     止损 3 tick 过紧、止盈目标偏小。
  4. 时段限制（14:00-15:00 和 22:00+ 跳过入场）合理，
     但 eod_force_close 在 14:45 和 22:30 可能需要提前。
"""
from __future__ import annotations

from typing import Optional

from backtest.models import Bar, Side
from backtest.registry import register_strategy


# ── 工具函数 ──────────────────────────────────────────────────

def _bar_time_minutes(t: str) -> int:
    try:
        parts = t.split(" ")[1].split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return 0


def _is_new_trading_day(bar: Bar, prev_bar: Optional[Bar]) -> bool:
    if prev_bar is None:
        return True
    prev_t = _bar_time_minutes(prev_bar.time)
    cur_t = _bar_time_minutes(bar.time)
    if prev_t >= 15 * 60 and prev_t < 21 * 60 and cur_t >= 21 * 60:
        return True
    prev_date = prev_bar.time.split(" ")[0]
    cur_date = bar.time.split(" ")[0]
    if cur_date != prev_date and cur_t < 21 * 60:
        return True
    return False


def _is_night_session(bar: Bar) -> bool:
    t = _bar_time_minutes(bar.time)
    return t >= 21 * 60 or t < 3 * 60


def _is_eod_force_close(bar: Bar) -> bool:
    t = _bar_time_minutes(bar.time)
    if t >= 14 * 60 + 45:
        return True
    if t >= 22 * 60 + 30:
        return True
    return False


def _entity_ratio(bar: Bar) -> float:
    rng = bar.high - bar.low
    if rng <= 0:
        return 0.0
    return abs(bar.close - bar.open) / rng


def _ohlc4(o: float, h: float, l: float, c: float) -> float:
    return (o + h + l + c) / 4.0


def _fib_upper(h: float, l: float) -> float:
    return h - (h - l) * 0.382


def _fib_lower(h: float, l: float) -> float:
    return l + (h - l) * 0.382


def _ma5(closes: list[float]) -> float:
    if len(closes) < 5:
        return sum(closes) / len(closes) if closes else 0.0
    return sum(closes[-5:]) / 5.0


def _daily_trend(daily_bars: list[dict]) -> str:
    """v2.1 日线趋势判定：三取二。
    
    daily_bars: list of {o, h, l, c}, most recent last.
    Returns: "up", "down", or "sideways"
    """
    if len(daily_bars) < 4:
        return "sideways"
    
    recent = daily_bars[-6:]  # up to 6
    
    # Method 1: candle count
    bulls = sum(1 for d in recent if d["c"] > d["o"])
    bears = len(recent) - bulls
    m1 = "up" if bulls >= 4 else ("down" if bears >= 4 else "sideways")
    
    # Method 2: structure
    lows = [d["l"] for d in recent]
    highs = [d["h"] for d in recent]
    higher_lows = all(lows[i] >= lows[i-1] for i in range(1, len(lows)))
    lower_highs = all(highs[i] <= highs[i-1] for i in range(1, len(highs)))
    m2 = "up" if higher_lows else ("down" if lower_highs else "sideways")
    
    # Method 3: MA5
    closes = [d["c"] for d in recent]
    ma5_now = _ma5(closes)
    ma5_prev = _ma5(closes[:-1]) if len(closes) > 1 else ma5_now
    m3 = "up" if ma5_now > ma5_prev else "down"
    
    votes = [m1, m2, m3]
    up_votes = votes.count("up")
    down_votes = votes.count("down")
    
    if up_votes >= 2:
        return "up"
    elif down_votes >= 2:
        return "down"
    return "sideways"


# ── 策略入口 ──────────────────────────────────────────────────

@register_strategy("fiali_mode_a")
def on_bar(
    bar: Bar,
    ctx,
    *,
    zone_ticks: int = 3,
    entity_min_ratio: float = 0.4,
    stop_ticks: int = 3,
    min_range: int = 25,
    time_stop_minutes: int = 60,
    fixed_qty: int = 1,
    scale_ratio: float = 0.5,
    max_flips: int = 1,
    night_stop_ratio: float = 0.67,
    gap_ratio: float = 0.5,
    daily_trend_lookback: int = 6,
    tick_value: float = 10.0,
    tick_size: float = 1.0,
) -> None:
    s = ctx.state

    # ── 初始化 ──
    if not s.get("fa_init"):
        s["fa_init"] = True
        s["fa_day_idx"] = 0
        s["fa_day_o"] = 0.0
        s["fa_day_h"] = float("-inf")
        s["fa_day_l"] = float("inf")
        s["fa_direction"] = None
        s["fa_anchor"] = 0.0
        s["fa_anchor_name"] = ""
        s["fa_range"] = 0.0
        s["fa_flips_today"] = 0
        s["fa_entry_bar_idx"] = -1
        s["fa_entry_price"] = 0.0
        s["fa_stop_price"] = 0.0
        s["fa_first_target"] = 0.0
        s["fa_hit_first"] = False
        s["fa_entry_day_idx"] = -1
        s["fa_prev_complete_day"] = {}
        s["fa_daily_bars"] = []          # v2.1: rolling daily bars
        s["fa_daily_trend"] = "sideways" # v2.1: current daily trend
        s["fa_day_bars_start"] = 0
        s["fa_had_position"] = False

    # ── 持仓管理 ──
    if ctx.position_side is not None:
        _manage_position(bar, ctx, s)
        return

    if ctx.pending_orders:
        return

    # ── 交易日切换 ──
    prev_bar = ctx.history[-2] if len(ctx.history) >= 2 else None
    if _is_new_trading_day(bar, prev_bar):
        # 保存刚完成的交易日
        if s["fa_day_idx"] > 0:
            completed = {
                "o": s["fa_day_o"],
                "h": s["fa_day_h"],
                "l": s["fa_day_l"],
                "c": ctx.history[-2].close if len(ctx.history) >= 2 else bar.close,
            }
            s["fa_prev_complete_day"] = completed
            # v2.1: 追加到日线历史
            s["fa_daily_bars"].append(completed)
            if len(s["fa_daily_bars"]) > daily_trend_lookback + 5:
                s["fa_daily_bars"] = s["fa_daily_bars"][-daily_trend_lookback - 5:]
            # v2.1: 更新日线趋势
            s["fa_daily_trend"] = _daily_trend(s["fa_daily_bars"])

        # 重置日状态
        s["fa_day_idx"] += 1
        s["fa_day_o"] = bar.open
        s["fa_day_h"] = bar.high
        s["fa_day_l"] = bar.low
        s["fa_flips_today"] = 0
        s["fa_hit_first"] = False
        s["fa_entry_bar_idx"] = -1
        s["fa_had_position"] = False
        s["fa_day_bars_start"] = len(ctx.history) - 1

        # 计算当日设置
        prev = s["fa_prev_complete_day"]
        if prev and all(k in prev for k in ("o", "h", "l", "c")):
            _calc_day_setup(bar, ctx, s, prev, min_range, zone_ticks, gap_ratio)
    else:
        s["fa_day_h"] = max(s["fa_day_h"], bar.high)
        s["fa_day_l"] = min(s["fa_day_l"], bar.low)

    # ── 方向为空 → 跳过 ──
    if s["fa_direction"] is None:
        return

    direction: Side = s["fa_direction"]
    anchor = s["fa_anchor"]

    # ── Mode A 入场检测 ──
    zone_lo = anchor - zone_ticks * tick_size
    zone_hi = anchor + zone_ticks * tick_size

    if direction == "long":
        if bar.low <= zone_hi and bar.high >= zone_lo:
            if bar.close > bar.open and _entity_ratio(bar) >= entity_min_ratio:
                _enter_long(bar, ctx, s, zone_ticks, tick_size)
    elif direction == "short":
        if bar.high >= zone_lo and bar.low <= zone_hi:
            if bar.close < bar.open and _entity_ratio(bar) >= entity_min_ratio:
                _enter_short(bar, ctx, s, zone_ticks, tick_size)

    # ── 方向翻转检测（夜盘不触发） ──
    if not _is_night_session(bar) and s["fa_flips_today"] < max_flips and not s["fa_had_position"]:
        if direction == "long" and bar.close < anchor:
            s["fa_below_count"] = s.get("fa_below_count", 0) + 1
            if s["fa_below_count"] >= 3:
                # v2.1: daily trend protects — don't flip against trend
                if s["fa_daily_trend"] != "up":
                    s["fa_direction"] = "short"
                    s["fa_flips_today"] += 1
                    s["fa_below_count"] = 0
                    _calc_anchor_from_prev(s, gap_ratio, "short")
        elif direction == "short" and bar.close > anchor:
            s["fa_above_count"] = s.get("fa_above_count", 0) + 1
            if s["fa_above_count"] >= 3:
                if s["fa_daily_trend"] != "down":
                    s["fa_direction"] = "long"
                    s["fa_flips_today"] += 1
                    s["fa_above_count"] = 0
                    _calc_anchor_from_prev(s, gap_ratio, "long")
        else:
            s["fa_below_count"] = 0
            s["fa_above_count"] = 0

    # ── 时段限制 ──
    t_now = _bar_time_minutes(bar.time)
    if (14 * 60 <= t_now < 15 * 60) or t_now >= 22 * 60:
        return


# ── 辅助函数 ──────────────────────────────────────────────────

def _calc_day_setup(bar, ctx, s, prev, min_range, zone_ticks, gap_ratio):
    """v2.1: 新交易日设置 — Layer 1 日线趋势 + Layer 2 OHLC4。"""
    o, h, l, c = prev["o"], prev["h"], prev["l"], prev["c"]
    ohlc4 = _ohlc4(o, h, l, c)
    day_range = h - l
    s["fa_range"] = day_range

    if day_range < min_range:
        s["fa_direction"] = None
        s["fa_anchor"] = 0.0
        return

    # ── Layer 2: OHLC4 方向判定（Layer 1 日线趋势已在上面更新） ──
    today_open = s["fa_day_o"]
    daily_trend = s.get("fa_daily_trend", "sideways")

    if today_open > ohlc4:
        ohcl4_dir = "long"
    elif today_open < ohlc4:
        ohcl4_dir = "short"
    else:
        ohcl4_dir = None

    # ── v2.1: 日线趋势过滤 ──
    if daily_trend == "up":
        # 上升趋势只做多
        if ohcl4_dir == "short":
            s["fa_direction"] = None  # 忽略做空信号
        else:
            s["fa_direction"] = "long"
    elif daily_trend == "down":
        if ohcl4_dir == "long":
            s["fa_direction"] = None
        else:
            s["fa_direction"] = "short"
    else:
        # 震荡 → 直接用 OHLC4 方向
        s["fa_direction"] = ohcl4_dir

    # 跳空检测
    gap = abs(today_open - ohlc4)
    if gap > day_range * gap_ratio:
        if today_open > ohlc4:
            s["fa_anchor"] = _fib_upper(h, l)
            s["fa_anchor_name"] = "fib_upper"
        else:
            s["fa_anchor"] = _fib_lower(h, l)
            s["fa_anchor_name"] = "fib_lower"
    else:
        s["fa_anchor"] = ohlc4
        s["fa_anchor_name"] = "ohlc4"

    is_night = _is_night_session(bar)
    s["fa_first_target"] = day_range / 4.0 if is_night else day_range / 3.0


def _calc_anchor_from_prev(s, gap_ratio, new_dir):
    prev = s["fa_prev_complete_day"]
    if not prev:
        return
    o, h, l, c = prev["o"], prev["h"], prev["l"], prev["c"]
    ohlc4 = _ohlc4(o, h, l, c)
    day_range = h - l
    gap = abs(s["fa_day_o"] - ohlc4)
    if gap > day_range * gap_ratio:
        s["fa_anchor"] = _fib_upper(h, l) if new_dir == "short" else _fib_lower(h, l)
    else:
        s["fa_anchor"] = ohlc4


def _enter_long(bar, ctx, s, zone_ticks, tick_size):
    qty = int(ctx.state.get("fa_fixed_qty", 1))
    s["fa_entry_bar_idx"] = len(ctx.history) - 1
    s["fa_entry_price"] = bar.close
    s["fa_stop_price"] = bar.low - zone_ticks * tick_size
    s["fa_hit_first"] = False
    s["fa_entry_day_idx"] = s["fa_day_idx"]
    s["fa_had_position"] = True
    ctx.buy(qty, reason=f"ModeA_L@{bar.close:.1f}")


def _enter_short(bar, ctx, s, zone_ticks, tick_size):
    qty = int(ctx.state.get("fa_fixed_qty", 1))
    s["fa_entry_bar_idx"] = len(ctx.history) - 1
    s["fa_entry_price"] = bar.close
    s["fa_stop_price"] = bar.high + zone_ticks * tick_size
    s["fa_hit_first"] = False
    s["fa_entry_day_idx"] = s["fa_day_idx"]
    s["fa_had_position"] = True
    ctx.sell(qty, reason=f"ModeA_S@{bar.close:.1f}")


def _manage_position(bar, ctx, s):
    side = ctx.position_side
    if side is None:
        return

    stop = s.get("fa_stop_price", 0.0)
    first_target_val = s.get("fa_first_target", 0.0)
    entry_price = s.get("fa_entry_price", 0.0)
    tick_size_val = ctx.state.get("fa_tick_size", 1.0)

    if side == "long" and bar.low <= stop:
        ctx.close(reason=f"SL@{stop:.1f}")
        return
    if side == "short" and bar.high >= stop:
        ctx.close(reason=f"SL@{stop:.1f}")
        return

    if not s.get("fa_hit_first", False) and first_target_val > 0 and entry_price > 0:
        if side == "long" and bar.high >= entry_price + first_target_val * tick_size_val:
            s["fa_hit_first"] = True
            s["fa_stop_price"] = entry_price
            ctx.close(reason=f"TP1@{entry_price + first_target_val * tick_size_val:.1f}")
            return
        if side == "short" and bar.low <= entry_price - first_target_val * tick_size_val:
            s["fa_hit_first"] = True
            s["fa_stop_price"] = entry_price
            ctx.close(reason=f"TP1@{entry_price - first_target_val * tick_size_val:.1f}")
            return

    entry_idx = s.get("fa_entry_bar_idx", -1)
    if entry_idx >= 0:
        bars_held = len(ctx.history) - entry_idx
        time_stop_bars = int(ctx.state.get("fa_time_stop_minutes", 60) / 5)
        if bars_held >= time_stop_bars:
            ctx.close(reason="time_stop")
            return

    if _is_eod_force_close(bar):
        ctx.close(reason="eod_force")
        return
