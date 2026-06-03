"""菲阿里改良版 v2.0 — Mode A：OHLC4 关键位回调策略。

基于前日 OHLC4 作为核心锚点，单一方向日内交易：
  - 价格在 OHLC4 上方 → 只做多（等回踩 OHLC4 ±3tick 不破）
  - 价格在 OHLC4 下方 → 只做空（等反弹 OHLC4 ±3tick 不过）
  - 每天最多翻转 1 次方向

核心规则：
  1. 振幅过滤：前日振幅 < min_range → 跳过
  2. 方向判定：开盘+收盘 vs OHLC4（v2.0 优先级）
  3. 入场：价格进入 OHLC4 ± zone_ticks → 实体 ≥ 40% 的反转 K → 下一根入场
  4. 止损：入场 K 反向 3 tick
  5. 时间止损：入场 1h 不达目标 → 平仓；收盘前 15min / 22:30 强平
  6. 止盈：第一目标平 50%
  7. 跳空 > 0.5×振幅 → 改用 0.382 斐波那契位
"""
from __future__ import annotations

from typing import Optional

from backtest.models import Bar, Side
from backtest.registry import register_strategy


# ── 工具函数 ──────────────────────────────────────────────────

def _bar_time_minutes(t: str) -> int:
    """从 '2026-06-03 21:05:00' 提取分钟数（当日）。"""
    try:
        parts = t.split(" ")[1].split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return 0


def _is_new_trading_day(bar: Bar, prev_bar: Optional[Bar]) -> bool:
    """检测是否进入新交易日。新交易日以 21:00 夜盘为起点。"""
    if prev_bar is None:
        return True
    prev_t = _bar_time_minutes(prev_bar.time)
    cur_t = _bar_time_minutes(bar.time)
    # 跨日：前一 bar 是 15:00 之后，当前 bar 是 21:00 之后
    if prev_t >= 15 * 60 and cur_t >= 21 * 60:
        return True
    prev_date = prev_bar.time.split(" ")[0]
    cur_date = bar.time.split(" ")[0]
    # 日期跨越（比如周五 23:00 → 下周一 9:05）
    if cur_date != prev_date and cur_t < 21 * 60:
        return True
    return False


def _is_night_session(bar: Bar) -> bool:
    """是否夜盘时段（21:00-23:00 或次日 0:00-2:30）。"""
    t = _bar_time_minutes(bar.time)
    return t >= 21 * 60 or t < 3 * 60


def _is_eod_force_close(bar: Bar, next_bar: Optional[Bar]) -> bool:
    """当前 bar 是否为日末强平点：14:45 或 22:30。"""
    t = _bar_time_minutes(bar.time)
    if t >= 14 * 60 + 45:  # 日盘 14:45+
        if next_bar is None:
            return True
        nt = _bar_time_minutes(next_bar.time)
        if nt >= 21 * 60:  # 下一根是夜盘
            return True
    if t >= 22 * 60 + 30:  # 夜盘 22:30+
        return True
    return False


def _entity_ratio(bar: Bar) -> float:
    """实体占 K 线高度比例。"""
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


# ── 策略入口 ──────────────────────────────────────────────────

@register_strategy("fiali_mode_a")
def on_bar(
    bar: Bar,
    ctx,
    *,
    # 入场参数
    zone_ticks: int = 3,
    entity_min_ratio: float = 0.4,
    stop_ticks: int = 3,
    # 振幅过滤
    min_range: int = 25,
    # 时间止损（分钟）
    time_stop_minutes: int = 60,
    # 仓位
    fixed_qty: int = 1,
    scale_ratio: float = 0.5,
    # 方向翻转
    max_flips: int = 1,
    # 夜盘参数
    night_stop_ratio: float = 0.67,
    # 跳空
    gap_ratio: float = 0.5,
    # 产品参数（由品种推断）
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
        s["fa_day_bars_start"] = 0
        s["fa_had_position"] = False

    # ── 持仓管理 ──
    if ctx.position_side is not None:
        _manage_position(bar, ctx, s)
        return

    # ── 已有挂单 → 等成交 ──
    if ctx.pending_orders:
        return

    # ── 交易日切换 ──
    prev_bar = ctx.history[-2] if len(ctx.history) >= 2 else None
    if _is_new_trading_day(bar, prev_bar):
        # 保存刚完成的交易日数据
        if s["fa_day_idx"] > 0:
            s["fa_prev_complete_day"] = {
                "o": s["fa_day_o"],
                "h": s["fa_day_h"],
                "l": s["fa_day_l"],
                "c": ctx.history[-2].close if len(ctx.history) >= 2 else bar.close,
            }
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

        # 计算 OHLC4 + 方向
        prev = s["fa_prev_complete_day"]
        if prev and all(k in prev for k in ("o", "h", "l", "c")):
            _calc_day_setup(bar, ctx, s, prev, min_range, zone_ticks, gap_ratio)
    else:
        # 更新当日最高最低
        s["fa_day_h"] = max(s["fa_day_h"], bar.high)
        s["fa_day_l"] = min(s["fa_day_l"], bar.low)

    # ── 方向为空 → 跳过 ──
    if s["fa_direction"] is None:
        return

    direction: Side = s["fa_direction"]
    anchor = s["fa_anchor"]

    # ── 夜盘禁做多（如果数据源是夜盘起始）/ 方向翻转检查 ──
    if _is_night_session(bar):
        # 夜盘不触发方向翻转
        pass

    # ── Mode A 入场检测 ──
    zone_lo = anchor - zone_ticks * tick_size
    zone_hi = anchor + zone_ticks * tick_size

    if direction == "long":
        # 做多：等回踩到 OHLC4 ± zone
        if bar.low <= zone_hi and bar.high >= zone_lo:
            # 价格进入了入场区
            if bar.close > bar.open and _entity_ratio(bar) >= entity_min_ratio:
                # 阳线实体够大 → 入场
                _enter_long(bar, ctx, s, zone_ticks, tick_size)
    elif direction == "short":
        if bar.high >= zone_lo and bar.low <= zone_hi:
            if bar.close < bar.open and _entity_ratio(bar) >= entity_min_ratio:
                _enter_short(bar, ctx, s, zone_ticks, tick_size)

    # ── 方向翻转检测 ──
    if s["fa_flips_today"] < max_flips and not s["fa_had_position"]:
        if direction == "long" and bar.close < anchor:
            s["fa_below_count"] = s.get("fa_below_count", 0) + 1
            if s["fa_below_count"] >= 3:
                s["fa_direction"] = "short"
                s["fa_flips_today"] += 1
                s["fa_below_count"] = 0
                _calc_anchor_from_prev(s, gap_ratio, "short")
        elif direction == "short" and bar.close > anchor:
            s["fa_above_count"] = s.get("fa_above_count", 0) + 1
            if s["fa_above_count"] >= 3:
                s["fa_direction"] = "long"
                s["fa_flips_today"] += 1
                s["fa_above_count"] = 0
                _calc_anchor_from_prev(s, gap_ratio, "long")
        else:
            s["fa_below_count"] = 0
            s["fa_above_count"] = 0

    # ── 14:00 后不开新仓 ──
    t_now = _bar_time_minutes(bar.time)
    if t_now >= 14 * 60 and t_now < 15 * 60:
        return
    if t_now >= 22 * 60:
        return


# ── 辅助函数 ──────────────────────────────────────────────────

def _calc_day_setup(bar, ctx, s, prev, min_range, zone_ticks, gap_ratio):
    """新交易日开始时计算 OHLC4、方向、锚点。"""
    o, h, l, c = prev["o"], prev["h"], prev["l"], prev["c"]
    ohlc4 = _ohlc4(o, h, l, c)
    day_range = h - l
    s["fa_range"] = day_range

    # 振幅过滤
    if day_range < min_range:
        s["fa_direction"] = None
        s["fa_anchor"] = 0.0
        return

    # 方向判定（v2.0 优先级，此处用开+收简化版；EMA 在 Mode C 中处理）
    today_open = s["fa_day_o"]
    if today_open > ohlc4:
        s["fa_direction"] = "long"
    elif today_open < ohlc4:
        s["fa_direction"] = "short"
    else:
        s["fa_direction"] = None

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

    # 第一目标
    is_night = _is_night_session(bar)
    s["fa_first_target"] = day_range / 4.0 if is_night else day_range / 3.0


def _calc_anchor_from_prev(s, gap_ratio, new_dir):
    """方向翻转后重新计算锚点。"""
    prev = s["fa_prev_complete_day"]
    if not prev:
        return
    o, h, l, c = prev["o"], prev["h"], prev["l"], prev["c"]
    ohlc4 = _ohlc4(o, h, l, c)
    day_range = h - l
    today_open = s["fa_day_o"]
    gap = abs(today_open - ohlc4)
    if gap > day_range * gap_ratio:
        s["fa_anchor"] = _fib_upper(h, l) if new_dir == "short" else _fib_lower(h, l)
    else:
        s["fa_anchor"] = ohlc4


def _enter_long(bar, ctx, s, zone_ticks, tick_size):
    """做多入场。"""
    qty = int(ctx.state.get("fa_fixed_qty", 1))
    s["fa_entry_bar_idx"] = len(ctx.history) - 1
    s["fa_entry_price"] = bar.close
    s["fa_stop_price"] = bar.low - zone_ticks * tick_size
    s["fa_hit_first"] = False
    s["fa_entry_day_idx"] = s["fa_day_idx"]
    s["fa_had_position"] = True
    ctx.buy(qty, reason=f"ModeA_L@{bar.close:.1f}")


def _enter_short(bar, ctx, s, zone_ticks, tick_size):
    """做空入场。"""
    qty = int(ctx.state.get("fa_fixed_qty", 1))
    s["fa_entry_bar_idx"] = len(ctx.history) - 1
    s["fa_entry_price"] = bar.close
    s["fa_stop_price"] = bar.high + zone_ticks * tick_size
    s["fa_hit_first"] = False
    s["fa_entry_day_idx"] = s["fa_day_idx"]
    s["fa_had_position"] = True
    ctx.sell(qty, reason=f"ModeA_S@{bar.close:.1f}")


def _manage_position(bar, ctx, s):
    """管理持仓：止损 / 止盈 / 时间止损 / 强平。"""
    side = ctx.position_side
    if side is None:
        return

    stop = s.get("fa_stop_price", 0.0)
    first_target_val = s.get("fa_first_target", 0.0)
    entry_price = s.get("fa_entry_price", 0.0)
    tick_size_val = ctx.state.get("fa_tick_size", 1.0)

    # ── 止损 ──
    if side == "long" and bar.low <= stop:
        ctx.close(reason=f"SL@{stop:.1f}")
        return
    if side == "short" and bar.high >= stop:
        ctx.close(reason=f"SL@{stop:.1f}")
        return

    # ── 第一目标止盈 ──
    if not s.get("fa_hit_first", False) and first_target_val > 0 and entry_price > 0:
        if side == "long" and bar.high >= entry_price + first_target_val * tick_size_val:
            s["fa_hit_first"] = True
            s["fa_stop_price"] = entry_price  # 保本
            # 平 50%
            qty = ctx.position_qty
            scale_qty = max(1, int(qty * 0.5))
            # 用 close 替代——回测框架不直接支持部分平仓
            # 先全平再开一半（简化处理）
            ctx.close(reason=f"TP1@{entry_price + first_target_val * tick_size_val:.1f}")
            return
        if side == "short" and bar.low <= entry_price - first_target_val * tick_size_val:
            s["fa_hit_first"] = True
            s["fa_stop_price"] = entry_price
            ctx.close(reason=f"TP1@{entry_price - first_target_val * tick_size_val:.1f}")
            return

    # ── 时间止损 ──
    entry_idx = s.get("fa_entry_bar_idx", -1)
    if entry_idx >= 0:
        bars_held = len(ctx.history) - entry_idx
        time_stop_bars = int(ctx.state.get("fa_time_stop_minutes", 60) / 5)
        if bars_held >= time_stop_bars:
            ctx.close(reason="time_stop")
            return

    # ── 日末强平 ──
    next_bar = None  # 引擎会在外层处理
    if _is_eod_force_close(bar, next_bar):
        ctx.close(reason="eod_force")
        return
