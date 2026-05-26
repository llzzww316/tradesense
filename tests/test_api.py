"""/api/backtest 路由的最小 E2E 测试（用 TestClient + mock 数据 + 桩策略）。"""
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backtest.registry import register_strategy, _STRATEGIES


_STUB_NAME = "_test_stub_strategy"


@pytest.fixture
def stub_strategy():
    """注册一个最小桩策略：第 5 根开多，第 15 根平仓。测试结束后注销。"""
    if _STUB_NAME in _STRATEGIES:
        del _STRATEGIES[_STUB_NAME]

    @register_strategy(_STUB_NAME)
    def _stub(bar, ctx, qty: int = 1):
        i = len(ctx.history) - 1
        if i == 5 and ctx.position_side is None:
            ctx.buy(qty=qty, reason="stub-open")
        elif i == 15 and ctx.position_side is not None:
            ctx.close(reason="stub-close")

    yield _STUB_NAME
    del _STRATEGIES[_STUB_NAME]


@pytest.fixture
def client(monkeypatch):
    import backtest.api as api

    def fake_fetch(market, symbol, period, start_date=None, end_date=None, count=800):
        rows = []
        t = pd.Timestamp("2025-10-01 09:00:00")
        for i in range(50):
            c = 3000 + i
            rows.append({"bob": t, "open": c, "high": c + 1, "low": c - 1,
                         "close": float(c), "volume": 100})
            t += pd.Timedelta(minutes=5)
        return pd.DataFrame(rows)

    monkeypatch.setattr(api, "fetch_kline_by_date", fake_fetch)

    from server import app
    return TestClient(app)


def test_list_strategies_endpoint_ok(client, stub_strategy):
    r = client.get("/api/backtest/strategies")
    assert r.status_code == 200
    body = r.json()
    assert "strategies" in body
    names = [s["name"] for s in body["strategies"]]
    assert stub_strategy in names


def test_run_backtest_basic(client, stub_strategy):
    payload = {
        "symbol": "螺纹钢",
        "period": "5m",
        "initial_capital": 100000,
        "slippage_ticks": 1,
        "intraday_only": False,
        "strategy": stub_strategy,
        "strategy_params": {"qty": 1},
    }
    r = client.post("/api/backtest/run", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert "bars" in body and "fills" in body and "trades" in body
    assert "equity_curve" in body and "metrics" in body
    assert body["metrics"]["final_position"] in ("long", "short", "flat")


def test_unknown_symbol_returns_400(client, stub_strategy):
    r = client.post("/api/backtest/run", json={
        "symbol": "不存在的品种", "period": "5m",
        "strategy": stub_strategy, "strategy_params": {},
    })
    assert r.status_code in (400, 404)


def test_unknown_strategy_returns_400(client):
    r = client.post("/api/backtest/run", json={
        "symbol": "螺纹钢", "period": "5m",
        "strategy": "not_exist", "strategy_params": {},
    })
    assert r.status_code == 400
