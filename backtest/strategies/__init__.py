"""内置策略包。在此导入策略模块以触发 @register_strategy 注册。"""
# 示例（取消注释并导入你的策略模块）：
# from backtest.strategies import my_strategy  # noqa: F401
from backtest.strategies import ema_pullback  # noqa: F401
from backtest.strategies import exhaustion_fade  # noqa: F401
from backtest.strategies import exhaustion_fade_a  # noqa: F401
from backtest.strategies import pinbar_structure  # noqa: F401
from backtest.strategies import bollinger_reversion  # noqa: F401
from backtest.strategies import donchian_breakout  # noqa: F401
from backtest.strategies import volume_pa  # noqa: F401
from backtest.strategies import dual_tf_pa  # noqa: F401
from backtest.strategies import weixin_formula  # noqa: F401
