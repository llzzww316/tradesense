"""
TradeSense Backend - 掘金K线数据服务
支持5分钟K线显示 + 1分钟步进回放
"""
import json
import traceback
import pandas as pd
from pathlib import Path
from gm.api import history, set_token, set_serv_addr
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI()

# 允许前端访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Token from MEMORY.md
TOKEN = "4f55349af303c29b273eab9e9f257c39c47177b8"

# 初始化掘金连接
set_token(TOKEN)
set_serv_addr("127.0.0.1:7001")

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

# 数据权限范围：最近180天，最早不早于 2025-10-10
DATA_START = "2025-10-10"


def resolve_symbol(symbol: str) -> str:
    """解析品种代码，支持中文名或直接代码"""
    if symbol in COMMON_SYMBOLS:
        return COMMON_SYMBOLS[symbol]
    if "." in symbol:
        return symbol
    return None


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
        "usage": "可以使用中文名或直接使用代码",
    }


@app.get("/search_symbols")
async def search_symbols(q: str = Query("", description="搜索关键词")):
    """模糊搜索品种"""
    q = q.lower()
    results = []
    for name, info in SYMBOLS_CONFIG.get("symbols", {}).items():
        code = info["code"]
        if q in name.lower() or q in code.lower():
            results.append({"name": name, "code": code, "tick_value": info.get("tick_value", 10)})
    return {"results": results}


@app.get("/replay_data")
async def get_replay_data(
    symbol: str = Query(..., description="品种名称或代码，如：螺纹钢、SHFE.rb2610"),
    display_period: str = Query("5m", description="显示周期"),
    step_period: str = Query("1m", description="步进周期（回放用）"),
    count: int = Query(2000, description="K线数量，最大2000"),
    ma_period: int = Query(20, description="EMA周期"),
    start_date: str = Query(None, description="开始日期，如2025-10-01"),
    end_date: str = Query(None, description="结束日期，如2026-04-01"),
):
    """
    获取回放数据：5分钟显示K线 + 1分钟步进数据
    用于支持"按1分钟走，但显示5分钟K线结构"的回放
    """
    # 解析品种代码
    symbol_code = resolve_symbol(symbol)
    if symbol_code is None:
        return {"error": f"未知品种: {symbol}，请使用中文名或代码格式（如 SHFE.rb2610）"}

    period_map = {
        "1m": "1m", "5m": "5m", "15m": "15m",
        "30m": "30m", "60m": "60m", "1h": "60m", "4h": "240m", "1d": "1d",
    }

    display_gm = period_map.get(display_period, "5m")
    step_gm = period_map.get(step_period, "1m")

    try:
        # 确定日期范围
        start_time = start_date if start_date else DATA_START
        end_time = end_date if end_date else "2030-12-31"

        # 获取显示周期K线（用于结构）
        display_bars = history(
            symbol=symbol_code,
            start_time=start_time,
            end_time=end_time,
            df=True,
            adjust=1,
            frequency=display_gm,
            fields="bob,open,high,low,close,volume",
        )

        # 获取步进周期K线（用于回放）
        step_bars = history(
            symbol=symbol_code,
            start_time=start_time,
            end_time=end_time,
            df=True,
            adjust=1,
            frequency=step_gm,
            fields="bob,open,high,low,close,volume",
        )

        if display_bars is None or display_bars.empty:
            return {"error": "No display period data"}
        if step_bars is None or step_bars.empty:
            return {"error": "No step period data"}

        # 计算显示周期EMA
        display_bars["ema"] = calculate_ema(display_bars["close"], ma_period)
        display_bars["time"] = display_bars["bob"].dt.strftime("%Y-%m-%d %H:%M:%S")
        display_bars = display_bars.tail(count)
        # 计算步进数据条数：确保可覆盖 display 的完整时间范围
        period_minutes = {
            "1m": 1,
            "5m": 5,
            "15m": 15,
            "30m": 30,
            "60m": 60,
            "1h": 60,
            "4h": 240,
            "1d": 1440,
        }
        display_minutes = period_minutes.get(display_period, 5)
        step_minutes = period_minutes.get(step_period, 1)
        multiplier = max(1, display_minutes // step_minutes)
        step_bars = step_bars.tail(count * multiplier)

        # 按时间升序排序（从旧到新）
        display_bars = display_bars.sort_values("bob")
        step_bars = step_bars.sort_values("bob")

        # 步进数据：1分钟K线
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

        # 构建步进K线列表（1分钟）
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
            "symbol_code": symbol_code,
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
