"""/api/backtest 路由的最小 E2E 测试（用 TestClient + mock 数据）。"""
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import backtest.strategies  # noqa: F401  先注册内置策略


@pytest.fixture
def client(monkeypatch):
    # 先 patch data_provider：不要读本机文件，返回合成 K
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


def test_list_strategies(client):
    r = client.get("/api/backtest/strategies")
    assert r.status_code == 200
    assert "double_ma" in r.json()["strategies"]


def test_run_backtest_basic(client):
    payload = {
        "symbol": "螺纹钢",
        "period": "5m",
        "initial_capital": 100000,
        "slippage_ticks": 1,
        "intraday_only": False,
        "strategy": "double_ma",
        "strategy_params": {"fast": 3, "slow": 8},
    }
    r = client.post("/api/backtest/run", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert "bars" in body and "fills" in body and "trades" in body
    assert "equity_curve" in body and "metrics" in body
    assert body["metrics"]["final_position"] in ("long", "short", "flat")


def test_unknown_symbol_returns_400(client):
    r = client.post("/api/backtest/run", json={
        "symbol": "不存在的品种", "period": "5m",
        "strategy": "double_ma", "strategy_params": {},
    })
    assert r.status_code in (400, 404)


def test_unknown_strategy_returns_400(client):
    r = client.post("/api/backtest/run", json={
        "symbol": "螺纹钢", "period": "5m",
        "strategy": "not_exist", "strategy_params": {},
    })
    assert r.status_code == 400
