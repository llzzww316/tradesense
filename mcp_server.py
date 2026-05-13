"""
TradeSense MCP Server — stdio 单进程模式
直接调用 kline_service 读取本机通达信 VIPDOC，无需先启动 HTTP 后端。
"""
import json
import logging
import sys
from typing import Any
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from config import get_symbols_config, resolve_symbol_code
import kline_service as svc

logger = logging.getLogger(__name__)

app = Server("tradesense")


@app.list_tools()
async def list_tools() -> list[Tool]:
    """列出所有可用工具"""
    return [
        Tool(
            name="get_symbols",
            description="获取支持的交易品种列表（常用品种代码参考）",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_contracts",
            description="列出某品种本机 vipdoc 下可用的通达信合约代码",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "品种中文名，如：螺纹钢"},
                },
                "required": ["symbol"],
            },
        ),
        Tool(
            name="get_klines",
            description="获取 K 线数据，返回 OHLCV 和 EMA。可选 contract（本机 vipdoc）。",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "品种名称或代码，如：螺纹钢、PVC、SHFE.rb2610",
                    },
                    "contract": {
                        "type": "string",
                        "description": "可选；通达信合约代码如 RB2610、V2609。不传则用 symbols.json 默认 mootdx_code",
                    },
                    "period": {
                        "type": "string",
                        "description": "K线周期：1m, 5m, 15m, 30m, 60m, 1d",
                        "default": "5m",
                    },
                    "count": {
                        "type": "integer",
                        "description": "K线数量上限，默认100；若传入 start_date/end_date，内部会按上限 2000 放大请求以免截断区间",
                        "default": 100,
                    },
                    "ma_period": {
                        "type": "integer",
                        "description": "EMA周期，默认20",
                        "default": 20,
                    },
                    "start_date": {"type": "string", "description": "开始日期，如 2026-03-10"},
                    "end_date": {"type": "string", "description": "结束日期，如 2026-03-12"},
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
            description="获取最新价格。支持中文名或品种代码；可选 contract 指定具体合约代码",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "品种名称或代码"},
                    "contract": {
                        "type": "string",
                        "description": "可选；合约代码（如 RB2610），与 get_klines 含义相同",
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
            result = _tool_get_symbols()
        elif name == "get_contracts":
            result = _tool_get_contracts(arguments)
        elif name == "get_klines":
            result = _tool_get_klines(arguments)
        elif name == "get_latest_price":
            result = _tool_get_latest_price(arguments)
        else:
            result = {"error": f"Unknown tool: {name}"}
    except svc.ServiceError as e:
        result = {"error": str(e)}
    except Exception as e:
        logger.exception("工具调用失败: %s", name)
        result = {"error": str(e)}

    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]


def _tool_get_symbols() -> dict:
    return {
        "symbols": get_symbols_config().get("symbols", {}),
        "usage": "可以使用中文名（如：螺纹钢）或直接使用代码（如：SHFE.rb2610）",
    }


def _tool_get_contracts(args: dict) -> dict:
    symbol = args.get("symbol")
    if not symbol:
        return {"error": "symbol 必填"}
    return svc.list_contracts(symbol)


def _tool_get_klines(args: dict) -> dict:
    symbol_input = args.get("symbol")
    if not symbol_input:
        return {"error": "symbol 必填"}

    period = args.get("period", "5m")
    count = args.get("count", 100)
    ma_period = args.get("ma_period", 20)
    start_date = args.get("start_date")
    end_date = args.get("end_date")
    range_start = args.get("range_start")
    range_end = args.get("range_end")
    contract_opt = args.get("contract")

    if (range_start and not range_end) or (range_end and not range_start):
        return {"error": "range_start 与 range_end 必须同时传入"}

    # replay_payload 在「仅有日历区间、无 range」时仍会对结果 tail(count)；拉满上限以免截断区间
    has_calendar = bool(start_date or end_date)
    has_range_pair = bool(range_start and range_end)
    try:
        request_count = int(count)
    except (TypeError, ValueError):
        request_count = 100
    if has_calendar and not has_range_pair:
        request_count = 2000

    data = svc.get_replay_payload(
        symbol=symbol_input,
        contract=str(contract_opt).strip() if contract_opt else None,
        display_period=period,
        step_period="1m",
        count=request_count,
        ma_period=ma_period,
        start_date=start_date,
        end_date=end_date,
        range_start=range_start,
        range_end=range_end,
    )

    display_bars = data.get("display", [])
    if has_calendar or has_range_pair:
        klines = display_bars
    else:
        try:
            cap = int(count)
        except (TypeError, ValueError):
            cap = 100
        klines = display_bars[-cap:] if len(display_bars) > cap else display_bars

    symbol_code = resolve_symbol_code(symbol_input) or data.get("symbol_code", symbol_input)
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
    if data.get("contract"):
        out["contract"] = data["contract"]
    return out


def _tool_get_latest_price(args: dict) -> dict:
    symbol_input = args.get("symbol")
    if not symbol_input:
        return {"error": "symbol 必填"}
    contract_opt = args.get("contract")

    symbol_code = resolve_symbol_code(symbol_input)
    if symbol_code is None:
        return {"error": f"未知品种: {symbol_input}"}

    data = svc.get_replay_payload(
        symbol=symbol_input,
        contract=str(contract_opt).strip() if contract_opt else None,
        display_period="1m",
        step_period="1m",
        count=1,
    )
    display_bars = data.get("display", [])
    if not display_bars:
        return {"error": "获取数据失败"}

    latest = display_bars[-1]
    out = {
        "symbol": symbol_input,
        "symbol_code": symbol_code,
        "price": latest["close"],
        "time": latest["time"],
    }
    if data.get("contract"):
        out["contract"] = data["contract"]
    return out


async def main():
    """启动 MCP Server（stdio）"""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    # stdio 模式下 stdout 被 MCP 占用，日志必须走 stderr
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stderr,
    )
    asyncio.run(main())
