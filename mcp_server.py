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
            description="获取 K 线数据，返回 OHLCV 和 EMA。支持中文名或品种代码。可选 start_date/end_date（日历区间）或 range_start/range_end（精确到秒的显示 K 线时间窗，须成对）；与后端 /replay_data 一致。",
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
                        "description": "K线数量上限，默认100；若传入 start_date/end_date，内部会按后端上限放大请求以免截断区间",
                        "default": 100,
                    },
                    "ma_period": {
                        "type": "integer",
                        "description": "EMA周期，默认20",
                        "default": 20,
                    },
                    "start_date": {
                        "type": "string",
                        "description": "开始日期（与 replay_data 一致），如 2026-03-10；可与 end_date 组合限定日历区间",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "结束日期，如 2026-03-12",
                    },
                    "range_start": {
                        "type": "string",
                        "description": "显示周期 K 线时间窗下界，须与 range_end 同时传入，格式如 2026-03-10 09:00:00",
                    },
                    "range_end": {
                        "type": "string",
                        "description": "显示周期 K 线时间窗上界（含），须与 range_start 同时传入",
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
    start_date = args.get("start_date")
    end_date = args.get("end_date")
    range_start = args.get("range_start")
    range_end = args.get("range_end")

    if (range_start and not range_end) or (range_end and not range_start):
        return {"error": "range_start 与 range_end 必须同时传入"}

    # 解析品种代码
    symbol_code = resolve_symbol(symbol_input)
    if symbol_code is None:
        return {"error": f"未知品种: {symbol_input}，请使用中文名或代码格式（如 SHFE.rb2610）"}

    # replay_data 在「仅有日历区间、无 range」时仍会对结果 tail(count)；拉满后端上限以免截断区间
    has_calendar = bool(start_date or end_date)
    has_range_pair = bool(range_start and range_end)
    request_count = count
    if has_calendar and not has_range_pair:
        try:
            request_count = max(int(count), 2000)
        except (TypeError, ValueError):
            request_count = 2000
        request_count = min(request_count, 2000)

    # 调用后端 API
    async with httpx.AsyncClient(timeout=30) as client:
        params: dict[str, Any] = {
            "symbol": symbol_input,
            "display_period": period,
            "step_period": "1m",
            "count": request_count,
            "ma_period": ma_period,
        }
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if range_start:
            params["range_start"] = range_start
        if range_end:
            params["range_end"] = range_end
        resp = await client.get(f"{API_BASE}/replay_data", params=params)
        data = resp.json()
    
    if "error" in data:
        return {"error": data["error"]}

    # 返回显示周期的 K 线
    display_bars = data.get("display", [])
    if has_calendar or has_range_pair:
        klines = display_bars
    else:
        try:
            cap = int(count)
        except (TypeError, ValueError):
            cap = 100
        klines = display_bars[-cap:] if len(display_bars) > cap else display_bars

    out: dict[str, Any] = {
        "symbol": symbol_input,
        "symbol_code": symbol_code,
        "period": period,
        "count": len(klines),
        "klines": klines,
    }
    if start_date:
        out["start_date"] = start_date
    if end_date:
        out["end_date"] = end_date
    if range_start:
        out["range_start"] = range_start
    if range_end:
        out["range_end"] = range_end
    return out


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
