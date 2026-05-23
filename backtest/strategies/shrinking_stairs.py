"""收缩楼梯反转策略

来源：价格行为交易·新手实战指南 + Al Brooks 三部曲
- 手册（3）：楔形 / 三次推动 / 动能衰减 / 75% 规则
- 手册（16）：反转 / TBTL 原则 / 收缩楼梯
- 手册（17）：主要趋势反转三阶段

规则总览：
1. 摆动点检测：N 根 K 确认摆动高/低（增量式，每根 K 只检查一个候选位）
2. 收缩楼梯识别：3 段同向推进幅度严格递减
3. TBTL 过滤：楼梯跨越 ≥ tbtl_min_bars 根 K
4. 反转信号 K：大实体趋势 K 反向
5. 入场：下一根开盘成交（broker 默认行为）
6. 止损：楼梯极值 ± buffer_ticks
7. 离场优先级（高 → 低）：
   a) 硬止损：楼梯极值止损被触
   b) 跟进 K 失败：入场后第一根 K 是强反向趋势 K → 平仓
   c) 1R 保本：浮盈 ≥ 1R → 止损移到入场价
   d) 追踪止损：新摆动点确认 → 止损追至新摆动点

回测记录（2026-05-22，默认参数，5 分钟周期）：

螺纹钢 RB2610：
  max_r_ticks  trades  win%    PF    pnl    sharpe
  0（无限制）    40    47.5%  1.14   +580    0.15
  30            60    40.0%  1.11   +410    0.59
  20            60    30.0%  0.52  -1900   -3.67
  15            59    30.5%  0.60  -1384   -3.12
  10            29    41.4%  1.40   +426    1.23  ← 最优

PVC V2609：
  max_r_ticks  trades  win%    PF    pnl    sharpe
  0（无限制）    56    32.1%  0.57  -5774   -2.51
  30            39    35.9%  1.53  +1094    2.13  ← 最优
  20            23    26.1%  1.34   +353    1.20
  15            15    20.0%  0.84    -50   -0.27
  10             6    33.3%  1.01     +1    0.01

结论：
- R 上限过滤显著改善回测表现，PVC 从巨亏翻正
- 最优 R 上限因品种波动率而异（螺纹 10 跳 vs PVC 30 跳）
- 后续考虑改为 ATR 动态倍数（max_r_atr_mult），用 2x ATR 自动适配波动率
"""
from __future__ import annotations

from typing import Optional

from backtest.context import StrategyContext
from backtest.indicators import (
    confirm_swing_high, confirm_swing_low, trend_bar_side,
)
from backtest.models import Bar, Side
from backtest.registry import register_strategy


# ---------------------------------------------------------------------------
# 摆动点检测（增量式）
# ---------------------------------------------------------------------------

def _try_confirm_swing(
    history: list[Bar], lookback: int = 3
) -> Optional[tuple[int, float, str]]:
    """尝试确认 position (n-lookback-1) 处的摆动点。
    需要 lookback 根前驱 + lookback 根后继才能确认，故延迟 lookback 根 K。
    返回 (bar_index, price, "high"|"low") 或 None。
    """
    n = len(history)
    ci = n - lookback - 1
    if ci < lookback:
        return None

    cand = history[ci]

    is_high = all(
        history[j].high < cand.high
        for j in range(ci - lookback, ci + lookback + 1) if j != ci
    )
    if is_high:
        return (ci, cand.high, "high")

    is_low = all(
        history[j].low > cand.low
        for j in range(ci - lookback, ci + lookback + 1) if j != ci
    )
    if is_low:
        return (ci, cand.low, "low")

    return None


def _get_alternating_swings(
    pts: list[tuple[int, float, str]],
) -> list[tuple[int, float, str]]:
    """从摆动点列表提取交替高/低序列；连续同类型保留更极端的。"""
    if not pts:
        return []
    result = [pts[0]]
    for pt in pts[1:]:
        last = result[-1]
        if pt[2] != last[2]:
            result.append(pt)
        else:
            if pt[2] == "high" and pt[1] > last[1]:
                result[-1] = pt
            elif pt[2] == "low" and pt[1] < last[1]:
                result[-1] = pt
    return result


# ---------------------------------------------------------------------------
# 收缩楼梯检测
# ---------------------------------------------------------------------------

def _detect_shrinking_stairs(
    alt: list[tuple[int, float, str]],
    min_stairs: int = 3,
    shrink_ratio: float = 1.0,
) -> Optional[tuple[Side, float, list[float]]]:
    """检测收缩楼梯。

    返回 (反转方向, 楼梯极值, 各段幅度) 或 None。
    shrink_ratio: 每段推进 ≤ 前一段 × shrink_ratio，1.0 = 任何严格递减即可。
    """
    need = 2 * min_stairs
    if len(alt) < need:
        return None

    recent = alt[-need:]
    first_type = recent[0][2]

    if first_type == "low":
        # 上升楼梯 L0,H1,L1,H2,L2,H3 → 反转做空
        legs: list[float] = []
        for i in range(min_stairs):
            legs.append(recent[2 * i + 1][1] - recent[2 * i][1])
        if any(l <= 0 for l in legs):
            return None
        if not all(legs[i] <= legs[i - 1] * shrink_ratio for i in range(1, len(legs))):
            return None
        extreme = max(p[1] for p in recent if p[2] == "high")
        return ("short", extreme, legs)

    if first_type == "high":
        # 下降楼梯 H0,L1,H1,L2,H2,L3 → 反转做多
        legs = []
        for i in range(min_stairs):
            legs.append(recent[2 * i][1] - recent[2 * i + 1][1])
        if any(l <= 0 for l in legs):
            return None
        if not all(legs[i] <= legs[i - 1] * shrink_ratio for i in range(1, len(legs))):
            return None
        extreme = min(p[1] for p in recent if p[2] == "low")
        return ("long", extreme, legs)

    return None


# ---------------------------------------------------------------------------
# 持仓状态清理
# ---------------------------------------------------------------------------

_POS_KEYS = (
    "stop_price", "entry_price", "risk", "entry_bar_index",
    "follow_checked", "breakeven_done", "pending_entry",
)


def _clear_pos_state(state: dict) -> None:
    for k in _POS_KEYS:
        state.pop(k, None)


# ---------------------------------------------------------------------------
# 主策略
# ---------------------------------------------------------------------------

@register_strategy("shrinking_stairs_reversal")
def on_bar(
    bar: Bar,
    ctx: StrategyContext,
    *,
    ema_period: int = 20,
    swing_lookback: int = 3,
    min_stairs: int = 3,
    shrink_ratio: float = 1.0,
    tbtl_min_bars: int = 10,
    body_ratio_min: float = 0.5,
    close_extreme_ratio: float = 0.6,
    stop_buffer_ticks: int = 3,
    tick_size: float = 1.0,
    breakeven_at_r: float = 1.0,
    max_r_ticks: int = 0,
    **_: object,
) -> None:
    history = ctx.history
    closes = ctx.closes
    n = len(closes)
    warmup = max(ema_period + 10, 2 * swing_lookback + 2 * min_stairs + 5, 30)
    if n < warmup:
        return

    # ── 摆动点增量检测 ──────────────────────────────────────────
    swing_pts: list[tuple[int, float, str]] = ctx.state.get("swing_pts", [])
    last_ci = ctx.state.get("last_swing_ci", swing_lookback)
    ci = n - swing_lookback - 1
    if ci > last_ci:
        sw = _try_confirm_swing(history, swing_lookback)
        if sw is not None and (not swing_pts or swing_pts[-1][0] != sw[0]):
            swing_pts.append(sw)
        ctx.state["last_swing_ci"] = ci
        ctx.state["swing_pts"] = swing_pts

    # ── 处理上根下单、本根开盘成交 ──────────────────────────────
    pending = ctx.state.get("pending_entry")
    if ctx.position_side is not None and pending is not None:
        side = pending["side"]
        if side == "long":
            stop_price = pending["stairs_extreme"] - stop_buffer_ticks * tick_size
        else:
            stop_price = pending["stairs_extreme"] + stop_buffer_ticks * tick_size
        ctx.state["stop_price"] = stop_price
        ctx.state["entry_price"] = bar.open
        ctx.state["risk"] = abs(bar.open - stop_price)
        ctx.state["entry_bar_index"] = n - 1
        ctx.state["follow_checked"] = False
        ctx.state["breakeven_done"] = False
        ctx.state.pop("pending_entry", None)

    # ── 持仓管理 ───────────────────────────────────────────────
    if ctx.position_side is not None:
        side = ctx.position_side
        stop_price = ctx.state.get("stop_price")
        entry_price = ctx.state.get("entry_price")
        risk = ctx.state.get("risk", 1.0)

        # 1a 硬止损
        if stop_price is not None:
            if side == "long" and bar.low <= stop_price:
                ctx.close(reason=f"stop @ {stop_price:.4f}")
                _clear_pos_state(ctx.state)
                return
            if side == "short" and bar.high >= stop_price:
                ctx.close(reason=f"stop @ {stop_price:.4f}")
                _clear_pos_state(ctx.state)
                return

        # 1b 跟进 K 失败
        if not ctx.state.get("follow_checked") and entry_price is not None:
            entry_idx = ctx.state.get("entry_bar_index")
            if entry_idx is not None and n - 1 == entry_idx + 1:
                ctx.state["follow_checked"] = True
                if side == "long" and trend_bar_side(bar, body_ratio_min, close_extreme_ratio) == "short":
                    ctx.close(reason="follow-through bear")
                    _clear_pos_state(ctx.state)
                    return
                if side == "short" and trend_bar_side(bar, body_ratio_min, close_extreme_ratio) == "long":
                    ctx.close(reason="follow-through bull")
                    _clear_pos_state(ctx.state)
                    return

        # 1c 1R 保本
        if not ctx.state.get("breakeven_done") and entry_price is not None and risk > 0:
            pr = (bar.close - entry_price) / risk if side == "long" else (entry_price - bar.close) / risk
            if pr >= breakeven_at_r:
                ctx.state["stop_price"] = entry_price
                ctx.state["breakeven_done"] = True

        # 1d 追踪止损（保本后，新摆动点 → 移止损）
        if ctx.state.get("breakeven_done") and stop_price is not None:
            if side == "long":
                sl = confirm_swing_low(history)
                if sl is not None:
                    ns = sl - stop_buffer_ticks * tick_size
                    if ns > ctx.state["stop_price"]:
                        ctx.state["stop_price"] = ns
            elif side == "short":
                sh = confirm_swing_high(history)
                if sh is not None:
                    ns = sh + stop_buffer_ticks * tick_size
                    if ns < ctx.state["stop_price"]:
                        ctx.state["stop_price"] = ns

        return

    # ── 无持仓：入场逻辑 ──────────────────────────────────────
    _clear_pos_state(ctx.state)

    alt = _get_alternating_swings(swing_pts)
    need = 2 * min_stairs
    if len(alt) < need:
        return

    # TBTL：楼梯跨越 K 线数 ≥ tbtl_min_bars
    span_start = alt[-need][0]
    if (n - 1 - span_start) < tbtl_min_bars:
        return

    # 收缩楼梯检测
    result = _detect_shrinking_stairs(alt, min_stairs, shrink_ratio)
    if result is None:
        return
    rev_dir, stairs_extreme, legs = result

    # 反转信号 K：当前 bar 必须是强反向趋势 K
    if trend_bar_side(bar, body_ratio_min, close_extreme_ratio) != rev_dir:
        return

    # R 上限过滤：估算入场风险，超限放弃
    if max_r_ticks > 0:
        if rev_dir == "long":
            est_r = bar.close - (stairs_extreme - stop_buffer_ticks * tick_size)
        else:
            est_r = (stairs_extreme + stop_buffer_ticks * tick_size) - bar.close
        if est_r <= 0 or est_r > max_r_ticks * tick_size:
            return

    # 入场
    ctx.state["pending_entry"] = {"side": rev_dir, "stairs_extreme": stairs_extreme}
    if rev_dir == "long":
        ctx.buy(1, reason=f"stairs long legs={[round(l, 2) for l in legs]}")
    else:
        ctx.sell(1, reason=f"stairs short legs={[round(l, 2) for l in legs]}")
