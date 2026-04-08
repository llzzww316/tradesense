"""
TradeSense MCP Server
让 AI 能直接调用掘金数据（通过后端 API）
"""
import json
import traceback
import httpx
from pathlib import Path
from typing import Any
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# 创建 MCP Server 实例
app = Server("tradesense")

# 后端 API 地址
API_BASE = "http://127.0.0.1:8765"

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


def resolve_symbol(symbol: str) -> str:
    """解析品种代码，支持中文名或直接代码"""
    if symbol in COMMON_SYMBOLS:
        return COMMON_SYMBOLS[symbol]
    if "." in symbol:
        return symbol
    return None


@app.list_tools()
async def list_tools() -> list[Tool]:
    """列出所有可用工具"""
    return [
        Tool(
            name="get_symbols",
            description="获取支持的交易品种列表（常用品种代码参考）",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_klines",
            description="获取 K 线数据，返回 OHLCV 和 EMA。支持中文名（如：螺纹钢、PVC）或品种代码（如：SHFE.rb2610）",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "品种名称或代码，如：螺纹钢、PVC、SHFE.rb2610",
                    },
                    "period": {
                        "type": "string",
                        "description": "K线周期：1m, 5m, 15m, 30m, 60m, 1d",
                        "default": "5m",
                    },
                    "count": {
                        "type": "integer",
                        "description": "K线数量，默认100",
                        "default": 100,
                    },
                    "ma_period": {
                        "type": "integer",
                        "description": "EMA周期，默认20",
                        "default": 20,
                    },
                },
                "required": ["symbol"],
            },
        ),
        Tool(
            name="get_latest_price",
            description="获取最新价格。支持中文名或品种代码",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "品种名称或代码",
                    },
                },
                "required": ["symbol"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """执行工具调用"""
    try:
        if name == "get_symbols":
            result = await handle_get_symbols()
        elif name == "get_klines":
            result = await handle_get_klines(arguments)
        elif name == "get_latest_price":
            result = await handle_get_latest_price(arguments)
        else:
            result = {"error": f"Unknown tool: {name}"}
        
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
    
    except Exception as e:
        traceback.print_exc()
        return [TextContent(type="text", text=json.dumps({"error": str(e)}, ensure_ascii=False))]


async def handle_get_symbols() -> dict:
    """获取品种列表"""
    return {
        "symbols": SYMBOLS_CONFIG.get("symbols", {}),
        "usage": "可以使用中文名（如：螺纹钢）或直接使用代码（如：SHFE.rb2610）",
    }


async def handle_get_klines(args: dict) -> dict:
    """获取 K 线数据（通过后端 API）"""
    symbol_input = args.get("symbol")
    period = args.get("period", "5m")
    count = args.get("count", 100)
    ma_period = args.get("ma_period", 20)
    
    # 解析品种代码
    symbol_code = resolve_symbol(symbol_input)
    if symbol_code is None:
        return {"error": f"未知品种: {symbol_input}，请使用中文名或代码格式（如 SHFE.rb2610）"}
    
    # 调用后端 API
    async with httpx.AsyncClient(timeout=30) as client:
        params = {
            "symbol": symbol_input,
            "display_period": period,
            "step_period": "1m",
            "count": count,
            "ma_period": ma_period,
        }
        resp = await client.get(f"{API_BASE}/replay_data", params=params)
        data = resp.json()
    
    if "error" in data:
        return {"error": data["error"]}
    
    # 返回显示周期的 K 线
    display_bars = data.get("display", [])
    klines = display_bars[-count:] if len(display_bars) > count else display_bars
    
    return {
        "symbol": symbol_input,
        "symbol_code": symbol_code,
        "period": period,
        "count": len(klines),
        "klines": klines,
    }


async def handle_get_latest_price(args: dict) -> dict:
    """获取最新价格（通过后端 API）"""
    symbol_input = args.get("symbol")
    
    symbol_code = resolve_symbol(symbol_input)
    if symbol_code is None:
        return {"error": f"未知品种: {symbol_input}"}
    
    # 调用后端 API 获取 1 分钟数据
    async with httpx.AsyncClient(timeout=30) as client:
        params = {
            "symbol": symbol_input,
            "display_period": "1m",
            "step_period": "1m",
            "count": 1,
        }
        resp = await client.get(f"{API_BASE}/replay_data", params=params)
        data = resp.json()
    
    if "error" in data:
        return {"error": data["error"]}
    
    display_bars = data.get("display", [])
    if not display_bars:
        return {"error": "获取数据失败"}
    
    latest = display_bars[-1]
    return {
        "symbol": symbol_input,
        "symbol_code": symbol_code,
        "price": latest["close"],
        "time": latest["time"],
    }


async def main():
    """启动 MCP Server"""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
