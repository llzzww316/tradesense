"""registry: 用装饰器注册策略函数，引擎按名字查找。"""
import pytest
from backtest.registry import _STRATEGIES, register_strategy, get_strategy, list_strategies


@pytest.fixture(autouse=True)
def _isolated_registry():
    """测试完恢复注册表，避免污染 test_api 里依赖 double_ma 的用例。"""
    snapshot = dict(_STRATEGIES)
    _STRATEGIES.clear()
    try:
        yield
    finally:
        _STRATEGIES.clear()
        _STRATEGIES.update(snapshot)


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
