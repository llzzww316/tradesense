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
    if not info:
        return None, None, None
    return info.get("mootdx_market"), info.get("mootdx_code"), symbol


def resolve_symbol_code(symbol: str) -> str | None:
    """兼容旧 mcp_server.resolve_symbol：返回 'SHFE.RB' 这种代码；显式 'XX.YY' 原样返回。"""
    cfg = get_symbols_config().get("symbols", {})
    if symbol in cfg:
        return cfg[symbol]["code"]
    if isinstance(symbol, str) and "." in symbol:
        return symbol
    return None
