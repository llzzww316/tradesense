"""绩效指标：total_return / max_drawdown / sharpe / calmar / 胜率 / 盈亏比 / 连胜连败 / avg_holding。"""
from __future__ import annotations

import math
from typing import Optional

from backtest.models import EquityPoint, Trade


def _std(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(var)


def _equity_returns(points: list[EquityPoint]) -> list[float]:
    rets = []
    for i in range(1, len(points)):
        prev = points[i - 1].equity
        cur = points[i].equity
        if prev == 0:
            continue
        rets.append((cur - prev) / prev)
    return rets


def compute_metrics(
    *,
    equity_curve: list[EquityPoint],
    trades: list[Trade],
    initial_capital: float,
    final_position: str,
    bars_per_year: int = 252 * 24 * 12,
) -> dict:
    total_trades = len(trades)
    wins = [t.net_pnl for t in trades if t.net_pnl > 0]
    losses = [-t.net_pnl for t in trades if t.net_pnl < 0]
    win_rate = (len(wins) / total_trades) if total_trades else 0.0
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0
    profit_factor = (sum(wins) / sum(losses)) if losses else (float("inf") if wins else 0.0)

    streak_win = streak_loss = cur_w = cur_l = 0
    for t in trades:
        if t.net_pnl > 0:
            cur_w += 1; cur_l = 0
            streak_win = max(streak_win, cur_w)
        elif t.net_pnl < 0:
            cur_l += 1; cur_w = 0
            streak_loss = max(streak_loss, cur_l)

    max_dd = min((p.drawdown for p in equity_curve), default=0.0)
    peak = max((p.equity for p in equity_curve), default=initial_capital)
    max_dd_pct = (max_dd / peak) if peak else 0.0

    final_equity = equity_curve[-1].equity if equity_curve else initial_capital
    total_return = (final_equity - initial_capital) / initial_capital if initial_capital else 0.0

    rets = _equity_returns(equity_curve)
    std = _std(rets)
    sharpe: Optional[float]
    if std == 0 or len(rets) < 2:
        sharpe = None
    else:
        mean = sum(rets) / len(rets)
        sharpe = (mean / std) * math.sqrt(bars_per_year)

    calmar: Optional[float]
    if max_dd == 0:
        calmar = None
    else:
        if len(equity_curve) <= 1:
            annual = total_return
        elif 1 + total_return <= 0:
            # 权益已归零/转负（爆仓），复利年化无定义，退化为线性回报
            annual = total_return
        else:
            years = len(equity_curve) / bars_per_year
            annual = ((1 + total_return) ** (1 / years)) - 1 if years > 0 else total_return
        calmar = annual / abs(max_dd_pct) if max_dd_pct else None

    avg_holding = (
        sum(t.holding_bars for t in trades) / total_trades
    ) if total_trades else 0.0

    return {
        "total_trades": total_trades,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "max_win_streak": streak_win,
        "max_loss_streak": streak_loss,
        "max_drawdown": max_dd,
        "max_drawdown_pct": max_dd_pct,
        "total_return": total_return,
        "sharpe": sharpe,
        "calmar": calmar,
        "avg_holding_bars": avg_holding,
        "final_equity": final_equity,
        "final_position": final_position,
    }
