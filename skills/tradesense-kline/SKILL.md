---
name: tradesense_kline
description: 通过 TradeSense MCP 读取本机通达信 VIPDOC 的 K 线（OHLCV + EMA）；MCP stdio 模式，无需先启动 HTTP 后端。
---

# TradeSense K 线（OpenClaw）

## 何时使用

用户询问 **K 线、蜡烛图、OHLC、历史行情、复盘数据、某品种走势**，且应使用 **TradeSense 返回的本机通达信离线数据** 时：必须通过 MCP 工具拉取，不要用 shell 随意抓取网页或未说明来源的数据。

## 前置条件

- **MCP 已在 OpenClaw 注册**，Gateway 已重启使配置生效（见 `docs/openclaw-integration.md`）。
- **通达信 VIPDOC 目录可达**：Windows 默认 `C:/new_tdx`；WSL 下用 `TRADESENSE_TDX_DIR=/mnt/c/new_tdx`。
- **无需** 启动 `server.py` —— MCP 直接读本地文件（通过 `kline_service` + `data_provider`）。浏览器回放界面可选，独立于 MCP。

## MCP 工具（由 `mcp_server.py` 提供）

| 工具 | 用途 |
|------|------|
| `get_symbols` | 列出配置内品种；不确定代码或中文名映射时先调用。 |
| `get_contracts` | 列出某品种本机 vipdoc 下可用的合约代码（换月前查看下一个主力）。 |
| `get_klines` | 拉取 K 线序列（含 EMA）；主要入口。 |
| `get_latest_price` | 最新价快照。 |

### `get_klines` 参数

- `symbol`（必填）：中文名（如 `螺纹钢`）或代码（如 `SHFE.rb2610`）。
- `contract`（可选）：具体合约代码（如 `RB2605`），须本机 vipdoc 存在；不传则用 `symbols.json` 默认。不确定时先用 `get_contracts` 查。
- `period`（可选）：`1m` \| `5m` \| `15m` \| `30m` \| `60m` \| `1d`，默认 `5m`。
- `count`（可选）：无日期条件时，作为**返回条数上限**（从最新一侧截取），默认 `100`。有日历区间时由 MCP 自动放大请求至上限 2000，**返回该区间内全部**显示周期 K 线。
- `ma_period`（可选）：EMA 周期，默认 `20`。
- `start_date` / `end_date`（可选）：**日历区间**，格式 `YYYY-MM-DD`（例：`2026-03-10`、`2026-03-12`）。年份须与数据合约一致。
- `range_start` / `range_end`（可选）：**精确时间窗**（显示周期 K 线的 `bob` 范围），须**成对**传入，格式 `YYYY-MM-DD HH:MM:SS`。用于与 UI 回放对齐或收窄到具体几根 bar。

**推荐用法**

- 用户说「3 月 10 日到 12 日、5 分钟」→ `get_klines`，`symbol=螺纹钢`，`period=5m`，`start_date=2026-03-10`，`end_date=2026-03-12`。
- 用户给出明确起止时刻 → `range_start` + `range_end`，勿只传一半（会报错）。
- 换月期：先 `get_contracts` 看可选月份，再在 `get_klines` 里传 `contract`。

返回 JSON：`klines` 为 OHLC（及 `ema`）；若使用了日期参数，响应中可能回显 `start_date` / `end_date` / `range_*` 便于核对。向用户解释时摘取首尾时间、根数、关键价位，避免无意义整段粘贴。

**说明**：区间内实际有多少根 K 线取决于**交易时段与数据源**；若 `end_date` 当天无数据，最后一根可能落在前一交易日。

## 调用顺序

1. 若品种不明确 → `get_symbols`，再 `get_klines`。
2. 若合约不确定 → `get_contracts`，挑好 `contract` 再 `get_klines`。
3. 其余直接 `get_klines`。

## 故障排查

- **`未知品种`**：对照 `get_symbols` 或 `symbols.json`。
- **`本机 vipdoc 中未找到合约 ...`**：检查通达信是否已下载该合约行情；或用 `get_contracts` 看本机实际存在哪些。
- **`TRADESENSE_TDX_DIR`**：WSL / 非默认路径下务必在 MCP env 里设置，否则会去读不存在的 `C:/new_tdx`。
- **`symbols.json` 刚改完没生效**：`config.py` 按 mtime 自动热加载；若 MCP 是长驻进程仍不生效，重启 Gateway。

## 维护说明

修改启动方式、工具名或参数时，同步更新 `docs/openclaw-integration.md` 与本文件；**对外运行 Python 的文档示例须保持 `uv run`**。
