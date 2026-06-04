"""Donchian 日线突破策略（海龟法则变体）。

核心思想：价格创 N 日新高 = 方向确认，顺势入场。
适用于 A 股日线级别趋势跟踪，天然适配 T+1 规则。

规则：
  入场（全部满足）:
    1. bar.close > 最近 entry_period 日最高价（Donchian 上轨突破）
    2. bar.volume > vol_ratio × 最近 vol_lookback 日均量（放量确认）
    3. bar.close > EMA(trend_ema)（中期趋势向上）

  出场（任一触发）:
    1. bar.close < 最近 exit_period 日最低价（Donchian 下轨 = 趋势反转）
    2. bar.close < 入场后最高价 - atr_stop_mult × ATR（移动止损）

────────────────────────────────────────────────────────────────
回测记录 (30只沪深主板, 日线, 2024-01-01 ~ 2026-06-04)
────────────────────────────────────────────────────────────────
配置: initial_capital=100000, entry_period=20, exit_period=10,
      atr_stop_mult=2.0, vol_ratio=1.5, trend_ema=50,
      commission_rate=0.025%, stamp_tax=0.1%, lot_size=100

汇总:
  有交易:   30/30
  盈利:     12 只 (40%)
  平均收益:     -0.00%     ← 基本打平
  平均胜率:      32.1%     ← 突破策略正常范围
  平均盈亏比(PF): 1.05     ← 勉强及格
  平均持仓:     9.6 天

Top 5:
  SH.601606  +1.94%  PF=1.98  33.3%胜率  12笔
  SH.603179  +1.90%  PF=1.66  42.9%胜率   7笔
  SZ.001696  +0.87%  PF=3.04  50.0%胜率  12笔
  SH.600611  +0.49%  PF=4.07  33.3%胜率   6笔  ← 最高PF
  SH.600176  +0.49%  PF=2.49  54.5%胜率  11笔

Bottom 5:
  SH.603192  -0.98%  PF=0.37  18.2%胜率  11笔
  SZ.001872  -0.81%  PF=0.11  22.2%胜率   9笔
  SH.600571  -0.75%  PF=0.27  30.0%胜率  10笔
  SH.603027  -0.75%  PF=0.02  11.1%胜率   9笔
  SH.600829  -0.69%  PF=0.05  27.3%胜率  11笔

分析:
  1. 品种差异巨大：SH.600611(PF=4.07) vs SH.603027(PF=0.02)，
     同参数在不同股票上表现天差地别——品种选择 > 参数调优
  2. 3只PF>3的股票说明策略逻辑不是废的，只是需要筛选
  3. 3只胜率0%的股票(SZ.000557/SZ.000017/SH.600853)全是假突破
  4. ~10天持仓周期合理，无过度交易问题

下一步:
  - 品种筛选：只做历史PF>1.5的股票
  - ATR过滤：排除波动率过低的僵尸股
  - 风险定仓：按2%风险+ATR算仓位
  - 多品种组合：同时持3-5只分散假突破
"""
from __future__ import annotations

from backtest.indicators import atr, ema_inc
from backtest.models import Bar
from backtest.registry import register_strategy


def _donchian_high(history: list[Bar], period: int) -> float:
    """最近 period 根 bar 的最高价（不含当前 bar）。"""
    if len(history) < period + 1:
        return float("inf")  # 数据不足，返回 inf 使条件不满足
    return max(b.high for b in history[-period - 1:-1])


def _donchian_low(history: list[Bar], period: int) -> float:
    """最近 period 根 bar 的最低价（不含当前 bar）。"""
    if len(history) < period + 1:
        return 0.0  # 数据不足，返回 0 使条件不满足
    return min(b.low for b in history[-period - 1:-1])


def _avg_volume(history: list[Bar], n: int) -> float:
    """最近 n 根 bar 的均量（不含当前 bar）。"""
    if len(history) < n + 1:
        return 0.0
    return sum(b.volume for b in history[-n - 1:-1]) / n


@register_strategy("donchian")
def on_bar(
    bar: Bar,
    ctx,
    *,
    # Donchian 通道参数
    entry_period: int = 20,
    exit_period: int = 10,
    # ATR 移动止损
    atr_period: int = 20,
    atr_stop_mult: float = 2.0,
    # 成交量过滤
    vol_ratio: float = 1.5,
    vol_lookback: int = 20,
    # 趋势过滤
    trend_ema: int = 50,
    # 仓位
    fixed_qty: int = 1,
) -> None:
    """Donchian 突破策略 — on_bar 回调。"""
    s = ctx.state

    # ── 初始化 ──
    if not s.get("dc_init"):
        s["dc_init"] = True
        s["dc_ema_key"] = f"dc_ema_{trend_ema}"
        s["dc_highest"] = 0.0       # 入场后最高收盘价
        s["dc_entry_atr"] = 0.0     # 入场时的 ATR 值

    # ── 持仓管理 ──
    if ctx.position_side == "long":
        _manage_long(bar, ctx, s, exit_period, atr_period, atr_stop_mult)
        return

    if ctx.position_side is not None:
        return  # 空头持仓（理论上 A 股不会出现）

    if ctx.pending_orders:
        return

    # ── 数据不足则跳过 ──
    min_bars = max(entry_period, trend_ema) + 1
    if len(ctx.history) < min_bars:
        return

    # ── EMA 趋势过滤 ──
    ema_val = ema_inc(s, s["dc_ema_key"], bar.close, trend_ema)
    if bar.close < ema_val:
        return

    # ── Donchian 上轨突破 ──
    dc_high = _donchian_high(ctx.history, entry_period)
    if bar.close <= dc_high:
        return

    # ── 成交量确认 ──
    avg_vol = _avg_volume(ctx.history, vol_lookback)
    if avg_vol > 0 and bar.volume <= vol_ratio * avg_vol:
        return

    # ── 入场 ──
    atr_val = atr(ctx.history, atr_period)
    s["dc_highest"] = bar.close
    s["dc_entry_atr"] = atr_val if atr_val == atr_val else 0.0  # NaN check

    ctx.buy(fixed_qty, reason=f"DC_L@{bar.close:.2f}>")


def _manage_long(
    bar: Bar,
    ctx,
    s: dict,
    exit_period: int,
    atr_period: int,
    atr_stop_mult: float,
) -> None:
    """管理多头持仓：Donchian 下轨出场 / ATR 移动止损。"""
    # ── 更新最高价 ──
    if bar.close > s["dc_highest"]:
        s["dc_highest"] = bar.close

    # ── Donchian 下轨出场（趋势反转） ──
    dc_low = _donchian_low(ctx.history, exit_period)
    if bar.close < dc_low:
        ctx.close(reason=f"DC_exit@{dc_low:.2f}")
        return

    # ── ATR 移动止损 ──
    atr_val = s.get("dc_entry_atr", 0.0)
    if atr_val > 0:
        stop_price = s["dc_highest"] - atr_stop_mult * atr_val
        if bar.close < stop_price:
            ctx.close(reason=f"ATR_SL@{stop_price:.2f}")
