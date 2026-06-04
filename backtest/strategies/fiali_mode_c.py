"""菲阿里改良版 v2.0 — Mode C：EMA 回调策略。

基于 EMA（螺纹钢 EMA34 / PVC EMA21）的趋势回调入场：
  - EMA ↑ → 只做多（等回踩 EMA ±2tick 不破）
  - EMA ↓ → 只做空（等反弹 EMA ±2tick 不过）

核心规则：
  1. 用 ema_inc 增量计算 EMA
  2. EMA 方向 = 当前 EMA vs 前一根 EMA
  3. 入场：价格触及 EMA ± 2 tick + 反转 K（实体≥40%）→ 下一根入场
  4. 止损：EMA 反向 3 tick
  5. 时间止损：入场 1h 不达目标 → 平仓
  6. 日末 14:45 / 22:30 强平
  7. 夜盘优先使用 Mode C

────────────────────────────────────────────────────────────────
回测记录 (螺纹钢2610, 5m, 2026-03-01 ~ 2026-06-03)
────────────────────────────────────────────────────────────────
配置: initial_capital=100000, tick_size=1, tick_value=10,
      margin_rate=0.10, fee_per_lot=3, slippage=1, intraday_only=True

结果:
  最终权益:   90,842.00
  总收益率:       -9.16%
  最大回撤:   -9,380.00 (-9.37%)
  总交易数:     313 (胜45 / 负268)
  胜率:          14.38%
  盈亏比:         0.12
  平均盈利:      27.33
  平均亏损:      38.76
  Sharpe:       -53.55

问题分析:
  1. 交易频率过高: 3个月313笔，EMA在震荡市频繁交叉产生大量假信号。
     螺纹钢5分钟级别噪音大，EMA34反应滞后但交叉频繁。
  2. 尾盘入场严重: 大量交易在 22:00-23:00 入场后立即被 eod_force 强平。
     建议: 22:00 之后禁止开仓。
  3. 止损设置不合理: EMA反向3tick止损太紧，正常波动就被扫损。
     建议: 扩大止损至 5-8 tick，或用ATR动态止损。
  4. 时段限制缺失: 策略没有在14:00-15:00和22:00+跳过入场，
     导致频繁在不适合的时段开仓。
  5. EMA周期问题: EMA34在5分钟级别可能过长，建议测试更短周期(EMA13/EMA21)。
"""
from __future__ import annotations

from typing import Optional

from backtest.indicators import ema_inc
from backtest.models import Bar, Side
from backtest.registry import register_strategy


def _bar_time_minutes(t: str) -> int:
    try:
        parts = t.split(" ")[1].split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return 0


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


@register_strategy("fiali_mode_c")
def on_bar(
    bar: Bar,
    ctx,
    *,
    # EMA 参数（由品种决定）
    ema_period: int = 34,
    # 入场
    zone_ticks: int = 2,
    entity_min_ratio: float = 0.4,
    stop_ticks: int = 3,
    # 时间止损（分钟）
    time_stop_minutes: int = 60,
    # 仓位
    fixed_qty: int = 1,
    # tick 规格
    tick_size: float = 1.0,
) -> None:
    s = ctx.state

    # ── 初始化 ──
    if not s.get("fc_init"):
        s["fc_init"] = True
        s["fc_ema_key"] = f"fc_ema_{ema_period}"
        s["fc_entry_bar_idx"] = -1
        s["fc_stop_price"] = 0.0
        s["fc_entry_price"] = 0.0
        s["fc_hit_first"] = False
        s["fc_last_entry_day"] = ""

    # ── 持仓管理 ──
    if ctx.position_side is not None:
        _manage_position(bar, ctx, s, ema_period, stop_ticks, tick_size)
        return

    if ctx.pending_orders:
        return

    # ── 增量 EMA ──
    ema_now = ema_inc(s, s["fc_ema_key"], bar.close, ema_period)
    ema_prev = s.get("fc_ema_prev", ema_now)
    s["fc_ema_prev"] = ema_now

    # EMA 方向
    if ema_now > ema_prev:
        ema_dir: Optional[Side] = "long"
    elif ema_now < ema_prev:
        ema_dir = "short"
    else:
        return  # 无方向，不交易

    # ── 入场检测 ──
    zone_lo = ema_now - zone_ticks * tick_size
    zone_hi = ema_now + zone_ticks * tick_size

    if ema_dir == "long":
        # 做多：等回踩 EMA 不破
        if bar.low <= zone_hi and bar.high >= zone_lo:
            if bar.close > ema_now and bar.close > bar.open and _entity_ratio(bar) >= entity_min_ratio:
                s["fc_entry_bar_idx"] = len(ctx.history) - 1
                s["fc_entry_price"] = bar.close
                s["fc_stop_price"] = ema_now - stop_ticks * tick_size
                s["fc_hit_first"] = False
                s["fc_last_entry_day"] = bar.time.split(" ")[0]
                ctx.buy(fixed_qty, reason=f"ModeC_L@{bar.close:.1f}")

    elif ema_dir == "short":
        # 做空：等反弹 EMA 不过
        if bar.high >= zone_lo and bar.low <= zone_hi:
            if bar.close < ema_now and bar.close < bar.open and _entity_ratio(bar) >= entity_min_ratio:
                s["fc_entry_bar_idx"] = len(ctx.history) - 1
                s["fc_entry_price"] = bar.close
                s["fc_stop_price"] = ema_now + stop_ticks * tick_size
                s["fc_hit_first"] = False
                s["fc_last_entry_day"] = bar.time.split(" ")[0]
                ctx.sell(fixed_qty, reason=f"ModeC_S@{bar.close:.1f}")

    # ── 14:00/22:00 后不开新仓 ──
    t_now = _bar_time_minutes(bar.time)
    if (14 * 60 <= t_now < 15 * 60) or t_now >= 22 * 60:
        return


def _manage_position(bar, ctx, s, ema_period, stop_ticks, tick_size):
    """管理持仓。"""
    side = ctx.position_side
    stop = s.get("fc_stop_price", 0.0)
    ema_now = s.get(s["fc_ema_key"], 0.0)

    # ── 止损 ──
    if side == "long" and bar.low <= stop:
        ctx.close(reason=f"SL@{stop:.1f}")
        return
    if side == "short" and bar.high >= stop:
        ctx.close(reason=f"SL@{stop:.1f}")
        return

    # ── 第一目标触及 → 保本 — 简化处理（无固定目标，EMA 穿越平仓）─
    # ── EMA 反向穿越 → 平仓 ──
    if side == "long" and bar.close < ema_now:
        ctx.close(reason=f"EMA_cross@{ema_now:.1f}")
        return
    if side == "short" and bar.close > ema_now:
        ctx.close(reason=f"EMA_cross@{ema_now:.1f}")
        return

    # ── 时间止损 ──
    entry_idx = s.get("fc_entry_bar_idx", -1)
    if entry_idx >= 0:
        bars_held = len(ctx.history) - entry_idx
        time_stop_bars = int(s.get("fc_time_stop_minutes", 60))
        time_stop_bars = max(1, time_stop_bars // 5)
        if bars_held >= time_stop_bars:
            ctx.close(reason="time_stop")
            return

    # ── 日末强平 ──
    if _is_eod_force_close(bar):
        ctx.close(reason="eod_force")
        return
