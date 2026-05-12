import math
import pytest

from backtest.metrics import compute_metrics
from backtest.models import EquityPoint, Trade


def _ep(t, e, dd=0.0):
    return EquityPoint(time=t, equity=e, drawdown=dd)


def _trade(net, holding=1, side="long"):
    return Trade(
        open_time="t1", close_time="t2", side=side, qty=1,
        open_price=100.0, close_price=100.0 + net,
        fee=0.0, pnl=net, net_pnl=net, holding_bars=holding,
    )


def test_empty_trades_produces_zero_metrics():
    m = compute_metrics(
        equity_curve=[_ep("t1", 100.0), _ep("t2", 100.0)],
        trades=[], initial_capital=100.0, final_position="flat",
    )
    assert m["total_trades"] == 0
    assert m["win_rate"] == 0.0
    assert m["total_return"] == pytest.approx(0.0)
    assert m["final_position"] == "flat"


def test_basic_win_loss_stats():
    trades = [_trade(10), _trade(-5), _trade(20), _trade(-3), _trade(8)]
    m = compute_metrics(equity_curve=[_ep("t", 100.0)], trades=trades,
                        initial_capital=100.0, final_position="flat")
    assert m["total_trades"] == 5
    assert m["winning_trades"] == 3
    assert m["losing_trades"] == 2
    assert m["win_rate"] == pytest.approx(0.6)
    assert m["avg_win"] == pytest.approx((10 + 20 + 8) / 3)
    assert m["avg_loss"] == pytest.approx((5 + 3) / 2)
    assert m["profit_factor"] == pytest.approx((10 + 20 + 8) / (5 + 3))


def test_max_consecutive_streaks():
    trades = [_trade(1), _trade(1), _trade(-1), _trade(-1), _trade(-1), _trade(1)]
    m = compute_metrics(equity_curve=[_ep("t", 100.0)], trades=trades,
                        initial_capital=100.0, final_position="flat")
    assert m["max_win_streak"] == 2
    assert m["max_loss_streak"] == 3


def test_total_and_max_drawdown_from_equity():
    eq = [
        _ep("t1", 100.0, 0.0),
        _ep("t2", 120.0, 0.0),
        _ep("t3", 90.0, -30.0),
        _ep("t4", 110.0, -10.0),
        _ep("t5", 140.0, 0.0),
        _ep("t6", 80.0, -60.0),
    ]
    m = compute_metrics(equity_curve=eq, trades=[], initial_capital=100.0,
                        final_position="flat")
    assert m["max_drawdown"] == pytest.approx(-60.0)
    assert m["max_drawdown_pct"] == pytest.approx(-60.0 / 140.0)
    assert m["total_return"] == pytest.approx((80.0 - 100.0) / 100.0)


def test_sharpe_on_constant_equity_is_zero_or_none():
    """权益一条直线：return series std=0，sharpe 设为 None（不制造除零）。"""
    eq = [_ep(f"t{i}", 100.0) for i in range(10)]
    m = compute_metrics(equity_curve=eq, trades=[], initial_capital=100.0,
                        final_position="flat")
    assert m["sharpe"] is None


def test_avg_holding_bars():
    trades = [_trade(1, holding=2), _trade(-1, holding=6), _trade(1, holding=4)]
    m = compute_metrics(equity_curve=[_ep("t", 100)], trades=trades,
                        initial_capital=100.0, final_position="flat")
    assert m["avg_holding_bars"] == pytest.approx(4.0)


def test_calmar_is_annual_return_over_mdd():
    eq = [_ep(f"t{i}", 100.0 + i) for i in range(10)]
    m = compute_metrics(equity_curve=eq, trades=[], initial_capital=100.0,
                        final_position="flat")
    # total_return = 9/100 = 0.09；mdd=0 → calmar 设为 None
    assert m["calmar"] is None
