"""股票趋势跟踪策略（Always In + 强度分级入场）

核心理念（来自价格行为手册）：
- 强 AI（评分 ≥ 4）：多根大实体连续收极值 → 直接入场，不等回调
- 弱 AI（评分 = 3）：跟进 K 弱 → 等价格回踩均线确认后入场
- AI 强翻转（评分 ≥ 4）→ 平仓；弱翻转（评分 = 3）→ 继续持有

适用：A 股日线趋势跟踪，long_only。默认开启趋势过滤（trend_filter_ema=60），避开长期下跌
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from backtest.context import StrategyContext
from backtest.models import Bar, Side
from backtest.registry import register_strategy


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _ema(values: list[float], span: int) -> float:
    if len(values) < span:
        return float("nan")
    return float(pd.Series(values).ewm(span=span, adjust=False).mean().iloc[-1])


def _atr(bars: list[Bar], period: int) -> float:
    """Average True Range，用于动态止损。"""
    n = len(bars)
    if n < period + 1:
        return float("nan")
    tr_values = []
    for i in range(1, n):
        hl = bars[i].high - bars[i].low
        hc = abs(bars[i].high - bars[i - 1].close)
        lc = abs(bars[i].low - bars[i - 1].close)
        tr_values.append(max(hl, hc, lc))
    if len(tr_values) < period:
        return float("nan")
    return float(pd.Series(tr_values[-period:]).mean())


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


def _is_bull_bar(bar: Bar) -> bool:
    return bar.close > bar.open


def _is_bear_bar(bar: Bar) -> bool:
    return bar.close < bar.open


# ---------------------------------------------------------------------------
# AI 方向判定：5 项清单评分
# ---------------------------------------------------------------------------

def _ai_direction(
    history: list[Bar],
    ema_now: float,
    lookback: int = 5,
    body_ratio_min: float = 0.5,
    close_extreme_ratio: float = 0.6,
    min_score: int = 3,
) -> tuple[Optional[Side], str, int]:
    """5 项清单评分，返回 (direction, strength, score)。

    strength: "strong" (≥4 项) | "weak" (3 项) | "none" (<3 项)
    """
    n = len(history)
    if n < lookback or ema_now != ema_now:
        return None, "none", 0

    recent = history[-lookback:]

    best_dir: Optional[Side] = None
    best_score = 0
    scores: dict[Side, int] = {}

    for direction in ("long", "short"):
        score = 0

        # 1. 有大实体趋势 K
        has_trend = any(
            _trend_bar_side(b, body_ratio_min, close_extreme_ratio) == direction
            for b in recent
        )
        if has_trend:
            score += 1

        # 2. 跟进 K 同向（收盘方向一致的占比 ≥ 60%）
        if direction == "long":
            same_dir = sum(1 for b in recent if b.close > b.open)
        else:
            same_dir = sum(1 for b in recent if b.close < b.open)
        if same_dir / lookback >= 0.6:
            score += 1

        # 3. 收盘在 EMA 一侧（占比 ≥ 60%）
        if direction == "long":
            above_ema = sum(1 for b in recent if b.close > ema_now)
        else:
            above_ema = sum(1 for b in recent if b.close < ema_now)
        if above_ema / lookback >= 0.6:
            score += 1

        # 4. 突破近期高/低点（当前 close 突破前 20 根极值）
        window = 20
        if n >= window + lookback:
            seg = history[-(window + lookback): -lookback]
            if seg:
                if direction == "long" and recent[-1].close >= max(b.high for b in seg):
                    score += 1
                elif direction == "short" and recent[-1].close <= min(b.low for b in seg):
                    score += 1

        # 5. 逆势者被套（无超过 2 根连续反向趋势 K）
        opposite = "short" if direction == "long" else "long"
        max_consec = 0
        cur = 0
        for b in recent:
            if _trend_bar_side(b, body_ratio_min, close_extreme_ratio) == opposite:
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


# ---------------------------------------------------------------------------
# TR 过滤：K 线重叠率 + EMA 平坦度
# ---------------------------------------------------------------------------

def _is_trading_range(
    history: list[Bar],
    closes: list[float],
    ema_now: float,
    lookback: int = 20,
    overlap_threshold: float = 0.6,
) -> bool:
    """双重条件：K 线重叠率过高 或 EMA 过于平坦 → 视为 TR。"""
    n = len(history)
    if n < lookback or ema_now <= 0:
        return False

    recent = history[-lookback:]

    # 条件 1：K 线重叠率
    overlap_count = 0
    for i in range(1, len(recent)):
        prev, cur = recent[i - 1], recent[i]
        rng = max(prev.high, cur.high) - min(prev.low, cur.low)
        if rng <= 0:
            overlap_count += 1
            continue
        overlap = min(prev.high, cur.high) - max(prev.low, cur.low)
        if overlap > 0 and overlap / rng > overlap_threshold:
            overlap_count += 1
    if overlap_count / (len(recent) - 1) > 0.6:
        return True

    # 条件 2：EMA 平坦度
    if len(closes) >= lookback:
        ema_series = pd.Series(closes).ewm(span=20, adjust=False).mean()
        recent_ema = ema_series.iloc[-lookback:]
        ema_range = recent_ema.max() - recent_ema.min()
        if ema_range / ema_now < 0.001:
            return True

    return False


# ---------------------------------------------------------------------------
# 摆动点检测（用于追踪止损）
# ---------------------------------------------------------------------------

def _confirm_swing_low(history: list[Bar]) -> Optional[float]:
    """最近 3 根 K 中，中间那根 low 是最低 → 确认摆动低。"""
    if len(history) < 3:
        return None
    a, b, c = history[-3], history[-2], history[-1]
    if b.low <= a.low and b.low <= c.low:
        return b.low
    return None


def _confirm_swing_high(history: list[Bar]) -> Optional[float]:
    """最近 3 根 K 中，中间那根 high 是最高 → 确认摆动高。"""
    if len(history) < 3:
        return None
    a, b, c = history[-3], history[-2], history[-1]
    if b.high >= a.high and b.high >= c.high:
        return b.high
    return None


def _recent_low(history: list[Bar], lookback: int = 20) -> float:
    """最近 lookback 根 K 的最低价。"""
    recent = history[-lookback:]
    return min(b.low for b in recent)


# ---------------------------------------------------------------------------
# 入场信号
# ---------------------------------------------------------------------------

def _ema_pullback_signal(
    history: list[Bar], ema_now: float, atr_val: float
) -> tuple[bool, float]:
    """均线回踩信号：价格回踩 EMA 后出现阳线反弹。

    条件:
    1. 前一根 K 收盘在 EMA 附近（1% 以内）或下方
    2. 当前 K 为阳线（反弹确认）
    3. 回踩深度不深于 2×ATR（不是趋势反转）

    返回 (has_signal, stop_price)
    """
    if len(history) < 5:
        return False, 0.0

    prev = history[-2]
    curr = history[-1]

    # 前一根收盘在 EMA 附近或下方
    near_ema = prev.close <= ema_now * 1.01

    # 当前是阳线反弹
    bouncing = _is_bull_bar(curr)

    # 回踩没有踩穿（EMA - 最低价 < 2×ATR）
    shallow = (ema_now - min(prev.low, curr.low)) < 2 * atr_val

    if near_ema and bouncing and shallow:
        stop = min(_recent_low(history, 10), prev.low, curr.low) - atr_val * 1.5
        return True, stop

    return False, 0.0


# ---------------------------------------------------------------------------
# 主策略
# ---------------------------------------------------------------------------

@register_strategy("stock_trend")
def on_bar(
    bar: Bar,
    ctx: StrategyContext,
    *,
    ema_period: int = 20,
    ai_lookback: int = 5,
    ai_min_score: int = 3,
    body_ratio_min: float = 0.5,
    close_extreme_ratio: float = 0.6,
    tr_lookback: int = 20,
    tr_overlap_threshold: float = 0.6,
    atr_period: int = 14,
    atr_stop_mult: float = 2.0,
    breakeven_at_r: float = 1.0,
    tick_size: float = 0.01,
    long_only: bool = True,
    trend_filter_ema: int = 60,
    **_: object,
) -> None:
    closes = ctx.closes
    history = ctx.history
    n = len(closes)
    warmup = max(ema_period + ai_lookback, tr_lookback + 1, atr_period + 5, trend_filter_ema + 10, 35)
    if n < warmup:
        return

    ema_now = _ema(closes, ema_period)
    trend_ema = _ema(closes, trend_filter_ema)
    ai_dir, ai_strength, ai_score = _ai_direction(
        history, ema_now, ai_lookback, body_ratio_min, close_extreme_ratio, ai_min_score
    )
    prev_ai_dir = ctx.state.get("prev_ai_dir")
    atr_val = _atr(history, atr_period)
    if atr_val != atr_val or atr_val <= 0:
        atr_val = tick_size * 50  # fallback

    # --- 0) 处理"上根发出 buy，本根开盘已成交" ---
    pending = ctx.state.get("pending_entry")
    if ctx.position_side is not None and pending is not None:
        side = pending["side"]
        stop_price = pending["stop"]
        ctx.state["stop_price"] = stop_price
        ctx.state["entry_price"] = bar.open
        ctx.state["risk"] = abs(bar.open - stop_price)
        ctx.state["entry_bar_index"] = n - 1
        ctx.state["follow_checked"] = False
        ctx.state["breakeven_done"] = False
        ctx.state.pop("pending_entry", None)

    # --- 1) 持仓中：按优先级检查离场 ---
    if ctx.position_side is not None:
        side = ctx.position_side
        stop_price = ctx.state.get("stop_price")
        entry_price = ctx.state.get("entry_price")
        risk = ctx.state.get("risk", 1.0)
        follow_checked = ctx.state.get("follow_checked", False)
        breakeven_done = ctx.state.get("breakeven_done", False)

        # 1a. 硬止损（最高优先级）
        if stop_price is not None:
            if side == "long" and bar.low <= stop_price:
                ctx.close(reason=f"stop @ {stop_price:.4f}")
                ctx.state["prev_ai_dir"] = ai_dir
                return
            if side == "short" and bar.high >= stop_price:
                ctx.close(reason=f"stop @ {stop_price:.4f}")
                ctx.state["prev_ai_dir"] = ai_dir
                return

        # 1b. AI 翻转：仅强翻转（评分 ≥ 4）平仓，弱翻转过滤掉日线噪声
        if (side == "long" and ai_dir == "short") or (side == "short" and ai_dir == "long"):
            if ai_strength == "strong":
                ctx.close(reason=f"AI flip to {ai_dir} (strong)")
                ctx.state["prev_ai_dir"] = ai_dir
                return

        # 1c. 跟进 K（默认关闭，日线上意义不大）
        if not follow_checked and entry_price is not None:
            entry_idx = ctx.state.get("entry_bar_index")
            if entry_idx is not None and n - 1 == entry_idx + 1:
                ctx.state["follow_checked"] = True

        # 1d. 1R 保本
        if not breakeven_done and entry_price is not None and risk > 0:
            profit_r = (bar.close - entry_price) / risk if side == "long" else (entry_price - bar.close) / risk
            if profit_r >= breakeven_at_r:
                ctx.state["stop_price"] = entry_price
                ctx.state["breakeven_done"] = True

        # 1e. 追踪止损：保本后，新摆动点确认 → 止损追至新摆动点
        if breakeven_done and stop_price is not None:
            if side == "long":
                swing_low = _confirm_swing_low(history)
                if swing_low is not None:
                    new_stop = swing_low - atr_stop_mult * atr_val
                    if new_stop > ctx.state["stop_price"]:
                        ctx.state["stop_price"] = new_stop
            elif side == "short":
                swing_high = _confirm_swing_high(history)
                if swing_high is not None:
                    new_stop = swing_high + atr_stop_mult * atr_val
                    if new_stop < ctx.state["stop_price"]:
                        ctx.state["stop_price"] = new_stop

        ctx.state["prev_ai_dir"] = ai_dir
        return

    # --- 2) 无持仓：清理残留状态 ---
    for key in ("stop_price", "entry_price", "risk", "entry_bar_index",
                "follow_checked", "breakeven_done"):
        ctx.state.pop(key, None)

    # --- 3) AI 方向变化时重置 ---
    if ai_dir != prev_ai_dir:
        ctx.state.pop("strong_ai_phase", None)

    # --- 4) 入场前置过滤 ---
    if ai_dir is None:
        ctx.state["prev_ai_dir"] = ai_dir
        return
    if _is_trading_range(history, closes, ema_now, tr_lookback, tr_overlap_threshold):
        ctx.state["prev_ai_dir"] = ai_dir
        return

    # --- 5) 入场逻辑 ---
    if ai_dir == "long":

        # 长期趋势过滤：价格在 long-term EMA 以下不入场（避开结构性下跌）
        if trend_filter_ema and trend_ema == trend_ema and bar.close < trend_ema:
            ctx.state["prev_ai_dir"] = ai_dir
            return

        # 5a. 强 AI：直接入场（不等回调）
        if ai_strength == "strong" and not ctx.state.get("strong_ai_phase"):
            ctx.state["strong_ai_phase"] = True
            stop = _recent_low(history, 20) - atr_stop_mult * atr_val
            # 止损不能太紧：至少 0.5×ATR 距离
            min_stop = bar.open - atr_val * 1.5
            stop = min(stop, min_stop)  # 用更紧的那个
            ctx.state["pending_entry"] = {"side": "long", "stop": stop}
            ctx.buy(1, reason="strong AI entry")

        # 5b. 弱 AI：均线回踩入场
        elif ai_strength == "weak":
            signal, stop = _ema_pullback_signal(history, ema_now, atr_val)
            if signal:
                ctx.state["pending_entry"] = {"side": "long", "stop": stop}
                ctx.buy(1, reason="ema pullback")

    elif ai_dir == "short" and not long_only:
        # 长期趋势过滤：价格在 long-term EMA 以上不做空
        if trend_filter_ema and trend_ema == trend_ema and bar.close > trend_ema:
            ctx.state["prev_ai_dir"] = ai_dir
            return

        # 做空路径（股票策略默认关闭）
        # 对称逻辑：强 AI Short → 直接入场；弱 AI Short → 均线反弹入场
        if ai_strength == "strong" and not ctx.state.get("strong_ai_phase"):
            ctx.state["strong_ai_phase"] = True
            stop = _recent_low(history, 20) + atr_stop_mult * atr_val
            min_stop = bar.open + atr_val * 1.5
            stop = max(stop, min_stop)
            ctx.state["pending_entry"] = {"side": "short", "stop": stop}
            ctx.sell(1, reason="strong AI short")

    # --- 6) 重置强 AI 阶段标志（仅在 AI 方向改变时重置，强度波动不影响）---
    if ai_dir != "long":
        ctx.state.pop("strong_ai_phase", None)

    ctx.state["prev_ai_dir"] = ai_dir
