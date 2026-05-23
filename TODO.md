# 优化待办

## 性能优化

- [x] **策略 EMA/ATR 增量化** — 5 个策略每根 bar 都 `pd.Series.ewm()` 算整段历史，O(N²)，5000 根 = 千万次级运算。改为增量乘加，用 `ctx.state` 缓存 last_ema/span
- [ ] **期货/股票数据层去重** — `data_provider.py` 8 个镜像函数（`_read_minute/_read_stock_minute`、`_cached_read/_cached_stock_read`、`fetch_kline/fetch_stock_kline` 等），抽象 `_KlineSource` dataclass 可减半
