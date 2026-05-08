# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

TradeSense 是一个 K 线回放训练系统，支持多周期组合显示（显示周期 + 步进周期），内置模拟交易功能。用于盘感训练、策略复盘和入场点位练习。

## 启动命令

### 一页启动（浏览器回放）
```bash
uv run python server.py
```

浏览器访问 **`http://localhost:8765/`** ：同一进程托管「静态前端 + REST API（`/api/...`）」。Swagger：**`http://localhost:8765/api/docs`**。

### 仅跑 MCP（OpenClaw / WSL 等）
```bash
uv run python mcp_server.py
```

MCP 是 **stdio 模式**，直接调用 `kline_service` 读取本机通达信 VIPDOC，**不需要先启动 `server.py`**。两者各自独立，也可并存。`symbols.json` 改动由 `config.py` 按 mtime 自动热加载；若要立即生效也可 `POST /api/reload_symbols`。

### TDX 目录配置

通达信数据根目录由 `TRADESENSE_TDX_DIR` 环境变量决定，默认 `C:/new_tdx`。WSL 里可设为 `/mnt/c/new_tdx`。

## 架构说明

### 分层

- **`data_provider.py`** — tdxpy 读取 VIPDOC；`lru_cache` 按 `(path, mtime, period)` 缓存完整 DataFrame
- **`config.py`** — `symbols.json` 热加载（mtime 比对），`get_symbols_config()` / `resolve_symbol()`
- **`kline_service.py`** — 业务层：品种解析 / 合约校验 / EMA / JSON 友好输出；抛 `ServiceError` 子类
- **`server.py`** — FastAPI：把 `ServiceError` 映射到 HTTP 状态码；`StaticFiles` 挂 `frontend/`
- **`mcp_server.py`** — MCP stdio：直接调 `kline_service`，无 HTTP 依赖

### 后端 HTTP（`server.py`）

- **FastAPI** + `StaticFiles(html=True)`，单进程同时服务 `/api/*` 与 `frontend/`。
- **K 线来源**：`data_provider.py` 使用 **tdxpy** 读取本机 **通达信 VIPDOC** 离线文件（`lc1` / `lc5` / 扩展日线）。无离线文件则返回 404。根目录由 `TRADESENSE_TDX_DIR` 环境变量决定，默认 `C:/new_tdx`。
- **CORS** 通配源、不带凭据（本地工具场景）。
- 错误约定：业务错误走 `HTTPException`（`{"detail": ...}` + 非 2xx 状态码）。
- 主要 HTTP 路径（前缀 **`/api`**）：
  - `GET /api/symbols`
  - `POST /api/reload_symbols`
  - `GET /api/contracts?symbol=`
  - `GET /api/search_symbols?q=`
  - `GET /api/replay_data`（可选 `contract`）；响应中带 **`contract`**

### MCP（`mcp_server.py`）

- stdio 协议。工具：`get_symbols` / `get_contracts` / `get_klines` / `get_latest_price`，全部直接调 `kline_service`。
- 不起 HTTP，不依赖 `server.py`。WSL 路径：`command=python3 args=[/path/to/mcp_server.py] env={TRADESENSE_TDX_DIR=/mnt/c/new_tdx}`。

### 前端架构 (`frontend/index.html` + `app.js` + `styles.css`)

- **Lightweight Charts**（CDN v4.1.0）绘制 K 线
- 品种 + **合约**双下拉（合约选项来自 `/api/contracts`）；变更合约且在已回放会话中时自动重新加载数据。
- 核心能力：回放控制、进度条、十字线 OHLC、模拟交易与 `localStorage` 持久化、周期切换时可保持时间窗再拉数（`range_*`）

### 数据流

1. 前端载入品种后请求 `/api/contracts`，再请求 `/api/replay_data`（可加 `contract=…`）。
2. 后端解析 `symbols.json` → `market` + 选用的合约代码读盘，经 `fetch_replay_data` 返回两路周期数据。
3. 对显示周期计算 EMA，将 `bob` 格式化为 `time` 字符串返回 JSON。
4. 前端用显示周期画图，用步进序列驱动回放；播放中未完成 K 线由步进聚合更新。

### 模拟交易规则

- 每跳价值等见 `symbols.json` 的 `tick_value` 与前端 `TICK_VALUE`。
- 手续费：按 `localStorage`（元/手/次）；不做保证金，不支持反手；浮盈按当前步进收盘价。

### 后端支持的周期（与 `data_provider` 一致）

- 分钟：`1m`；以及来自 `lc5` 文件的 `5m`、`15m`、`30m`、`60m`/`1h`（更高周期由 5 分钟栅格重采样）。
- 日线：`1d`。
- 未实现的周期（如前端的 `4h`）不要写进后端能力说明。

## 依赖

`requirements.txt`：`fastapi`、`uvicorn`、`pandas`、`tdxpy`、`mcp`。MCP 不再需要 `httpx`（已内嵌调用）。

前端：Lightweight Charts v4.1.0（CDN）。

## 关键实现细节

### EMA

使用 pandas `ewm(span=period, adjust=False).mean()`，仅打在**显示周期**序列上。

### 时间处理

- 数据层统一 `bob` 为 pandas 时间；API 输出的 `time` 为 `%Y-%m-%d %H:%M:%S` 字符串。
- 前端 `toChartTime` 按字符串解析后以 UTC 秒时间戳传给图表，与既有 `formatChartTime`（按 UTC 显示标签）保持一致。

### 数据同步与条数

- 步进条数估算：`display_minutes / step_minutes`（整数比）乘以 `count` 等逻辑见 `fetch_replay_data`。
- 主力换月：更新 `symbols.json` 中的 `mootdx_code`（及按需调整合约）——`config.py` 按 mtime 自动热加载，**无需重启**；必要时 `POST /api/reload_symbols` 立即触发。

### K 线颜色

- 涨：`#ef5350`；跌：`#26a69a`；EMA：`#ff9800`。
