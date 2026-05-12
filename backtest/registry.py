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
