"""Always In 顺势回调策略（方案② 升级版）：补全 docstring 规则 + 信号 K 质量 + TR 过滤。

来源：价格行为交易·新手实战指南 + 市场结构判断01 + Al Brooks 三部曲
- 第三节：5 项 Always In 检查清单（≥4 项确立）→ 简化为「连续 N 根同侧收盘 + 至少 1 根趋势 K」
- 第六节：信号 K 质量分级（趋势 K 实体大、收盘接近极值）
- 第七节：80% 法则 → TR 区间禁入
- 市场结构判断01 第一铁律：AI 翻转立即平仓
- 新手指南第四节：跟随棒反向立即离场，别死等止损
- Brooks《Reading Price Charts》ch10.19：止损 = 信号 K 极值 ± 1 跳

规则总览：
1. AI 方向：连续 N 根收盘同侧 EMA + 期间至少 1 根趋势 K（实体率 ≥ body_ratio_min）
2. 入场前置：非 TR（最近 tr_lookback 根 K 的 range / EMA ≥ tr_range_ratio）
3. 信号 K：当前 K 是同向趋势 K（实体大、收盘接近极值）+ 收盘相对前 K 续势
4. 入场：下一根开盘成交（broker 默认行为）
5. 离场优先级（高 → 低）：
   a) 硬止损：信号 K 极值 ± stop_ticks 跳（持仓后每根 K 检查 high/low 是否击穿）
   b) AI 翻转：方向反转 → 立刻平
   c) 跟进 K 反向：入场 K 之后第一根 K 反向收盘 → 立刻平
   d) 收盘破 EMA（兜底软离场）
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from backtest.context import StrategyContext
from backtest.models import Bar, Side
from backtest.registry import register_strategy


def _ema(values: list[float], span: int) -> float:
    if len(values) < span:
        return float("nan")
    return float(pd.Series(values).ewm(span=span, adjust=False).mean().iloc[-1])


def _trend_bar_side(
    bar: Bar, body_ratio_min: float = 0.5, close_extreme_ratio: float = 0.6
) -> Optional[Side]:
    """识别趋势 K：实体占比 ≥ body_ratio_min 且收盘接近一端极值。"""
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


def _ai_direction(
    history: list[Bar], ema_now: float, confirm_bars: int, body_ratio_min: float
) -> Optional[Side]:
    """5 项清单简化版：连续 N 根同侧收盘 + 期间至少 1 根趋势 K。"""
    if len(history) < confirm_bars:
        return None
    recent = history[-confirm_bars:]
    closes_above = all(b.close > ema_now for b in recent)
    closes_below = all(b.close < ema_now for b in recent)
    if closes_above and any(_trend_bar_side(b, body_ratio_min) == "long" for b in recent):
        return "long"
    if closes_below and any(_trend_bar_side(b, body_ratio_min) == "short" for b in recent):
        return "short"
    return None


def _is_trading_range(
    history: list[Bar], lookback: int, ema_now: float, range_ratio: float
) -> bool:
    """最近 lookback 根 K 的 (max_high - min_low) / EMA < range_ratio → 视为 TR。"""
    if len(history) < lookback or ema_now <= 0:
        return False
    seg = history[-lookback:]
    high = max(b.high for b in seg)
    low = min(b.low for b in seg)
    return (high - low) / ema_now < range_ratio


@register_strategy("always_in_pullback")
def on_bar(
    bar: Bar,
    ctx: StrategyContext,
    *,
    ema_period: int = 20,
    ai_confirm_bars: int = 3,
    stop_ticks: int = 5,
    tick_size: float = 1.0,
    body_ratio_min: float = 0.5,
    tr_lookback: int = 20,
    tr_range_ratio: float = 0.015,
    **_: object,
) -> None:
    closes = ctx.closes
    history = ctx.history
    n = len(closes)
    warmup = max(ema_period + ai_confirm_bars, tr_lookback + 1)
    if n < warmup:
        return

    ema_now = _ema(closes, ema_period)
    ai_dir = _ai_direction(history, ema_now, ai_confirm_bars, body_ratio_min)

    # --- 0) 处理"上根发出 buy/sell，本根开盘已成交"：初始化硬止损与入场索引 ---
    pending = ctx.state.get("pending_entry")
    if ctx.position_side is not None and pending is not None:
        if pending["side"] == "long":
            ctx.state["stop_price"] = pending["signal_low"] - stop_ticks * tick_size
        else:
            ctx.state["stop_price"] = pending["signal_high"] + stop_ticks * tick_size
        ctx.state["entry_bar_index"] = n - 1  # 当前根 = 入场 K
        ctx.state.pop("pending_entry", None)

    # --- 1) 持仓中：按优先级检查离场 ---
    if ctx.position_side is not None:
        side = ctx.position_side
        stop_price = ctx.state.get("stop_price")
        entry_idx = ctx.state.get("entry_bar_index")

        # 1a. 硬止损（最高优先级）
        if stop_price is not None:
            if side == "long" and bar.low <= stop_price:
                ctx.close(reason=f"stop @ {stop_price:.4f}")
                ctx.state["last_ai_dir"] = ai_dir
                return
            if side == "short" and bar.high >= stop_price:
                ctx.close(reason=f"stop @ {stop_price:.4f}")
                ctx.state["last_ai_dir"] = ai_dir
                return

        # 1b. AI 翻转 → 立刻平
        if (side == "long" and ai_dir == "short") or (side == "short" and ai_dir == "long"):
            ctx.close(reason=f"AI flip to {ai_dir}")
            ctx.state["last_ai_dir"] = ai_dir
            return

        # 1c. 跟进 K 反向收盘（仅入场 K 之后的第一根 K）
        if entry_idx is not None and n - 1 == entry_idx + 1:
            if side == "long" and bar.close < bar.open:
                ctx.close(reason="follow-through bear close")
                ctx.state["last_ai_dir"] = ai_dir
                return
            if side == "short" and bar.close > bar.open:
                ctx.close(reason="follow-through bull close")
                ctx.state["last_ai_dir"] = ai_dir
                return

        # 1d. 兜底：收盘破 EMA
        if side == "long" and bar.close < ema_now:
            ctx.close(reason="close below EMA")
        elif side == "short" and bar.close > ema_now:
            ctx.close(reason="close above EMA")

        ctx.state["last_ai_dir"] = ai_dir
        return

    # --- 2) 无持仓：清理上一笔残留状态 ---
    if "stop_price" in ctx.state:
        ctx.state.pop("stop_price", None)
        ctx.state.pop("entry_bar_index", None)

    # --- 3) 入场前置过滤 ---
    if ai_dir is None:
        ctx.state["last_ai_dir"] = ai_dir
        return
    if _is_trading_range(history, tr_lookback, ema_now, tr_range_ratio):
        ctx.state["last_ai_dir"] = ai_dir
        return

    # --- 4) 信号 K 判定（必须是同向趋势 K + 续势收盘） ---
    signal_side = _trend_bar_side(bar, body_ratio_min)
    prev_close = closes[-2]

    if ai_dir == "long" and signal_side == "long" and bar.close > prev_close:
        ctx.state["pending_entry"] = {
            "side": "long",
            "signal_low": bar.low,
            "signal_high": bar.high,
        }
        ctx.buy(1, reason="AI long + trend signal bar")
    elif ai_dir == "short" and signal_side == "short" and bar.close < prev_close:
        ctx.state["pending_entry"] = {
            "side": "short",
            "signal_low": bar.low,
            "signal_high": bar.high,
        }
        ctx.sell(1, reason="AI short + trend signal bar")

    ctx.state["last_ai_dir"] = ai_dir
