# 优化待办

## 性能优化

- [ ] **策略 EMA/ATR 增量化** — 5 个策略每根 bar 都 `pd.Series.ewm()` 算整段历史，O(N²)，5000 根 = 千万次级运算。改为增量乘加，用 `ctx.state` 缓存 last_ema/span
- [ ] **期货/股票数据层去重** — `data_provider.py` 8 个镜像函数（`_read_minute/_read_stock_minute`、`_cached_read/_cached_stock_read`、`fetch_kline/fetch_stock_kline` 等），抽象 `_KlineSource` dataclass 可减半

## 代码卫生

- [ ] **`engine._close_fill_to_trade` 调私有方法** — 直接调 `account._price_to_pnl` 破坏封装，应提升为公有 `compute_pnl` 或平仓时让 `apply_fill` 顺便返回 pnl
- [ ] **`_calculate_ema` warmup 门槛多余** — `ewm(adjust=False)` 第一根就有值，`len(closes) < period` 的前 N-1 根白白设 None
- [ ] **`BacktestResult` bars 序列化低效** — `[asdict(b) for b in bars]` 逐根 dataclass 转换，5000 根开销大，可从 `bars_df.to_dict(orient="records")` 出
- [ ] **`import backtest.api` 放 `server.py` 中间** — PEP 8 不规范，挪到文件头部
- [ ] **缺失策略 smoke test** — `shrinking_stairs / stock_trend / a_share_trend` 无测试，各加一个"合成数据跑通不抛异常"的用例
- [ ] **CORS `allow_origins=["*"]`** — 本地 OK，部署需改 allowlist（备忘，无需现在动）
