"""
SSE（Server-Sent Events）推送管理器：后台轮询实时行情，扇出到所有订阅者。
"""
import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field

import realtime_provider as rp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 订阅数据结构
# ---------------------------------------------------------------------------

@dataclass
class SSESubscription:
    sub_id: str
    symbols: list[str]
    queue: asyncio.Queue
    created_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# SSE 管理器
# ---------------------------------------------------------------------------

class SSEManager:
    """管理 SSE 客户端订阅和后台轮询。"""

    def __init__(self, poll_interval: float = 3.0) -> None:
        self._subscriptions: dict[str, SSESubscription] = {}
        self._poll_task: asyncio.Task | None = None
        self._poll_interval = poll_interval
        self._last_event_time: float = 0.0

    async def subscribe(self, symbols: list[str]):
        """订阅指定品种的实时报价，异步生成 SSE 事件字符串。"""
        sub = SSESubscription(
            sub_id=str(uuid.uuid4())[:8],
            symbols=list(symbols),
            queue=asyncio.Queue(),
        )
        self._subscriptions[sub.sub_id] = sub
        logger.info("SSE 订阅 [%s]: %s", sub.sub_id, symbols)

        try:
            while True:
                try:
                    event = await asyncio.wait_for(sub.queue.get(), timeout=30.0)
                    yield event
                except asyncio.TimeoutError:
                    # 30 秒无数据，发送 keepalive
                    yield ":keepalive\n\n"
        finally:
            del self._subscriptions[sub.sub_id]
            logger.info("SSE 取消订阅 [%s]", sub.sub_id)

    async def start(self) -> None:
        """启动后台轮询任务。"""
        if self._poll_task is not None:
            return
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("SSE 轮询已启动，间隔 %.1fs", self._poll_interval)

    async def stop(self) -> None:
        """停止轮询。"""
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        logger.info("SSE 轮询已停止")

    async def _poll_loop(self) -> None:
        """后台轮询：收集所有订阅品种，拉取报价，扇出到各订阅者。"""
        while True:
            try:
                await asyncio.sleep(self._poll_interval)

                # 收集所有活跃订阅的品种（去重）
                all_symbols: set[str] = set()
                for sub in self._subscriptions.values():
                    all_symbols.update(sub.symbols)

                if not all_symbols:
                    continue

                # 并发拉取所有品种的报价
                tasks = {
                    sym: asyncio.to_thread(rp.get_realtime_quote, sym)
                    for sym in all_symbols
                }
                results: dict[str, dict] = {}
                for sym, task in tasks.items():
                    try:
                        results[sym] = await task
                    except Exception as e:
                        results[sym] = {"symbol": sym, "error": str(e), "source": None}

                # 扇出到各订阅者
                for sub in list(self._subscriptions.values()):
                    for sym in sub.symbols:
                        quote = results.get(sym)
                        if quote is None:
                            continue
                        event_str = self._format_event(quote)
                        try:
                            sub.queue.put_nowait(event_str)
                        except asyncio.QueueFull:
                            logger.warning("SSE 订阅 [%s] 队列已满，丢弃事件", sub.sub_id)

                self._last_event_time = time.time()

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("SSE 轮询异常")
                await asyncio.sleep(self._poll_interval)

    @staticmethod
    def _format_event(quote: dict) -> str:
        """将报价 dict 格式化为 SSE 事件字符串。"""
        error = quote.get("error")
        if error:
            payload = json.dumps(quote, ensure_ascii=False)
            return f"event: error\ndata: {payload}\n\n"
        else:
            payload = json.dumps(quote, ensure_ascii=False)
            return f"event: quote\ndata: {payload}\n\n"


# 模块级单例
sse_manager = SSEManager()
