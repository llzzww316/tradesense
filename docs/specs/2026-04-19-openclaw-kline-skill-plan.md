# TradeSense OpenClaw K 线技能 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 TradeSense 仓库内新增 OpenClaw 可用的 `SKILL.md` 与接线文档，使 agent 在需要 K 线时走 MCP 工具链；本机运行 Python 一律通过 `uv run`；不修改 OpenClaw 上游仓库。

**Architecture:** 技能仅含 Markdown 说明，引导模型调用已存在的 `mcp_server.py` 暴露的 MCP 工具；用户在本机 `openclaw.json`（或 `openclaw mcp set`）注册 stdio MCP，`skills.load.extraDirs` 指向本仓库 `skills/`。

**Tech Stack:** Markdown、`uv`、既有 `mcp_server.py` / `server.py`、OpenClaw CLI（`openclaw mcp`、`openclaw skills`）。

**依据规格:** `docs/specs/2026-04-19-openclaw-kline-skill-design.md`

---

### Task 1: 新增技能目录与 `SKILL.md`

**Files:**

- Create: `skills/tradesense-kline/SKILL.md`

- [ ] **Step 1: 写入完整 `SKILL.md`**

将下列文件**原样**创建为 `skills/tradesense-kline/SKILL.md`（含 frontmatter）：

```markdown
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
```

- [ ] **Step 2: 提交**

```powershell
cd C:\1\projects\claw-projects\tradesense
git add skills/tradesense-kline/SKILL.md
git commit -m "feat(skills): add OpenClaw tradesense_kline SKILL"
```

---

### Task 2: 新增 OpenClaw 接线文档

**Files:**

- Create: `docs/openclaw-integration.md`

- [ ] **Step 1: 写入完整 `docs/openclaw-integration.md`**

```markdown
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
```

- [ ] **Step 2: 提交**

```powershell
cd C:\1\projects\claw-projects\tradesense
git add docs/openclaw-integration.md
git commit -m "docs: add OpenClaw integration guide"
```

---

### Task 3: README 增加入口链接

**Files:**

- Modify: `README.md`（在 `## 使用场景` 之前插入新节）

- [ ] **Step 1: 在 `README.md` 中 `## 使用场景` 标题之上插入以下 Markdown 块**

```markdown
## OpenClaw 集成

- 设计说明：`docs/specs/2026-04-19-openclaw-kline-skill-design.md`
- 接线步骤：`docs/openclaw-integration.md`
- 技能文件：`skills/tradesense-kline/SKILL.md`

本仓库内启动 Python 服务或 MCP 时，请使用 `uv run`（详见集成文档）。

```

- [ ] **Step 2: 可选 — 将「快速开始」中 `python server.py` 一行旁注兼容 uv**

在 `### 1. 启动后端服务` 代码块下方增加一句（不删除原有 `pip`/`python` 说明，避免破坏旧文档依赖）：

```markdown
（推荐）使用 uv：`uv pip install -r requirements.txt` 后 `uv run python server.py`。
```

- [ ] **Step 3: 提交**

```powershell
cd C:\1\projects\claw-projects\tradesense
git add README.md
git commit -m "docs(readme): link OpenClaw integration and uv hint"
```

---

### Task 4: 手动验收（无自动化测试）

**Files:** 无新增文件。

- [ ] **Step 1: HTTP 服务**

在 `TRADESENSE_REPO_ROOT` 执行：

```bash
uv run python server.py
```

预期：进程无立即退出；日志无 ImportError；端口监听与 `mcp_server.py` 中 `API_BASE` 一致（默认 `8765`）。

- [ ] **Step 2: MCP 进程可启动**

第二终端：

```bash
cd TRADESENSE_REPO_ROOT
uv run python mcp_server.py
```

预期：进程阻塞等待 stdio（无 traceback）；Ctrl+C 可退出。

- [ ] **Step 3: OpenClaw 侧**

完成 Task 2 文档中的 `mcp set` 与 `extraDirs` 后：

```bash
openclaw mcp list
openclaw skills list
```

预期：`tradesense` 在 MCP 列表中；技能列表含 `tradesense_kline`。

- [ ] **Step 4: 端到端**

通过你常用的 OpenClaw 对话入口发送：「用 TradeSense 拉取螺纹钢 5m 最近 20 根 K 线」。

预期：模型发起 `get_klines`（或先 `get_symbols`）；返回含 `klines` 的 JSON，无「未找到 MCP」类错误。

---

## Plan self-review

| 规格章节 | 对应任务 |
|----------|----------|
| §1 目标 / uv | Task 1 `SKILL.md`；Task 2、3 文档 |
| §3 架构 | Task 1–2 |
| §4 交付物 | Task 1–2；Task 3 链入 README |
| §5 用户接线 | Task 2 全文 |
| §6 技能内容要求 | Task 1 模板已覆盖 |
| §7 测试建议 | Task 4 |
| §8 范围外 | 无任务（刻意不碰 OpenClaw 上游） |

**Placeholder scan:** 无 TBD；`TRADESENSE_REPO_ROOT` 为文档占位符，与规格一致。

---

## Execution handoff

Plan complete and saved to `docs/specs/2026-04-19-openclaw-kline-skill-plan.md`.

**1. Subagent-Driven（推荐）** — 每个 Task 派生子代理，任务间人工快速复核。  
**2. Inline Execution** — 本会话按 Task 顺序改文件并提交。

请选择 **1** 或 **2**（或自行按 checkbox 执行，无需子代理）。
