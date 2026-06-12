"""趋势日策略 — 9:05 首根 K 定方向，顺势追踪入场 + 2R 移动止损。

策略逻辑（来自 docs/superpowers/specs/2026-06-12-trend-day-strategy-design.md）：
  1. 9:05 首根 K 确认方向：阳线+实体>60% → 做多；阴线+实体>60% → 做空
  2. 顺势突破入场：挂 Stop 单于 signal bar 的 high/low
  3. 可选回调入场：价格回撤 0.5 × bar_range 时 Limit 入场
  4. 初始止损：signal_bar 的另一端 ± 2 tick
  5. 2R 移动止损：价格移动 1R → 止损移到入场价；移动 2R → 止损移到入场价 + 1R
  6. 14:50 尾盘强平

品种：RB（螺纹钢）、PVC
周期：5 分钟
"""
from __future__ import annotations

from typing import Optional

from backtest.models import Bar, Side
from backtest.registry import register_strategy


# ── 工具函数 ──────────────────────────────────────────────────

def _bar_time_minutes(t: str) -> int:
    """从 bar.time 字符串提取分钟数。"""
    try:
        parts = t.split(" ")[1].split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return 0


def _is_new_trading_day(bar: Bar, prev_bar: Optional[Bar]) -> bool:
    """判断是否进入新交易日。"""
    if prev_bar is None:
        return True
    prev_t = _bar_time_minutes(prev_bar.time)
    cur_t = _bar_time_minutes(bar.time)
    # 午后 → 夜盘切换
    if prev_t >= 15 * 60 and prev_t < 21 * 60 and cur_t >= 21 * 60:
        return True
    # 日期变化（非夜盘内）
    prev_date = prev_bar.time.split(" ")[0]
    cur_date = bar.time.split(" ")[0]
    if cur_date != prev_date and cur_t < 21 * 60:
        return True
    return False


def _entity_ratio(bar: Bar) -> float:
    """K 线实体占比。"""
    rng = bar.high - bar.low
    if rng <= 0:
        return 0.0
    return abs(bar.close - bar.open) / rng


# ── 策略入口 ──────────────────────────────────────────────────

@register_strategy("trend_day")
def on_bar(
    bar: Bar,
    ctx,
    *,
    entity_min_ratio: float = 0.6,
    pullback_ratio: float = 0.5,
    initial_sl_ticks: int = 2,
    enable_pullback: bool = True,
    enable_trailing: bool = True,
    fixed_qty: int = 1,
    eod_minutes: int = 14 * 60 + 50,
    tick_size: float = 1.0,
    tick_value: float = 10.0,
) -> None:
    s = ctx.state

    # ── 初始化 ──
    if not s.get("td_init"):
        s["td_init"] = True
        s["td_signal_bar_h"] = 0.0
        s["td_signal_bar_l"] = 0.0
        s["td_direction"] = None       # "long" or "short"
        s["td_entry_price"] = 0.0
        s["td_initial_stop"] = 0.0
        s["td_1r"] = 0.0               # 1R 距离
        s["td_breakout_fired"] = False  # 突破入场已触发
        s["td_pullback_fired"] = False  # 回调入场已触发
        s["td_trailing_active"] = False # 移动止损激活
        s["td_highest_since_entry"] = 0.0
        s["td_lowest_since_entry"] = float("inf")
        s["td_eod_closed"] = False      # 当日已尾盘平仓

    # ── 持仓管理（有仓位时） ──
    if ctx.position_side is not None:
        _manage_position(bar, ctx, s, tick_size, enable_trailing, eod_minutes)
        return

    # ── 挂单中 → 跳过 ──
    if ctx.pending_orders:
        return

    # ── 交易日切换 ──
    prev_bar = ctx.history[-2] if len(ctx.history) >= 2 else None
    if _is_new_trading_day(bar, prev_bar):
        s["td_signal_bar_h"] = 0.0
        s["td_signal_bar_l"] = 0.0
        s["td_direction"] = None
        s["td_entry_price"] = 0.0
        s["td_initial_stop"] = 0.0
        s["td_1r"] = 0.0
        s["td_breakout_fired"] = False
        s["td_pullback_fired"] = False
        s["td_trailing_active"] = False
        s["td_highest_since_entry"] = 0.0
        s["td_lowest_since_entry"] = float("inf")
        s["td_eod_closed"] = False

    t_now = _bar_time_minutes(bar.time)

    # ── 9:05 K 线收盘确认方向 ──
    # 9:05 对应 9*60+5=545，即 bar.time 的分钟数 == 545
    if t_now == 9 * 60 + 5 and s["td_direction"] is None:
        if _entity_ratio(bar) >= entity_min_ratio:
            if bar.close > bar.open:
                s["td_direction"] = "long"
                s["td_signal_bar_h"] = bar.high
                s["td_signal_bar_l"] = bar.low
            elif bar.close < bar.open:
                s["td_direction"] = "short"
                s["td_signal_bar_h"] = bar.high
                s["td_signal_bar_l"] = bar.low

    # ── 无方向 → 跳过 ──
    if s["td_direction"] is None:
        return

    # ── 已尾盘平仓 → 跳过 ──
    if s["td_eod_closed"]:
        return

    direction: Side = s["td_direction"]
    sig_h = s["td_signal_bar_h"]
    sig_l = s["td_signal_bar_l"]

    # ── 入场检测（9:06 之后） ──
    if t_now <= 9 * 60 + 5:
        return

    # ── 尾盘停止入场 ──
    if t_now >= eod_minutes - 5:
        return

    entry_range = sig_h - sig_l
    if entry_range <= 0:
        return

    # ── 突破入场（Stop 单） ──
    if not s["td_breakout_fired"]:
        if direction == "long":
            stop_price = sig_h
            if bar.high >= stop_price:
                # 突破触发
                initial_sl = sig_l - initial_sl_ticks * tick_size
                s["td_entry_price"] = stop_price
                s["td_initial_stop"] = initial_sl
                s["td_1r"] = stop_price - initial_sl
                s["td_breakout_fired"] = True
                s["td_highest_since_entry"] = bar.high
                ctx.buy(fixed_qty, reason=f"BO_L@{stop_price:.1f}")
                return
        elif direction == "short":
            stop_price = sig_l
            if bar.low <= stop_price:
                initial_sl = sig_h + initial_sl_ticks * tick_size
                s["td_entry_price"] = stop_price
                s["td_initial_stop"] = initial_sl
                s["td_1r"] = initial_sl - stop_price
                s["td_breakout_fired"] = True
                s["td_lowest_since_entry"] = bar.low
                ctx.sell(fixed_qty, reason=f"BO_S@{stop_price:.1f}")
                return

    # ── 回调入场（Limit 单，手动检测） ──
    if enable_pullback and not s["td_pullback_fired"] and s["td_breakout_fired"]:
        entry_price = s["td_entry_price"]
        pullback_price = entry_price - pullback_ratio * entry_range if direction == "long" \
            else entry_price + pullback_ratio * entry_range

        if direction == "long" and bar.low <= pullback_price:
            # 回调到位，按 pullback_price 入场
            initial_sl = sig_l - initial_sl_ticks * tick_size
            # 更新入场价（取两次入场的均价？还是独立？这里用独立止损）
            s["td_pullback_fired"] = True
            s["td_highest_since_entry"] = max(s["td_highest_since_entry"], bar.high)
            ctx.buy(fixed_qty, reason=f"PB_L@{pullback_price:.1f}")
            return
        elif direction == "short" and bar.high >= pullback_price:
            initial_sl = sig_h + initial_sl_ticks * tick_size
            s["td_pullback_fired"] = True
            s["td_lowest_since_entry"] = min(s["td_lowest_since_entry"], bar.low)
            ctx.sell(fixed_qty, reason=f"PB_S@{pullback_price:.1f}")
            return


# ── 持仓管理 ──────────────────────────────────────────────────

def _manage_position(
    bar: Bar,
    ctx,
    s: dict,
    tick_size: float,
    enable_trailing: bool,
    eod_minutes: int,
) -> None:
    """管理持仓：止损 / 移动止损 / 尾盘强平。"""
    side = ctx.position_side
    if side is None:
        return

    entry_price = s.get("td_entry_price", 0.0)
    initial_stop = s.get("td_initial_stop", 0.0)
    r1 = s.get("td_1r", 0.0)
    t_now = _bar_time_minutes(bar.time)

    # ── 更新极值 ──
    if side == "long":
        s["td_highest_since_entry"] = max(s.get("td_highest_since_entry", 0.0), bar.high)
    elif side == "short":
        s["td_lowest_since_entry"] = min(s.get("td_lowest_since_entry", float("inf")), bar.low)

    # ── 初始止损 ──
    current_stop = initial_stop

    # ── 2R 移动止损 ──
    if enable_trailing and r1 > 0:
        if side == "long":
            highest = s.get("td_highest_since_entry", bar.high)
            move_r = (highest - entry_price) / r1  # 已移动了几 R
            if move_r >= 2.0:
                # 2R → 止损移到 entry + 1R
                current_stop = entry_price + r1
                s["td_trailing_active"] = True
            elif move_r >= 1.0:
                # 1R → 止损移到 entry
                current_stop = entry_price
                s["td_trailing_active"] = True
        elif side == "short":
            lowest = s.get("td_lowest_since_entry", bar.low)
            move_r = (entry_price - lowest) / r1
            if move_r >= 2.0:
                current_stop = entry_price - r1
                s["td_trailing_active"] = True
            elif move_r >= 1.0:
                current_stop = entry_price
                s["td_trailing_active"] = True

    # ── 止损触发 ──
    if side == "long" and bar.low <= current_stop:
        ctx.close(reason=f"SL@{current_stop:.1f}")
        return
    if side == "short" and bar.high >= current_stop:
        ctx.close(reason=f"SL@{current_stop:.1f}")
        return

    # ── 尾盘强平 ──
    if t_now >= eod_minutes:
        ctx.close(reason="eod")
        s["td_eod_closed"] = True
        return
