"""FastAPI 子路由：/api/backtest/*"""
from __future__ import annotations

from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import backtest.strategies  # noqa: F401  触发内置策略注册

from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig
from backtest.registry import get_strategy, get_strategy_params, list_strategies
from config import get_symbols_config
from data_provider import VALID_PERIODS, fetch_kline_by_date, fetch_stock_kline_by_date


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

    # 股票专用参数（前端可传，期货时忽略）
    commission_rate: Optional[float] = None
    stamp_tax_rate: Optional[float] = None
    transfer_fee_rate: Optional[float] = None
    lot_size: Optional[int] = None


@router.get("/strategies")
async def list_all() -> dict:
    strategies = []
    for name in list_strategies():
        strategies.append({"name": name, "params": get_strategy_params(name)})
    return {"strategies": strategies}


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

    if req.period not in VALID_PERIODS:
        raise HTTPException(400, detail=f"不支持的周期: {req.period}，可选值: {sorted(VALID_PERIODS)}")

    market_type = sym_info.get("market_type", "futures")
    is_stock = market_type == "stock"

    # --- 根据品种类型解析参数默认值 ---
    tick_size = req.tick_size if req.tick_size is not None else sym_info.get("tick_size", 1.0)
    tick_value = req.tick_value if req.tick_value is not None else sym_info.get("tick_value", 10.0)
    margin_rate = req.margin_rate if req.margin_rate is not None else sym_info.get("margin_rate", 0.10)
    fee_per_lot = req.fee_per_lot if req.fee_per_lot is not None else sym_info.get("fee_per_lot", 3.0)
    commission_rate = req.commission_rate if req.commission_rate is not None else sym_info.get("commission_rate", 0.00025)
    stamp_tax_rate = req.stamp_tax_rate if req.stamp_tax_rate is not None else sym_info.get("stamp_tax_rate", 0.001)
    transfer_fee_rate = req.transfer_fee_rate if req.transfer_fee_rate is not None else sym_info.get("transfer_fee_rate", 0.00001)
    lot_size = req.lot_size if req.lot_size is not None else sym_info.get("lot_size", 100)

    if is_stock:
        # --- 股票回测数据源 ---
        code = sym_info.get("code", "")
        stock_code = code.split(".")[1] if "." in code else code
        exchange = code.split(".")[0].lower() if "." in code else "sh"

        has_date = bool(req.start_date or req.end_date)
        df = fetch_stock_kline_by_date(
            exchange=exchange, symbol=stock_code, period=req.period,
            start_date=req.start_date, end_date=req.end_date,
            count=None if has_date else 5000,
        )
        if df.empty:
            raise HTTPException(404, detail="回测区间内无 A 股 K 线数据")

        cfg = BacktestConfig(
            symbol=req.symbol, contract=code, period=req.period,
            start_date=req.start_date, end_date=req.end_date,
            initial_capital=req.initial_capital,
            tick_size=float(tick_size), tick_value=float(tick_value),
            margin_rate=float(margin_rate), fee_per_lot=float(fee_per_lot),
            slippage_ticks=req.slippage_ticks, intraday_only=False,
            strategy=req.strategy, strategy_params=req.strategy_params,
            instrument_type="stock",
            commission_rate=float(commission_rate),
            stamp_tax_rate=float(stamp_tax_rate),
            transfer_fee_rate=float(transfer_fee_rate),
            lot_size=int(lot_size),
        )
    else:
        # --- 期货回测数据源（原逻辑） ---
        contract = req.contract or sym_info.get("mootdx_code")
        market = sym_info.get("mootdx_market")
        if market is None or not contract:
            raise HTTPException(400, detail="品种缺少 mootdx_market / mootdx_code 配置")

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
