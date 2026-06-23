"""realtime_provider 单元测试：mock tdxpy API，验证格式归一化、周期映射、回退逻辑。"""
from unittest.mock import MagicMock, patch, PropertyMock
import pytest
import pandas as pd


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _mock_tdxpy():
    """替换模块级的 tdxpy API 类，避免真实网络连接。"""
    mock_hq = MagicMock()
    mock_exhq = MagicMock()
    with patch("realtime_provider.TdxHq_API", return_value=mock_hq), \
         patch("realtime_provider.TdxExHq_API", return_value=mock_exhq):
        # 在 import 后重置单例
        import realtime_provider as rp
        rp._manager._hq_api = mock_hq
        rp._manager._exhq_api = mock_exhq
        # mock client._closed = False → 连接存活
        mock_hq.client._closed = False
        mock_exhq.client._closed = False
        yield rp, mock_hq, mock_exhq


@pytest.fixture
def _mock_config():
    """替换 config 模块的函数，返回测试用品种配置。"""
    test_symbols = {
        "螺纹钢": {
            "code": "SHFE.RB", "market_type": "futures",
            "mootdx_market": 30, "mootdx_code": "RB2610",
            "tick_value": 10, "tick_size": 1,
        },
        "招商银行": {
            "code": "SH.600036", "market_type": "stock",
        },
    }
    with patch("realtime_provider.get_symbols_config", return_value={"symbols": test_symbols}), \
         patch("realtime_provider.get_market_type") as mock_mt, \
         patch("realtime_provider.get_stock_exchange_and_code") as mock_se, \
         patch("realtime_provider.resolve_symbol") as mock_rs:
        mock_mt.side_effect = lambda s: test_symbols.get(s, {}).get("market_type")
        mock_se.side_effect = lambda s: ("sh", "600036") if s == "招商银行" else None
        mock_rs.side_effect = lambda s: (
            (30, "RB2610", "螺纹钢") if s == "螺纹钢"
            else (1, "600036", "招商银行") if s == "招商银行"
            else (None, None, None)
        )
        yield


# ── 周期映射测试 ────────────────────────────────────────────────────


class TestPeriodMapping:
    def test_hq_mapping(self):
        from realtime_provider import _PERIOD_CATEGORY_HQ
        assert _PERIOD_CATEGORY_HQ["1m"] == 8
        assert _PERIOD_CATEGORY_HQ["5m"] == 0
        assert _PERIOD_CATEGORY_HQ["15m"] == 1
        assert _PERIOD_CATEGORY_HQ["30m"] == 2
        assert _PERIOD_CATEGORY_HQ["60m"] == 3
        assert _PERIOD_CATEGORY_HQ["1h"] == 3
        assert _PERIOD_CATEGORY_HQ["1d"] == 4

    def test_exhq_mapping(self):
        from realtime_provider import _PERIOD_CATEGORY_EXHQ
        assert _PERIOD_CATEGORY_EXHQ["1m"] == 7  # 扩展行情专用
        assert _PERIOD_CATEGORY_EXHQ["5m"] == 0

    def test_all_periods_valid(self):
        from realtime_provider import VALID_PERIODS
        expected = {"1m", "5m", "15m", "30m", "60m", "1h", "1d"}
        assert VALID_PERIODS == expected


# ── 格式归一化测试 ──────────────────────────────────────────────────


class TestNormalizeBars:
    def test_normalize_stock_bars(self, _mock_tdxpy):
        rp, _, _ = _mock_tdxpy
        raw = [
            {"datetime": "2026-06-23 10:00", "open": 35.0, "high": 36.0,
             "low": 34.5, "close": 35.5, "vol": 1000},
            {"datetime": "2026-06-23 10:05", "open": 35.5, "high": 37.0,
             "low": 35.0, "close": 36.2, "vol": 1500},
        ]
        df = rp._normalize_bars(raw)
        assert list(df.columns) == ["bob", "open", "high", "low", "close", "volume"]
        assert len(df) == 2
        assert df.iloc[0]["open"] == 35.0
        assert df.iloc[1]["volume"] == 1500
        assert pd.api.types.is_datetime64_any_dtype(df["bob"])

    def test_normalize_empty_bars(self, _mock_tdxpy):
        rp, _, _ = _mock_tdxpy
        df = rp._normalize_bars([])
        assert df.empty
        assert list(df.columns) == ["bob", "open", "high", "low", "close", "volume"]


# ── 报价获取测试（A 股）─────────────────────────────────────────────


class TestQuoteStock:
    def test_success(self, _mock_tdxpy, _mock_config):
        rp, mock_hq, _ = _mock_tdxpy
        mock_hq.get_security_quotes.return_value = [
            {
                "market": 1, "code": "600036",
                "price": 36.50, "last_close": 36.00,
                "open": 36.20, "high": 37.00, "low": 35.80,
                "vol": 128000, "servertime": "10:30:15.123",
                "bid1": 36.49, "ask1": 36.51,
                "bid_vol1": 500, "ask_vol1": 300,
            }
        ]
        result = rp.get_realtime_quote("招商银行")
        assert result["price"] == 36.50
        assert result["open"] == 36.20
        assert result["volume"] == 128000
        assert result["source"] == "hq"
        assert result["bid1"] == 36.49
        assert result["ask1"] == 36.51
        assert "error" not in result

    def test_empty_result(self, _mock_tdxpy, _mock_config):
        rp, mock_hq, _ = _mock_tdxpy
        mock_hq.get_security_quotes.return_value = []
        result = rp.get_realtime_quote("招商银行")
        assert result["error"] == "no_data"
        assert result["source"] is None

    def test_unknown_symbol(self, _mock_tdxpy, _mock_config):
        rp, _, _ = _mock_tdxpy
        result = rp.get_realtime_quote("未知品种")
        assert result["error"] == "unknown_symbol"


# ── 报价获取测试（期货）─────────────────────────────────────────────


class TestQuoteFutures:
    def test_success(self, _mock_tdxpy, _mock_config):
        rp, _, mock_exhq = _mock_tdxpy
        mock_exhq.get_instrument_quote.return_value = {
            "price": 3650.0, "open": 3640.0, "high": 3670.0,
            "low": 3630.0, "zongliang": 1500000,
            "pre_close": 3620.0,
            "bid1": 3649.0, "ask1": 3651.0,
            "bid_vol1": 200, "ask_vol1": 150,
        }
        result = rp.get_realtime_quote("螺纹钢")
        assert result["price"] == 3650.0
        assert result["contract"] == "RB2610"
        assert result["source"] == "exhq"
        assert result["last_close"] == 3620.0

    def test_exhq_failure_returns_error(self, _mock_tdxpy, _mock_config):
        rp, _, mock_exhq = _mock_tdxpy
        mock_exhq.get_instrument_quote.side_effect = ConnectionError("连接失败")
        # 重置内部状态使重连也失败
        rp._manager._exhq_api = None
        with patch.object(rp._manager, "_ensure_exhq", side_effect=ConnectionError("所有服务器不可用")):
            result = rp.get_realtime_quote("螺纹钢")
            assert result["error"] == "exhq_unavailable"
            assert result["source"] is None


# ── K 线获取测试 ────────────────────────────────────────────────────


class TestBars:
    def test_stock_bars(self, _mock_tdxpy, _mock_config):
        rp, mock_hq, _ = _mock_tdxpy
        mock_hq.get_security_bars.return_value = [
            {"datetime": "2026-06-23 10:00", "open": 35.0, "high": 36.0,
             "low": 34.5, "close": 35.5, "vol": 1000},
            {"datetime": "2026-06-23 10:05", "open": 35.5, "high": 37.0,
             "low": 35.0, "close": 36.2, "vol": 1500},
        ]
        df = rp.get_realtime_bars("招商银行", period="5m", count=10)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2
        assert "bob" in df.columns
        assert "close" in df.columns

    def test_futures_bars_exhq_failure(self, _mock_tdxpy, _mock_config):
        rp, _, mock_exhq = _mock_tdxpy
        mock_exhq.get_instrument_bars.side_effect = ConnectionError("失败")
        rp._manager._exhq_api = None
        with patch.object(rp._manager, "_ensure_exhq", side_effect=ConnectionError("不可用")):
            result = rp.get_realtime_bars("螺纹钢", period="5m", count=100)
            assert isinstance(result, dict)
            assert result["error"] == "exhq_unavailable"

    def test_invalid_period(self, _mock_tdxpy, _mock_config):
        rp, _, _ = _mock_tdxpy
        result = rp.get_realtime_bars("招商银行", period="2m", count=100)
        assert isinstance(result, dict)
        assert "invalid_period" in result["error"]


# ── 健康检查测试 ────────────────────────────────────────────────────


class TestHealth:
    def test_health_reports_status(self, _mock_tdxpy, _mock_config):
        rp, mock_hq, mock_exhq = _mock_tdxpy
        mock_hq.client._closed = False
        mock_exhq.client._closed = False
        h = rp.health()
        assert h["hq"]["connected"] is True
        assert h["exhq"]["connected"] is True


# ── 连接管理测试 ────────────────────────────────────────────────────


class TestConnectionManager:
    def test_ensure_hq_success(self, _mock_tdxpy, _mock_config):
        rp, mock_hq, _ = _mock_tdxpy
        api = rp._manager._ensure_hq()
        assert api is mock_hq

    def test_ensure_hq_reconnects_on_closed(self, _mock_tdxpy, _mock_config):
        rp, mock_hq, _ = _mock_tdxpy
        # 模拟连接已关闭
        mock_hq.client._closed = True
        api = rp._manager._ensure_hq()
        # 应该尝试重新连接
        mock_hq.connect.assert_called()
