"""Always In 顺势回调策略（H2/L2 二次入场版）

来源：价格行为交易·新手实战指南 + 市场结构判断01-04 + Al Brooks 三部曲
- 手册（1）：5 项 Always In 检查清单（≥3 项确立，≥4 项强 AI）
- 手册（2）：H2/L2 二次入场（对手方失败两次才入场）
- 手册（6）：四棒序列 + 信号 K 质量分级
- 手册（8）：趋势四步确认 + 五维强度评估
- 手册（13）：80% 法则 → TR 区间禁入
- 市场结构判断01：AI 翻转立即平仓，第一铁律
- Brooks《Reading Price Charts》ch10.19：止损 = 重要摆动点极值外

规则总览：
1. AI 方向：5 项清单评分（≥min_score 项确立），强/弱区分
2. 入场前置：非 TR（K线重叠率 + EMA平坦度双重过滤）
3. H2/L2 二次入场：AI 确立后等回调，H1 不入，H2 入场
4. 入场：下一根开盘成交（broker 默认行为）
5. 止损：回调摆动极值 ± buffer_ticks
6. 离场优先级（高 → 低）：
   a) 硬止损：摆动点止损被触
   b) AI 翻转：方向反转 → 立刻平仓
   c) 跟进 K 失败：入场后第一根 K 是强反向趋势 K → 平仓
   d) 1R 保本：浮盈 ≥ 1R → 止损移到入场价
   e) 追踪止损：新摆动点确认 → 止损追至新摆动点

─────────────────────────────────────────────────────
版本历史（v4-v8 已回退，以下记录避免重走老路）：

v4 — AI 翻转三重确认（score ≥ 5 + 连续 2 根确认 + 当前 bar 为反向趋势 K）
  问题：翻转太滞后，持仓中 AI 已明显翻转却还在等确认，导致利润回吐严重
  教训：AI 翻转是「该跑就跑」的信号，不应设过高门槛；合理的做法是弱翻转缩仓、强翻转清仓

v5 — 入场质量过滤（信号 K 必须同向阳/阴线 + 止损距离 ≤ 2% 价格）
  问题：PVC PF 从 1.30 骤降到 0.46；2% 过滤太严，PVC 波动大导致好的 H2 信号被误杀
  教训：止损距离过滤不适用于所有品种，应按品种 ATR 自适应或干脆不加

v6 — 冷却期（平仓后 cooldown_bars 根 K 不入场）+ Chop 检测（快速双翻暂停交易）
  问题：冷却期让策略在趋势恢复后错过再入场时机；Chop 检测误判导致真趋势中被禁入
  教训：机械冷却不如依赖 H2/L2 本身的质量过滤；真趋势中的回调也是 H2 机会

v7 — 跟进 K 加强（反向趋势 K 还需击穿止损才离场）
  问题：本来跟进 K 是「第一根反向就跑」的保护机制，加了击穿止损的条件后形同虚设
  教训：跟进 K 的价值在于快速认错，不应与止损条件绑定

v8 — 保本阶梯（1.5R→保本，3R→entry+0.5R）
  问题：螺纹钢 PF 从 0.58 暴降到 0.27；半利止损太激进，锁小利挡大趋势
  教训：1R 保本是合理的，但 entry+0.5R 的半利止损把盈利交易截断了；
        追踪止损（摆动点）已经能起到保护利润的作用，不需要额外的半利阶梯

核心结论：
  - H2/L2 + 摆动点止损 + 1R 保本 + 追踪止损 是有效组合（v3 验证）
  - 过度保护（翻转门槛↑、冷却期、半利阶梯）适得其反
  - 品种选择比参数调优更重要：PVC 1h 适合趋势跟随，螺纹钢 1h 噪声太大
─────────────────────────────────────────────────────
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
    if n < lookback or ema_now != ema_now:  # nan check
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
# H2/L2 二次入场状态机
# ---------------------------------------------------------------------------

_PULLBACK_NONE = "none"
_PULLBACK_STARTED = "started"
_PULLBACK_H1_SEEN = "h1_seen"


def _detect_pullback_state(
    bar: Bar,
    prev_bar: Bar,
    ai_dir: Side,
    state: dict,
) -> tuple[str, Optional[Side]]:
    """检测 H2/L2 信号，返回 (new_state, signal_side)。

    state keys:
        pb_state: str — "none" | "started" | "h1_seen"
        pb_extreme: float — 回调期间的极值（long=最低low, short=最高high）
        h1_bar_index: int — H1 出现时的 K 序号
    """
    pb_state = state.get("pb_state", _PULLBACK_NONE)
    pb_extreme = state.get("pb_extreme")
    signal: Optional[Side] = None

    if ai_dir == "long":
        is_pullback = _is_bear_bar(bar) or bar.close < prev_bar.close
        is_higher_high = bar.high > prev_bar.high

        if pb_state == _PULLBACK_NONE:
            if is_pullback:
                pb_state = _PULLBACK_STARTED
                pb_extreme = bar.low

        elif pb_state == _PULLBACK_STARTED:
            pb_extreme = min(pb_extreme, bar.low) if pb_extreme is not None else bar.low
            if is_higher_high and _is_bull_bar(bar):
                pb_state = _PULLBACK_H1_SEEN
                state["h1_bar_index"] = state.get("_n", 0)

        elif pb_state == _PULLBACK_H1_SEEN:
            pb_extreme = min(pb_extreme, bar.low) if pb_extreme is not None else bar.low
            h1_idx = state.get("h1_bar_index", 0)
            cur_idx = state.get("_n", 0)
            if cur_idx > h1_idx:
                if is_higher_high and _is_bull_bar(bar):
                    signal = "long"

    elif ai_dir == "short":
        is_pullback = _is_bull_bar(bar) or bar.close > prev_bar.close
        is_lower_low = bar.low < prev_bar.low

        if pb_state == _PULLBACK_NONE:
            if is_pullback:
                pb_state = _PULLBACK_STARTED
                pb_extreme = bar.high

        elif pb_state == _PULLBACK_STARTED:
            pb_extreme = max(pb_extreme, bar.high) if pb_extreme is not None else bar.high
            if is_lower_low and _is_bear_bar(bar):
                pb_state = _PULLBACK_H1_SEEN
                state["h1_bar_index"] = state.get("_n", 0)

        elif pb_state == _PULLBACK_H1_SEEN:
            pb_extreme = max(pb_extreme, bar.high) if pb_extreme is not None else bar.high
            h1_idx = state.get("h1_bar_index", 0)
            cur_idx = state.get("_n", 0)
            if cur_idx > h1_idx:
                if is_lower_low and _is_bear_bar(bar):
                    signal = "short"

    state["pb_state"] = pb_state
    state["pb_extreme"] = pb_extreme

    return pb_state, signal


def _reset_pullback(state: dict) -> None:
    state["pb_state"] = _PULLBACK_NONE
    state["pb_extreme"] = None
    state.pop("h1_bar_index", None)


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


# ---------------------------------------------------------------------------
# 主策略
# ---------------------------------------------------------------------------

@register_strategy("always_in_pullback")
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
    stop_buffer_ticks: int = 3,
    tick_size: float = 1.0,
    breakeven_at_r: float = 1.0,
    **_: object,
) -> None:
    closes = ctx.closes
    history = ctx.history
    n = len(closes)
    warmup = max(ema_period + ai_lookback, tr_lookback + 1, 25)
    if n < warmup:
        return

    ema_now = _ema(closes, ema_period)
    ai_dir, ai_strength, ai_score = _ai_direction(
        history, ema_now, ai_lookback, body_ratio_min, close_extreme_ratio, ai_min_score
    )
    prev_ai_dir = ctx.state.get("prev_ai_dir")

    # --- 0) 处理"上根发出 buy/sell，本根开盘已成交" ---
    pending = ctx.state.get("pending_entry")
    if ctx.position_side is not None and pending is not None:
        side = pending["side"]
        if side == "long":
            stop_price = pending["pb_extreme"] - stop_buffer_ticks * tick_size
        else:
            stop_price = pending["pb_extreme"] + stop_buffer_ticks * tick_size
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
                _reset_pullback(ctx.state)
                ctx.state["prev_ai_dir"] = ai_dir
                return
            if side == "short" and bar.high >= stop_price:
                ctx.close(reason=f"stop @ {stop_price:.4f}")
                _reset_pullback(ctx.state)
                ctx.state["prev_ai_dir"] = ai_dir
                return

        # 1b. AI 翻转 → 立刻平仓（不做额外确认门槛）
        if (side == "long" and ai_dir == "short") or (side == "short" and ai_dir == "long"):
            ctx.close(reason=f"AI flip to {ai_dir}")
            _reset_pullback(ctx.state)
            ctx.state["prev_ai_dir"] = ai_dir
            return

        # 1c. 跟进 K 失败：入场后第一根 K 是强反向趋势 K → 平仓
        if not follow_checked and entry_price is not None:
            entry_idx = ctx.state.get("entry_bar_index")
            if entry_idx is not None and n - 1 == entry_idx + 1:
                ctx.state["follow_checked"] = True
                if side == "long":
                    if _trend_bar_side(bar, body_ratio_min, close_extreme_ratio) == "short":
                        ctx.close(reason="follow-through bear trend bar")
                        _reset_pullback(ctx.state)
                        ctx.state["prev_ai_dir"] = ai_dir
                        return
                elif side == "short":
                    if _trend_bar_side(bar, body_ratio_min, close_extreme_ratio) == "long":
                        ctx.close(reason="follow-through bull trend bar")
                        _reset_pullback(ctx.state)
                        ctx.state["prev_ai_dir"] = ai_dir
                        return

        # 1d. 1R 保本：浮盈 ≥ breakeven_at_r * R → 止损移到入场价（一次性，不回退）
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
                    new_stop = swing_low - stop_buffer_ticks * tick_size
                    if new_stop > ctx.state["stop_price"]:
                        ctx.state["stop_price"] = new_stop
            elif side == "short":
                swing_high = _confirm_swing_high(history)
                if swing_high is not None:
                    new_stop = swing_high + stop_buffer_ticks * tick_size
                    if new_stop < ctx.state["stop_price"]:
                        ctx.state["stop_price"] = new_stop

        ctx.state["prev_ai_dir"] = ai_dir
        return

    # --- 2) 无持仓：清理上一笔残留状态 ---
    for key in ("stop_price", "entry_price", "risk", "entry_bar_index",
                "follow_checked", "breakeven_done"):
        ctx.state.pop(key, None)

    # --- 3) AI 方向变化时重置回调状态 ---
    if ai_dir != prev_ai_dir:
        _reset_pullback(ctx.state)

    # --- 4) 入场前置过滤 ---
    if ai_dir is None:
        ctx.state["prev_ai_dir"] = ai_dir
        return
    if _is_trading_range(history, closes, ema_now, tr_lookback, tr_overlap_threshold):
        ctx.state["prev_ai_dir"] = ai_dir
        return

    # --- 5) H2/L2 二次入场检测 ---
    ctx.state["_n"] = n
    prev_bar = history[-2]
    _, signal = _detect_pullback_state(bar, prev_bar, ai_dir, ctx.state)

    if signal == "long":
        pb_extreme = ctx.state.get("pb_extreme")
        if pb_extreme is not None:
            ctx.state["pending_entry"] = {
                "side": "long",
                "pb_extreme": pb_extreme,
            }
            ctx.buy(1, reason="H2 long")
            _reset_pullback(ctx.state)
    elif signal == "short":
        pb_extreme = ctx.state.get("pb_extreme")
        if pb_extreme is not None:
            ctx.state["pending_entry"] = {
                "side": "short",
                "pb_extreme": pb_extreme,
            }
            ctx.sell(1, reason="L2 short")
            _reset_pullback(ctx.state)

    # --- 6) AI 确立但还没到 H2：如果趋势恢复创新高/新低，重置回调状态 ---
    if ai_dir == "long" and n >= 2:
        recent_high = max(b.high for b in history[-20:]) if n >= 20 else max(b.high for b in history)
        if bar.close >= recent_high:
            _reset_pullback(ctx.state)
    elif ai_dir == "short" and n >= 2:
        recent_low = min(b.low for b in history[-20:]) if n >= 20 else min(b.low for b in history)
        if bar.close <= recent_low:
            _reset_pullback(ctx.state)

    ctx.state["prev_ai_dir"] = ai_dir
