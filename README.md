# TradeSense - K线回放训练系统

K线回放训练工具，支持多周期组合（显示周期 + 步进周期）。

## 功能

- 📊 **K线回放**：按时间步进展示历史行情
- 📈 **多周期组合**：显示周期 + 步进周期自由搭配
  - 例如：5分钟显示 + 1分钟步进
- 🔢 **EMA指标**：支持 EMA(10)、EMA(20)、EMA(60)
- 🎛️ **播放控制**：播放、暂停、上一根、下一根
- ⌨️ **键盘快捷键**：← → 切换，空格播放/暂停
- 📉 **进度条**：点击跳转

## 技术栈

- **前端**：Lightweight Charts (TradingView 开源图表库)
- **后端**：Python FastAPI + 掘金SDK
- **数据源**：掘金量化

## 快速开始

### 1. 启动后端服务

```bash
cd tradesense
pip install -r requirements.txt
python server.py
```

服务启动后运行在 `http://localhost:8765`

### 2. 打开前端

直接用浏览器打开 `frontend/index.html`

或者用本地服务器：

```bash
npx http-server -p 8080
```

然后访问 `http://localhost:8080/frontend/index.html`

### 3. 开始回放

1. 选择品种（螺纹钢/PVC）
2. 选择显示周期和步进周期
3. 点击"加载数据"
4. 使用 ← → 键或按钮控制回放

## 项目结构

```
tradesense/
├── server.py           # 后端服务
├── requirements.txt     # Python依赖
├── README.md
└── frontend/
    └── index.html      # 前端页面
```

## 使用场景

- 盘感训练
- 策略复盘
- 入场点位练习
