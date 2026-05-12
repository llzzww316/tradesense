"""Account 负责持仓、权益、可用、爆仓判断；所有成交都走它。"""
import pytest
from backtest.account import Account
from backtest.models import Fill


def _mk_account(initial=100000.0, margin_rate=0.10, tick_size=1.0, tick_value=10.0):
    return Account(
        initial_capital=initial,
        margin_rate=margin_rate,
        tick_size=tick_size,
        tick_value=tick_value,
    )


def test_initial_state():
    a = _mk_account()
    assert a.equity == 100000.0
    assert a.available == 100000.0
    assert a.position is None
    assert a.realized_pnl == 0.0
    assert a.total_fee == 0.0


def test_open_long_occupies_margin():
    a = _mk_account()
    # 开多 2 手 @ 3000；保证金 = 3000 * 2 * 10 * 0.10 = 6000
    a.apply_fill(Fill(time="t1", action="open_long", qty=2, price=3000.0, fee=6.0))
    assert a.position.side == "long"
    assert a.position.qty == 2
    assert a.position.margin == 6000.0
    # fee 从 available 扣
    assert a.available == pytest.approx(100000.0 - 6000.0 - 6.0)
    assert a.total_fee == 6.0


def test_update_on_close_marks_unrealized():
    a = _mk_account()
    a.apply_fill(Fill(time="t1", action="open_long", qty=1, price=3000.0, fee=3.0))
    # 收盘价 3005；浮盈 = (3005-3000)/1 * 10 * 1 = 50
    a.update_on_close(close_price=3005.0)
    assert a.unrealized_pnl == pytest.approx(50.0)
    # equity = 初始 - fee + 浮盈
    assert a.equity == pytest.approx(100000.0 - 3.0 + 50.0)


def test_close_long_realizes_pnl():
    a = _mk_account()
    a.apply_fill(Fill(time="t1", action="open_long", qty=1, price=3000.0, fee=3.0))
    a.apply_fill(Fill(time="t2", action="close", qty=1, price=3010.0, fee=3.0))
    # 毛利 = 10 跳 * 10 元 = 100；净利 = 100 - 6 = 94
    assert a.position is None
    assert a.realized_pnl == pytest.approx(100.0)
    assert a.total_fee == pytest.approx(6.0)
    # 全部返还：available = initial + realized - total_fee
    assert a.available == pytest.approx(100000.0 + 100.0 - 6.0)


def test_open_short_then_close_profit():
    a = _mk_account()
    a.apply_fill(Fill(time="t1", action="open_short", qty=1, price=3000.0, fee=3.0))
    a.apply_fill(Fill(time="t2", action="close", qty=1, price=2990.0, fee=3.0))
    # 空头 3000 → 2990 赚 10 跳 = 100
    assert a.realized_pnl == pytest.approx(100.0)


def test_liquidated_when_available_negative():
    # 小资金 + 大仓位让可用打穿
    a = _mk_account(initial=6000.0)
    a.apply_fill(Fill(time="t1", action="open_long", qty=1, price=3000.0, fee=3.0))
    # 保证金 3000，available ~2997；收盘暴跌让浮亏 > available
    a.update_on_close(close_price=2600.0)
    assert a.is_liquidated() is True


def test_not_liquidated_when_flat():
    a = _mk_account()
    a.update_on_close(close_price=3000.0)
    assert a.is_liquidated() is False


def test_reject_partial_close_not_supported():
    """本期简化：平仓一次性全部平掉；qty 不等于持仓手数直接报错。"""
    a = _mk_account()
    a.apply_fill(Fill(time="t1", action="open_long", qty=2, price=3000.0, fee=6.0))
    with pytest.raises(ValueError):
        a.apply_fill(Fill(time="t2", action="close", qty=1, price=3005.0, fee=3.0))


def test_reject_reverse_before_close():
    """不支持反手：持多时不允许直接 open_short。"""
    a = _mk_account()
    a.apply_fill(Fill(time="t1", action="open_long", qty=1, price=3000.0, fee=3.0))
    with pytest.raises(ValueError):
        a.apply_fill(Fill(time="t2", action="open_short", qty=1, price=3000.0, fee=3.0))
