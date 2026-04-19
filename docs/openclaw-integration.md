# TradeSense × OpenClaw 接线说明

本文说明如何在本机把 **TradeSense MCP** 与 **技能目录** 接到 OpenClaw。**不会**修改 OpenClaw 源码仓库；仅改你机器上的 OpenClaw 配置。

## 1. 依赖

- 已安装 [uv](https://docs.astral.sh/uv/)
- 已安装 OpenClaw CLI，且 Gateway 使用方式与你的环境一致

## 2. 安装 Python 依赖（TradeSense 根目录）

```bash
cd TRADESENSE_REPO_ROOT
uv pip install -r requirements.txt
```

若项目后续改为 `uv sync` + `pyproject.toml`，以仓库 README 为准，但必须通过 `uv` 管理环境。

## 3. 启动 HTTP 服务（终端保持运行）

```bash
cd TRADESENSE_REPO_ROOT
uv run python server.py
```

确认 `http://127.0.0.1:8765` 可访问（具体 health 路径以 `server.py` 为准）。

## 4. 注册 MCP（OpenClaw）

将 `TRADESENSE_REPO_ROOT` 替换为**本机绝对路径**（Windows 示例：`C:/1/projects/claw-projects/tradesense`，注意 JSON 里反斜杠需转义或改用正斜杠）。

**PowerShell：**

```powershell
openclaw mcp set tradesense '{"command":"uv","args":["run","python","mcp_server.py"],"cwd":"TRADESENSE_REPO_ROOT"}'
```

**Bash：**

```bash
openclaw mcp set tradesense '{"command":"uv","args":["run","python","mcp_server.py"],"cwd":"TRADESENSE_REPO_ROOT"}'
```

验证：

```bash
openclaw mcp list
```

输出中应包含名称 `tradesense`。

## 5. 注册技能扫描目录

在 OpenClaw 配置文件（例如 `~/.openclaw/openclaw.json`）中增加或合并 `skills.load.extraDirs`，使其中包含：

`TRADESENSE_REPO_ROOT/skills`

示例片段：

```json
{
  "skills": {
    "load": {
      "extraDirs": ["TRADESENSE_REPO_ROOT/skills"]
    }
  }
}
```

若你为 agent 配置了 `agents.defaults.skills` 或 `agents.list[].skills` 白名单，请加入 `tradesense_kline`（与 `SKILL.md` 中 `name` 一致）。

## 6. 重启 Gateway

修改 MCP 或 skills 后需重启 Gateway（例如 `openclaw gateway restart`，以你环境文档为准）。

## 7. 验收

```bash
openclaw skills list
```

应能看到 `tradesense_kline`（或 OpenClaw 展示用的等价名称）。

发一条测试消息（示例）：「查螺纹钢 5 分钟最近 50 根 K 线」，确认模型调用 MCP `get_klines` 且返回合理 JSON。

## 相关设计

- `docs/specs/2026-04-19-openclaw-kline-skill-design.md`
