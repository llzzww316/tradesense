# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

TradeSense 是一个 K线回放训练系统，支持多周期组合显示（显示周期 + 步进周期）。用于盘感训练、策略复盘和入场点位练习。

## 启动命令

### 后端服务
```bash
uv run python server.py
# 或
python server.py
```

服务运行在 `http://localhost:8kt5`

### 前端
直接用浏览器打开 `frontend/index.html`，或使用：
```bash
npx http-server -p 8080
```
然后访问 `http://localhost:8080/frontend/index.html`

## 架构说明

### 后端架构 (server.py)

- **FastAPI** 提供 RESTful API
- **掘金SDK** (`gm.api`) 获取历史K线数据
- **CORS** 中间件允许前端跨域访问
- 两个主要端点：
  - `GET /symbols` - 返回支持的品种列表（螺纹钢、PVC）
  - `GET /replay_data` - 获取回放数据，包含：
    - `display`: 显示周期的K线数据（用于图表结构）
    - `step`: 步进周期的K线数据（用于精细回放）
    - 支持日期范围过滤、EMA计算

### 前端架构 (frontend/index.html)

- **单文件** 的纯前端实现
- **Lightweight Charts** (TradingView) 绘制K线图
- 核心功能模块：
  - 图表初始化与渲染
  - 日期范围选择器
  - 数据加载与处理
  - 回放控制（播放/暂停/步进）
  - 键盘快捷键支持（← → 切换，空格播放/暂停）
  - 进度条与跳转

### 数据流

1. 前端通过 `/replay_data` 请求指定品种和周期的数据
2. 后端调用掘金SDK获取两个周期（显示周期 + 步进周期）的历史K线
3. 返回JSON包含 `display`（大周期）和 `step`（小周期）两组数据
4. 前端用显示周期渲染K线结构，用步进周期控制回放进度

### 支持的周期

- `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1d`

### 掘金连接配置

- Token: 存储在代码中的固定 token
- 服务器地址: `127.0.0.1:7001` (本地掘金服务)
- 数据权限: 最近180天，最早不早于 `2025-10-06`

## 依赖

后端依赖（requirements.txt）:
- `gm` - 掘金量化SDK
- `fastapi` - Web框架
- `uvicorn` - ASGI服务器
- `pandas` - 数据处理（用于EMA计算）

前端依赖（CDN引入）:
- Lightweight Charts v4.1.0

## 关键实现细节

### EMA计算
使用 pandas `ewm(span=period, adjust=False).mean()` 计算指数移动平均

### 时间处理
- 掘金返回 `bob` (bar of business) 时间字段
- 前端转换为 UTC 时间戳，显示时调整为东八区时间

### 数据同步
- 显示周期和步进周期K线通过时间戳对齐
- 步进数据量通常约为显示数据的5倍（5分钟显示 + 1分钟步进）
