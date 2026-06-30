"""
配置热加载层：以 symbols.json 的 mtime 为准，文件一改下次读取自动生效。
server.py / mcp_server.py 通过 get_symbols_config() 访问，避免各自持有冻结的 dict。
"""
import json
import logging
from pathlib import Path
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

CONFIG_FILE = Path(__file__).resolve().parent / "symbols.json"

_cache: dict[str, Any] = {"mtime": None, "data": {"symbols": {}}}
_lock = Lock()


def _read_from_disk() -> dict:
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.exception("加载配置文件失败: %s", CONFIG_FILE)
        return {"symbols": {}}


def get_symbols_config(force: bool = False) -> dict:
    """返回最新 symbols 配置；若 symbols.json mtime 变化则重读，force=True 强制重读。"""
    try:
        mtime = CONFIG_FILE.stat().st_mtime
    except FileNotFoundError:
        mtime = None

    with _lock:
        if force or _cache["mtime"] != mtime:
            _cache["data"] = _read_from_disk()
            _cache["mtime"] = mtime
            logger.info("symbols.json 已加载: %s 项", len(_cache["data"].get("symbols", {})))
        return _cache["data"]


def resolve_symbol(symbol: str) -> tuple:
    """返回 (mootdx_market, mootdx_code, display_name)；未知品种返回 (None, None, None)。"""
    cfg = get_symbols_config().get("symbols", {})
    info = cfg.get(symbol)
    if info:
        return info.get("mootdx_market"), info.get("mootdx_code"), symbol

    # 模糊匹配：按代码（600519 / SH.600519）或按中文名子串
    for name, info in cfg.items():
        code = info.get("code", "").lower()
        sym = symbol.lower()
        if code == sym or code.replace(".", "") == sym or sym in code or code in sym:
            return info.get("mootdx_market"), info.get("mootdx_code"), name
        if symbol in name or name in symbol:
            return info.get("mootdx_market"), info.get("mootdx_code"), name
    return None, None, None


def resolve_symbol_code(symbol: str) -> str | None:
    """兼容旧 mcp_server.resolve_symbol：返回 'SHFE.RB' 这种代码；显式 'XX.YY' 原样返回。"""
    cfg = get_symbols_config().get("symbols", {})
    if symbol in cfg:
        return cfg[symbol]["code"]
    # 模糊匹配
    for name, info in cfg.items():
        code = info.get("code", "").lower()
        sym = symbol.lower()
        if code == sym or code.replace(".", "") == sym or sym in code or code in sym:
            return info["code"]
        if symbol in name or name in symbol:
            return info["code"]
    if isinstance(symbol, str) and "." in symbol:
        return symbol
    return None


def get_market_type(symbol: str) -> str | None:
    """返回 'futures' 或 'stock'，未知品种返回 None。"""
    cfg = get_symbols_config().get("symbols", {})
    info = cfg.get(symbol)
    if info:
        return info.get("market_type")
    for name, info in cfg.items():
        code = info.get("code", "").lower()
        sym = symbol.lower()
        if code == sym or code.replace(".", "") == sym or sym in code or code in sym:
            return info.get("market_type")
        if symbol in name or name in symbol:
            return info.get("market_type")
    return None


def get_stock_exchange_and_code(symbol: str) -> tuple[str, str] | None:
    """A 股品种返回 (exchange, code)，如 ('sh', '600519')；非股票或未知返回 None。"""
    cfg = get_symbols_config().get("symbols", {})
    info = cfg.get(symbol)
    if info and info.get("market_type") == "stock":
        code_str = info.get("code", "")
        if "." in code_str:
            exchange, stock_code = code_str.split(".", 1)
            return exchange.lower(), stock_code

    # 模糊匹配
    for name, info in cfg.items():
        if info.get("market_type") != "stock":
            continue
        code = info.get("code", "").lower()
        sym = symbol.lower()
        if code == sym or code.replace(".", "") == sym or sym in code or code in sym:
            exchange, stock_code = code.split(".", 1)
            return exchange.lower(), stock_code
        if symbol in name or name in symbol:
            code_str = info.get("code", "")
            if "." in code_str:
                exchange, stock_code = code_str.split(".", 1)
                return exchange.lower(), stock_code
    return None
