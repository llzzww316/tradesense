"""StrategyContext 是策略在 on_bar 里访问的只读上下文 + 下单 API。"""
import pytest
from backtest.context import StrategyContext
from backtest.models import Bar


def _bar(t, c):
    return Bar(time=t, open=c, high=c + 1, low=c - 1, close=c, volume=100)


def test_context_exposes_history_up_to_current():
    ctx = StrategyContext()
    for i in range(5):
        ctx._push_bar(_bar(f"t{i}", 3000.0 + i))
    closes = ctx.closes
    assert list(closes) == [3000.0, 3001.0, 3002.0, 3003.0, 3004.0]
    assert ctx.current_bar.time == "t4"
    assert len(ctx.history) == 5


def test_context_position_side_initially_none():
    ctx = StrategyContext()
    assert ctx.position_side is None
    assert ctx.position_qty == 0


def test_context_position_mutators():
    ctx = StrategyContext()
    ctx._set_position("long", 2)
    assert ctx.position_side == "long"
    assert ctx.position_qty == 2
    ctx._set_position(None, 0)
    assert ctx.position_side is None


def test_buy_sell_close_queue_orders():
    ctx = StrategyContext()
    ctx.buy(1, reason="cross up")
    ctx.close(reason="cross down")
    ctx.sell(2, reason="short")
    assert len(ctx.pending_orders) == 3
    assert ctx.pending_orders[0].action == "open_long"
    assert ctx.pending_orders[0].reason == "cross up"
    assert ctx.pending_orders[1].action == "close"
    assert ctx.pending_orders[2].action == "open_short"
    assert ctx.pending_orders[2].qty == 2


def test_drain_pending_clears_queue():
    ctx = StrategyContext()
    ctx.buy(1)
    drained = ctx.drain_pending()
    assert len(drained) == 1
    assert ctx.pending_orders == []


def test_user_storage_for_indicators():
    """ctx.state 是用户自己的可变字典，策略跨 bar 保存指标用。"""
    ctx = StrategyContext()
    ctx.state["ema_slow"] = 3000.0
    assert ctx.state["ema_slow"] == 3000.0
