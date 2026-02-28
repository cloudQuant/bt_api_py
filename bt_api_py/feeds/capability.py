"""
Capability 机制 — 声明场所支持的功能

每个 Feed 实现通过 capabilities() 返回支持的能力集合。
上层调用前可通过 require_capability() 检查，缺失能力时抛出 NotSupportedError。
"""

from enum import Enum, unique


@unique
class Capability(str, Enum):
    """场所能力枚举 — 覆盖所有可能的交易功能"""

    # ── 行情 ──
    GET_TICK = "get_tick"
    GET_DEPTH = "get_depth"
    GET_KLINE = "get_kline"
    GET_FUNDING_RATE = "get_funding_rate"
    GET_MARK_PRICE = "get_mark_price"
    GET_CLEAR_PRICE = "get_clear_price"

    # ── 交易 ──
    MAKE_ORDER = "make_order"
    CANCEL_ORDER = "cancel_order"
    CANCEL_ALL = "cancel_all"
    CANCEL_ALL_SYMBOL = "cancel_all_symbol"
    QUERY_ORDER = "query_order"
    QUERY_OPEN_ORDERS = "query_open_orders"
    GET_DEALS = "get_deals"

    # ── 账户 ──
    GET_BALANCE = "get_balance"
    GET_ACCOUNT = "get_account"
    GET_POSITION = "get_position"

    # ── 流式数据 ──
    MARKET_STREAM = "market_stream"
    ACCOUNT_STREAM = "account_stream"

    # ── 保证金 ──
    CROSS_MARGIN = "cross_margin"
    ISOLATED_MARGIN = "isolated_margin"

    # ── 高级功能 ──
    HEDGE_MODE = "hedge_mode"
    BATCH_ORDER = "batch_order"
    CONDITIONAL_ORDER = "conditional_order"
    TRAILING_STOP = "trailing_stop"
    OCO_ORDER = "oco_order"

    # ── 信息查询 ──
    GET_EXCHANGE_INFO = "get_exchange_info"
    GET_SERVER_TIME = "get_server_time"


class NotSupportedError(Exception):
    """场所不支持该能力"""

    def __init__(self, capability, venue: str = ""):
        self.capability = capability
        self.venue = venue
        cap_name = capability.value if isinstance(capability, Capability) else str(capability)
        msg = f"Capability '{cap_name}' is not supported"
        if venue:
            msg += f" by {venue}"
        super().__init__(msg)


class CapabilityMixin:
    """Capability 声明混入类

    子类覆盖 _capabilities() 返回支持的能力集合。

    使用方式::

        class BinanceSwapFeed(Feed, CapabilityMixin):
            @classmethod
            def _capabilities(cls) -> Set[Capability]:
                return {
                    Capability.MAKE_ORDER,
                    Capability.CANCEL_ORDER,
                    Capability.GET_TICK,
                    ...
                }
    """

    @classmethod
    def _capabilities(cls) -> set[Capability]:
        """子类覆盖此方法声明支持的能力"""
        return set()

    @property
    def capabilities(self) -> set[Capability]:
        """返回该 Feed 支持的能力集合"""
        return self._capabilities()

    def has_capability(self, cap: Capability) -> bool:
        """检查是否支持指定能力"""
        return cap in self.capabilities

    def require_capability(self, cap: Capability):
        """要求指定能力，不支持时抛出 NotSupportedError"""
        if not self.has_capability(cap):
            venue = getattr(self, "exchange_name", self.__class__.__name__)
            raise NotSupportedError(cap, venue)
