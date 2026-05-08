# TradeSense - K线回放训练系统

K 线回放训练工具，支持多周期组合（显示周期 + 步进周期）。K 线来自本机通达信离线数据（经后端读取）。

## 功能

- **K线回放**：按时间步进展示历史行情
- **多周期组合**：显示周期 + 步进周期自由搭配（例如：5 分钟显示 + 1 分钟步进）
- **EMA**：EMA(10)、EMA(20)、EMA(60) 等（由后端在显示周期上计算）
- **播放控制**：播放、暂停、上一根、下一根
- **键盘快捷键**：← → 切换，空格播放/暂停
- **进度条**：点击跳转
- **模拟交易**：开平仓、权益与手续费（见前端说明）

## 技术栈

- **前端**：Lightweight Charts（TradingView 开源图表库），`frontend/index.html` + `app.js` + `styles.css`
- **后端**：Python FastAPI，`server.py` 对外 API，`data_provider.py`（tdxpy 读通达信 VIPDOC）

## 数据前提

须在本机安装通达信并下载对应扩展市场行情。后端读取 `$TRADESENSE_TDX_DIR`（默认 `C:/new_tdx`）下 `vipdoc/ds` 中的 `lc1` / `lc5` / 日线文件。品种与合约代码在 `symbols.json`，改动后由 `config.py` 按 mtime 自动热加载，**换月无需重启**。

## 快速开始

### 浏览器回放

```bash
cd tradesense
uv pip install -r requirements.txt
uv run python server.py
```

终端会提示根地址：**在浏览器打开 `http://localhost:8765/`** 即可（API 在同一端口下的 `/api`，Swagger：`/api/docs`）。

### MCP 单进程模式（OpenClaw / WSL 等）

```bash
uv run python mcp_server.py
```

MCP 直接调用业务层读取本机通达信数据，**不依赖 `server.py`**。两者可独立启动，也可并存。

### WSL 下的 MCP 配置

```json
{
  "command": "python3",
  "args": ["/home/you/tradesense/mcp_server.py"],
  "env": { "TRADESENSE_TDX_DIR": "/mnt/c/new_tdx" }
}
```

1. 选择品种，再在同一行选择具体**合约月份**（由后端扫描通达信 vipdoc，`GET /api/contracts`）
2. 选择显示周期、步进周期与日期范围（可选）
3. 点击「加载数据」，用按钮或键盘步进回放

## 项目结构

```
tradesense/
├── server.py            # FastAPI 服务（前端 + REST）
├── mcp_server.py        # MCP stdio（不依赖 server.py）
├── kline_service.py     # 业务层：品种/合约/EMA/JSON
├── config.py            # symbols.json 热加载
├── data_provider.py     # 通达信 VIPDOC 读取（tdxpy + lru_cache）
├── symbols.json         # 品种与通达信扩展市场映射
├── requirements.txt
├── README.md
├── CLAUDE.md
├── docs/
│   ├── openclaw-integration.md
│   └── specs/
└── frontend/
    ├── index.html
    ├── app.js
    └── styles.css
```

## OpenClaw 集成

- 设计说明：`docs/specs/2026-04-19-openclaw-kline-skill-design.md`
- 接线步骤：`docs/openclaw-integration.md`
- 技能文件：`skills/tradesense-kline/SKILL.md`

本仓库内启动 Python 服务或 MCP 时，请使用 `uv run`（详见集成文档）。

## 使用场景

- 盘感训练
- 策略复盘
- 入场点位练习
