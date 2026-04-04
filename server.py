"""
TradeSense Backend - 掘金K线数据服务
支持5分钟K线显示 + 1分钟步进回放
"""
import json
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

# 支持的品种
SYMBOLS = {
    "螺纹钢": "SHFE.rb2605",
    "PVC": "DCE.v2605",
}

# 数据权限范围：最近180天，最早不早于 2025-10-06
DATA_START = "2025-10-06"


def calculate_ema(closes, period):
    """计算EMA"""
    import pandas as pd
    if len(closes) < period:
        return pd.Series([None] * len(closes), index=closes.index)
    return closes.ewm(span=period, adjust=False).mean()


@app.get("/symbols")
async def get_symbols():
    """返回支持的品种列表"""
    return {"symbols": list(SYMBOLS.keys())}


@app.get("/bars")
async def get_bars(
    symbol: str = Query(..., description="品种名称，如螺纹钢"),
    period: str = Query("5m", description="显示周期，如5m, 1m"),
    count: int = Query(500, description="K线数量，最大1000"),
):
    """获取K线数据（用于显示）"""
    if symbol not in SYMBOLS:
        return {"error": f"Unknown symbol: {symbol}"}
    
    # 周期映射
    period_map = {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "60m",
        "4h": "240m",
        "1d": "1d",
    }
    gm_period = period_map.get(period, "5m")
    
    try:
        bars = history(
            symbol=SYMBOLS[symbol],
            start_time=DATA_START,
            end_time="2026-04-04",
            df=True,
            adjust=1,
            frequency=gm_period,
            fields="bob,open,high,low,close,volume",
        )
        
        if bars is None or bars.empty:
            return {"error": "No data"}
        
        bars["time"] = bars["bob"].dt.strftime("%Y-%m-%d %H:%M:%S")
        bars = bars.tail(count)
        
        # 按时间升序排序（从旧到新）
        bars = bars.sort_values("bob")
        
        data = []
        for _, row in bars.iterrows():
            data.append({
                "time": row["time"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]) if "volume" in row else 0,
            })
        
        return {"bars": data, "symbol": symbol, "period": period}
    
    except Exception as e:
        return {"error": str(e)}


@app.get("/replay_data")
async def get_replay_data(
    symbol: str = Query(..., description="品种名称"),
    display_period: str = Query("5m", description="显示周期"),
    step_period: str = Query("1m", description="步进周期（回放用）"),
    count: int = Query(500, description="K线数量，最大500"),
    ma_period: int = Query(20, description="EMA周期"),
):
    """
    获取回放数据：5分钟显示K线 + 1分钟步进数据
    用于支持"按1分钟走，但显示5分钟K线结构"的回放
    """
    import pandas as pd
    
    if symbol not in SYMBOLS:
        return {"error": f"Unknown symbol: {symbol}"}
    
    period_map = {
        "1m": "1m", "5m": "5m", "15m": "15m",
        "30m": "30m", "1h": "60m", "4h": "240m", "1d": "1d",
    }
    
    display_gm = period_map.get(display_period, "5m")
    step_gm = period_map.get(step_period, "1m")
    
    try:
        # 获取显示周期K线（用于结构）
        display_bars = history(
            symbol=SYMBOLS[symbol],
            start_time=DATA_START,
            end_time="2026-04-04",
            df=True,
            adjust=1,
            frequency=display_gm,
            fields="bob,open,high,low,close,volume",
        )
        
        # 获取步进周期K线（用于回放）
        step_bars = history(
            symbol=SYMBOLS[symbol],
            start_time=DATA_START,
            end_time="2026-04-04",
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
        step_bars = step_bars.tail(count * 5 if display_period == "5m" else count)  # 5分钟≈5根1分钟
        
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
            "display": display_data,
            "step": step_data,
            "displayPeriod": display_period,
            "stepPeriod": step_period,
            "maPeriod": ma_period,
        }
    
    except Exception as e:
        return {"error": str(e)}


@app.get("/ema")
async def get_ema(
    symbol: str = Query(..., description="品种名称"),
    period: str = Query("5m", description="周期"),
    count: int = Query(500, description="K线数量"),
    ma_period: int = Query(20, description="EMA周期"),
):
    """计算EMA指标"""
    import pandas as pd
    
    if symbol not in SYMBOLS:
        return {"error": f"Unknown symbol: {symbol}"}
    
    period_map = {
        "1m": "1m", "5m": "5m", "15m": "15m",
        "30m": "30m", "1h": "60m", "4h": "240m", "1d": "1d",
    }
    gm_period = period_map.get(period, "5m")
    
    try:
        bars = history(
            symbol=SYMBOLS[symbol],
            start_time=DATA_START,
            end_time="2026-04-04",
            df=True,
            adjust=1,
            frequency=gm_period,
            fields="bob,open,high,low,close,volume",
        )
        
        if bars is None or bars.empty:
            return {"error": "No data"}
        
        bars["ema"] = bars["close"].ewm(span=ma_period, adjust=False).mean()
        bars["time"] = bars["bob"].dt.strftime("%Y-%m-%d %H:%M:%S")
        bars = bars.tail(count)
        
        data = []
        for _, row in bars.iterrows():
            if pd.notna(row["ema"]):
                data.append({
                    "time": row["time"],
                    "value": float(row["ema"]),
                })
        
        return {"ema": data, "period": ma_period}
    
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    print("TradeSense Backend starting on http://localhost:8765")
    uvicorn.run(app, host="0.0.0.0", port=8765)
