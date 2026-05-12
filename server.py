"""
TradeSense Backend — FastAPI K 线回放服务
数据：`data_provider`（tdxpy 读取本机通达信 VIPDOC）。
同源交付：REST 在 `/api`，`frontend/` 静态页由本进程提供；`uv run python server.py` 后访问 http://localhost:8765/ 即可。
"""
import logging
from pathlib import Path
from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn

from config import get_symbols_config
import kline_service as svc

logger = logging.getLogger(__name__)

app = FastAPI(
    title="TradeSense",
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
    redoc_url=None,
)

FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"

# 允许前端访问（本地工具场景：通配源但不带凭据，避开浏览器的 * + credentials 限制）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- 业务异常 → HTTP 状态码 映射 ----
_ERROR_STATUS = {
    svc.UnknownSymbolError: 404,
    svc.ContractNotFoundError: 404,
    svc.NoDataError: 404,
    svc.ContractMismatchError: 400,
    svc.InvalidRequestError: 400,
}


def _raise_http(exc: svc.ServiceError) -> None:
    status = _ERROR_STATUS.get(type(exc), 500)
    raise HTTPException(status_code=status, detail=str(exc))


api_router = APIRouter(prefix="/api", tags=["api"])


@api_router.get("/symbols")
async def get_symbols():
    """返回支持的品种列表"""
    return {
        "symbols": get_symbols_config().get("symbols", {}),
        "usage": "可以使用中文名",
    }


@api_router.post("/reload_symbols")
async def reload_symbols():
    """强制从磁盘重载 symbols.json（平时会按 mtime 自动热加载，手动接口仅用于立即兜底）。"""
    cfg = get_symbols_config(force=True)
    return {
        "reloaded": True,
        "count": len(cfg.get("symbols", {})),
        "updated_at": cfg.get("updated_at"),
    }


@api_router.get("/contracts")
async def list_contracts(symbol: str = Query(..., description="品种中文名，如：螺纹钢")):
    """扫描本机 vipdoc，返回该品种下可选的通达信合约代码。"""
    try:
        return svc.list_contracts(symbol)
    except svc.ServiceError as e:
        _raise_http(e)


@api_router.get("/search_symbols")
async def search_symbols(q: str = Query("", description="搜索关键词")):
    """模糊搜索品种"""
    return {"results": svc.search_symbols(q)}


@api_router.get("/replay_data")
async def get_replay_data(
    symbol: str = Query(..., description="品种名称，如：螺纹钢、PVC"),
    contract: str = Query(
        None,
        description="可选；具体合约代码（如 RB2605）。不传则使用 symbols.json 中该品种的默认 mootdx_code",
    ),
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
    """获取回放数据：显示周期K线 + 步进周期数据"""
    try:
        return svc.get_replay_payload(
            symbol=symbol,
            contract=contract,
            display_period=display_period,
            step_period=step_period,
            count=count,
            ma_period=ma_period,
            start_date=start_date,
            end_date=end_date,
            range_start=range_start,
            range_end=range_end,
        )
    except svc.ServiceError as e:
        _raise_http(e)
    except Exception as e:
        logger.exception("replay_data 处理失败: symbol=%s contract=%s", symbol, contract)
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")


app.include_router(api_router)

from backtest.api import router as backtest_router
app.include_router(backtest_router)


def _serve_frontend() -> None:
    """把 frontend/ 挂到根路径。/api/* 由 api_router 拦截，StaticFiles 只接管其余请求。"""
    if not FRONTEND_DIR.is_dir():
        logger.warning("frontend directory missing: %s — only /api routes available", FRONTEND_DIR)
        return

    # html=True 会把 "/" 映射到 index.html，同时直接暴露 app.js / styles.css / favicon 等资源。
    # 必须在 include_router 之后 mount，/api/* 才能走 API，否则会被静态回退吞掉。
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


_serve_frontend()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logger.info(
        "TradeSense: http://127.0.0.1:8765/  |  API Swagger: http://127.0.0.1:8765/api/docs"
    )
    uvicorn.run(app, host="0.0.0.0", port=8765)
