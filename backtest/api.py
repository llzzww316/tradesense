"""FastAPI 子路由：/api/backtest/*"""
from __future__ import annotations

from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import backtest.strategies  # noqa: F401  触发内置策略注册

from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig
from backtest.registry import get_strategy, list_strategies
from config import get_symbols_config
from data_provider import fetch_kline_by_date


router = APIRouter(prefix="/api/backtest", tags=["backtest"])


class RunBacktestRequest(BaseModel):
    symbol: str
    contract: Optional[str] = None
    period: str = "5m"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    initial_capital: float = 100_000.0
    tick_size: Optional[float] = None
    tick_value: Optional[float] = None
    margin_rate: Optional[float] = None
    fee_per_lot: Optional[float] = None
    slippage_ticks: int = 1
    intraday_only: bool = False
    strategy: str
    strategy_params: dict = Field(default_factory=dict)


@router.get("/strategies")
async def list_all() -> dict:
    return {"strategies": list_strategies()}


@router.post("/run")
async def run_backtest(req: RunBacktestRequest) -> dict:
    cfg_symbols = get_symbols_config().get("symbols", {})
    sym_info = cfg_symbols.get(req.symbol)
    if sym_info is None:
        raise HTTPException(404, detail=f"未知品种: {req.symbol}")

    try:
        get_strategy(req.strategy)
    except KeyError as e:
        raise HTTPException(400, detail=str(e))

    tick_size = req.tick_size if req.tick_size is not None else sym_info.get("tick_size", 1.0)
    tick_value = req.tick_value if req.tick_value is not None else sym_info.get("tick_value", 10.0)
    margin_rate = req.margin_rate if req.margin_rate is not None else sym_info.get("margin_rate", 0.10)
    fee_per_lot = req.fee_per_lot if req.fee_per_lot is not None else sym_info.get("fee_per_lot", 3.0)

    contract = req.contract or sym_info.get("mootdx_code")
    market = sym_info.get("mootdx_market")
    if market is None or not contract:
        raise HTTPException(400, detail="品种缺少 mootdx_market / mootdx_code 配置")

    # 有日期区间时不截断，让区间内全部 K 线进回测；无区间时保留 5000 根作为兜底上限
    has_date = bool(req.start_date or req.end_date)
    df = fetch_kline_by_date(
        market=market, symbol=contract, period=req.period,
        start_date=req.start_date, end_date=req.end_date,
        count=None if has_date else 5000,
    )
    if df.empty:
        raise HTTPException(404, detail="回测区间内无 K 线数据")

    cfg = BacktestConfig(
        symbol=req.symbol, contract=contract, period=req.period,
        start_date=req.start_date, end_date=req.end_date,
        initial_capital=req.initial_capital,
        tick_size=float(tick_size), tick_value=float(tick_value),
        margin_rate=float(margin_rate), fee_per_lot=float(fee_per_lot),
        slippage_ticks=req.slippage_ticks, intraday_only=req.intraday_only,
        strategy=req.strategy, strategy_params=req.strategy_params,
    )
    result = BacktestEngine(cfg, df).run()

    return {
        "config": asdict(cfg),
        "bars": [asdict(b) for b in result.bars],
        "fills": [asdict(f) for f in result.fills],
        "trades": [asdict(t) for t in result.trades],
        "equity_curve": [asdict(p) for p in result.equity_curve],
        "metrics": result.metrics,
        "liquidated": result.liquidated,
        "liquidated_at": result.liquidated_at,
    }
