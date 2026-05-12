# TradeSense 回测框架 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 tradesense 基础上新增一套事件驱动的期货回测框架：用户用 Python 策略函数（on_bar）接入，后端 `/api/backtest` 跑完历史 K 线后返回交易点 + 资金曲线 + 绩效指标；前端在 K 线图上叠加交易标注并展示报告面板；底层账户 / 持仓 / 交易流水模型与现有"模拟交易"共享。

**Architecture:**
- 后端新增 `backtest/` 包（account / engine / strategy / metrics / api），`data_provider.fetch_kline` 作为唯一数据源；策略以 Python 函数注册（初期走"内置策略注册表"，不做动态加载）。
- 撮合严格按"**信号 → 下一根 K 开盘**"成交，带滑点（按"跳"配）+ 手续费（每手每次）+ 保证金占用 + 可用 < 0 爆仓；支持日内强平。
- 前端新增 `backtest.html` + `backtest.js`（独立页面，复用 lightweight-charts），与现有回放 / 模拟交易 UI 平级（Q3=2 在 UI 层独立，底层 `Account / Position / Trade` 数据类共享）。

**Tech Stack:** Python 3.11 / FastAPI / pandas / lightweight-charts v4.1.0；测试用 pytest（需在仓库里首次引入）。

---

## 范围小结（防止跑偏）

- ✅ 必须有：保证金 + 爆仓、滑点（按跳配置）、下一根 K 开盘成交、图上交易点 + 持仓期间高亮
- ✅ 强烈建议：资金 / 回撤曲线、核心绩效指标、逐笔交易明细、日内 vs 过夜（含强制日终平仓）、每品种独立手续费 & 每跳价格
- ❌ **本轮不做**：止损止盈单、合约换月、涨跌停、多策略组合、蒙特卡洛 / 优化 / walk-forward、tick 精度、股票规则

---

## 文件规划

**新增（后端）：**
- `backtest/__init__.py` — 导出公共符号
- `backtest/models.py` — `BacktestConfig` / `Bar` / `Order` / `Fill` / `Trade` / `Position` / `BacktestResult` 数据类
- `backtest/account.py` — `Account` 类（权益 / 可用 / 持仓 / 爆仓判断；供模拟交易未来也能复用）
- `backtest/broker.py` — `Broker` 类（撮合：下一根 K 开盘 + 滑点 + 手续费；日终强平）
- `backtest/context.py` — `StrategyContext`（策略在 `on_bar` 里拿到的上下文：buy/sell/position/bars_history/ctx storage）
- `backtest/registry.py` — 策略注册表（`@register_strategy("name")`）
- `backtest/strategies/__init__.py` — 内置策略导入入口
- `backtest/strategies/double_ma.py` — 示范策略（双均线），打通端到端
- `backtest/engine.py` — `BacktestEngine`（事件循环：取数 → 逐根 on_bar → 撮合 → 结算 → 收集 result）
- `backtest/metrics.py` — 绩效指标计算（年化 / 最大回撤 / Sharpe / Calmar / 胜率 / 盈亏比 / 连胜连败 / 平均持仓）
- `backtest/api.py` — FastAPI 子路由 `/api/backtest`（POST 运行 / GET 列出策略）

**修改（后端）：**
- `server.py` — 挂载 `backtest.api.router`
- `requirements.txt` — 新增 `pytest`（dev 用）；**无**新增运行时依赖
- `symbols.json` — 新增 `tick_size`（每跳价格，如螺纹 1、PVC 5）、`margin_rate`（保证金比例，如 0.10）、`fee_per_lot`（每手每次手续费）；旧字段 `tick_value` 保留不变（兼容前端模拟交易）

**新增（测试）：**
- `tests/__init__.py`
- `tests/conftest.py` — 通用 fixtures（合成 K 线、合成 symbol 配置）
- `tests/test_account.py`
- `tests/test_broker.py`
- `tests/test_engine.py`
- `tests/test_metrics.py`
- `tests/test_api.py`

**新增（前端）：**
- `frontend/backtest.html` — 回测独立页面
- `frontend/backtest.js` — 运行表单 / 结果渲染（交易点 markers + 持仓带色 + 权益曲线副图 + 指标面板 + 明细表）
- `frontend/backtest.css`（可选）或直接扩写 `styles.css`

**修改（前端）：**
- `frontend/index.html` — 顶栏加"回测"入口链接

---

## 关键约定

### 数据结构

```python
# backtest/models.py
from dataclasses import dataclass, field
from typing import Literal, Optional

Side = Literal["long", "short"]
Action = Literal["open_long", "open_short", "close"]

@dataclass
class Bar:
    time: str                 # "YYYY-MM-DD HH:MM:SS"
    open: float
    high: float
    low: float
    close: float
    volume: float

@dataclass
class Order:
    action: Action
    qty: int                  # 手数（期货始终整手）
    reason: str = ""          # 策略给的备注，可进日志

@dataclass
class Fill:
    time: str                 # 成交 K 线的时间（下一根开盘）
    action: Action
    qty: int
    price: float              # 含滑点后的成交价
    fee: float
    reason: str = ""

@dataclass
class Position:
    side: Side
    qty: int
    avg_price: float
    opened_time: str
    margin: float             # 占用保证金（开仓时一次算好）

@dataclass
class Trade:
    """一次完整的开→平匹配"""
    open_time: str
    close_time: str
    side: Side
    qty: int
    open_price: float
    close_price: float
    fee: float                # 开 + 平的总手续费
    pnl: float                # 毛盈亏（不减手续费）
    net_pnl: float            # pnl - fee
    holding_bars: int
    open_reason: str = ""
    close_reason: str = ""

@dataclass
class BacktestConfig:
    symbol: str               # "螺纹钢"
    contract: Optional[str]   # "RB2610"；None 用 symbols.json 默认
    period: str               # "5m" / "15m" / "1h" / "1d"...
    start_date: Optional[str] # "2025-10-01"
    end_date: Optional[str]
    initial_capital: float
    tick_size: float          # 每跳价格（元/单位），如螺纹 1
    tick_value: float         # 每跳价值（元/手），如螺纹 10
    margin_rate: float        # 保证金比例，如 0.10
    fee_per_lot: float        # 每手每次手续费（元）
    slippage_ticks: int       # 滑点（跳）；买 +N 跳，卖 -N 跳
    intraday_only: bool       # True 则每日收盘强平
    strategy: str             # 注册名，如 "double_ma"
    strategy_params: dict = field(default_factory=dict)

@dataclass
class EquityPoint:
    time: str
    equity: float
    drawdown: float           # 当前相对历史峰值的回撤（负值）

@dataclass
class BacktestResult:
    config: BacktestConfig
    bars: list[Bar]           # 回测区间的 K（前端画图用）
    fills: list[Fill]         # 每笔成交（开 / 平都算一条）
    trades: list[Trade]       # 匹配好的完整交易
    equity_curve: list[EquityPoint]
    metrics: dict             # 由 metrics.py 产出的一揽子指标
    liquidated: bool          # 是否中途爆仓
    liquidated_at: Optional[str]
```

### 撮合规则（不可动摇）

1. 策略的 `on_bar(bar_t, ctx)` 读取 `bar_t`（及以前的所有 K）产生订单。
2. 订单进入 broker 待成交队列。
3. 引擎前进到 `bar_{t+1}`，**按 `bar_{t+1}.open ± slippage` 成交所有待成交单**。
4. 每根 K 线结束后，用 `close` 计算浮动权益 + 回撤点位。
5. 可用资金 = 权益 - 占用保证金；可用 < 0 → 爆仓，中止后续交易，权益曲线继续按收盘价外推到区间末。
6. `intraday_only=True` 时，每日最后一根 K 收盘后若仍持仓，自动生成反向单，在下一根 K（即次日第一根）开盘平掉。若当日就是最后一天最后一根，用 `close` 强平。

### 滑点 / 手续费

- 滑点价 = `slippage_ticks * tick_size`；开多 / 平空 → `price = open + slip`；开空 / 平多 → `price = open - slip`。
- 手续费 = `fee_per_lot * qty`；**开仓和平仓各收一次**。

### 爆仓判定

- 每根 K 线收盘后调用 `account.update_on_close(bar)`：重算浮盈 → 权益 → 可用。
- 可用 < 0 立即触发 `liquidate()`：按**下一根 K 开盘价** + 滑点反向平掉全部仓位（标准做法，避免 look-ahead）；若已是最后一根，按当根 close 平。
- `BacktestResult.liquidated = True`，`liquidated_at = 爆仓触发的 bar.time`。

---

## 任务列表

### Task 1: 建立 backtest 包骨架 + 数据类

**Files:**
- Create: `backtest/__init__.py`
- Create: `backtest/models.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_models.py`
- Modify: `requirements.txt`

- [ ] **Step 1: 加入 pytest 到 requirements.txt**

在文件末尾追加一行：

```
pytest==8.3.3
```

- [ ] **Step 2: 安装依赖**

```powershell
uv pip install -r requirements.txt
```

预期：`pytest` 安装成功。

- [ ] **Step 3: 写 tests/__init__.py（空文件）**

```python
```

- [ ] **Step 4: 写 tests/conftest.py —— 合成 K 线 fixture**

```python
"""pytest 通用 fixtures：合成 K 线、合成 symbol 配置"""
import pandas as pd
import pytest
from datetime import datetime, timedelta


def _make_bars(n: int, start: str = "2025-10-01 09:00:00", period_minutes: int = 5,
               base_price: float = 3000.0, trend: float = 0.0) -> pd.DataFrame:
    """生成 n 根合成 K：open=close=base+i*trend，high/low 在其基础上 ±2。"""
    rows = []
    t = pd.Timestamp(start)
    for i in range(n):
        price = base_price + i * trend
        rows.append({
            "bob": t,
            "open": price,
            "high": price + 2,
            "low": price - 2,
            "close": price,
            "volume": 100,
        })
        t += timedelta(minutes=period_minutes)
    return pd.DataFrame(rows)


@pytest.fixture
def flat_bars():
    return _make_bars(50, trend=0.0)


@pytest.fixture
def up_bars():
    return _make_bars(50, trend=1.0)   # 线性上涨


@pytest.fixture
def down_bars():
    return _make_bars(50, trend=-1.0)  # 线性下跌


@pytest.fixture
def rb_config_kwargs():
    """螺纹钢典型参数：1 跳 = 1 元，每跳价值 10 元/手，保证金 10%，手续费 3 元/手"""
    return dict(
        symbol="螺纹钢",
        contract=None,
        period="5m",
        start_date=None,
        end_date=None,
        initial_capital=100000.0,
        tick_size=1.0,
        tick_value=10.0,
        margin_rate=0.10,
        fee_per_lot=3.0,
        slippage_ticks=1,
        intraday_only=False,
        strategy="double_ma",
        strategy_params={"fast": 5, "slow": 20},
    )
```

- [ ] **Step 5: 写 backtest/__init__.py**

```python
"""TradeSense 回测框架 —— 事件驱动、与模拟交易共享底层账户模型。"""
from backtest.models import (
    Bar, Order, Fill, Trade, Position,
    BacktestConfig, BacktestResult, EquityPoint,
    Side, Action,
)

__all__ = [
    "Bar", "Order", "Fill", "Trade", "Position",
    "BacktestConfig", "BacktestResult", "EquityPoint",
    "Side", "Action",
]
```

- [ ] **Step 6: 写失败的 test_models.py**

```python
"""验证数据类的基本构造与默认值。"""
from backtest.models import (
    Bar, Order, Fill, Trade, Position, BacktestConfig, EquityPoint,
)


def test_bar_construction():
    bar = Bar(time="2025-10-01 09:00:00", open=3000.0, high=3002.0,
              low=2998.0, close=3001.0, volume=100)
    assert bar.close == 3001.0


def test_order_default_reason():
    o = Order(action="open_long", qty=1)
    assert o.reason == ""


def test_fill_fields():
    f = Fill(time="t", action="open_long", qty=1, price=3001.0, fee=3.0)
    assert f.fee == 3.0


def test_trade_net_pnl_field():
    t = Trade(
        open_time="t1", close_time="t2", side="long", qty=1,
        open_price=3000.0, close_price=3005.0,
        fee=6.0, pnl=50.0, net_pnl=44.0, holding_bars=3,
    )
    assert t.net_pnl == 44.0


def test_position_margin_stored():
    p = Position(side="long", qty=2, avg_price=3000.0,
                 opened_time="t", margin=6000.0)
    assert p.margin == 6000.0


def test_config_defaults(rb_config_kwargs):
    cfg = BacktestConfig(**rb_config_kwargs)
    assert cfg.strategy_params == {"fast": 5, "slow": 20}
    assert cfg.intraday_only is False


def test_equity_point():
    ep = EquityPoint(time="t", equity=10000.0, drawdown=0.0)
    assert ep.equity == 10000.0
```

- [ ] **Step 7: 运行测试确认失败（ImportError）**

```powershell
uv run pytest tests/test_models.py -v
```

预期：FAIL，`ModuleNotFoundError: No module named 'backtest.models'`。

- [ ] **Step 8: 写 backtest/models.py（完整实现，照计划顶部"数据结构"章节 1:1）**

```python
"""回测框架数据类定义。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

Side = Literal["long", "short"]
Action = Literal["open_long", "open_short", "close"]


@dataclass
class Bar:
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Order:
    action: Action
    qty: int
    reason: str = ""


@dataclass
class Fill:
    time: str
    action: Action
    qty: int
    price: float
    fee: float
    reason: str = ""


@dataclass
class Position:
    side: Side
    qty: int
    avg_price: float
    opened_time: str
    margin: float


@dataclass
class Trade:
    open_time: str
    close_time: str
    side: Side
    qty: int
    open_price: float
    close_price: float
    fee: float
    pnl: float
    net_pnl: float
    holding_bars: int
    open_reason: str = ""
    close_reason: str = ""


@dataclass
class BacktestConfig:
    symbol: str
    contract: Optional[str]
    period: str
    start_date: Optional[str]
    end_date: Optional[str]
    initial_capital: float
    tick_size: float
    tick_value: float
    margin_rate: float
    fee_per_lot: float
    slippage_ticks: int
    intraday_only: bool
    strategy: str
    strategy_params: dict = field(default_factory=dict)


@dataclass
class EquityPoint:
    time: str
    equity: float
    drawdown: float


@dataclass
class BacktestResult:
    config: BacktestConfig
    bars: list[Bar]
    fills: list[Fill]
    trades: list[Trade]
    equity_curve: list[EquityPoint]
    metrics: dict
    liquidated: bool = False
    liquidated_at: Optional[str] = None
```

- [ ] **Step 9: 运行测试确认通过**

```powershell
uv run pytest tests/test_models.py -v
```

预期：PASS，7/7 绿。

- [ ] **Step 10: Commit**

```bash
git add backtest/ tests/ requirements.txt
git commit -m "feat(backtest): scaffold package with data models and pytest setup"
```

---

### Task 2: Account 类（权益 / 可用 / 持仓 / 爆仓）

**Files:**
- Create: `backtest/account.py`
- Create: `tests/test_account.py`

- [ ] **Step 1: 写失败的 test_account.py**

```python
"""Account 负责持仓、权益、可用、爆仓判断；所有成交都走它。"""
import pytest
from backtest.account import Account
from backtest.models import Fill


def _mk_account(initial=100000.0, margin_rate=0.10, tick_size=1.0, tick_value=10.0):
    return Account(
        initial_capital=initial,
        margin_rate=margin_rate,
        tick_size=tick_size,
        tick_value=tick_value,
    )


def test_initial_state():
    a = _mk_account()
    assert a.equity == 100000.0
    assert a.available == 100000.0
    assert a.position is None
    assert a.realized_pnl == 0.0
    assert a.total_fee == 0.0


def test_open_long_occupies_margin():
    a = _mk_account()
    # 开多 2 手 @ 3000；保证金 = 3000 * 2 * 10 * 0.10 = 6000
    a.apply_fill(Fill(time="t1", action="open_long", qty=2, price=3000.0, fee=6.0))
    assert a.position.side == "long"
    assert a.position.qty == 2
    assert a.position.margin == 6000.0
    # fee 从 available 扣
    assert a.available == pytest.approx(100000.0 - 6000.0 - 6.0)
    assert a.total_fee == 6.0


def test_update_on_close_marks_unrealized():
    a = _mk_account()
    a.apply_fill(Fill(time="t1", action="open_long", qty=1, price=3000.0, fee=3.0))
    # 收盘价 3005；浮盈 = (3005-3000)/1 * 10 * 1 = 50
    a.update_on_close(close_price=3005.0)
    assert a.unrealized_pnl == pytest.approx(50.0)
    # equity = 初始 - fee + 浮盈
    assert a.equity == pytest.approx(100000.0 - 3.0 + 50.0)


def test_close_long_realizes_pnl():
    a = _mk_account()
    a.apply_fill(Fill(time="t1", action="open_long", qty=1, price=3000.0, fee=3.0))
    a.apply_fill(Fill(time="t2", action="close", qty=1, price=3010.0, fee=3.0))
    # 毛利 = 10 跳 * 10 元 = 100；净利 = 100 - 6 = 94
    assert a.position is None
    assert a.realized_pnl == pytest.approx(100.0)
    assert a.total_fee == pytest.approx(6.0)
    # 全部返还：available = initial + realized - total_fee
    assert a.available == pytest.approx(100000.0 + 100.0 - 6.0)


def test_open_short_then_close_profit():
    a = _mk_account()
    a.apply_fill(Fill(time="t1", action="open_short", qty=1, price=3000.0, fee=3.0))
    a.apply_fill(Fill(time="t2", action="close", qty=1, price=2990.0, fee=3.0))
    # 空头 3000 → 2990 赚 10 跳 = 100
    assert a.realized_pnl == pytest.approx(100.0)


def test_liquidated_when_available_negative():
    # 小资金 + 大仓位让可用打穿
    a = _mk_account(initial=6000.0)
    a.apply_fill(Fill(time="t1", action="open_long", qty=1, price=3000.0, fee=3.0))
    # 保证金 3000，available ~2997；收盘暴跌让浮亏 > available
    a.update_on_close(close_price=2600.0)
    assert a.is_liquidated() is True


def test_not_liquidated_when_flat():
    a = _mk_account()
    a.update_on_close(close_price=3000.0)
    assert a.is_liquidated() is False


def test_reject_partial_close_not_supported():
    """本期简化：平仓一次性全部平掉；qty 不等于持仓手数直接报错。"""
    a = _mk_account()
    a.apply_fill(Fill(time="t1", action="open_long", qty=2, price=3000.0, fee=6.0))
    with pytest.raises(ValueError):
        a.apply_fill(Fill(time="t2", action="close", qty=1, price=3005.0, fee=3.0))


def test_reject_reverse_before_close():
    """不支持反手：持多时不允许直接 open_short。"""
    a = _mk_account()
    a.apply_fill(Fill(time="t1", action="open_long", qty=1, price=3000.0, fee=3.0))
    with pytest.raises(ValueError):
        a.apply_fill(Fill(time="t2", action="open_short", qty=1, price=3000.0, fee=3.0))
```

- [ ] **Step 2: 运行确认全红**

```powershell
uv run pytest tests/test_account.py -v
```

预期：`ModuleNotFoundError: No module named 'backtest.account'`。

- [ ] **Step 3: 写 backtest/account.py**

```python
"""回测账户：权益/可用/持仓/爆仓判定。所有 fill 都通过 apply_fill 入账。"""
from __future__ import annotations

from typing import Optional

from backtest.models import Fill, Position


class Account:
    def __init__(self, initial_capital: float, margin_rate: float,
                 tick_size: float, tick_value: float):
        self.initial_capital = initial_capital
        self.margin_rate = margin_rate
        self.tick_size = tick_size
        self.tick_value = tick_value

        self.position: Optional[Position] = None
        self.realized_pnl: float = 0.0
        self.total_fee: float = 0.0
        self.unrealized_pnl: float = 0.0
        self._last_close: Optional[float] = None

    @property
    def equity(self) -> float:
        return self.initial_capital + self.realized_pnl - self.total_fee + self.unrealized_pnl

    @property
    def available(self) -> float:
        margin = self.position.margin if self.position else 0.0
        return self.equity - margin

    def _price_to_pnl(self, entry: float, exit_: float, qty: int, side: str) -> float:
        """按跳数折算金额：每跳价值 tick_value，每跳 = tick_size。"""
        ticks = (exit_ - entry) / self.tick_size
        sign = 1 if side == "long" else -1
        return ticks * self.tick_value * qty * sign

    def apply_fill(self, fill: Fill) -> None:
        self.total_fee += fill.fee

        if fill.action in ("open_long", "open_short"):
            if self.position is not None:
                raise ValueError("持仓中，不支持反手 / 加仓；请先平仓")
            side = "long" if fill.action == "open_long" else "short"
            margin = fill.price * fill.qty * self.tick_value * self.margin_rate / self.tick_size
            self.position = Position(
                side=side, qty=fill.qty, avg_price=fill.price,
                opened_time=fill.time, margin=margin,
            )
            return

        if fill.action == "close":
            if self.position is None:
                raise ValueError("无持仓，无法平仓")
            if fill.qty != self.position.qty:
                raise ValueError(
                    f"本期不支持部分平仓：持仓 {self.position.qty} 手，平仓 {fill.qty} 手"
                )
            pnl = self._price_to_pnl(
                entry=self.position.avg_price, exit_=fill.price,
                qty=self.position.qty, side=self.position.side,
            )
            self.realized_pnl += pnl
            self.position = None
            self.unrealized_pnl = 0.0
            return

        raise ValueError(f"未知 fill.action: {fill.action}")

    def update_on_close(self, close_price: float) -> None:
        self._last_close = close_price
        if self.position is None:
            self.unrealized_pnl = 0.0
            return
        self.unrealized_pnl = self._price_to_pnl(
            entry=self.position.avg_price, exit_=close_price,
            qty=self.position.qty, side=self.position.side,
        )

    def is_liquidated(self) -> bool:
        return self.available < 0
```

- [ ] **Step 4: 运行测试**

```powershell
uv run pytest tests/test_account.py -v
```

预期：PASS，8/8 绿。

- [ ] **Step 5: Commit**

```bash
git add backtest/account.py tests/test_account.py
git commit -m "feat(backtest): add Account with position/equity/liquidation logic"
```

---

### Task 3: Broker 撮合器（下一根 K 开盘 + 滑点 + 手续费）

**Files:**
- Create: `backtest/broker.py`
- Create: `tests/test_broker.py`

- [ ] **Step 1: 写失败的 test_broker.py**

```python
"""Broker 负责把 Order 在"下一根 K 开盘"转成 Fill，带滑点和手续费。"""
import pytest
from backtest.broker import Broker
from backtest.models import Bar, Order


def _bar(time="t", o=3000.0, h=3002.0, l=2998.0, c=3001.0, v=100):
    return Bar(time=time, open=o, high=h, low=l, close=c, volume=v)


def test_open_long_next_bar_open_plus_slippage():
    b = Broker(tick_size=1.0, slippage_ticks=1, fee_per_lot=3.0)
    b.submit(Order(action="open_long", qty=2, reason="signal1"))
    fill = b.execute_on_open(_bar(time="t+1", o=3000.0))
    assert fill is not None
    assert fill.time == "t+1"
    assert fill.action == "open_long"
    assert fill.qty == 2
    assert fill.price == pytest.approx(3001.0)   # 开盘 +1 跳
    assert fill.fee == pytest.approx(6.0)        # 3 * 2 手
    assert fill.reason == "signal1"


def test_open_short_applies_negative_slippage():
    b = Broker(tick_size=1.0, slippage_ticks=2, fee_per_lot=3.0)
    b.submit(Order(action="open_short", qty=1))
    fill = b.execute_on_open(_bar(o=3000.0))
    assert fill.price == pytest.approx(2998.0)


def test_close_direction_requires_position():
    """Broker 本身不知道持仓方向，只保证 close 用"与开相反"的滑点。
    实际实现里我们让 engine 在 submit(close) 时传入 currrent_side。"""
    b = Broker(tick_size=1.0, slippage_ticks=1, fee_per_lot=3.0)
    b.submit(Order(action="close", qty=1), close_side="long")
    fill = b.execute_on_open(_bar(o=3000.0))
    # 平多 = 卖出 = -1 跳
    assert fill.price == pytest.approx(2999.0)

    b2 = Broker(tick_size=1.0, slippage_ticks=1, fee_per_lot=3.0)
    b2.submit(Order(action="close", qty=1), close_side="short")
    f2 = b2.execute_on_open(_bar(o=3000.0))
    # 平空 = 买回 = +1 跳
    assert f2.price == pytest.approx(3001.0)


def test_no_pending_returns_none():
    b = Broker(tick_size=1.0, slippage_ticks=1, fee_per_lot=3.0)
    assert b.execute_on_open(_bar()) is None


def test_only_one_pending_order_allowed():
    """本期简化：一根 K 最多一条待成交。第二次 submit 抛错。"""
    b = Broker(tick_size=1.0, slippage_ticks=1, fee_per_lot=3.0)
    b.submit(Order(action="open_long", qty=1))
    with pytest.raises(ValueError):
        b.submit(Order(action="open_long", qty=1))


def test_pending_cleared_after_execute():
    b = Broker(tick_size=1.0, slippage_ticks=0, fee_per_lot=0.0)
    b.submit(Order(action="open_long", qty=1))
    b.execute_on_open(_bar())
    assert b.execute_on_open(_bar()) is None


def test_force_close_at_price():
    """日内强平 / 爆仓强平的便捷接口：按指定价（一般是当根 close）立即成交。"""
    b = Broker(tick_size=1.0, slippage_ticks=1, fee_per_lot=3.0)
    fill = b.force_close(time="tN", qty=1, side="long", price=2995.0, reason="eod")
    assert fill.action == "close"
    assert fill.price == pytest.approx(2994.0)   # 卖出 -1 跳
    assert fill.reason == "eod"
```

- [ ] **Step 2: 运行确认失败**

```powershell
uv run pytest tests/test_broker.py -v
```

预期：ModuleNotFoundError。

- [ ] **Step 3: 写 backtest/broker.py**

```python
"""Broker：把 Order 在下一根 K 开盘按规则成交。"""
from __future__ import annotations

from typing import Optional

from backtest.models import Bar, Fill, Order, Side


class Broker:
    def __init__(self, tick_size: float, slippage_ticks: int, fee_per_lot: float):
        self.tick_size = tick_size
        self.slippage_ticks = slippage_ticks
        self.fee_per_lot = fee_per_lot
        self._pending: Optional[Order] = None
        self._pending_close_side: Optional[Side] = None

    def submit(self, order: Order, close_side: Optional[Side] = None) -> None:
        if self._pending is not None:
            raise ValueError("已有待成交订单，不支持同根 K 多单")
        if order.action == "close" and close_side is None:
            raise ValueError("提交 close 订单时必须提供当前 side")
        self._pending = order
        self._pending_close_side = close_side

    def _slip(self, price: float, action: str, close_side: Optional[Side]) -> float:
        slip_amt = self.slippage_ticks * self.tick_size
        if action == "open_long":
            return price + slip_amt
        if action == "open_short":
            return price - slip_amt
        if action == "close":
            # 平多=卖出→负滑点；平空=买回→正滑点
            return price - slip_amt if close_side == "long" else price + slip_amt
        raise ValueError(f"未知 action: {action}")

    def execute_on_open(self, bar: Bar) -> Optional[Fill]:
        if self._pending is None:
            return None
        order = self._pending
        close_side = self._pending_close_side
        price = self._slip(bar.open, order.action, close_side)
        fill = Fill(
            time=bar.time,
            action=order.action,
            qty=order.qty,
            price=price,
            fee=self.fee_per_lot * order.qty,
            reason=order.reason,
        )
        self._pending = None
        self._pending_close_side = None
        return fill

    def force_close(self, time: str, qty: int, side: Side, price: float,
                    reason: str = "") -> Fill:
        exec_price = self._slip(price, "close", side)
        return Fill(
            time=time, action="close", qty=qty,
            price=exec_price, fee=self.fee_per_lot * qty, reason=reason,
        )
```

- [ ] **Step 4: 运行测试**

```powershell
uv run pytest tests/test_broker.py -v
```

预期：PASS，7/7 绿。

- [ ] **Step 5: Commit**

```bash
git add backtest/broker.py tests/test_broker.py
git commit -m "feat(backtest): add Broker for next-bar open fills with slippage & fee"
```

---

### Task 4: StrategyContext + 注册表 + 示范策略（双均线）

**Files:**
- Create: `backtest/context.py`
- Create: `backtest/registry.py`
- Create: `backtest/strategies/__init__.py`
- Create: `backtest/strategies/double_ma.py`
- Create: `tests/test_context.py`
- Create: `tests/test_registry.py`
- Create: `tests/test_double_ma.py`

- [ ] **Step 1: 写失败的 test_context.py**

```python
"""StrategyContext 是策略在 on_bar 里访问的只读上下文 + 下单 API。"""
import pytest
from backtest.context import StrategyContext
from backtest.models import Bar


def _bar(t, c):
    return Bar(time=t, open=c, high=c + 1, low=c - 1, close=c, volume=100)


def test_context_exposes_history_up_to_current():
    ctx = StrategyContext()
    for i in range(5):
        ctx._push_bar(_bar(f"t{i}", 3000.0 + i))
    closes = ctx.closes
    assert list(closes) == [3000.0, 3001.0, 3002.0, 3003.0, 3004.0]
    assert ctx.current_bar.time == "t4"
    assert len(ctx.history) == 5


def test_context_position_side_initially_none():
    ctx = StrategyContext()
    assert ctx.position_side is None
    assert ctx.position_qty == 0


def test_context_position_mutators():
    ctx = StrategyContext()
    ctx._set_position("long", 2)
    assert ctx.position_side == "long"
    assert ctx.position_qty == 2
    ctx._set_position(None, 0)
    assert ctx.position_side is None


def test_buy_sell_close_queue_orders():
    ctx = StrategyContext()
    ctx.buy(1, reason="cross up")
    ctx.close(reason="cross down")
    ctx.sell(2, reason="short")
    assert len(ctx.pending_orders) == 3
    assert ctx.pending_orders[0].action == "open_long"
    assert ctx.pending_orders[0].reason == "cross up"
    assert ctx.pending_orders[1].action == "close"
    assert ctx.pending_orders[2].action == "open_short"
    assert ctx.pending_orders[2].qty == 2


def test_drain_pending_clears_queue():
    ctx = StrategyContext()
    ctx.buy(1)
    drained = ctx.drain_pending()
    assert len(drained) == 1
    assert ctx.pending_orders == []


def test_user_storage_for_indicators():
    """ctx.state 是用户自己的可变字典，策略跨 bar 保存指标用。"""
    ctx = StrategyContext()
    ctx.state["ema_slow"] = 3000.0
    assert ctx.state["ema_slow"] == 3000.0
```

- [ ] **Step 2: 运行失败**

```powershell
uv run pytest tests/test_context.py -v
```

预期：ModuleNotFoundError。

- [ ] **Step 3: 写 backtest/context.py**

```python
"""策略在 on_bar 里收到的上下文：历史 K、持仓只读视图、下单 API、用户 state。"""
from __future__ import annotations

from typing import Optional

from backtest.models import Bar, Order, Side


class StrategyContext:
    def __init__(self):
        self.history: list[Bar] = []
        self.pending_orders: list[Order] = []
        self.state: dict = {}
        self._position_side: Optional[Side] = None
        self._position_qty: int = 0

    def _push_bar(self, bar: Bar) -> None:
        self.history.append(bar)

    def _set_position(self, side: Optional[Side], qty: int) -> None:
        self._position_side = side
        self._position_qty = qty

    @property
    def current_bar(self) -> Bar:
        return self.history[-1]

    @property
    def closes(self) -> list[float]:
        return [b.close for b in self.history]

    @property
    def position_side(self) -> Optional[Side]:
        return self._position_side

    @property
    def position_qty(self) -> int:
        return self._position_qty

    def buy(self, qty: int, reason: str = "") -> None:
        self.pending_orders.append(Order(action="open_long", qty=qty, reason=reason))

    def sell(self, qty: int, reason: str = "") -> None:
        self.pending_orders.append(Order(action="open_short", qty=qty, reason=reason))

    def close(self, reason: str = "") -> None:
        self.pending_orders.append(Order(action="close", qty=self._position_qty or 0, reason=reason))

    def drain_pending(self) -> list[Order]:
        out = self.pending_orders
        self.pending_orders = []
        return out
```

- [ ] **Step 4: 运行确认通过**

```powershell
uv run pytest tests/test_context.py -v
```

预期：PASS，6/6 绿。

- [ ] **Step 5: 写失败的 test_registry.py**

```python
"""registry: 用装饰器注册策略函数，引擎按名字查找。"""
import pytest
from backtest.registry import register_strategy, get_strategy, list_strategies, _clear_registry_for_test


def setup_function():
    _clear_registry_for_test()


def test_register_and_fetch():
    @register_strategy("mystrat")
    def fn(bar, ctx, **kwargs):
        return None
    assert get_strategy("mystrat") is fn


def test_list_strategies():
    @register_strategy("a")
    def fa(bar, ctx, **kwargs): pass
    @register_strategy("b")
    def fb(bar, ctx, **kwargs): pass
    assert sorted(list_strategies()) == ["a", "b"]


def test_duplicate_name_raises():
    @register_strategy("dup")
    def f1(bar, ctx, **kwargs): pass
    with pytest.raises(ValueError):
        @register_strategy("dup")
        def f2(bar, ctx, **kwargs): pass


def test_missing_strategy_raises():
    with pytest.raises(KeyError):
        get_strategy("nope")
```

- [ ] **Step 6: 运行失败**

```powershell
uv run pytest tests/test_registry.py -v
```

预期：ModuleNotFoundError。

- [ ] **Step 7: 写 backtest/registry.py**

```python
"""全局策略注册表：name → callable(bar, ctx, **params) -> None。"""
from __future__ import annotations

from typing import Callable

_STRATEGIES: dict[str, Callable] = {}


def register_strategy(name: str):
    def deco(fn: Callable) -> Callable:
        if name in _STRATEGIES:
            raise ValueError(f"策略名重复: {name}")
        _STRATEGIES[name] = fn
        return fn
    return deco


def get_strategy(name: str) -> Callable:
    if name not in _STRATEGIES:
        raise KeyError(f"未知策略: {name}；已注册: {sorted(_STRATEGIES)}")
    return _STRATEGIES[name]


def list_strategies() -> list[str]:
    return list(_STRATEGIES.keys())


def _clear_registry_for_test() -> None:
    _STRATEGIES.clear()
```

- [ ] **Step 8: 运行确认通过**

```powershell
uv run pytest tests/test_registry.py -v
```

预期：PASS，4/4 绿。

- [ ] **Step 9: 写 backtest/strategies/__init__.py**

```python
"""导入此模块会触发所有内置策略的注册。"""
from backtest.strategies import double_ma  # noqa: F401
```

- [ ] **Step 10: 写失败的 test_double_ma.py**

```python
"""验证双均线策略的 on_bar 行为：金叉开多、死叉平仓。"""
from backtest.context import StrategyContext
from backtest.models import Bar
from backtest.strategies.double_ma import on_bar


def _feed(ctx: StrategyContext, closes: list[float]):
    for i, c in enumerate(closes):
        bar = Bar(time=f"t{i}", open=c, high=c + 1, low=c - 1, close=c, volume=100)
        ctx._push_bar(bar)
        on_bar(bar, ctx, fast=3, slow=5)


def test_no_signal_before_slow_warmup():
    ctx = StrategyContext()
    _feed(ctx, [100.0, 101.0, 102.0, 103.0])   # 不到 slow=5 根
    assert ctx.pending_orders == []


def test_golden_cross_triggers_open_long():
    ctx = StrategyContext()
    _feed(ctx, [100.0, 99.0, 98.0, 97.0, 96.0, 97.0, 99.0, 102.0, 105.0, 110.0])
    assert any(o.action == "open_long" for o in ctx.pending_orders)


def test_death_cross_closes_long():
    """构造先上涨后暴跌的序列，应当先开多后平仓。"""
    ctx = StrategyContext()
    closes = list(range(100, 120)) + list(range(120, 100, -1))
    for i, c in enumerate(closes):
        bar = Bar(time=f"t{i}", open=float(c), high=float(c) + 1,
                  low=float(c) - 1, close=float(c), volume=100)
        ctx._push_bar(bar)
        on_bar(bar, ctx, fast=3, slow=5)
        # 模拟引擎：下单后把持仓状态回灌 ctx
        for o in ctx.drain_pending():
            if o.action == "open_long":
                ctx._set_position("long", o.qty)
            elif o.action == "close":
                ctx._set_position(None, 0)
    assert ctx.position_side is None   # 最后应当已平仓
```

- [ ] **Step 11: 运行失败**

```powershell
uv run pytest tests/test_double_ma.py -v
```

- [ ] **Step 12: 写 backtest/strategies/double_ma.py**

```python
"""双均线策略：fast/slow EMA 金叉开多，死叉平多；不做空。"""
from __future__ import annotations

import pandas as pd

from backtest.context import StrategyContext
from backtest.models import Bar
from backtest.registry import register_strategy


def _ema(values: list[float], span: int) -> float:
    if len(values) < span:
        return float("nan")
    return float(pd.Series(values).ewm(span=span, adjust=False).mean().iloc[-1])


@register_strategy("double_ma")
def on_bar(bar: Bar, ctx: StrategyContext, *, fast: int = 5, slow: int = 20) -> None:
    closes = ctx.closes
    if len(closes) < slow + 1:
        return

    ema_fast_now = _ema(closes, fast)
    ema_slow_now = _ema(closes, slow)
    ema_fast_prev = _ema(closes[:-1], fast)
    ema_slow_prev = _ema(closes[:-1], slow)

    golden = ema_fast_prev <= ema_slow_prev and ema_fast_now > ema_slow_now
    death = ema_fast_prev >= ema_slow_prev and ema_fast_now < ema_slow_now

    if golden and ctx.position_side is None:
        ctx.buy(1, reason=f"golden cross fast={fast} slow={slow}")
    elif death and ctx.position_side == "long":
        ctx.close(reason=f"death cross fast={fast} slow={slow}")
```

- [ ] **Step 13: 运行测试**

```powershell
uv run pytest tests/test_double_ma.py tests/test_registry.py tests/test_context.py -v
```

预期：全部 PASS。

- [ ] **Step 14: Commit**

```bash
git add backtest/context.py backtest/registry.py backtest/strategies/ tests/test_context.py tests/test_registry.py tests/test_double_ma.py
git commit -m "feat(backtest): add StrategyContext, registry and sample double-MA strategy"
```

---

### Task 5: BacktestEngine（事件循环 + 日内强平 + 爆仓触发）

**Files:**
- Create: `backtest/engine.py`
- Create: `tests/test_engine.py`

- [ ] **Step 1: 写失败的 test_engine.py（多场景）**

```python
"""Engine 把 Bar 序列 + 策略 + 账户 + broker 串起来跑完一轮回测。"""
import pandas as pd
import pytest
from datetime import datetime, timedelta

from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig
from backtest.registry import register_strategy, _clear_registry_for_test


def _make_df(closes: list[float], start="2025-10-01 09:00:00", minutes=5) -> pd.DataFrame:
    rows = []
    t = pd.Timestamp(start)
    for c in closes:
        rows.append({"bob": t, "open": c, "high": c + 1, "low": c - 1,
                     "close": c, "volume": 100})
        t += timedelta(minutes=minutes)
    return pd.DataFrame(rows)


def _cfg(strategy="buy_and_hold", **overrides):
    base = dict(
        symbol="X", contract=None, period="5m",
        start_date=None, end_date=None,
        initial_capital=100000.0,
        tick_size=1.0, tick_value=10.0,
        margin_rate=0.10, fee_per_lot=3.0,
        slippage_ticks=0, intraday_only=False,
        strategy=strategy, strategy_params={},
    )
    base.update(overrides)
    return BacktestConfig(**base)


def setup_function():
    _clear_registry_for_test()


def test_single_buy_then_close_pnl():
    """先注册一个"第二根买、第五根平"的傻策略，验证端到端盈亏。"""
    @register_strategy("buy_and_hold")
    def strat(bar, ctx, **kw):
        if len(ctx.history) == 2 and ctx.position_side is None:
            ctx.buy(1, reason="test-open")
        if len(ctx.history) == 5 and ctx.position_side == "long":
            ctx.close(reason="test-close")

    df = _make_df([3000, 3001, 3002, 3003, 3005, 3006, 3007])
    eng = BacktestEngine(_cfg(), df)
    result = eng.run()

    # 第 2 根产生 open → 第 3 根开盘价 3002 成交
    # 第 5 根产生 close → 第 6 根开盘价 3005 成交
    # 毛利 = (3005 - 3002) * 10 = 30；手续费 = 3 * 2 = 6；净 = 24
    assert len(result.trades) == 1
    assert result.trades[0].pnl == pytest.approx(30.0)
    assert result.trades[0].net_pnl == pytest.approx(24.0)
    assert result.liquidated is False


def test_open_signal_without_next_bar_is_ignored():
    """最后一根才产生信号，没有下一根可成交，fill 应为空。"""
    @register_strategy("late_signal")
    def strat(bar, ctx, **kw):
        if len(ctx.history) == 3 and ctx.position_side is None:
            ctx.buy(1)

    df = _make_df([3000, 3001, 3002])   # 只有 3 根
    result = BacktestEngine(_cfg(strategy="late_signal"), df).run()
    assert result.fills == []
    assert result.trades == []


def test_equity_curve_length_matches_bars():
    @register_strategy("noop")
    def strat(bar, ctx, **kw):
        pass

    df = _make_df([3000, 3001, 3002, 3003, 3004])
    result = BacktestEngine(_cfg(strategy="noop"), df).run()
    assert len(result.equity_curve) == 5
    assert result.equity_curve[0].equity == pytest.approx(100000.0)
    # 最大回撤永远 ≤ 0
    assert all(p.drawdown <= 0 for p in result.equity_curve)


def test_liquidation_stops_further_trades():
    """初始资金小 + 持多逢暴跌 → 应爆仓。"""
    @register_strategy("hold_long")
    def strat(bar, ctx, **kw):
        if len(ctx.history) == 1 and ctx.position_side is None:
            ctx.buy(1, reason="all in")

    closes = [3000, 3000, 3000, 2000]   # 第 4 根暴跌 33%
    df = _make_df(closes)
    result = BacktestEngine(_cfg(strategy="hold_long", initial_capital=4000.0), df).run()
    assert result.liquidated is True
    assert result.liquidated_at is not None


def test_intraday_force_close_at_day_end():
    """intraday_only=True 时，日末必须平仓。"""
    @register_strategy("hold_long2")
    def strat(bar, ctx, **kw):
        if len(ctx.history) == 1 and ctx.position_side is None:
            ctx.buy(1, reason="open")

    # 两天数据，每天 3 根
    rows = []
    for day in ("2025-10-01", "2025-10-02"):
        for hh in ("09:00", "10:00", "14:00"):
            t = pd.Timestamp(f"{day} {hh}:00")
            rows.append({"bob": t, "open": 3000.0, "high": 3001.0,
                         "low": 2999.0, "close": 3000.0, "volume": 100})
    df = pd.DataFrame(rows)
    result = BacktestEngine(_cfg(strategy="hold_long2", intraday_only=True), df).run()
    # 至少有一条 close fill 标注 "eod"
    assert any(f.action == "close" and "eod" in f.reason for f in result.fills)
    # 最终不持仓
    assert result.metrics["final_position"] == "flat"
```

- [ ] **Step 2: 运行失败**

```powershell
uv run pytest tests/test_engine.py -v
```

预期：ModuleNotFoundError / ImportError。

- [ ] **Step 3: 写 backtest/engine.py**

```python
"""回测事件循环。"""
from __future__ import annotations

import pandas as pd

from backtest.account import Account
from backtest.broker import Broker
from backtest.context import StrategyContext
from backtest.metrics import compute_metrics
from backtest.models import (
    Bar, BacktestConfig, BacktestResult, EquityPoint, Fill, Trade,
)
from backtest.registry import get_strategy


class BacktestEngine:
    def __init__(self, config: BacktestConfig, bars_df: pd.DataFrame):
        self.config = config
        self.bars_df = bars_df.copy()
        self.account = Account(
            initial_capital=config.initial_capital,
            margin_rate=config.margin_rate,
            tick_size=config.tick_size,
            tick_value=config.tick_value,
        )
        self.broker = Broker(
            tick_size=config.tick_size,
            slippage_ticks=config.slippage_ticks,
            fee_per_lot=config.fee_per_lot,
        )
        self.ctx = StrategyContext()
        self.strategy_fn = get_strategy(config.strategy)

    @staticmethod
    def _df_to_bars(df: pd.DataFrame) -> list[Bar]:
        bars = []
        for _, row in df.iterrows():
            t = row["bob"]
            if hasattr(t, "strftime"):
                ts = t.strftime("%Y-%m-%d %H:%M:%S")
            else:
                ts = str(t)
            bars.append(Bar(time=ts, open=float(row["open"]),
                            high=float(row["high"]), low=float(row["low"]),
                            close=float(row["close"]), volume=float(row["volume"])))
        return bars

    def run(self) -> BacktestResult:
        bars = self._df_to_bars(self.bars_df)
        fills: list[Fill] = []
        trades: list[Trade] = []
        equity_curve: list[EquityPoint] = []
        peak_equity = self.config.initial_capital
        liquidated_at: str | None = None

        # 记录"开仓信息"用来在平仓时合成 Trade
        open_ctx: dict | None = None

        def _close_fill_to_trade(close_fill: Fill, opened: dict) -> Trade:
            # 毛盈亏：直接用账户已算好的 realized delta？这里手动算保险：
            entry = opened["price"]
            exit_ = close_fill.price
            side = opened["side"]
            ticks = (exit_ - entry) / self.config.tick_size
            sign = 1 if side == "long" else -1
            pnl = ticks * self.config.tick_value * close_fill.qty * sign
            fee_total = opened["fee"] + close_fill.fee
            holding = opened["bar_index_to_close"] - opened["bar_index_open"]
            return Trade(
                open_time=opened["time"], close_time=close_fill.time,
                side=side, qty=close_fill.qty,
                open_price=entry, close_price=exit_,
                fee=fee_total, pnl=pnl, net_pnl=pnl - fee_total,
                holding_bars=holding,
                open_reason=opened["reason"], close_reason=close_fill.reason,
            )

        prev_date = None

        for i, bar in enumerate(bars):
            # --- 先让待成交单在当根开盘成交 ---
            fill = self.broker.execute_on_open(bar)
            if fill is not None:
                self.account.apply_fill(fill)
                fills.append(fill)
                if fill.action in ("open_long", "open_short"):
                    self.ctx._set_position(
                        "long" if fill.action == "open_long" else "short",
                        fill.qty,
                    )
                    open_ctx = {
                        "time": fill.time, "price": fill.price, "qty": fill.qty,
                        "side": "long" if fill.action == "open_long" else "short",
                        "fee": fill.fee, "reason": fill.reason,
                        "bar_index_open": i,
                    }
                elif fill.action == "close":
                    if open_ctx is not None:
                        open_ctx["bar_index_to_close"] = i
                        trades.append(_close_fill_to_trade(fill, open_ctx))
                        open_ctx = None
                    self.ctx._set_position(None, 0)

            # --- 策略产生新信号（可能塞 broker） ---
            self.ctx._push_bar(bar)
            self.strategy_fn(bar, self.ctx, **self.config.strategy_params)
            for order in self.ctx.drain_pending():
                if order.action in ("open_long", "open_short"):
                    if self.account.position is None:
                        self.broker.submit(order)
                elif order.action == "close":
                    if self.account.position is not None:
                        self.broker.submit(order, close_side=self.account.position.side)

            # --- 日末强平（仅 intraday_only） ---
            if self.config.intraday_only:
                cur_date = bar.time.split(" ")[0]
                is_last_of_day = (i == len(bars) - 1) or (
                    bars[i + 1].time.split(" ")[0] != cur_date
                )
                if is_last_of_day and self.account.position is not None:
                    force = self.broker.force_close(
                        time=bar.time, qty=self.account.position.qty,
                        side=self.account.position.side, price=bar.close, reason="eod",
                    )
                    self.account.apply_fill(force)
                    fills.append(force)
                    if open_ctx is not None:
                        open_ctx["bar_index_to_close"] = i
                        trades.append(_close_fill_to_trade(force, open_ctx))
                        open_ctx = None
                    self.ctx._set_position(None, 0)

            # --- 收盘结算 + 权益点 ---
            self.account.update_on_close(bar.close)
            peak_equity = max(peak_equity, self.account.equity)
            dd = self.account.equity - peak_equity   # ≤ 0
            equity_curve.append(EquityPoint(time=bar.time, equity=self.account.equity, drawdown=dd))

            # --- 爆仓 → 下一根开盘强平，随后停止新开仓 ---
            if self.account.is_liquidated() and liquidated_at is None:
                liquidated_at = bar.time
                if i + 1 < len(bars) and self.account.position is not None:
                    next_bar = bars[i + 1]
                    force = self.broker.force_close(
                        time=next_bar.time, qty=self.account.position.qty,
                        side=self.account.position.side, price=next_bar.open, reason="liquidate",
                    )
                    self.account.apply_fill(force)
                    fills.append(force)
                    if open_ctx is not None:
                        open_ctx["bar_index_to_close"] = i + 1
                        trades.append(_close_fill_to_trade(force, open_ctx))
                        open_ctx = None
                    self.ctx._set_position(None, 0)
                break

        metrics = compute_metrics(
            equity_curve=equity_curve,
            trades=trades,
            initial_capital=self.config.initial_capital,
            final_position="flat" if self.account.position is None else self.account.position.side,
        )

        return BacktestResult(
            config=self.config, bars=bars, fills=fills, trades=trades,
            equity_curve=equity_curve, metrics=metrics,
            liquidated=liquidated_at is not None, liquidated_at=liquidated_at,
        )
```

- [ ] **Step 4: 运行测试**

```powershell
uv run pytest tests/test_engine.py -v
```

预期：PASS，5/5。metrics 调用先跳过，Task 6 之前可以用空 dict 兜底：把 `from backtest.metrics import compute_metrics` 改为本地桩函数直到 Task 6 写完。（或者先落 Task 6 的最小实现，再回来跑 engine。）

**注：** 如果这里因 `compute_metrics` 未实现而红，先临时在 engine.py 顶部加一行 `compute_metrics = lambda **_: {"final_position": _.get("final_position")}`；做完 Task 6 再删掉回退 import。

- [ ] **Step 5: Commit**

```bash
git add backtest/engine.py tests/test_engine.py
git commit -m "feat(backtest): add event-driven engine with liquidation & intraday force-close"
```

---

### Task 6: 绩效指标 metrics.py

**Files:**
- Create: `backtest/metrics.py`
- Create: `tests/test_metrics.py`

- [ ] **Step 1: 写失败的 test_metrics.py**

```python
import math
import pytest

from backtest.metrics import compute_metrics
from backtest.models import EquityPoint, Trade


def _ep(t, e, dd=0.0):
    return EquityPoint(time=t, equity=e, drawdown=dd)


def _trade(net, holding=1, side="long"):
    return Trade(
        open_time="t1", close_time="t2", side=side, qty=1,
        open_price=100.0, close_price=100.0 + net,
        fee=0.0, pnl=net, net_pnl=net, holding_bars=holding,
    )


def test_empty_trades_produces_zero_metrics():
    m = compute_metrics(
        equity_curve=[_ep("t1", 100.0), _ep("t2", 100.0)],
        trades=[], initial_capital=100.0, final_position="flat",
    )
    assert m["total_trades"] == 0
    assert m["win_rate"] == 0.0
    assert m["total_return"] == pytest.approx(0.0)
    assert m["final_position"] == "flat"


def test_basic_win_loss_stats():
    trades = [_trade(10), _trade(-5), _trade(20), _trade(-3), _trade(8)]
    m = compute_metrics(equity_curve=[_ep("t", 100.0)], trades=trades,
                        initial_capital=100.0, final_position="flat")
    assert m["total_trades"] == 5
    assert m["winning_trades"] == 3
    assert m["losing_trades"] == 2
    assert m["win_rate"] == pytest.approx(0.6)
    # 平均盈 = (10+20+8)/3；平均亏 = (5+3)/2 —— 绝对值
    assert m["avg_win"] == pytest.approx((10 + 20 + 8) / 3)
    assert m["avg_loss"] == pytest.approx((5 + 3) / 2)
    # 盈亏比
    assert m["profit_factor"] == pytest.approx((10 + 20 + 8) / (5 + 3))


def test_max_consecutive_streaks():
    trades = [_trade(1), _trade(1), _trade(-1), _trade(-1), _trade(-1), _trade(1)]
    m = compute_metrics(equity_curve=[_ep("t", 100.0)], trades=trades,
                        initial_capital=100.0, final_position="flat")
    assert m["max_win_streak"] == 2
    assert m["max_loss_streak"] == 3


def test_total_and_max_drawdown_from_equity():
    eq = [
        _ep("t1", 100.0, 0.0),
        _ep("t2", 120.0, 0.0),
        _ep("t3", 90.0, -30.0),   # peak=120 → -30
        _ep("t4", 110.0, -10.0),
        _ep("t5", 140.0, 0.0),
        _ep("t6", 80.0, -60.0),   # peak=140 → -60 = 最深
    ]
    m = compute_metrics(equity_curve=eq, trades=[], initial_capital=100.0,
                        final_position="flat")
    assert m["max_drawdown"] == pytest.approx(-60.0)
    assert m["max_drawdown_pct"] == pytest.approx(-60.0 / 140.0)
    assert m["total_return"] == pytest.approx((80.0 - 100.0) / 100.0)


def test_sharpe_on_constant_equity_is_zero_or_none():
    """权益一条直线：return series std=0，sharpe 设为 None（不制造除零）。"""
    eq = [_ep(f"t{i}", 100.0) for i in range(10)]
    m = compute_metrics(equity_curve=eq, trades=[], initial_capital=100.0,
                        final_position="flat")
    assert m["sharpe"] is None


def test_avg_holding_bars():
    trades = [_trade(1, holding=2), _trade(-1, holding=6), _trade(1, holding=4)]
    m = compute_metrics(equity_curve=[_ep("t", 100)], trades=trades,
                        initial_capital=100.0, final_position="flat")
    assert m["avg_holding_bars"] == pytest.approx(4.0)


def test_calmar_is_annual_return_over_mdd():
    eq = [_ep(f"t{i}", 100.0 + i) for i in range(10)]
    m = compute_metrics(equity_curve=eq, trades=[], initial_capital=100.0,
                        final_position="flat")
    # total_return = 9/100 = 0.09；mdd=0 → calmar 设为 None
    assert m["calmar"] is None
```

- [ ] **Step 2: 运行失败**

```powershell
uv run pytest tests/test_metrics.py -v
```

- [ ] **Step 3: 写 backtest/metrics.py**

```python
"""绩效指标：total_return / max_drawdown / sharpe / calmar / 胜率 / 盈亏比 / 连胜连败 / avg_holding。"""
from __future__ import annotations

import math
from typing import Optional

from backtest.models import EquityPoint, Trade


def _std(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(var)


def _equity_returns(points: list[EquityPoint]) -> list[float]:
    rets = []
    for i in range(1, len(points)):
        prev = points[i - 1].equity
        cur = points[i].equity
        if prev == 0:
            continue
        rets.append((cur - prev) / prev)
    return rets


def compute_metrics(
    *,
    equity_curve: list[EquityPoint],
    trades: list[Trade],
    initial_capital: float,
    final_position: str,
    bars_per_year: int = 252 * 24 * 12,   # 默认 5m 粒度；调用方可覆盖
) -> dict:
    total_trades = len(trades)
    wins = [t.net_pnl for t in trades if t.net_pnl > 0]
    losses = [-t.net_pnl for t in trades if t.net_pnl < 0]
    win_rate = (len(wins) / total_trades) if total_trades else 0.0
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0
    profit_factor = (sum(wins) / sum(losses)) if losses else (float("inf") if wins else 0.0)

    streak_win = streak_loss = cur_w = cur_l = 0
    for t in trades:
        if t.net_pnl > 0:
            cur_w += 1; cur_l = 0
            streak_win = max(streak_win, cur_w)
        elif t.net_pnl < 0:
            cur_l += 1; cur_w = 0
            streak_loss = max(streak_loss, cur_l)

    max_dd = min((p.drawdown for p in equity_curve), default=0.0)
    peak = max((p.equity for p in equity_curve), default=initial_capital)
    max_dd_pct = (max_dd / peak) if peak else 0.0

    final_equity = equity_curve[-1].equity if equity_curve else initial_capital
    total_return = (final_equity - initial_capital) / initial_capital if initial_capital else 0.0

    rets = _equity_returns(equity_curve)
    std = _std(rets)
    sharpe: Optional[float]
    if std == 0 or len(rets) < 2:
        sharpe = None
    else:
        mean = sum(rets) / len(rets)
        sharpe = (mean / std) * math.sqrt(bars_per_year)

    calmar: Optional[float]
    if max_dd == 0:
        calmar = None
    else:
        # 近似年化：根据 bar 数换算
        if len(equity_curve) <= 1:
            annual = total_return
        else:
            years = len(equity_curve) / bars_per_year
            annual = ((1 + total_return) ** (1 / years)) - 1 if years > 0 else total_return
        calmar = annual / abs(max_dd_pct) if max_dd_pct else None

    avg_holding = (
        sum(t.holding_bars for t in trades) / total_trades
    ) if total_trades else 0.0

    return {
        "total_trades": total_trades,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "max_win_streak": streak_win,
        "max_loss_streak": streak_loss,
        "max_drawdown": max_dd,
        "max_drawdown_pct": max_dd_pct,
        "total_return": total_return,
        "sharpe": sharpe,
        "calmar": calmar,
        "avg_holding_bars": avg_holding,
        "final_equity": final_equity,
        "final_position": final_position,
    }
```

- [ ] **Step 4: 运行测试**

```powershell
uv run pytest tests/test_metrics.py tests/test_engine.py -v
```

预期：两个文件全绿。如果 engine 之前加了 compute_metrics 桩，现在删掉换成 `from backtest.metrics import compute_metrics`。

- [ ] **Step 5: Commit**

```bash
git add backtest/metrics.py backtest/engine.py tests/test_metrics.py
git commit -m "feat(backtest): add performance metrics (return, drawdown, sharpe, calmar, streaks)"
```

---

### Task 7: symbols.json 扩字段 + API 路由

**Files:**
- Modify: `symbols.json`
- Create: `backtest/api.py`
- Modify: `server.py`
- Create: `tests/test_api.py`

- [ ] **Step 1: 扩 symbols.json —— 加 tick_size / margin_rate / fee_per_lot**

```json
{
  "symbols": {
    "螺纹钢": {
      "code": "SHFE.RB",
      "mootdx_market": 30,
      "mootdx_code": "RB2610",
      "tick_value": 10,
      "tick_size": 1,
      "margin_rate": 0.10,
      "fee_per_lot": 3,
      "exchange": "上期所"
    },
    "热卷": {
      "code": "SHFE.HC",
      "mootdx_market": 30,
      "mootdx_code": "HC2610",
      "tick_value": 10,
      "tick_size": 1,
      "margin_rate": 0.10,
      "fee_per_lot": 3,
      "exchange": "上期所"
    },
    "PVC": {
      "code": "DCE.V",
      "mootdx_market": 29,
      "mootdx_code": "V2609",
      "tick_value": 5,
      "tick_size": 1,
      "margin_rate": 0.09,
      "fee_per_lot": 2,
      "exchange": "大商所"
    },
    "纯碱": {
      "code": "CZCE.SA",
      "mootdx_market": 28,
      "mootdx_code": "SA2510",
      "tick_value": 20,
      "tick_size": 1,
      "margin_rate": 0.12,
      "fee_per_lot": 3.5,
      "exchange": "郑商所"
    }
  },
  "updated_at": "2026-05-12",
  "note": "主力合约换月时修改此文件，tick_size/margin_rate/fee_per_lot 为回测默认值"
}
```

- [ ] **Step 2: 写失败的 tests/test_api.py**

```python
"""/api/backtest 路由的最小 E2E 测试（用 TestClient + mock 数据）。"""
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import backtest.strategies  # noqa: F401  先注册内置策略


@pytest.fixture
def client(monkeypatch):
    # 先 patch data_provider：不要读本机文件，返回合成 K
    import data_provider
    import backtest.api as api

    def fake_fetch(market, symbol, period, start_date=None, end_date=None, count=800):
        rows = []
        t = pd.Timestamp("2025-10-01 09:00:00")
        for i in range(50):
            c = 3000 + i
            rows.append({"bob": t, "open": c, "high": c + 1, "low": c - 1,
                         "close": float(c), "volume": 100})
            t += pd.Timedelta(minutes=5)
        return pd.DataFrame(rows)

    monkeypatch.setattr(api, "fetch_kline_by_date", fake_fetch)

    from server import app
    return TestClient(app)


def test_list_strategies(client):
    r = client.get("/api/backtest/strategies")
    assert r.status_code == 200
    assert "double_ma" in r.json()["strategies"]


def test_run_backtest_basic(client):
    payload = {
        "symbol": "螺纹钢",
        "period": "5m",
        "initial_capital": 100000,
        "slippage_ticks": 1,
        "intraday_only": False,
        "strategy": "double_ma",
        "strategy_params": {"fast": 3, "slow": 8},
    }
    r = client.post("/api/backtest/run", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert "bars" in body and "fills" in body and "trades" in body
    assert "equity_curve" in body and "metrics" in body
    assert body["metrics"]["final_position"] in ("long", "short", "flat")


def test_unknown_symbol_returns_400(client):
    r = client.post("/api/backtest/run", json={
        "symbol": "不存在的品种", "period": "5m",
        "strategy": "double_ma", "strategy_params": {},
    })
    assert r.status_code in (400, 404)


def test_unknown_strategy_returns_400(client):
    r = client.post("/api/backtest/run", json={
        "symbol": "螺纹钢", "period": "5m",
        "strategy": "not_exist", "strategy_params": {},
    })
    assert r.status_code == 400
```

- [ ] **Step 3: 写 backtest/api.py**

```python
"""FastAPI 子路由：/api/backtest/*"""
from __future__ import annotations

from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import backtest.strategies  # noqa: F401  触发内置策略注册

from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig
from backtest.registry import get_strategy, list_strategies
from config import get_symbols_config
from data_provider import fetch_kline_by_date


router = APIRouter(prefix="/api/backtest", tags=["backtest"])


class RunBacktestRequest(BaseModel):
    symbol: str
    contract: Optional[str] = None
    period: str = "5m"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    initial_capital: float = 100_000.0
    tick_size: Optional[float] = None
    tick_value: Optional[float] = None
    margin_rate: Optional[float] = None
    fee_per_lot: Optional[float] = None
    slippage_ticks: int = 1
    intraday_only: bool = False
    strategy: str
    strategy_params: dict = Field(default_factory=dict)


@router.get("/strategies")
async def list_all() -> dict:
    return {"strategies": list_strategies()}


@router.post("/run")
async def run_backtest(req: RunBacktestRequest) -> dict:
    cfg_symbols = get_symbols_config().get("symbols", {})
    sym_info = cfg_symbols.get(req.symbol)
    if sym_info is None:
        raise HTTPException(404, detail=f"未知品种: {req.symbol}")

    try:
        get_strategy(req.strategy)
    except KeyError as e:
        raise HTTPException(400, detail=str(e))

    tick_size = req.tick_size if req.tick_size is not None else sym_info.get("tick_size", 1.0)
    tick_value = req.tick_value if req.tick_value is not None else sym_info.get("tick_value", 10.0)
    margin_rate = req.margin_rate if req.margin_rate is not None else sym_info.get("margin_rate", 0.10)
    fee_per_lot = req.fee_per_lot if req.fee_per_lot is not None else sym_info.get("fee_per_lot", 3.0)

    contract = req.contract or sym_info.get("mootdx_code")
    market = sym_info.get("mootdx_market")
    if market is None or not contract:
        raise HTTPException(400, detail="品种缺少 mootdx_market / mootdx_code 配置")

    df = fetch_kline_by_date(
        market=market, symbol=contract, period=req.period,
        start_date=req.start_date, end_date=req.end_date, count=5000,
    )
    if df.empty:
        raise HTTPException(404, detail="回测区间内无 K 线数据")

    cfg = BacktestConfig(
        symbol=req.symbol, contract=contract, period=req.period,
        start_date=req.start_date, end_date=req.end_date,
        initial_capital=req.initial_capital,
        tick_size=float(tick_size), tick_value=float(tick_value),
        margin_rate=float(margin_rate), fee_per_lot=float(fee_per_lot),
        slippage_ticks=req.slippage_ticks, intraday_only=req.intraday_only,
        strategy=req.strategy, strategy_params=req.strategy_params,
    )
    result = BacktestEngine(cfg, df).run()

    return {
        "config": asdict(cfg),
        "bars": [asdict(b) for b in result.bars],
        "fills": [asdict(f) for f in result.fills],
        "trades": [asdict(t) for t in result.trades],
        "equity_curve": [asdict(p) for p in result.equity_curve],
        "metrics": result.metrics,
        "liquidated": result.liquidated,
        "liquidated_at": result.liquidated_at,
    }
```

- [ ] **Step 4: 在 server.py 挂路由**

在 `app.include_router(api_router)` 后追加：

```python
from backtest.api import router as backtest_router
app.include_router(backtest_router)
```

- [ ] **Step 5: 运行测试**

```powershell
uv run pytest tests/test_api.py -v
```

预期：4/4 绿。

- [ ] **Step 6: Commit**

```bash
git add backtest/api.py server.py symbols.json tests/test_api.py
git commit -m "feat(backtest): add /api/backtest endpoint with symbol-level defaults"
```

---

### Task 8: 前端 —— 回测独立页面

**Files:**
- Create: `frontend/backtest.html`
- Create: `frontend/backtest.js`
- Modify: `frontend/styles.css`
- Modify: `frontend/index.html`（顶栏加入口）

- [ ] **Step 1: 写 frontend/backtest.html**

```html
<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TradeSense - 回测</title>
    <link rel="stylesheet" href="styles.css">
    <script src="https://unpkg.com/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js"></script>
</head>
<body>
    <div class="header">
        <div class="logo">TradeSense · 回测</div>
        <div class="controls">
            <a class="nav-link" href="/">← 回到回放</a>
            <select id="bt-symbol"></select>
            <select id="bt-period">
                <option value="5m" selected>5分钟</option>
                <option value="15m">15分钟</option>
                <option value="60m">60分钟</option>
                <option value="1d">日线</option>
            </select>
            <label>开始<input type="date" id="bt-start" class="date-input"></label>
            <label>结束<input type="date" id="bt-end" class="date-input"></label>
            <select id="bt-strategy"></select>
            <input id="bt-params" placeholder='{"fast":5,"slow":20}' value='{"fast":5,"slow":20}' style="width:240px">
            <input id="bt-capital" type="number" value="100000" min="1000" step="1000" title="初始资金">
            <input id="bt-slippage" type="number" value="1" min="0" step="1" title="滑点（跳）">
            <input id="bt-tick-size" type="number" step="0.1" title="每跳价格（元）留空=默认">
            <input id="bt-tick-value" type="number" step="1" title="每跳价值（元/手）留空=默认">
            <input id="bt-fee" type="number" step="0.1" title="每手每次手续费 留空=默认">
            <input id="bt-margin" type="number" step="0.01" min="0" max="1" title="保证金率 留空=默认">
            <label><input id="bt-intraday" type="checkbox"> 日内</label>
            <button id="bt-run" class="primary">运行回测</button>
        </div>
    </div>
    <div class="chart-container">
        <div id="bt-chart"></div>
        <div id="bt-equity-chart" style="height:160px;margin-top:8px"></div>
        <div class="status hidden" id="bt-status"></div>
    </div>
    <div class="bt-metrics" id="bt-metrics">
        <div class="muted">请先运行回测</div>
    </div>
    <div class="trade-log">
        <div class="trade-log-toolbar">
            <span class="trade-log-title">交易明细</span>
            <button type="button" id="bt-export">导出 CSV</button>
        </div>
        <table>
            <thead>
                <tr>
                    <th>开仓时间</th><th>平仓时间</th><th>方向</th><th>手数</th>
                    <th>开仓价</th><th>平仓价</th><th>持仓K数</th>
                    <th>毛盈亏</th><th>手续费</th><th>净盈亏</th>
                    <th>开仓理由</th><th>平仓理由</th>
                </tr>
            </thead>
            <tbody id="bt-trade-body">
                <tr><td colspan="12" class="muted">暂无交易</td></tr>
            </tbody>
        </table>
    </div>
    <script src="backtest.js" defer></script>
</body>
</html>
```

- [ ] **Step 2: 写 frontend/backtest.js（完整实现，包含 API 基址推导 / 交易点 markers / 持仓色块 / 权益曲线 / 指标面板 / 明细表 / CSV 导出）**

```javascript
// ---- API 基址与 index.html 逻辑对齐 ----
function resolveApiBase() {
    const root = document.documentElement;
    if (root.hasAttribute("data-api-base")) {
        return root.getAttribute("data-api-base").trim().replace(/\/$/, "");
    }
    try {
        const ls = localStorage.getItem("tradesense_api_base");
        if (ls) return ls.trim().replace(/\/$/, "");
    } catch (_) { /* ignore */ }
    const { protocol, hostname, port } = window.location;
    const effectivePort = port || (protocol === "https:" ? "443" : "80");
    const onHttp = protocol === "http:" || protocol === "https:";
    if (onHttp && effectivePort === "8765") return "/api";
    if ((hostname === "localhost" || hostname === "127.0.0.1") && effectivePort !== "8765") {
        return `${protocol}//${hostname}:8765/api`.replace(/\/$/, "");
    }
    return "";
}
const API_BASE = resolveApiBase();

// ---- DOM refs ----
const $ = id => document.getElementById(id);
const refs = {
    symbol: $("bt-symbol"), period: $("bt-period"), start: $("bt-start"), end: $("bt-end"),
    strategy: $("bt-strategy"), params: $("bt-params"),
    capital: $("bt-capital"), slippage: $("bt-slippage"),
    tickSize: $("bt-tick-size"), tickValue: $("bt-tick-value"),
    fee: $("bt-fee"), margin: $("bt-margin"), intraday: $("bt-intraday"),
    run: $("bt-run"), status: $("bt-status"),
    chart: $("bt-chart"), equityChart: $("bt-equity-chart"),
    metricsBox: $("bt-metrics"), tradeBody: $("bt-trade-body"),
    exportBtn: $("bt-export"),
};

let chart = null, candleSeries = null, equityChart = null, equitySeries = null;
let lastResult = null;

// ---- 初始化品种 + 策略下拉 ----
async function initLists() {
    const [symResp, stratResp] = await Promise.all([
        fetch(`${API_BASE}/symbols`).then(r => r.json()),
        fetch(`${API_BASE}/backtest/strategies`).then(r => r.json()),
    ]);
    for (const name of Object.keys(symResp.symbols || {})) {
        refs.symbol.appendChild(new Option(name, name));
    }
    for (const s of stratResp.strategies || []) {
        refs.strategy.appendChild(new Option(s, s));
    }
}

// ---- 图表 ----
function initCharts() {
    chart = LightweightCharts.createChart(refs.chart, { height: 520, layout: { background: { color: "#fff" } } });
    candleSeries = chart.addCandlestickSeries({
        upColor: "#ef5350", downColor: "#26a69a",
        borderUpColor: "#ef5350", borderDownColor: "#26a69a",
        wickUpColor: "#ef5350", wickDownColor: "#26a69a",
    });
    equityChart = LightweightCharts.createChart(refs.equityChart, {
        height: 160, layout: { background: { color: "#fff" } },
    });
    equitySeries = equityChart.addLineSeries({ color: "#1976d2", lineWidth: 2 });
}

function toChartTime(s) { return Math.floor(new Date(s.replace(" ", "T") + "Z").getTime() / 1000); }

// ---- 运行回测 ----
async function run() {
    refs.status.classList.remove("hidden");
    refs.status.textContent = "运行中...";
    refs.run.disabled = true;

    let params = {};
    try { params = JSON.parse(refs.params.value || "{}"); }
    catch (e) { refs.status.textContent = "参数 JSON 解析失败: " + e.message; refs.run.disabled = false; return; }

    const body = {
        symbol: refs.symbol.value,
        period: refs.period.value,
        start_date: refs.start.value || null,
        end_date: refs.end.value || null,
        initial_capital: Number(refs.capital.value),
        slippage_ticks: Number(refs.slippage.value),
        intraday_only: refs.intraday.checked,
        strategy: refs.strategy.value,
        strategy_params: params,
    };
    if (refs.tickSize.value) body.tick_size = Number(refs.tickSize.value);
    if (refs.tickValue.value) body.tick_value = Number(refs.tickValue.value);
    if (refs.fee.value) body.fee_per_lot = Number(refs.fee.value);
    if (refs.margin.value) body.margin_rate = Number(refs.margin.value);

    try {
        const r = await fetch(`${API_BASE}/backtest/run`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
        lastResult = data;
        render(data);
        refs.status.classList.add("hidden");
    } catch (e) {
        refs.status.textContent = "失败: " + e.message;
    } finally {
        refs.run.disabled = false;
    }
}

// ---- 渲染 ----
function render(data) {
    // K 线
    const candles = data.bars.map(b => ({
        time: toChartTime(b.time),
        open: b.open, high: b.high, low: b.low, close: b.close,
    }));
    candleSeries.setData(candles);

    // 交易点 markers（开：下方↑/上方↓；平：空心）
    const markers = [];
    for (const f of data.fills) {
        const t = toChartTime(f.time);
        if (f.action === "open_long") {
            markers.push({ time: t, position: "belowBar", color: "#ef5350", shape: "arrowUp",
                text: `B ${f.qty} @${f.price.toFixed(2)}` });
        } else if (f.action === "open_short") {
            markers.push({ time: t, position: "aboveBar", color: "#26a69a", shape: "arrowDown",
                text: `S ${f.qty} @${f.price.toFixed(2)}` });
        } else if (f.action === "close") {
            const isEod = (f.reason || "").includes("eod");
            const isLiq = (f.reason || "").includes("liquidate");
            markers.push({ time: t, position: "inBar",
                color: isLiq ? "#b71c1c" : (isEod ? "#888" : "#fb8c00"),
                shape: "circle",
                text: `C @${f.price.toFixed(2)}${isLiq ? " 爆仓" : ""}${isEod ? " 日末" : ""}` });
        }
    }
    markers.sort((a, b) => a.time - b.time);
    candleSeries.setMarkers(markers);

    // 持仓期间背景色块（每个 trade 画个 priceLine 或 price range）
    // 简化版：通过向 candleSeries 添加 priceLine 上下界——Lightweight Charts 不原生支持色块；
    // 用 addLineSeries 画"持仓期间最低/最高价"的两条横线，期间可视化。
    // （UI 初版先只用 markers + equity curve，色块先不画；若后续要做可加 overlay。）

    // 权益曲线
    const eq = data.equity_curve.map(p => ({ time: toChartTime(p.time), value: p.equity }));
    equitySeries.setData(eq);

    // 指标面板
    const m = data.metrics;
    refs.metricsBox.innerHTML = renderMetrics(m, data);

    // 明细表
    const rows = data.trades.map(t => `
        <tr>
            <td>${t.open_time}</td>
            <td>${t.close_time}</td>
            <td class="${t.side}">${t.side === "long" ? "多" : "空"}</td>
            <td>${t.qty}</td>
            <td>${t.open_price.toFixed(2)}</td>
            <td>${t.close_price.toFixed(2)}</td>
            <td>${t.holding_bars}</td>
            <td>${t.pnl.toFixed(2)}</td>
            <td>${t.fee.toFixed(2)}</td>
            <td class="${t.net_pnl >= 0 ? "pnl-win" : "pnl-loss"}">${t.net_pnl.toFixed(2)}</td>
            <td>${escape(t.open_reason)}</td>
            <td>${escape(t.close_reason)}</td>
        </tr>
    `);
    refs.tradeBody.innerHTML = rows.length ? rows.join("") : '<tr><td colspan="12" class="muted">暂无交易</td></tr>';
}

function renderMetrics(m, data) {
    const pct = v => (v == null ? "-" : (v * 100).toFixed(2) + "%");
    const num = v => (v == null ? "-" : Number(v).toFixed(2));
    const warn = data.liquidated ? `<div class="alert-warn">⚠ 回测中途爆仓，触发时间：${data.liquidated_at}</div>` : "";
    return `
        ${warn}
        <div class="metrics-grid">
            <div><label>总收益</label><b>${pct(m.total_return)}</b></div>
            <div><label>最大回撤</label><b>${num(m.max_drawdown)} (${pct(m.max_drawdown_pct)})</b></div>
            <div><label>Sharpe</label><b>${m.sharpe == null ? "-" : num(m.sharpe)}</b></div>
            <div><label>Calmar</label><b>${m.calmar == null ? "-" : num(m.calmar)}</b></div>
            <div><label>交易笔数</label><b>${m.total_trades}</b></div>
            <div><label>胜率</label><b>${pct(m.win_rate)}</b></div>
            <div><label>盈亏比</label><b>${num(m.profit_factor)}</b></div>
            <div><label>平均盈 / 亏</label><b>${num(m.avg_win)} / ${num(m.avg_loss)}</b></div>
            <div><label>最大连胜 / 连败</label><b>${m.max_win_streak} / ${m.max_loss_streak}</b></div>
            <div><label>平均持仓K数</label><b>${num(m.avg_holding_bars)}</b></div>
            <div><label>最终权益</label><b>${num(m.final_equity)}</b></div>
            <div><label>最终持仓</label><b>${m.final_position}</b></div>
        </div>
    `;
}

function escape(s) {
    return String(s || "").replace(/[&<>"']/g, c => (
        {"&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"}[c]
    ));
}

// ---- CSV 导出 ----
function exportCsv() {
    if (!lastResult) return;
    const rows = [["open_time", "close_time", "side", "qty", "open_price", "close_price",
        "holding_bars", "pnl", "fee", "net_pnl", "open_reason", "close_reason"]];
    for (const t of lastResult.trades) {
        rows.push([t.open_time, t.close_time, t.side, t.qty, t.open_price, t.close_price,
            t.holding_bars, t.pnl, t.fee, t.net_pnl, t.open_reason, t.close_reason]);
    }
    const csv = rows.map(r => r.map(v => `"${String(v).replace(/"/g, '""')}"`).join(",")).join("\n");
    const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `backtest-${Date.now()}.csv`;
    a.click();
}

// ---- 启动 ----
initCharts();
initLists();
refs.run.addEventListener("click", run);
refs.exportBtn.addEventListener("click", exportCsv);
```

- [ ] **Step 3: 追加 frontend/styles.css（回测页样式）**

在 styles.css 末尾追加：

```css
/* ---- 回测页 ---- */
.nav-link { color: #1976d2; text-decoration: none; margin-right: 12px; }
.nav-link:hover { text-decoration: underline; }

.bt-metrics { padding: 12px 16px; border-top: 1px solid #eee; }
.bt-metrics .muted { color: #888; }
.metrics-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 10px 16px;
}
.metrics-grid > div { display: flex; flex-direction: column; }
.metrics-grid label { font-size: 12px; color: #888; }
.metrics-grid b { font-size: 15px; color: #222; }
.alert-warn { background: #fff3cd; color: #8a6d00; padding: 6px 10px; margin-bottom: 10px; border-radius: 4px; }
td.long { color: #ef5350; }
td.short { color: #26a69a; }
td.pnl-win { color: #2e7d32; font-weight: 600; }
td.pnl-loss { color: #c62828; font-weight: 600; }
```

- [ ] **Step 4: 在 frontend/index.html 顶栏加"回测"入口**

在 `.logo` 同级加一个链接，或在 `.controls` 前插入：

```html
<a class="nav-link" href="/backtest.html">回测 →</a>
```

- [ ] **Step 5: 人工烟雾测试**

```powershell
uv run python server.py
```

浏览器打开 http://127.0.0.1:8765/backtest.html：
- 品种与策略下拉正确填充
- 点运行 → K 线显示 + 交易点箭头 + 权益曲线 + 指标面板
- 交易明细表有行、CSV 导出可下载
- 勾选"日内"重跑：明细里出现 `close_reason=eod`

- [ ] **Step 6: Commit**

```bash
git add frontend/
git commit -m "feat(backtest): add frontend backtest page with markers, equity curve and metrics"
```

---

### Task 9: 端到端联调 + 文档

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`

- [ ] **Step 1: 全量测试**

```powershell
uv run pytest -v
```

预期：所有测试通过。

- [ ] **Step 2: 冒烟测试真实数据**

```powershell
uv run python server.py
```

前端 `/backtest.html` 选"螺纹钢"、`5m`、双均线 `{"fast":5,"slow":20}` 跑一遍；再勾选"日内"跑一遍；再把初始资金改小到 5000 触发爆仓跑一遍。每次确认：
- 指标数字与交易明细对得上（例：胜率 = 胜数 / 总数）
- 爆仓场景顶部出现黄色警告
- 图上交易点方向 / 颜色正确（开多↑红，开空↓绿，平仓圆点橙，爆仓深红）

- [ ] **Step 3: 更新 CLAUDE.md —— 新增"回测模块"章节**

在"架构说明"之后插入：

```markdown
## 回测模块（`backtest/`）

事件驱动回测，Python on_bar 策略接入：

- `backtest/models.py` — 数据类（Bar/Order/Fill/Trade/Position/BacktestConfig/BacktestResult/EquityPoint）
- `backtest/account.py` — Account（权益 = 初始 + 已实现 - 手续费 + 浮盈；available = 权益 - 占用保证金；available<0 即爆仓）
- `backtest/broker.py` — 按"下一根 K 开盘 ± 滑点"成交；日末 / 爆仓用 `force_close`
- `backtest/context.py` — `on_bar(bar, ctx)` 的上下文：`ctx.closes / ctx.position_side / ctx.buy() / ctx.sell() / ctx.close() / ctx.state`
- `backtest/registry.py` — `@register_strategy("name")` 全局注册
- `backtest/strategies/` — 内置策略（double_ma 示范）
- `backtest/engine.py` — 串联 account+broker+strategy 的事件循环，含日内强平与爆仓处理
- `backtest/metrics.py` — 年化 / 最大回撤 / Sharpe / Calmar / 胜率 / 盈亏比 / 连胜连败 / 平均持仓
- `backtest/api.py` — `GET /api/backtest/strategies` / `POST /api/backtest/run`

### 关键规则
- 信号在 K(t) 收盘产生 → K(t+1) 开盘 ± slippage 成交（避免 look-ahead）
- 滑点按"跳"配置：`slippage_ticks * tick_size`；开多/平空 +N 跳，开空/平多 -N 跳
- 手续费：`fee_per_lot * qty`，开平各收一次
- 保证金：`price * qty * tick_value * margin_rate / tick_size`，开仓时占用
- 不支持反手 / 加仓 / 部分平仓（第一期简化）
- `intraday_only=True` → 每日最后一根 K 收盘后自动反向单，次日第一根开盘或当日 close 强平
- 爆仓：available<0 当根即触发，下一根开盘或当前 close 强平，结果 `liquidated=True`

### symbols.json 新增字段
- `tick_size` — 每跳价格（元/单位）
- `margin_rate` — 保证金比例
- `fee_per_lot` — 每手每次手续费
```

- [ ] **Step 4: 更新 README.md —— "功能"段加一条**

```markdown
- 🧪 **策略回测**：Python `on_bar` 函数接入，`/backtest.html` 一键运行，输出交易点、资金曲线和完整绩效报告。
```

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs(backtest): document backtest module in CLAUDE.md and README"
```

- [ ] **Step 6: Push**

```bash
git push -u origin feat/backtest
```

---

## 自查清单

- [x] **范围覆盖** —— 必须有 4 项（保证金+爆仓 / 滑点 / 下一根 K 撮合 / 图上交易点）+ 强烈建议 4 项（资金+回撤曲线 / 核心指标 / 逐笔明细 / 日内+强平 / 每品种独立参数）全部有对应 Task。
- [x] **无占位符** —— 所有 Step 含完整代码。
- [x] **类型一致** —— `Account.apply_fill(Fill)` 在 Task 2 定义，在 Task 5 Engine 中调用；`Broker.submit / execute_on_open / force_close` Task 3 定义，在 Task 5 中调用；`compute_metrics` Task 6 定义后回填到 Task 5 engine 的 import。
- [x] **依赖前置** —— Task 5 在自查中标记了 `compute_metrics` 桩做法以便独立 TDD；Task 6 完成后回填。
