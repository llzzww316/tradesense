---
name: tradesense_kline
description: 通过 TradeSense MCP 从掘金获取 K 线（OHLCV）与相关工具；须先启动本仓库 HTTP 服务。
---

# TradeSense K 线（OpenClaw）

## 何时使用

用户询问 **K 线、蜡烛图、OHLC、历史行情、复盘数据、某品种走势**，且数据源应为 **TradeSense / 掘金** 时：必须使用 MCP 工具，不要用 shell 随意抓取网页或未说明来源的数据。

## 前置条件（A 模式）

1. **HTTP 服务已运行**：在 TradeSense 仓库根目录执行（**禁止**裸 `python`）：

   ```bash
   uv run python server.py
   ```

   默认监听 `http://127.0.0.1:8765`（与 `mcp_server.py` 内 `API_BASE` 一致）。

2. **OpenClaw 已注册本 MCP**，且 Gateway 已重启使配置生效（见 `docs/openclaw-integration.md`）。

## MCP 工具（由 `mcp_server.py` 提供）

| 工具 | 用途 |
|------|------|
| `get_symbols` | 列出配置内品种；不确定代码或中文名映射时先调用。 |
| `get_klines` | 拉取 K 线序列（含 EMA 相关后端逻辑）；主要入口。 |
| `get_latest_price` | 最新价快照。 |

### `get_klines` 参数

- `symbol`（必填）：中文名（如 `螺纹钢`）或代码（如 `SHFE.rb2610`）。
- `period`（可选）：`1m` \| `5m` \| `15m` \| `30m` \| `60m` \| `1d`，默认 `5m`。
- `count`（可选）：条数，默认 `100`。
- `ma_period`（可选）：EMA 周期，默认 `20`。

返回 JSON：`klines` 数组等为 MCP 文本负载；向用户解释时摘取关键字段，避免整段无意义倾倒。

## 调用顺序

1. 若品种不明确 → `get_symbols`，再 `get_klines`。
2. 否则直接 `get_klines`。

## 故障排查

- **连接 / 超时 / HTTP 错误**：确认已用 `uv run python server.py` 启动服务；检查 `mcp_server.py` 顶部 `API_BASE` 与真实端口一致。
- **`未知品种`**：对照 `get_symbols` 或 `symbols.json`。
- **掘金 / 凭证错误**：按仓库 `README.md` 与本地掘金配置排查；勿在对话中粘贴密钥。

## 维护说明

修改端口或启动方式时，同步更新 `docs/openclaw-integration.md` 与本文件；**对外运行 Python 的文档示例须保持 `uv run`**。
