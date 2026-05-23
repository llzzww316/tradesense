"""A 股趋势波段策略 v3 — 双周期：日线定方向，60min 找入场

核心理念（来自价格行为手册）：
- 日线级别定 Always In 方向（5 项清单评分）
- 60min 级别在日线多头方向下找 H2/EMA 反弹入场
- 手册（1）（5）（8）（12）用于日线方向判断
- 手册（2）（10）用于 60min 入场

双周期实现：
  在同一策略实例内，将收到的 60min bar 实时合成日 K。
  在合成的日 K 上计算 EMA 和 AI 方向。
  只有在日线方向为多头时，才允许在 60min 上找入场信号。

出场逻辑（所有级别通用）：
  a) 硬止损（摆动低点 + ATR buffer）
  b) 日线 AI 方向丢失 → 平仓
  c) 跟进 K 失败
  d) 1R 保本 → 追踪止损
  e) 三次推动衰竭

A 股适配：
  Long only、T+1 兼容（Swing 持仓）、日线为主 60min 入场
"""
from __future__ import annotations

from typing import Optional

from backtest.context import StrategyContext
from backtest.indicators import (
    atr, confirm_swing_low, ema_inc, is_bear_bar, is_bull_bar, trend_bar_side,
)
from backtest.models import Bar, Side
from backtest.registry import register_strategy


# =========== 合成日线 ===========

def _daily_date(bar: Bar) -> str:
    """从 bar.time 提取日期字符串。"""
    return bar.time.split(" ")[0]


def _update_daily_from_60m(
    bar: Bar, daily_bars: list[Bar],
) -> None:
    """将一根 60min bar 聚合到合成日线中。

    如果 bar 的日期与最后一根日 K 相同 → 更新（high/low/close）
    如果不同 → 新建一根日 K
    """
    cur_date = _daily_date(bar)
    if daily_bars:
        last_date = _daily_date(daily_bars[-1])
        if cur_date == last_date:
            last = daily_bars[-1]
            daily_bars[-1] = Bar(
                time=last.time,
                open=last.open,
                high=max(last.high, bar.high),
                low=min(last.low, bar.low),
                close=bar.close,
                volume=last.volume + bar.volume,
            )
            return
    daily_bars.append(Bar(
        time=bar.time, open=bar.open, high=bar.high,
        low=bar.low, close=bar.close, volume=bar.volume,
    ))


# =========== 日线 AI 方向判定（5 项清单） ===========

def _daily_ai_direction(
    daily_bars: list[Bar],
    ema_now: float,
    lookback: int = 5,
    body_ratio_min: float = 0.5,
    close_extreme_ratio: float = 0.6,
    min_score: int = 3,
) -> tuple[Optional[Side], str, int]:
    """在合成日线上判断 AI 方向。"""
    if len(daily_bars) < lookback or ema_now != ema_now:
        return None, "none", 0

    recent = daily_bars[-lookback:]
    best_dir: Optional[Side] = None
    best_score = 0
    scores: dict[Side, int] = {}

    for direction in ("long", "short"):
        score = 0

        has_trend = any(
            trend_bar_side(b, body_ratio_min, close_extreme_ratio) == direction
            for b in recent
        )
        if has_trend:
            score += 1

        if direction == "long":
            same_dir = sum(1 for b in recent if b.close > b.open)
        else:
            same_dir = sum(1 for b in recent if b.close < b.open)
        if same_dir / lookback >= 0.6:
            score += 1

        if direction == "long":
            above_ema = sum(1 for b in recent if b.close > ema_now)
        else:
            above_ema = sum(1 for b in recent if b.close < ema_now)
        if above_ema / lookback >= 0.6:
            score += 1

        window = 20
        if len(daily_bars) >= window + lookback:
            seg = daily_bars[-(window + lookback): -lookback]
            if seg:
                if direction == "long" and recent[-1].close >= max(b.high for b in seg):
                    score += 1
                elif direction == "short" and recent[-1].close <= min(b.low for b in seg):
                    score += 1

        opposite = "short" if direction == "long" else "long"
        max_consec = 0
        cur = 0
        for b in recent:
            if trend_bar_side(b, body_ratio_min, close_extreme_ratio) == opposite:
                cur += 1
                max_consec = max(max_consec, cur)
            else:
                cur = 0
        if max_consec < 2:
            score += 1

        scores[direction] = score
        if score > best_score:
            best_score = score
            best_dir = direction

    if best_score < min_score:
        return None, "none", 0

    other = "short" if best_dir == "long" else "long"
    if best_score - scores.get(other, 0) <= 0:
        return None, "none", 0

    strength = "strong" if best_score >= 4 else "weak"
    return best_dir, strength, best_score


# =========== 60min H2 回调入场 ===========

_PB_NONE = "none"
_PB_STARTED = "started"
_PB_H1 = "h1_seen"


def _detect_h2_60m(
    bar: Bar, prev_bar: Bar, state: dict,
) -> bool:
    """60min 级别 H2 二次入场检测。"""
    pb_state = state.get("pb_state", _PB_NONE)
    if pb_state == _PB_NONE:
        if is_bear_bar(bar) or bar.close < prev_bar.close:
            state["pb_state"] = _PB_STARTED
            state["pb_low"] = bar.low
    elif pb_state == _PB_STARTED:
        state["pb_low"] = min(state.get("pb_low", bar.low), bar.low)
        if is_bull_bar(bar) and bar.high > prev_bar.high:
            state["pb_state"] = _PB_H1
            state["h1_idx"] = state.get("_n", 0)
    elif pb_state == _PB_H1:
        state["pb_low"] = min(state.get("pb_low", bar.low), bar.low)
        h1_idx = state.get("h1_idx", 0)
        cur_idx = state.get("_n", 0)
        if cur_idx > h1_idx:
            if is_bull_bar(bar) and bar.high > prev_bar.high:
                state["pb_state"] = _PB_NONE
                return True
    return False


# =========== 60min EMA 反弹 ===========

def _ema_bounce_60m(history: list[Bar], ema_now: float, atr_val: float) -> bool:
    """60min 级别均线反弹。"""
    if len(history) < 5:
        return False
    prev = history[-2]
    curr = history[-1]
    near_ema = prev.close <= ema_now * 1.015
    bouncing = is_bull_bar(curr)
    shallow = (ema_now - min(prev.low, curr.low)) < 2 * atr_val
    return near_ema and bouncing and shallow


# =========== 摆动点检测（追踪止损）—— 使用 indicators.confirm_swing_low ===========


# =========== 三次推动衰竭 ===========

def _three_pushes_exhaustion(history: list[Bar]) -> bool:
    n = len(history)
    if n < 20:
        return False
    swings = []
    for i in range(max(0, n - 30), n):
        left = max(0, i - 2)
        right = min(n - 1, i + 2)
        if all(history[i].high >= history[j].high for j in range(left, right + 1) if j != i):
            swings.append(history[i].high)
    if len(swings) < 3:
        return False
    _, h1, h2, h3 = swings[-4], swings[-3], swings[-2], swings[-1]
    leg1 = h2 - h1
    leg2 = h3 - h2
    if leg1 <= 0 or leg2 <= 0 or leg2 >= leg1:
        return False
    return True


# =========== 涨跌停邻近检测 ===========

def _near_price_limit(history: list[Bar]) -> bool:
    if len(history) < 2:
        return False
    today = history[-1]
    prev_close = history[-2].close
    if prev_close <= 0:
        return False
    return (today.close - prev_close) / prev_close >= 0.07


# =========== 主策略 ===========

@register_strategy("a_share_trend")
def on_bar(
    bar: Bar,
    ctx: StrategyContext,
    *,
    # 日线方向参数
    daily_ema_period: int = 20,
    daily_ai_lookback: int = 5,
    daily_ai_min_score: int = 3,
    # 60min 入场参数
    ema_period: int = 20,
    atr_period: int = 14,
    atr_stop_mult: float = 2.0,
    breakeven_at_r: float = 1.0,
    three_pushes_exit: bool = True,
    tick_size: float = 0.01,
    **_: object,
) -> None:
    closes = ctx.closes
    history = ctx.history
    n = len(closes)

    # 60min 预热（至少 20 根才有意义）
    if n < 20:
        return

    # ── 1) 合成日线 ──────────────────────────────────────────────
    daily_bars: list[Bar] = ctx.state.get("daily_bars", [])
    prev_daily_count = len(daily_bars)
    _update_daily_from_60m(bar, daily_bars)
    ctx.state["daily_bars"] = daily_bars

    # 日线 EMA：仅在新建日 K 时喂入上一根的最终 close（避免同一天多次更新）
    if len(daily_bars) > prev_daily_count and len(daily_bars) >= 2:
        daily_ema = ema_inc(ctx.state, "daily_ema", daily_bars[-2].close, daily_ema_period)
    elif ctx.state.get("daily_ema") is not None:
        daily_ema = ctx.state["daily_ema"]
    else:
        daily_ema = float("nan")
    # 日线预热（至少 30 根合成日 K 才有足够数据判 AI）
    if len(daily_bars) < 30:
        ctx.state["prev_daily_ai"] = None
        return

    daily_ai_dir, daily_ai_str, daily_ai_score = _daily_ai_direction(
        daily_bars, daily_ema, daily_ai_lookback,
    )
    prev_daily_ai = ctx.state.get("prev_daily_ai")
    cur_bar_date = _daily_date(bar)

    # ── 2) 60min 级别计算 ────────────────────────────────────────
    ema_now = ema_inc(ctx.state, "ema_60m", bar.close, ema_period)
    atr_val = atr(history, atr_period)
    if atr_val != atr_val or atr_val <= 0:
        atr_val = tick_size * 50

    # ── 3) 处理"上根下单、本根开盘已成交" ────────────────────────
    pending = ctx.state.get("pending_entry")
    if ctx.position_side is not None and pending is not None:
        side = pending["side"]
        stop_price = pending["stop"]
        ctx.state["stop_price"] = stop_price
        ctx.state["entry_price"] = bar.open
        ctx.state["risk"] = abs(bar.open - stop_price)
        ctx.state["entry_bar_index"] = n - 1
        ctx.state["breakeven_done"] = False
        ctx.state.pop("pending_entry", None)

    # ── 4) 持仓中：离场 ──────────────────────────────────────────
    if ctx.position_side is not None:
        stop_price = ctx.state.get("stop_price")
        entry_price = ctx.state.get("entry_price")
        risk = ctx.state.get("risk", 1.0)
        breakeven_done = ctx.state.get("breakeven_done", False)

        # 4a. 硬止损
        if stop_price is not None and bar.low <= stop_price:
            ctx.close(reason=f"stop @ {stop_price:.4f}")
            ctx.state["prev_daily_ai"] = daily_ai_dir
            return

        # 4b. 日线 AI 方向丢失或翻转 → 平仓（这是双周期的核心）
        if daily_ai_dir != "long":
            ctx.close(reason=f"daily AI lost ({daily_ai_dir or 'none'})")
            ctx.state["prev_daily_ai"] = daily_ai_dir
            return

        # 4c. 跟进 K 失败
        if not ctx.state.get("follow_checked") and entry_price is not None:
            entry_idx = ctx.state.get("entry_bar_index")
            if entry_idx is not None and n - 1 == entry_idx + 1:
                ctx.state["follow_checked"] = True
                if trend_bar_side(bar) == "short":
                    ctx.close(reason="follow-through bear")
                    ctx.state["prev_daily_ai"] = daily_ai_dir
                    return

        # 4d. 1R 保本
        if not breakeven_done and entry_price is not None and risk > 0:
            profit_r = (bar.close - entry_price) / risk
            if profit_r >= breakeven_at_r:
                ctx.state["stop_price"] = entry_price
                ctx.state["breakeven_done"] = True

        # 4e. 追踪止损
        if breakeven_done and stop_price is not None:
            sl = confirm_swing_low(history)
            if sl is not None:
                ns = sl - atr_stop_mult * atr_val
                if ns > ctx.state["stop_price"]:
                    ctx.state["stop_price"] = ns

        # 4f. 三次推动衰竭出场
        if three_pushes_exit and breakeven_done:
            if _three_pushes_exhaustion(history):
                ctx.close(reason="three pushes exhaustion")
                ctx.state["prev_daily_ai"] = daily_ai_dir
                return

        ctx.state["prev_daily_ai"] = daily_ai_dir
        return

    # ── 5) 无持仓：入场 ──────────────────────────────────────────
    for k in ("stop_price", "entry_price", "risk", "entry_bar_index",
              "follow_checked", "breakeven_done"):
        ctx.state.pop(k, None)

    # 日线方向变化时重置回调状态
    if daily_ai_dir != prev_daily_ai:
        ctx.state.pop("pb_state", None)
        ctx.state.pop("pb_low", None)
        ctx.state.pop("h1_idx", None)

    # 核心过滤：日线方向必须为多头
    if daily_ai_dir != "long":
        ctx.state["prev_daily_ai"] = daily_ai_dir
        return

    # 额外过滤：涨停附近不追
    if _near_price_limit(history):
        ctx.state["prev_daily_ai"] = daily_ai_dir
        return

    # 日线强 AI 才入场（weak AI 不碰）
    if daily_ai_str != "strong":
        ctx.state["prev_daily_ai"] = daily_ai_dir
        return

    # ── 6) 60min 入场信号 ────────────────────────────────────────
    prev_bar = history[-2]
    ctx.state["_n"] = n

    # 6a. H2 二次入场
    ctx.state["pb_low"] = ctx.state.get("pb_low", bar.low)
    if _detect_h2_60m(bar, prev_bar, ctx.state):
        pb_low = ctx.state.get("pb_low", bar.low)
        stop = pb_low - atr_stop_mult * atr_val
        ctx.state["pending_entry"] = {"side": "long", "stop": stop}
        ctx.buy(1, reason="H2 60m")
        return

    # 6b. EMA 反弹入场
    if _ema_bounce_60m(history, ema_now, atr_val):
        idx_20 = max(0, n - 20)
        low_20 = min(b.low for b in history[idx_20:n])
        stop = low_20 - atr_stop_mult * atr_val
        ctx.state["pending_entry"] = {"side": "long", "stop": stop}
        ctx.buy(1, reason="ema bounce 60m")
        return

    ctx.state["prev_daily_ai"] = daily_ai_dir
