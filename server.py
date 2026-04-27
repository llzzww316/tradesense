"""
TradeSense Backend - mootdx K线数据服务
支持5分钟K线显示 + 1分钟步进回放
"""
import json
import traceback
import pandas as pd
from pathlib import Path
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from data_provider import fetch_replay_data, PERIOD_MINUTES

app = FastAPI()

# 允许前端访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 配置文件路径
CONFIG_FILE = Path(__file__).parent / "symbols.json"


def load_symbols_config():
    """从配置文件加载品种信息"""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config
    except Exception as e:
        print(f"加载配置文件失败: {e}")
        return {"symbols": {}}


# 加载配置
SYMBOLS_CONFIG = load_symbols_config()

# 常用品种代码（从配置文件读取）
COMMON_SYMBOLS = {name: info["code"] for name, info in SYMBOLS_CONFIG.get("symbols", {}).items()}
TICK_VALUES = {name: info["tick_value"] for name, info in SYMBOLS_CONFIG.get("symbols", {}).items()}

# mootdx 映射
MOOTDX_MARKETS = {name: info.get("mootdx_market") for name, info in SYMBOLS_CONFIG.get("symbols", {}).items()}
MOOTDX_CODES = {name: info.get("mootdx_code") for name, info in SYMBOLS_CONFIG.get("symbols", {}).items()}


def resolve_symbol(symbol: str) -> tuple:
    """
    解析品种代码，支持中文名。
    返回: (mootdx_market, mootdx_code, display_name) 或 (None, None, None)
    """
    if symbol in COMMON_SYMBOLS:
        return (
            MOOTDX_MARKETS.get(symbol),
            MOOTDX_CODES.get(symbol),
            symbol,
        )
    return None, None, None


def calculate_ema(closes, period):
    """计算EMA"""
    if len(closes) < period:
        return pd.Series([None] * len(closes), index=closes.index)
    return closes.ewm(span=period, adjust=False).mean()


@app.get("/symbols")
async def get_symbols():
    """返回支持的品种列表"""
    return {
        "symbols": SYMBOLS_CONFIG.get("symbols", {}),
        "usage": "可以使用中文名",
    }


@app.get("/search_symbols")
async def search_symbols(q: str = Query("", description="搜索关键词")):
    """模糊搜索品种"""
    q = q.lower()
    results = []
    for name, info in SYMBOLS_CONFIG.get("symbols", {}).items():
        code = info["code"]
        if q in name.lower() or q in code.lower():
            results.append({
                "name": name,
                "code": code,
                "tick_value": info.get("tick_value", 10),
            })
    return {"results": results}


@app.get("/replay_data")
async def get_replay_data(
    symbol: str = Query(..., description="品种名称，如：螺纹钢、PVC"),
    display_period: str = Query("5m", description="显示周期"),
    step_period: str = Query("1m", description="步进周期（回放用）"),
    count: int = Query(2000, description="K线数量，最大2000"),
    ma_period: int = Query(20, description="EMA周期"),
    start_date: str = Query(None, description="开始日期，如2025-10-01"),
    end_date: str = Query(None, description="结束日期，如2026-04-01"),
    range_start: str = Query(
        None,
        description="与 range_end 同时传入时按显示 K 线 bob 时间窗截取，格式 2026-03-25 13:35:00",
    ),
    range_end: str = Query(None, description="显示周期 K 线 bob 上界（含）"),
):
    """
    获取回放数据：显示周期K线 + 步进周期数据
    """
    market_id, mootdx_code, display_name = resolve_symbol(symbol)
    if market_id is None or mootdx_code is None:
        return {"error": f"未知品种: {symbol}，请使用中文名（如 螺纹钢、PVC）"}

    try:
        result = fetch_replay_data(
            market=market_id,
            symbol=mootdx_code,
            display_period=display_period,
            step_period=step_period,
            count=count,
            start_date=start_date,
            end_date=end_date,
            range_start=range_start,
            range_end=range_end,
        )

        if "error" in result:
            return result

        display_bars = result["display"]
        step_bars = result["step"]

        if display_bars.empty:
            return {"error": "No display period data"}
        if step_bars.empty:
            return {"error": "No step period data"}

        # 计算显示周期EMA
        display_bars["ema"] = calculate_ema(display_bars["close"], ma_period)
        display_bars["time"] = display_bars["bob"].dt.strftime("%Y-%m-%d %H:%M:%S")
        step_bars["time"] = step_bars["bob"].dt.strftime("%Y-%m-%d %H:%M:%S")

        # 构建显示K线列表
        display_data = []
        for _, row in display_bars.iterrows():
            display_data.append({
                "time": row["time"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "ema": float(row["ema"]) if pd.notna(row["ema"]) else None,
            })

        # 构建步进K线列表
        step_data = []
        for _, row in step_bars.iterrows():
            step_data.append({
                "time": row["time"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            })

        return {
            "symbol": symbol,
            "symbol_code": COMMON_SYMBOLS.get(symbol, symbol),
            "display": display_data,
            "step": step_data,
            "displayPeriod": display_period,
            "stepPeriod": step_period,
            "maPeriod": ma_period,
        }

    except Exception as e:
        traceback.print_exc()
        return {"error": f"Internal server error: {str(e)}"}


if __name__ == "__main__":
    print("TradeSense Backend starting on http://localhost:8765")
    uvicorn.run(app, host="0.0.0.0", port=8765)
