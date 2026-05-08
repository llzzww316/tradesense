# TradeSense × OpenClaw：K 线技能设计说明

> 状态：已定稿（实现前请另见 implementation plan）。  
> 约束：**不修改 OpenClaw 源码仓库**；可交付物均在 **TradeSense 仓库**；本机 Python 一律通过 **`uv`** 调用。

## 1. 目标与成功标准

**目标**：当 OpenClaw agent 需要 K 线（或同类行情序列）时，**优先通过 TradeSense 暴露的 MCP 工具**拉取数据，而不是随意用 shell 抓不明来源。

**成功标准**

- 用户在 OpenClaw 配置中注册 TradeSense MCP 后，agent 可调 `get_klines` 等工具并得到 OHLCV/EMA 等结构化结果。
- 技能文档明确：**先起 HTTP 服务**，再依赖 MCP；失败时有可操作的排查步骤。
- 文档与技能中凡出现「在本仓库运行 Python」，均写 **`uv run`**，不写裸 `python`。

## 2. 背景与现状

- **HTTP API**：`server.py`（FastAPI）默认监听 `http://127.0.0.1:8765`，业务路径前缀 **`/api`**；同上托管静态前端于站点根 `/`。K 线由 `data_provider.py` 读取本机 **通达信 VIPDOC**。
- **MCP**：`mcp_server.py` stdio MCP，HTTP 基底 `API_BASE` = `http://127.0.0.1:8765/api`。
- **OpenClaw**：技能为工作区内的 `SKILL.md`；出站 MCP 在配置 `mcp.servers` 中登记（见 OpenClaw 文档 *mcp* CLI / `mcp.servers`）。

## 3. 架构

| 组件 | 职责 |
|------|------|
| `uv run … server.py` | 长期运行，提供 REST/业务 API（端口以项目为准，默认 8765）。 |
| `uv run … mcp_server.py` | 由 OpenClaw 在需要时 **spawn stdio 子进程**；将 MCP 工具调用转为对本地 HTTP 的请求。 |
| OpenClaw `mcp.servers` | 仅存在于用户本机 `openclaw.json`（或通过 `openclaw mcp set` 写入），**不属于 TradeSense  git 树**。 |
| TradeSense 内 `skills/<id>/SKILL.md` | 教模型：**何时**用 MCP、**调用顺序**、**前置条件**与 **uv** 启动方式。 |

**数据流**：OpenClaw → MCP `get_klines` → `mcp_server.py` → `http://127.0.0.1:8765/api/…` → FastAPI → `data_provider` → 通达信 VIPDOC。

## 4. TradeSense 仓库内交付物（实现阶段落地）

以下路径为建议名，实现时可微调但需同步改文档。

| 路径 | 说明 |
|------|------|
| `skills/tradesense-kline/SKILL.md` | 技能正文：触发条件、工具名、参数约定、`uv` 启动命令、故障排查。 |
| `docs/openclaw-integration.md`（可选，或与本文合并） | 面向用户的「一步步接线」：安装依赖、`uv`、MCP `set` 示例、`skills.load.extraDirs` 示例。 |

**不在 TradeSense 内提交的内容**：用户机器上的 OpenClaw 全局/工作区 JSON。仓库内只提供**可复制示例**（占位符 `TRADESENSE_REPO_ROOT`）。

## 5. OpenClaw 侧接线（用户操作，非本仓库文件）

### 5.1 MCP（stdio）

使用 OpenClaw 文档中的 `mcp.servers` 形状：`command`、`args`、`cwd`（或 `workingDirectory`）。

示例（占位路径，Windows 下请改为实际目录；`command` 为 `uv`）：

```bash
openclaw mcp set tradesense "{\"command\":\"uv\",\"args\":[\"run\",\"python\",\"mcp_server.py\"],\"cwd\":\"TRADESENSE_REPO_ROOT\"}"
```

说明：

- **`cwd`** 必须为 TradeSense 仓库根目录（含 `mcp_server.py`、`symbols.json`）。
- 若团队固定使用 `uv run python` 以外的入口（例如将来增加 `pyproject` 脚本），以仓库 README 为准，但须保持 **经 `uv` 调用**。

### 5.2 技能扫描目录

在 OpenClaw 配置中增加 `skills.load.extraDirs`，指向本仓库下的 **`skills` 父目录**（与 OpenClaw 文档一致：extraDirs 为附加技能根列表）。示例：

```json
{
  "skills": {
    "load": {
      "extraDirs": ["TRADESENSE_REPO_ROOT/skills"]
    }
  }
}
```

若使用 `agents.defaults.skills` / `agents.list[].skills` 做白名单，将技能 id（`SKILL.md` frontmatter 的 `name`）加入允许列表。

### 5.3 启动顺序（A 模式，已定稿）

1. 终端一：`uv run python server.py`（或项目文档规定的等价 `uv run` 命令），确认 `8765` 可访问。  
2. 启动 / 重启 OpenClaw Gateway，使 MCP 与技能配置生效。  
3. 对话中请求 K 线；模型应走 MCP 工具而非随意 exec。

## 6. 技能内容要求（写入 `SKILL.md` 时遵守）

- **name**：唯一、snake_case（例如 `tradesense_kline`）。  
- **description**：一行说明「经 TradeSense MCP 调用本机后端 K 线 API（通达信离线数据）」。  
- **触发**：用户要 K 线、蜡烛图、OHLC、某合约历史行情、复盘用历史数据等 → 使用 MCP `get_klines`；不确定代码 → 先用 `get_symbols`。  
- **Python / 环境**：明确写「在本项目中启动服务或调试脚本时使用 `uv run …`，不要使用裸 `python`」。  
- **错误处理**：连接失败 / 空数据 → 提示检查 `server.py` 是否已用 `uv run` 启动、端口是否与 `mcp_server.py` 中 `API_BASE` 一致、通达信数据与 `symbols.json` / `TDX_DIR`（见 README、`data_provider.py`）。

## 7. 测试建议（验收清单）

- `uv run python server.py` 启动后，本机 curl 或浏览器探活 `8765`（具体路径以 `server.py` 为准）。  
- `uv run python mcp_server.py` 在终端手动跑一下（若项目有 MCP 自检方式则优先用项目脚本），确认无 import 错误。  
- OpenClaw：`openclaw mcp list` 可见 `tradesense`；发一条需 K 线的消息，确认模型发起工具调用且返回合理。  
- `openclaw skills list`（或等价命令）可见 `tradesense_kline`（以最终 `name` 为准）。

## 8. 范围外

- 不修改 OpenClaw 上游仓库。  
- 不在本设计内规定前端回放 UI 的改动。  
- 不把用户密钥写入本仓库；本项目 K 线路径不经过第三方行情账号（依赖本机通达信数据文件）。

## 9. 修订记录

| 日期 | 说明 |
|------|------|
| 2026-04-19 | 初版：A 模式、MCP + SKILL、仅 TradeSense 仓库存档、**uv** 约束。 |
| 2026-05-01 | 修订：数据源由「掘金 SDK」更正为通达信 VIPDOC（`tdxpy`）；与当前 `README` / `skills` 一致。 |
