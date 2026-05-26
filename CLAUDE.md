# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

TradeSense 是一个 K 线回放训练系统，支持多周期组合显示（显示周期 + 步进周期），内置模拟交易与事件驱动回测。用于盘感训练、策略复盘和入场点位练习。

数据来源是本机通达信 VIPDOC 离线文件（`tdxpy` 直读），**不联网**；前端 + REST API + MCP 三种接入方式共享同一业务层。

### 价格行为知识库 (`price-action/`)

`price-action/` 目录包含 **30 篇价格行为交易系统手册分析文档** + **策略操作指引**，来源为微信公众号「价格行为学解析」专栏，每篇均交叉引用了 **Qdrant `price-action` 向量库**中的 Al Brooks 原著（四本核心著作）。

**目录结构**：
- `price-action/html/` — HTML 格式文档 + `styles.css`（浏览器查看）
- `price-action/md/` — Markdown 格式文档

**文档结构**（按编号 1-17 为主线，辅以概述 + 专题 + 策略指引）：

| 范围 | 主题 |
|------|------|
| 手册（1） | 三要素框架 / Always In 判定 / 趋势 vs 区间 |
| 手册（2） | 交易幻觉 / 判断顺序 / H2-L2 二次入场 |
| 手册（3） | 楔形 / 三次推动 / 动能衰减 / 75% 规则 |
| 手册（4） | 双顶双底 / 失败的失败（Failed Failure） |
| 手册（5） | 突破→通道→区间循环 / 交易者方程 / 磁吸位 |
| 手册（6） | K 线信号系统：四棒序列（设置→信号→入场→跟随） |
| 手册（7） | 惊喜 K 线 / 超大 K 线识别与策略 |
| 手册（8） | 市场周期 / 趋势四步确认 / 五维强度评估 |
| 手册（9） | 信噪比 / 趋势延续形态（旗形/通道） / 高潮衰竭 |
| 手册（10） | 三大入场方式（Stop/Limit/Close）+ 高阶入场 |
| 手册（11） | 窄/宽通道分类与策略 |
| 手册（12） | 微通道 / 市场四阶段周期模型 |
| 手册（13） | 交易区间 / 真空区 / 80% 法则 |
| 手册（14） | 突破与缺口 / 真假突破 / 测量运动 |
| 手册（15） | 趋势强弱等级 / 重要高/低点 / 止损管理 |
| 手册（16） | 反转 / TBTL 原则 / 高潮反转 / 收缩楼梯 |
| 手册（17） | 主要趋势反转三阶段 / 最终旗形 |
| 概述 | 五层学习框架 / MTR / 三种市场状态 |
| 市场结构 01-04 | Always-In 精确判标 / 趋势衰退序列 / 回调 vs 反转量化阈值 |
| Regime + §1.1a x7 | Spike→Channel→TR 全生命周期 / Barb Wire / Buy Vacuum |
| 策略操作指引 | 收缩楼梯反转策略（html/，基于手册 3/16/17 整合实战回测） |

**Qdrant 查询**（需要时可检索 Al Brooks 原著）：
```bash
uv run --project C:/1/projects/claw-projects/doc-vectorizer python scripts/query.py --collection price-action --query "关键词" --top-k 5
```

## 启动命令

### 一页启动（浏览器回放 + 回测）
```bash
uv run python server.py
```

浏览器访问 **`http://localhost:8765/`**（回放页）或 **`http://localhost:8765/backtest.html`**（回测页）：同一进程托管「静态前端 + REST API（`/api/...`）」。Swagger：**`http://localhost:8765/api/docs`**。

### 仅跑 MCP（OpenClaw / WSL 等）
```bash
uv run python mcp_server.py
```

MCP 是 **stdio 模式**，直接调用 `kline_service` 读取本机通达信 VIPDOC，**不需要先启动 `server.py`**。两者各自独立，也可并存。`symbols.json` 改动由 `config.py` 按 mtime 自动热加载；若要立即生效也可 `POST /api/reload_symbols`。

### TDX 目录配置

通达信数据根目录由 `TRADESENSE_TDX_DIR` 环境变量决定，默认 `C:/new_tdx`。WSL 里可设为 `/mnt/c/new_tdx`。

读取的子目录：
- 期货（扩展市场）：`vipdoc/ds/{lday,fzline,minline}/{market}#{contract}.{day,lc5,lc1}`
- A 股：`vipdoc/{sh,sz}/{lday,fzline,minline}/{sh|sz}{code}.{day,lc5,lc1}`

### 测试

```bash
uv run pytest                              # 全量
uv run pytest tests/test_engine.py         # 单文件
uv run pytest tests/test_engine.py::test_long_open_close_pnl   # 单用例
uv run pytest -k "false_breakdown"         # 关键字筛选
```

测试位于 `tests/`，`conftest.py` 提供合成 K 线 fixtures（`flat_bars` / `up_bars` / `down_bars`）和 `rb_config_kwargs`（螺纹钢标准参数）。

## 架构说明

### 分层（业务层共享给 HTTP / MCP / 回测）

- **`data_provider.py`** — tdxpy 读取期货 + A 股 VIPDOC；`_KlineSource` 数据类抽象期货/股票路径差异；`lru_cache` 按 `(path, mtime, period)` 缓存；提供 `fetch_kline_by_date`（期货）+ `fetch_stock_kline_by_date`（股票）+ `fetch_replay_data`（回放双周期）
- **`config.py`** — `symbols.json` 热加载（mtime 比对），`get_symbols_config()` / `resolve_symbol()`
- **`kline_service.py`** — 业务层：品种解析 / 合约校验 / EMA / JSON 友好输出；抛 `ServiceError` 子类（`UnknownSymbolError` / `ContractNotFoundError` / `NoDataError` / `ContractMismatchError` / `InvalidRequestError`）
- **`server.py`** — FastAPI：把 `ServiceError` 映射到 HTTP 状态码；挂载 `backtest.api.router`；`StaticFiles` 挂 `frontend/`
- **`mcp_server.py`** — MCP stdio：直接调 `kline_service`，无 HTTP 依赖

### 后端 HTTP（`server.py`）

- **FastAPI** + `StaticFiles(html=True)`，单进程同时服务 `/api/*` 与 `frontend/`。`StaticFiles` 必须在 `include_router` 之后挂载，否则会吞掉 `/api/*`。
- **CORS** 通配源、不带凭据（本地工具场景）。
- 错误约定：业务错误走 `HTTPException`（`{"detail": ...}` + 非 2xx 状态码）。
- 主要 HTTP 路径（前缀 **`/api`**）：
  - `GET /api/symbols`
  - `POST /api/reload_symbols`
  - `GET /api/contracts?symbol=`（仅期货扫描 vipdoc/ds）
  - `GET /api/search_symbols?q=`
  - `GET /api/replay_data`（可选 `contract`、`range_start/range_end`）；响应中带 **`contract`**
  - `GET /api/backtest/strategies` — 列出已注册策略 + 自动反射出的可调参数
  - `POST /api/backtest/run` — 跑回测，返回 bars / fills / trades / equity_curve / metrics

### MCP（`mcp_server.py`）

- stdio 协议。工具：`get_symbols` / `get_contracts` / `get_klines` / `get_latest_price`，全部直接调 `kline_service`。
- 不起 HTTP，不依赖 `server.py`。WSL 路径：`command=python3 args=[/path/to/mcp_server.py] env={TRADESENSE_TDX_DIR=/mnt/c/new_tdx}`。

### 前端架构 (`frontend/`)

- `index.html` + `app.js` + `styles.css` — K 线回放页
- `backtest.html` + `backtest.js` — 回测页（策略下拉 → 参数表单 → 资金曲线 + 交易点 + 绩效指标）
- `shared.js` — 两个页面共享的工具函数（时间格式化、API 客户端等）
- **Lightweight Charts**（CDN v4.1.0）绘制 K 线
- 品种 + **合约**双下拉（合约选项来自 `/api/contracts`）；变更合约且在已回放会话中时自动重新加载数据
- 核心能力：回放控制、进度条、十字线 OHLC、模拟交易与 `localStorage` 持久化、周期切换时可保持时间窗再拉数（`range_*`）

### 数据流

1. 前端载入品种后请求 `/api/contracts`，再请求 `/api/replay_data`（可加 `contract=…`）。
2. 后端解析 `symbols.json` → `market` + 选用的合约代码读盘，经 `fetch_replay_data` 返回两路周期数据。
3. 对显示周期计算 EMA，将 `bob` 格式化为 `time` 字符串返回 JSON。
4. 前端用显示周期画图，用步进序列驱动回放；播放中未完成 K 线由步进聚合更新。

### 模拟交易规则（前端）

- 每跳价值等见 `symbols.json` 的 `tick_value` 与前端 `TICK_VALUE`。
- 手续费：按 `localStorage`（元/手/次）；不做保证金，不支持反手；浮盈按当前步进收盘价。

### 后端支持的周期（与 `data_provider` 一致）

- 分钟：`1m`；以及来自 `lc5` 文件的 `5m`、`15m`、`30m`、`60m`/`1h`（更高周期由 5 分钟栅格重采样）。
- 日线：`1d`。

## 回测模块（`backtest/`）

事件驱动回测，Python `on_bar` 策略接入；与模拟交易共享底层账户语义，但实现独立。

### 文件分工

- `backtest/models.py` — 数据类（Bar/Order/Fill/Trade/Position/BacktestConfig/BacktestResult/EquityPoint），含 `instrument_type` 字段（`"futures"` / `"stock"`）
- `backtest/account.py` — Account（权益 = 初始 + 已实现 - 手续费 + 浮盈；available = 权益 - 占用保证金；available<0 即爆仓），期货按跳数折算 PnL，股票按价差 × 股数
- `backtest/broker.py` — 按"下一根 K 开盘 ± 滑点"成交；日末 / 爆仓用 `force_close`；股票模式手续费 = 佣金 + 印花税（卖出）+ 过户费
- `backtest/context.py` — `on_bar(bar, ctx)` 的上下文：`ctx.history / ctx.closes / ctx.position_side / ctx.position_qty / ctx.buy() / ctx.sell() / ctx.close() / ctx.state`
- `backtest/indicators.py` — **共享技术指标**：`ema()` / `ema_inc()`（O(1) 增量 EMA，必须用它代替 `pd.ewm` 全量重算）/ `atr()` / `trend_bar_side()` / `confirm_swing_high/low()` / `detect_swings()` / `is_trading_range()`
- `backtest/registry.py` — `@register_strategy("name")` 全局注册；`get_strategy_params()` 通过 `inspect` 反射 on_bar 的关键字参数，自动暴露给前端
- `backtest/strategies/` — 内置策略目录（见下）
- `backtest/engine.py` — 串联 account+broker+strategy 的事件循环，含日内强平、爆仓处理、股票 T+1（买入当日不可卖）
- `backtest/metrics.py` — 年化 / 最大回撤 / Sharpe / Calmar / 胜率 / 盈亏比 / 连胜连败 / 平均持仓
- `backtest/api.py` — `GET /api/backtest/strategies` / `POST /api/backtest/run`，按 `market_type` 自动分流期货 / A 股数据源

### 内置策略

当前**已清空**，准备重写。`backtest/strategies/__init__.py` 是空文件，只剩 docstring；新策略注册流程不变——在文件里 `from . import <new_strategy>` 即可触发 `@register_strategy` 注册。

### 关键规则

- 信号在 K(t) 收盘产生 → K(t+1) 开盘 ± slippage 成交（避免 look-ahead）
- 滑点按"跳"配置：`slippage_ticks * tick_size`；开多/平空 +N 跳，开空/平多 -N 跳
- 手续费：
  - 期货：`fee_per_lot * qty`，开平各收一次
  - 股票：佣金 = `commission_rate * 名义金额`（最低 5 元）+ 印花税（卖出 0.1%）+ 过户费（双向 0.001%）
- 保证金（仅期货）：`price * qty * tick_value * margin_rate / tick_size`，开仓时占用
- 不支持反手 / 加仓 / 部分平仓（第一期简化）
- `intraday_only=True` → 期货日末强平（每日最后一根 K 收盘后反向单，次日第一根开盘或当日 close 强平）；**股票模式忽略此项**
- 股票 T+1：买入当日不可卖出（按 `bar.time` 的日期串比较）
- 爆仓：available<0 当根即触发，下一根开盘或当前 close 强平，结果 `liquidated=True`

### symbols.json 字段约定

通用：
- `market_type` — `"futures"` 或 `"stock"`，决定数据源 + 回测费率模型
- `tick_size` — 每跳价格（元/单位）
- `tick_value` — 每跳价值（元/手 或 元/股）
- `margin_rate` — 保证金比例（股票为 1.0）
- `fee_per_lot` — 期货每手每次手续费

期货专属：
- `mootdx_market` / `mootdx_code` — 通达信扩展市场号 + 默认主力合约代码

A 股专属：
- `code` — 形如 `"SH.600519"`，被切分为 `exchange + symbol`
- `commission_rate` / `stamp_tax_rate` / `transfer_fee_rate` / `lot_size`

## 依赖

`requirements.txt`：`fastapi`、`uvicorn`、`pandas`、`tdxpy`、`mcp`、`pytest`。

前端：Lightweight Charts v4.1.0（CDN）。

## 关键实现细节

### EMA

- 数据层（`kline_service`）给前端用的 EMA：pandas `ewm(span=period, adjust=False).mean()`，仅打在**显示周期**序列上。
- 策略内部的 EMA：**必须用 `backtest.indicators.ema_inc`**（增量 O(1)），不要每根 bar 调 `pd.Series.ewm()` 重算全量历史，那是 O(N²)。`atr` 也类似，仅取最近 `period+1` 根计算。

### 时间处理

- 数据层统一 `bob` 为 pandas 时间；API 输出的 `time` 为 `%Y-%m-%d %H:%M:%S` 字符串。
- 前端 `toChartTime` 按字符串解析后以 UTC 秒时间戳传给图表，与既有 `formatChartTime`（按 UTC 显示标签）保持一致。
- 股票 T+1 判定靠 `bar.time.split(" ")[0]` 取日期串，不要换成 timestamp 比较——日线时 `time` 末尾是 `00:00:00`。

### 数据同步与条数

- 步进条数估算：`display_minutes / step_minutes`（整数比）乘以 `count` 等逻辑见 `fetch_replay_data`。
- 主力换月：更新 `symbols.json` 中的 `mootdx_code`（及按需调整合约）——`config.py` 按 mtime 自动热加载，**无需重启**；必要时 `POST /api/reload_symbols` 立即触发。

### K 线颜色

- 涨：`#ef5350`；跌：`#26a69a`；EMA：`#ff9800`。
