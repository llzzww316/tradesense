"""Broker 负责把 Order 在"下一根 K 开盘"转成 Fill，带滑点和手续费。"""
import pytest
from backtest.broker import Broker
from backtest.models import Bar, Order


def _bar(time="t", o=3000.0, h=3002.0, l=2998.0, c=3001.0, v=100):
    return Bar(time=time, open=o, high=h, low=l, close=c, volume=v)


def test_open_long_next_bar_open_plus_slippage():
    b = Broker(tick_size=1.0, slippage_ticks=1, fee_per_lot=3.0)
    b.submit(Order(action="open_long", qty=2, reason="signal1"))
    fill = b.execute_on_open(_bar(time="t+1", o=3000.0))
    assert fill is not None
    assert fill.time == "t+1"
    assert fill.action == "open_long"
    assert fill.qty == 2
    assert fill.price == pytest.approx(3001.0)   # 开盘 +1 跳
    assert fill.fee == pytest.approx(6.0)        # 3 * 2 手
    assert fill.reason == "signal1"


def test_open_short_applies_negative_slippage():
    b = Broker(tick_size=1.0, slippage_ticks=2, fee_per_lot=3.0)
    b.submit(Order(action="open_short", qty=1))
    fill = b.execute_on_open(_bar(o=3000.0))
    assert fill.price == pytest.approx(2998.0)


def test_close_direction_requires_position():
    """Broker 本身不知道持仓方向，只保证 close 用"与开相反"的滑点。"""
    b = Broker(tick_size=1.0, slippage_ticks=1, fee_per_lot=3.0)
    b.submit(Order(action="close", qty=1), close_side="long")
    fill = b.execute_on_open(_bar(o=3000.0))
    # 平多 = 卖出 = -1 跳
    assert fill.price == pytest.approx(2999.0)

    b2 = Broker(tick_size=1.0, slippage_ticks=1, fee_per_lot=3.0)
    b2.submit(Order(action="close", qty=1), close_side="short")
    f2 = b2.execute_on_open(_bar(o=3000.0))
    # 平空 = 买回 = +1 跳
    assert f2.price == pytest.approx(3001.0)


def test_no_pending_returns_none():
    b = Broker(tick_size=1.0, slippage_ticks=1, fee_per_lot=3.0)
    assert b.execute_on_open(_bar()) is None


def test_only_one_pending_order_allowed():
    """本期简化：一根 K 最多一条待成交。第二次 submit 抛错。"""
    b = Broker(tick_size=1.0, slippage_ticks=1, fee_per_lot=3.0)
    b.submit(Order(action="open_long", qty=1))
    with pytest.raises(ValueError):
        b.submit(Order(action="open_long", qty=1))


def test_pending_cleared_after_execute():
    b = Broker(tick_size=1.0, slippage_ticks=0, fee_per_lot=0.0)
    b.submit(Order(action="open_long", qty=1))
    b.execute_on_open(_bar())
    assert b.execute_on_open(_bar()) is None


def test_force_close_at_price():
    """日内强平 / 爆仓强平的便捷接口：按指定价（一般是当根 close）立即成交。"""
    b = Broker(tick_size=1.0, slippage_ticks=1, fee_per_lot=3.0)
    fill = b.force_close(time="tN", qty=1, side="long", price=2995.0, reason="eod")
    assert fill.action == "close"
    assert fill.price == pytest.approx(2994.0)   # 卖出 -1 跳
    assert fill.reason == "eod"
