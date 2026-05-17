"""全局策略注册表：name → callable(bar, ctx, **params) -> None。"""
from __future__ import annotations

import inspect
from typing import Any, Callable

_STRATEGIES: dict[str, Callable] = {}

# 策略 on_bar 的前两个固定参数，提取 params 时跳过
_FIXED_PARAMS = {"bar", "ctx"}


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


def get_strategy_params(name: str) -> list[dict[str, Any]]:
    """返回策略的可配置参数列表，每个参数包含 name/type/default。"""
    fn = get_strategy(name)
    sig = inspect.signature(fn)
    params = []
    for pname, param in sig.parameters.items():
        if pname in _FIXED_PARAMS:
            continue
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            continue
        default = param.default if param.default is not inspect.Parameter.empty else None
        ptype = "number"
        if param.annotation is not inspect.Parameter.empty:
            anno = param.annotation
            if anno is str:
                ptype = "string"
            elif anno is bool:
                ptype = "boolean"
        params.append({"name": pname, "type": ptype, "default": default})
    return params


def _clear_registry_for_test() -> None:
    _STRATEGIES.clear()
