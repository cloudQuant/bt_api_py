# 设计文档 - 全球多市场统一交易框架

## 1. 概述

目标是将 bt_api_py 重构为覆盖 **CEX / DEX / CTP / QMT / IB** 的统一交易框架。

### 1.1 设计原则

| 原则 | 说明 |
|------|------|
| **统一协议** | 所有场所遵循统一接口协议（AbstractVenueFeed） |
| **配置驱动** | 静态信息走配置，动态逻辑走代码 |
| **能力显式** | 通过 Capability 声明能力边界 |
| **工具辅助** | 脚手架与校验工具减少重复劳动 |
| **重构优先** | 允许破坏性变更，以获得更好的架构 |

### 1.2 架构分层

```
应用层 (策略/风控/回测)
        ↓
统一接口层 (BtApi / InstrumentManager / Capability)
        ↓
注册表层 (ExchangeRegistry)
        ↓
基础设施层 (HttpClient / RateLimiter / ErrorFramework / Config)
        ↓
适配层 (CEX/DEX/Broker Adapter)
        ↓
实现层 (Binance / OKX / CTP / IB / QMT / ...)
```

---

## 2. 统一协议设计

### 2.1 AbstractVenueFeed

采用 Protocol 作为类型约束，定义所有场所必须实现的核心接口。

```python
from typing import Protocol, Any, Optional, runtime_checkable
import asyncio
import functools

@runtime_checkable
class AbstractVenueFeed(Protocol):
    """统一场所协议

    设计原则：
    1. 方法签名必须兼容现有 Feed 基类（extra_data + **kwargs 模式不变）
    2. 异步方法提供默认 run_in_executor 包装，HTTP 场所可覆盖为真异步
    3. connect/disconnect 对 HTTP 场所默认为 no-op
    """

    # 连接管理
    def connect(self) -> None:
        """建立连接（HTTP 场所可为 no-op）"""
        ...

    def disconnect(self) -> None:
        """断开连接"""
        ...

    def is_connected(self) -> bool:
        """检查连接状态"""
        ...

    # 行情查询（同步）— 签名与现有 Feed 保持一致
    def get_tick(self, symbol: str, extra_data=None, **kwargs) -> Any:
        """获取最新价格"""
        ...

    def get_depth(self, symbol: str, count: int = 20, extra_data=None, **kwargs) -> Any:
        """获取深度"""
        ...

    def get_kline(self, symbol: str, period: str, count: int = 100, extra_data=None, **kwargs) -> Any:
        """获取K线"""
        ...

    # 交易操作（同步）— 签名与现有 Feed 保持一致
    def make_order(
        self,
        symbol: str,
        volume: float,
        price: float,
        order_type: str,           # "buy-limit", "sell-market" 等，保持现有编码方式
        offset: str = "open",
        post_only: bool = False,
        client_order_id: str = None,
        extra_data=None,
        **kwargs
    ) -> Any:
        """下单"""
        ...

    def cancel_order(self, symbol: str, order_id: str, extra_data=None, **kwargs) -> Any:
        """撤单"""
        ...

    def cancel_all(self, symbol: Optional[str] = None, extra_data=None, **kwargs) -> Any:
        """撤销所有订单（可选能力）"""
        ...

    def query_order(self, symbol: str, order_id: str, extra_data=None, **kwargs) -> Any:
        """查询订单"""
        ...

    def get_open_orders(self, symbol: Optional[str] = None, extra_data=None, **kwargs) -> Any:
        """查询挂单"""
        ...

    # 账户查询（同步）
    def get_balance(self, symbol=None, extra_data=None, **kwargs) -> Any:
        """查询余额"""
        ...

    def get_account(self, symbol="ALL", extra_data=None, **kwargs) -> Any:
        """查询账户信息"""
        ...

    def get_position(self, symbol: Optional[str] = None, extra_data=None, **kwargs) -> Any:
        """查询持仓（期货/期权）"""
        ...

    # 异步版本
    # HTTP 场所（CEX）应覆盖为真正的 httpx 异步实现
    # 非 HTTP 场所（CTP/IB/QMT）使用默认的 run_in_executor 包装
    async def async_get_tick(self, symbol: str, extra_data=None, **kwargs) -> Any:
        ...

    async def async_make_order(
        self,
        symbol: str,
        volume: float,
        price: float,
        order_type: str,
        offset: str = "open",
        post_only: bool = False,
        client_order_id: str = None,
        extra_data=None,
        **kwargs
    ) -> Any:
        ...

    async def async_cancel_order(self, symbol: str, order_id: str, extra_data=None, **kwargs) -> Any:
        ...

    async def async_get_balance(self, symbol=None, extra_data=None, **kwargs) -> Any:
        ...

    # 能力声明
    @property
    def capabilities(self) -> set:
        """返回该 Feed 支持的能力集合"""
        ...


class AsyncWrapperMixin:
    """为非 HTTP 场所（CTP/IB/QMT）提供默认的异步包装

    HTTP 场所应覆盖这些方法为真正的 httpx 异步实现。
    非 HTTP 场所继承此 Mixin，自动将同步方法包装为异步。
    """

    async def async_get_tick(self, symbol, extra_data=None, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, functools.partial(self.get_tick, symbol, extra_data=extra_data, **kwargs)
        )

    async def async_make_order(self, symbol, volume, price, order_type,
                                offset="open", post_only=False, client_order_id=None,
                                extra_data=None, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, functools.partial(
                self.make_order, symbol, volume, price, order_type,
                offset=offset, post_only=post_only, client_order_id=client_order_id,
                extra_data=extra_data, **kwargs
            )
        )

    async def async_cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, functools.partial(self.cancel_order, symbol, order_id, extra_data=extra_data, **kwargs)
        )

    async def async_get_balance(self, symbol=None, extra_data=None, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, functools.partial(self.get_balance, symbol, extra_data=extra_data, **kwargs)
        )
```

---

### 2.2 Capability 机制（完整版）

完整的 Capability 体系，覆盖所有可能的交易功能。

```python
from enum import Enum
from typing import Set

class Capability(Enum):
    """完整的能力枚举"""

    # === 订单相关 ===
    MAKE_ORDER = "make_order"                # 下单
    CANCEL_ORDER = "cancel_order"            # 撤单
    CANCEL_ALL = "cancel_all"                # 撤销所有（全市场）
    CANCEL_ALL_SYMBOL = "cancel_all_symbol"  # 撤销所有（单交易对）
    MODIFY_ORDER = "modify_order"            # 改单
    QUERY_ORDER = "query_order"              # 查询订单
    QUERY_OPEN_ORDERS = "query_open_orders"  # 查询挂单
    QUERY_HISTORY_ORDERS = "query_history_orders"  # 查询历史订单
    QUERY_DEALS = "query_deals"              # 查询成交记录

    # === 账户相关 ===
    GET_BALANCE = "get_balance"              # 查询余额
    GET_ACCOUNT = "get_account"              # 查询账户信息
    GET_POSITION = "get_position"            # 查询持仓
    GET_MARGIN = "get_margin"                # 查询保证金
    GET_LEVERAGE = "get_leverage"            # 查询杠杆

    # === 行情相关 ===
    GET_TICK = "get_tick"                    # 获取最新价格
    GET_DEPTH = "get_depth"                  # 获取深度
    GET_KLINE = "get_kline"                  # 获取K线
    GET_FUNDING_RATE = "get_funding_rate"    # 获取资金费率
    GET_MARK_PRICE = "get_mark_price"        # 获取标记价格
    GET_INDEX_PRICE = "get_index_price"      # 获取指数价格

    # === 流相关 ===
    MARKET_STREAM = "market_stream"          # 市场数据流
    ACCOUNT_STREAM = "account_stream"        # 账户数据流
    PRIVATE_STREAM = "private_stream"        # 私有流（综合）

    # === 特殊订单类型 ===
    OCO_ORDER = "oco_order"                  # 止盈止损单
    ICEBERG_ORDER = "iceberg_order"          # 冰山单
    TWAP_ORDER = "twap_order"                # 时间加权平均
    VWAP_ORDER = "vwap_order"                # 成交量加权平均
    TRAILING_STOP = "trailing_stop"          # 跟踪止损

    # === 保证金模式 ===
    CROSS_MARGIN = "cross_margin"            # 全仓保证金
    ISOLATED_MARGIN = "isolated_margin"      # 逐仓保证金

    # === 其他 ===
    HEDGE_MODE = "hedge_mode"                # 对冲模式（同时持有多空）
    ADL_CHECK = "adl_check"                  # ADL 检查

class CapabilityProvider:
    """能力声明基类，所有 Feed 应继承此类"""

    @classmethod
    def capabilities(cls) -> Set[Capability]:
        """声明该 Feed 支持的能力（子类重写）"""
        return {
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
            Capability.QUERY_ORDER,
            Capability.GET_BALANCE,
        }

    def has_capability(self, capability: Capability) -> bool:
        """运行时检查是否支持某能力"""
        return capability in self.capabilities()

class NotSupportedError(Exception):
    """不支持的操作"""
    def __init__(self, capability: Capability, venue: str):
        self.capability = capability
        self.venue = venue
        super().__init__(f"{capability.value} not supported by {venue}")

# 使用示例
class BinanceSwapFeed(AbstractVenueFeed, CapabilityProvider):
    @classmethod
    def capabilities(cls) -> Set[Capability]:
        return {
            # Binance 支持的所有能力
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
            Capability.CANCEL_ALL,
            Capability.CANCEL_ALL_SYMBOL,
            Capability.QUERY_ORDER,
            Capability.QUERY_OPEN_ORDERS,
            Capability.GET_BALANCE,
            Capability.GET_POSITION,
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_FUNDING_RATE,
            Capability.GET_MARK_PRICE,
            Capability.MARKET_STREAM,
            Capability.ACCOUNT_STREAM,
            Capability.CROSS_MARGIN,
            Capability.ISOLATED_MARGIN,
            Capability.HEDGE_MODE,
        }

    def cancel_all(self, symbol=None, **kwargs):
        if Capability.CANCEL_ALL not in self.capabilities():
            raise NotSupportedError(Capability.CANCEL_ALL, "BINANCE")
        # ... 实现
```

---

## 3. 统一 Instrument 模型（完整版）

### 3.1 Asset 分类

```python
from enum import Enum

class Asset(str, Enum):
    """资产类型枚举"""
    SPOT = "spot"           # 现货
    SWAP = "swap"           # 永续合约
    FUTURE = "future"       # 交割合约
    OPTION = "option"       # 期权
    STK = "stk"             # 股票
    FUND = "fund"           # 基金
    BOND = "bond"           # 债券
    FX = "fx"               # 外汇
    INDEX = "index"         # 指数
```

### 3.2 Instrument 数据模型（完整版）

```python
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from datetime import datetime
from decimal import Decimal

@dataclass(frozen=True)  # 不可变，确保线程安全
class Instrument:
    """统一交易标的模型"""

    # === 基础标识 ===
    internal: str                    # 内部统一符号，如 BTC-USDT-SWAP, IF2506, 600530-STK
    venue: str                       # BINANCE___SWAP, CTP___FUTURE, IB___STK
    venue_symbol: str                # 交易所/券商原始符号，如 BTCUSDT, IF2506, APPLE-STK
    asset: Asset                     # 资产类型

    # === 标的属性 ===
    underlying: Optional[str] = None  # 标的：BTC、沪深300、Apple
    base_currency: Optional[str] = None   # 基础货币（现货/合约）
    quote_currency: Optional[str] = None  # 计价货币

    # === 合约属性（FUTURE/OPTION）===
    expiry: Optional[datetime] = None         # 到期日
    strike: Optional[Decimal] = None          # 执行价（期权）
    contract_size: Optional[Decimal] = None   # 合约乘数
    option_type: Optional[str] = None         # CALL/PUT

    # === 交易属性 ===
    tick_size: Optional[Decimal] = None       # 最小价格变动单位
    min_qty: Optional[Decimal] = None         # 最小下单数量
    max_qty: Optional[Decimal] = None         # 最大下单数量
    qty_step: Optional[Decimal] = None        # 数量精度
    min_notional: Optional[Decimal] = None    # 最小名义价值

    # === 状态信息 ===
    status: str = "active"                    # active/suspend/expire/delist
    list_time: Optional[datetime] = None      # 上市时间
    delist_time: Optional[datetime] = None    # 摘牌时间

    # === 扩展信息 ===
    extra: Dict[str, Any] = field(default_factory=dict)  # 其他场所特定信息

    @property
    def is_expired(self) -> bool:
        """是否已过期"""
        if self.expiry is None:
            return False
        return datetime.now() > self.expiry

    @property
    def is_listed(self) -> bool:
        """是否在交易"""
        if self.status != "active":
            return False
        if self.delist_time and datetime.now() > self.delist_time:
            return False
        return True

    def with_params(self, **kwargs) -> 'Instrument':
        """创建带有新参数的副本（用于测试或变体）"""
        return dataclasses.replace(self, **kwargs)

# 用于创建 Instrument 的工厂
class InstrumentFactory:
    """Instrument 工厂类"""

    @staticmethod
    def from_exchange(
        venue: str,
        venue_symbol: str,
        asset: Asset,
        **kwargs
    ) -> Instrument:
        """从交易所符号创建 Instrument"""
        # 根据 venue 和 venue_symbol 生成 internal 符号
        internal = InstrumentFactory._make_internal(venue, venue_symbol, asset)

        return Instrument(
            internal=internal,
            venue=venue,
            venue_symbol=venue_symbol,
            asset=asset,
            **kwargs
        )

    # 已知的 quote 货币列表（按长度降序排列，优先匹配长的）
    KNOWN_QUOTES = ["USDT", "USDC", "BUSD", "TUSD", "FDUSD", "USD", "BTC", "ETH", "BNB", "EUR", "GBP", "AUD", "TRY", "BRL"]

    @staticmethod
    def _make_internal(venue: str, venue_symbol: str, asset: Asset) -> str:
        """生成内部统一符号

        解析策略：
        1. 如果符号包含分隔符（-/_.），直接按分隔符拆分
        2. 否则，使用已知 quote 货币列表从后往前匹配
        3. 匹配失败时返回原始符号（不抛异常，留给上层处理）
        """
        # 已含分隔符的交易所（OKX, Bitget, KuCoin 等）
        for sep in ["-", "/", "_", "."]:
            if sep in venue_symbol:
                parts = venue_symbol.split(sep)
                # OKX: BTC-USDT-SWAP -> BTC-USDT-SWAP（保留原格式）
                return "-".join(parts)

        # 无分隔符（Binance: BTCUSDT, DOGEUSDT, SHIBUSDT）
        upper = venue_symbol.upper()
        for quote in InstrumentFactory.KNOWN_QUOTES:
            if upper.endswith(quote) and len(upper) > len(quote):
                base = upper[:-len(quote)]
                return f"{base}-{quote}"

        # 非 crypto 场所（CTP: IF2506, IB: AAPL）直接返回
        return venue_symbol
```

---

### 3.3 InstrumentManager（新组件）

```python
class InstrumentManager:
    """Instrument 管理器，替代/增强 SymbolManager"""

    def __init__(self):
        self._instruments: Dict[str, Instrument] = {}  # internal -> Instrument
        self._by_venue: Dict[str, Dict[str, Instrument]] = {}  # venue -> venue_symbol -> Instrument
        self._by_underlying: Dict[str, List[Instrument]] = {}  # underlying -> [Instrument]

    def register(self, instrument: Instrument) -> None:
        """注册 Instrument"""
        self._instruments[instrument.internal] = instrument
        self._by_venue.setdefault(instrument.venue, {})[instrument.venue_symbol] = instrument

        if instrument.underlying:
            self._by_underlying.setdefault(instrument.underlying, []).append(instrument)

    def get(self, internal: str) -> Optional[Instrument]:
        """获取 Instrument（通过内部符号）"""
        return self._instruments.get(internal)

    def get_by_venue(self, venue: str, venue_symbol: str) -> Optional[Instrument]:
        """获取 Instrument（通过场所符号）"""
        return self._by_venue.get(venue, {}).get(venue_symbol)

    def find(
        self,
        venue: Optional[str] = None,
        underlying: Optional[str] = None,
        asset: Optional[Asset] = None,
        active_only: bool = True
    ) -> List[Instrument]:
        """查找符合条件的 Instrument"""
        results = list(self._instruments.values())

        if venue:
            results = [i for i in results if i.venue == venue]
        if underlying:
            results = [i for i in results if i.underlying == underlying]
        if asset:
            results = [i for i in results if i.asset == asset]
        if active_only:
            results = [i for i in results if i.is_listed]

        return results

    def to_venue_symbol(self, internal: str, venue: str) -> Optional[str]:
        """内部符号转换为场所符号"""
        instrument = self.get(internal)
        if instrument and instrument.venue == venue:
            return instrument.venue_symbol
        return None

    def to_internal(self, venue_symbol: str, venue: str) -> Optional[str]:
        """场所符号转换为内部符号"""
        instrument = self.get_by_venue(venue, venue_symbol)
        return instrument.internal if instrument else None

    def load_from_config(self, config_path: str) -> None:
        """从配置文件批量加载 Instrument"""
        # 支持从 CSV/JSON/YAML 加载标的列表
        pass

# 全局单例
_instrument_manager = InstrumentManager()

def get_instrument_manager() -> InstrumentManager:
    return _instrument_manager
```

---

## 4. 连接与传输层

### 4.1 统一连接生命周期

```python
from enum import Enum
from threading import Lock

class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    AUTHENTICATED = "authenticated"
    ERROR = "error"

class ConnectionMixin:
    """连接管理混入类 — 仅用于 Feed 基类

    注意: BaseDataStream 已有完整的 ConnectionState 管理，无需此 Mixin。
    此 Mixin 仅为 Feed（REST 请求类）添加统一的连接生命周期接口。
    HTTP 场所的 connect/disconnect 默认为 no-op（保持向后兼容）。
    """

    def __init__(self):
        self._conn_state = ConnectionState.DISCONNECTED
        self._state_lock = Lock()

    @property
    def connection_state(self) -> ConnectionState:
        with self._state_lock:
            return self._conn_state

    def _set_connection_state(self, new_state: ConnectionState):
        with self._state_lock:
            self._conn_state = new_state

    def connect(self) -> None:
        """建立连接 — HTTP 场所默认 no-op，CTP/IB/QMT 子类需覆盖"""
        self._set_connection_state(ConnectionState.CONNECTED)

    def disconnect(self) -> None:
        """断开连接 — HTTP 场所默认 no-op，CTP/IB/QMT 子类需覆盖"""
        self._set_connection_state(ConnectionState.DISCONNECTED)

    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self._conn_state in (ConnectionState.CONNECTED, ConnectionState.AUTHENTICATED)
```

### 4.2 HTTP 客户端（使用 httpx）

```python
import httpx
import asyncio
from typing import Optional, Dict, Any

class HttpClient:
    """统一 HTTP 客户端"""

    def __init__(
        self,
        venue: str = "",
        timeout: float = 10.0,
        limits: Optional[httpx.Limits] = None,
        verify: bool = True,
        **kwargs
    ):
        self._venue = venue  # 场所标识，用于错误上下文
        self.timeout = timeout
        self.limits = limits or httpx.Limits(max_connections=100, max_keepalive_connections=20)

        # 同步客户端
        self._sync_client = httpx.Client(
            timeout=timeout,
            limits=self.limits,
            verify=verify,
            follow_redirects=True,
            **kwargs
        )

        # 异步客户端（延迟初始化）
        self._async_client: Optional[httpx.AsyncClient] = None

    def request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[Any] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """同步请求"""
        response = self._sync_client.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json,
            content=data,
            **kwargs
        )

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise self._handle_error(e.response)

        return response.json()

    async def async_request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[Any] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """异步请求"""
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(
                timeout=self.timeout,
                limits=self.limits,
                verify=True,
                follow_redirects=True
            )

        response = await self._async_client.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json,
            content=data,
            **kwargs
        )

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise self._handle_error(e.response)

        return response.json()

    def _handle_error(self, response: httpx.Response) -> Exception:
        """统一错误处理"""
        status = response.status_code
        try:
            body = response.json()
        except:
            body = {"text": response.text}

        if status == 429:
            from bt_api_py.error_framework import RateLimitError
            return RateLimitError(venue=self._venue, response=body)
        elif status == 401 or status == 403:
            from bt_api_py.error_framework import AuthenticationError
            return AuthenticationError(venue=self._venue, response=body)
        elif status >= 500:
            from bt_api_py.error_framework import ServerError
            return ServerError(venue=self._venue, status=status, response=body)
        else:
            from bt_api_py.error_framework import RequestError
            return RequestError(venue=self._venue, status=status, response=body)

    def close(self):
        """关闭客户端"""
        self._sync_client.close()
        if self._async_client:
            # 需要在 async context 中关闭
            pass

    async def aclose(self):
        """异步关闭"""
        self._sync_client.close()
        if self._async_client:
            await self._async_client.aclose()
```

---

## 5. 统一限流器（完整版）

### 5.1 限流器设计

```python
import time
import asyncio
from collections import deque
from typing import Optional, Dict, List, Callable
from dataclasses import dataclass
from enum import Enum
import fnmatch

class RateLimitType(str, Enum):
    SLIDING_WINDOW = "sliding_window"   # 滑动窗口
    FIXED_WINDOW = "fixed_window"       # 固定窗口
    TOKEN_BUCKET = "token_bucket"        # 令牌桶

class RateLimitScope(str, Enum):
    GLOBAL = "global"       # 全局限流
    ENDPOINT = "endpoint"   # 端点级限流
    IP = "ip"               # IP级限流

@dataclass
class RateLimitRule:
    """限流规则"""
    name: str                      # 规则名称
    type: RateLimitType            # 限流类型
    interval: int                  # 时间窗口（秒）
    limit: int                     # 限制数量
    scope: RateLimitScope          # 限流范围
    endpoint: Optional[str] = None  # 端点匹配（支持 glob）
    weight_map: Optional[Dict[str, int]] = None  # {method: weight}
    weight: int = 1                # 默认权重

    def match(self, method: str, path: str) -> bool:
        """判断请求是否匹配此规则"""
        if self.scope == RateLimitScope.GLOBAL:
            return True
        if self.scope == RateLimitScope.ENDPOINT and self.endpoint:
            return fnmatch.fnmatch(path, self.endpoint)
        return False

    def get_weight(self, method: str, path: str) -> int:
        """获取请求权重"""
        if self.weight_map:
            # 尝试精确匹配
            key = f"{method} {path}"
            if key in self.weight_map:
                return self.weight_map[key]
            # 尝试方法匹配
            if method in self.weight_map:
                return self.weight_map[method]
        return self.weight

class SlidingWindowLimiter:
    """滑动窗口限流器"""

    def __init__(self, interval: int, limit: int):
        self.interval = interval
        self.limit = limit
        self._requests: deque = deque()  # (timestamp, weight)

    def acquire(self, weight: int = 1) -> bool:
        """尝试获取许可"""
        now = time.time()

        # 移除窗口外的请求
        cutoff = now - self.interval
        while self._requests and self._requests[0][0] < cutoff:
            self._requests.popleft()

        # 计算当前窗口内的权重
        current_weight = sum(w for _, w in self._requests)

        if current_weight + weight <= self.limit:
            self._requests.append((now, weight))
            return True

        return False

    def wait_time(self) -> float:
        """计算需要等待的时间"""
        if not self._requests:
            return 0.0
        oldest, _ = self._requests[0]
        return max(0.0, oldest + self.interval - time.time())

class FixedWindowLimiter:
    """固定窗口限流器"""

    def __init__(self, interval: int, limit: int):
        self.interval = interval
        self.limit = limit
        self._window_start = 0.0
        self._window_count = 0

    def acquire(self, weight: int = 1) -> bool:
        now = time.time()
        window_start = int(now // self.interval) * self.interval

        if window_start != self._window_start:
            self._window_start = window_start
            self._window_count = 0

        if self._window_count + weight <= self.limit:
            self._window_count += weight
            return True

        return False

    def wait_time(self) -> float:
        now = time.time()
        window_end = self._window_start + self.interval
        return max(0.0, window_end - now)

class RateLimiter:
    """统一限流器"""

    def __init__(self, rules: List[RateLimitRule]):
        self.rules = rules
        self._limiters: Dict[str, Any] = {}  # {rule_name: limiter}
        self._lock = __import__('threading').Lock()  # 同步上下文用 threading.Lock

        # 初始化限流器
        for rule in rules:
            if rule.type == RateLimitType.SLIDING_WINDOW:
                self._limiters[rule.name] = SlidingWindowLimiter(rule.interval, rule.limit)
            elif rule.type == RateLimitType.FIXED_WINDOW:
                self._limiters[rule.name] = FixedWindowLimiter(rule.interval, rule.limit)
            elif rule.type == RateLimitType.TOKEN_BUCKET:
                # TODO: 实现令牌桶
                pass

    def acquire(
        self,
        method: str,
        path: str,
        weight: int = 1,
        block: bool = False,
        timeout: Optional[float] = None
    ) -> bool:
        """同步获取限流许可"""
        matched_rules = [r for r in self.rules if r.match(method, path)]

        if not matched_rules:
            return True

        for rule in matched_rules:
            limiter = self._limiters[rule.name]
            request_weight = rule.get_weight(method, path)

            if not limiter.acquire(request_weight * weight):
                if block:
                    # 等待直到可以获取许可
                    wait = limiter.wait_time()
                    if timeout is not None and wait > timeout:
                        return False
                    time.sleep(wait)
                    return self.acquire(method, path, weight, block=False)
                return False

        return True

    async def async_acquire(
        self,
        method: str,
        path: str,
        weight: int = 1
    ) -> bool:
        """异步获取限流许可"""
        while True:
            if self.acquire(method, path, weight, block=False):
                return True
            await asyncio.sleep(0.1)

    def get_limit_status(self) -> Dict[str, Dict]:
        """获取各限流器状态（用于监控）"""
        status = {}
        for rule in self.rules:
            limiter = self._limiters[rule.name]
            status[rule.name] = {
                "type": rule.type.value,
                "limit": rule.limit,
                "current": getattr(limiter, "_window_count", 0) if hasattr(limiter, "_window_count") else len(limiter._requests)
            }
        return status
```

### 5.2 限流配置示例

```yaml
# configs/exchanges/binance.yaml
rate_limits:
  # 全局滑动窗口
  - name: global_sliding
    type: sliding_window
    interval: 60
    limit: 12000
    scope: global
    weight: 1

  # 下单端点固定窗口（更严格）
  - name: order_endpoint
    type: fixed_window
    interval: 1
    limit: 100
    scope: endpoint
    endpoint: /api/v3/order*
    weight_map:
      POST: 10   # 下单消耗 10 倍权重
      DELETE: 5  # 撤单消耗 5 倍权重
      GET: 1     # 查询消耗 1 倍权重

  # 请求权重端点
  - name: request_weight
    type: fixed_window
    interval: 10
    limit: 2400
    scope: global
```

---

## 6. 配置系统设计（含 Schema 校验）

### 6.1 配置模型（使用 pydantic）

```python
from pydantic import BaseModel, Field, validator
from typing import Dict, Optional, List, Literal, Any, Union
from enum import Enum

class VenueType(str, Enum):
    CEX = "cex"
    DEX = "dex"
    BROKER = "broker"

class AuthType(str, Enum):
    NONE = "none"
    API_KEY = "api_key"
    HMAC_SHA256 = "hmac_sha256"
    HMAC_SHA512 = "hmac_sha512"
    OAUTH = "oauth"
    CERTIFICATE = "certificate"

class ConnectionType(str, Enum):
    HTTP = "http"
    WEBSOCKET = "websocket"
    SPI = "spi"              # CTP
    TWS = "tws"              # IB
    LOCAL_TERMINAL = "local_terminal"  # QMT
    RPC = "rpc"              # 区块链 RPC

# 基础 URL 配置
class BaseUrlsConfig(BaseModel):
    rest: Dict[str, str] = Field(default_factory=dict)
    wss: Dict[str, str] = Field(default_factory=dict)

# 连接配置
class ConnectionConfig(BaseModel):
    type: ConnectionType
    timeout: int = Field(default=10, ge=1, le=120)
    max_retries: int = Field(default=3, ge=0, le=10)

    # SPI/本地终端 特定配置
    md_front: Optional[str] = None       # 行情前置
    td_front: Optional[str] = None       # 交易前置
    exe_path: Optional[str] = None      # 可执行文件路径
    session_id: Optional[int] = None     # 会话 ID

# 认证配置
class AuthConfig(BaseModel):
    type: AuthType
    header_name: Optional[str] = None
    timestamp_key: Optional[str] = None
    signature_key: Optional[str] = None
    api_key_param: Optional[str] = None

# 限流规则配置
class RateLimitRuleConfig(BaseModel):
    name: str
    type: Literal["sliding_window", "fixed_window", "token_bucket"]
    interval: int = Field(..., gt=0)
    limit: int = Field(..., gt=0)
    scope: Literal["global", "endpoint", "ip"]
    endpoint: Optional[str] = None
    weight: int = Field(default=1, ge=1)
    weight_map: Optional[Dict[str, int]] = None

# 资产类型配置
class AssetTypeConfig(BaseModel):
    symbol_format: str = Field(..., description="如 {base}{quote} 或 {base}-{quote}")
    rest_paths: Dict[str, str] = Field(default_factory=dict)
    wss_paths: Dict[str, str] = Field(default_factory=dict)
    wss_channels: Dict[str, str] = Field(default_factory=dict)

    # 符号列表（可选，用于验证）
    symbols: Optional[List[str]] = None

# 主配置模型
class ExchangeConfig(BaseModel):
    id: str = Field(..., min_length=2, max_length=20, regex="^[A-Z0-9_]+$")
    display_name: str
    venue_type: VenueType
    website: Optional[str] = None
    api_doc: Optional[str] = None

    base_urls: Optional[BaseUrlsConfig] = None
    connection: ConnectionConfig
    authentication: Optional[AuthConfig] = None
    rate_limits: List[RateLimitRuleConfig] = Field(default_factory=list)
    asset_types: Dict[str, AssetTypeConfig]

    # DEX 特定
    chains: Optional[List[str]] = None      # 支持的区块链
    router_address: Optional[str] = None    # 路由合约地址
    factory_address: Optional[str] = None   # 工厂合约地址

    # Broker 特定
    broker_id: Optional[str] = None        # 券商 ID
    app_id: Optional[str] = None           # 应用 ID

    @validator('base_urls')
    def validate_base_urls(cls, v, values):
        venue_type = values.get('venue_type')
        conn_type = values.get('connection', {})
        if isinstance(conn_type, ConnectionConfig):
            conn_type = conn_type.type
        # CEX 必须有 base_urls
        if venue_type == VenueType.CEX and not v:
            raise ValueError("CEX must have base_urls")
        # DEX 和 Broker 可选 base_urls（如 Hyperliquid 类CEX DEX、IB Web API）
        # 不做强制限制
        return v

    @validator('connection')
    def validate_connection(cls, v, values):
        venue_type = values.get('venue_type')
        conn_type = v.type

        # CEX 必须使用 HTTP
        if venue_type == VenueType.CEX and conn_type not in (ConnectionType.HTTP, ConnectionType.WEBSOCKET):
            raise ValueError("CEX must use HTTP or WEBSOCKET connection")

        # CTP 必须使用 SPI
        if values.get('id') == 'CTP' and conn_type != ConnectionType.SPI:
            raise ValueError("CTP must use SPI connection")

        return v

# 从 YAML 加载配置
def load_exchange_config(config_path: str) -> ExchangeConfig:
    import yaml

    with open(config_path) as f:
        data = yaml.safe_load(f)

    return ExchangeConfig(**data)
```

---

## 7. 统一错误框架（完整版）

### 7.1 错误分类

```python
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

class ErrorCategory(str, Enum):
    NETWORK = "network"          # 网络相关错误
    AUTH = "auth"                # 认证相关错误
    RATE_LIMIT = "rate_limit"    # 限流错误
    BUSINESS = "business"        # 业务逻辑错误
    SYSTEM = "system"            # 系统/交易所错误
    CAPABILITY = "capability"    # 能力不支持
    VALIDATION = "validation"    # 参数验证错误

class UnifiedErrorCode(int, Enum):
    """统一错误码"""
    # 网络错误 (1xxx)
    NETWORK_TIMEOUT = 1001
    NETWORK_DISCONNECTED = 1002
    DNS_ERROR = 1003
    CONNECTION_REFUSED = 1004

    # 认证错误 (2xxx)
    INVALID_API_KEY = 2001
    INVALID_SIGNATURE = 2002
    EXPIRED_TIMESTAMP = 2003
    PERMISSION_DENIED = 2004
    SESSION_EXPIRED = 2005

    # 限流错误 (3xxx)
    RATE_LIMIT_EXCEEDED = 3001
    IP_BANNED = 3002
    TOO_MANY_REQUESTS = 3003

    # 业务错误 (4xxx)
    INVALID_SYMBOL = 4001
    INVALID_PRICE = 4002
    INVALID_VOLUME = 4003
    INSUFFICIENT_BALANCE = 4004
    INSUFFICIENT_MARGIN = 4005
    ORDER_NOT_FOUND = 4006
    ORDER_ALREADY_FILLED = 4007
    MARKET_CLOSED = 4008
    POSITION_NOT_FOUND = 4009
    DUPLICATE_ORDER = 4010
    INVALID_ORDER = 4011           # 订单被拒绝/无效
    PRECISION_ERROR = 4012         # 精度超限

    # 系统错误 (5xxx)
    EXCHANGE_MAINTENANCE = 5001
    EXCHANGE_OVERLOADED = 5002
    INTERNAL_ERROR = 5003
    UNSUPPORTED_OPERATION = 5004

    # 能力错误 (6xxx)
    NOT_SUPPORTED = 6001
    NOT_IMPLEMENTED = 6002

    # 验证错误 (7xxx)
    INVALID_PARAMETER = 7001
    MISSING_PARAMETER = 7002
    PARAMETER_OUT_OF_RANGE = 7003

@dataclass
class UnifiedError(Exception):
    """统一错误格式"""
    code: UnifiedErrorCode
    category: ErrorCategory
    venue: str
    message: str
    original_error: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)

    def __str__(self):
        return f"[{self.venue}] {self.code.name}: {self.message}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code.value,
            "code_name": self.code.name,
            "category": self.category.value,
            "venue": self.venue,
            "message": self.message,
            "context": self.context
        }
```

### 7.2 错误翻译器

```python
from abc import ABC, abstractmethod

class ErrorTranslator(ABC):
    """错误翻译器基类"""

    @classmethod
    @abstractmethod
    def translate(cls, raw_error: Any, venue: str) -> UnifiedError:
        """将原始错误转换为统一错误"""
        pass

class CEXErrorTranslator(ErrorTranslator):
    """CEX 通用错误翻译器（适用于大多数 HTTP API）"""

    # 通用 HTTP 状态码映射
    HTTP_STATUS_MAP = {
        400: (UnifiedErrorCode.INVALID_PARAMETER, "Invalid request parameters"),
        401: (UnifiedErrorCode.INVALID_API_KEY, "Invalid API key"),
        403: (UnifiedErrorCode.PERMISSION_DENIED, "Permission denied"),
        404: (UnifiedErrorCode.INVALID_SYMBOL, "Resource not found"),
        429: (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Rate limit exceeded"),
        500: (UnifiedErrorCode.INTERNAL_ERROR, "Internal server error"),
        503: (UnifiedErrorCode.EXCHANGE_OVERLOADED, "Service unavailable"),
        504: (UnifiedErrorCode.NETWORK_TIMEOUT, "Gateway timeout"),
    }

    @classmethod
    def translate(cls, raw_error: dict, venue: str) -> UnifiedError:
        """从 HTTP 响应翻译错误"""
        # 尝试从响应中提取错误信息
        code = raw_error.get("code")
        msg = raw_error.get("msg", raw_error.get("message", ""))
        status = raw_error.get("status")

        # 如果有特定错误码，使用特定映射
        if hasattr(cls, 'ERROR_MAP') and code in cls.ERROR_MAP:
            unified_code, default_msg = cls.ERROR_MAP[code]
            return UnifiedError(
                code=unified_code,
                category=cls._get_category(unified_code),
                venue=venue,
                message=msg or default_msg,
                original_error=f"{code}: {msg}",
                context={"raw_response": raw_error}
            )

        # 否则使用 HTTP 状态码映射
        if status and status in cls.HTTP_STATUS_MAP:
            unified_code, default_msg = cls.HTTP_STATUS_MAP[status]
            return UnifiedError(
                code=unified_code,
                category=cls._get_category(unified_code),
                venue=venue,
                message=msg or default_msg,
                original_error=f"{status}: {msg}",
                context={"raw_response": raw_error}
            )

        # 默认处理
        return UnifiedError(
            code=UnifiedErrorCode.INTERNAL_ERROR,
            category=ErrorCategory.SYSTEM,
            venue=venue,
            message=msg or "Unknown error",
            original_error=str(raw_error),
            context={"raw_response": raw_error}
        )

    @classmethod
    def _get_category(cls, code: UnifiedErrorCode) -> ErrorCategory:
        """从错误码获取类别"""
        if 1000 <= code.value < 2000:
            return ErrorCategory.NETWORK
        elif 2000 <= code.value < 3000:
            return ErrorCategory.AUTH
        elif 3000 <= code.value < 4000:
            return ErrorCategory.RATE_LIMIT
        elif 4000 <= code.value < 5000:
            return ErrorCategory.BUSINESS
        elif 5000 <= code.value < 6000:
            return ErrorCategory.SYSTEM
        elif 6000 <= code.value < 7000:
            return ErrorCategory.CAPABILITY
        else:
            return ErrorCategory.VALIDATION

# Binance 特定错误翻译器
class BinanceErrorTranslator(CEXErrorTranslator):
    ERROR_MAP = {
        -1000: (UnifiedErrorCode.INTERNAL_ERROR, "Unknown error"),
        -1001: (UnifiedErrorCode.INTERNAL_ERROR, "Disconnected"),
        -1002: (UnifiedErrorCode.PERMISSION_DENIED, "Unauthorized"),
        -1003: (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Too many requests"),
        -1006: (UnifiedErrorCode.EXCHANGE_OVERLOADED, "Unexpected disconnect"),
        -1021: (UnifiedErrorCode.EXPIRED_TIMESTAMP, "Timestamp outside recvWindow"),
        -1022: (UnifiedErrorCode.INVALID_SIGNATURE, "Signature not valid"),
        -1100: (UnifiedErrorCode.INVALID_PARAMETER, "Illegal characters in request"),
        -1101: (UnifiedErrorCode.INVALID_PARAMETER, "Too many parameters"),
        -1102: (UnifiedErrorCode.MISSING_PARAMETER, "Mandatory parameter was not sent"),
        -1103: (UnifiedErrorCode.INVALID_PARAMETER, "Unknown parameter"),
        -1104: (UnifiedErrorCode.INVALID_PARAMETER, "Parameter is empty"),
        -1106: (UnifiedErrorCode.INVALID_PARAMETER, "Parameter is not supported"),
        -1111: (UnifiedErrorCode.PRECISION_ERROR, "Precision is over the maximum defined"),
        -1112: (UnifiedErrorCode.INVALID_VOLUME, "Invalid order quantity"),
        -1114: (UnifiedErrorCode.INVALID_API_KEY, "Invalid API-key format"),
        -1115: (UnifiedErrorCode.PERMISSION_DENIED, "Invalid API-key, IP, or permissions"),
        -2010: (UnifiedErrorCode.ORDER_NOT_FOUND, "Order does not exist"),
        -2011: (UnifiedErrorCode.ORDER_ALREADY_FILLED, "Order already filled"),
        -2013: (UnifiedErrorCode.INVALID_ORDER, "Order rejected"),
        -2014: (UnifiedErrorCode.INVALID_API_KEY, "API-key format invalid"),
        -2015: (UnifiedErrorCode.PERMISSION_DENIED, "Invalid API-key, IP, or permissions for action"),
    }

# CTP 错误翻译器（继承 CEXErrorTranslator 以复用 _get_category）
class CTPErrorTranslator(CEXErrorTranslator):
    ERROR_MAP = {
        0: (None, "成功"),  # 不是错误
        -1: (UnifiedErrorCode.NETWORK_DISCONNECTED, "网络连接失败"),
        -2: (UnifiedErrorCode.INTERNAL_ERROR, "未处理请求超过许可数"),
        -3: (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "每秒发送请求数超过许可数"),
        -4: (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "上传请求数超过许可数"),
        -5: (UnifiedErrorCode.INTERNAL_ERROR, "发送请求失败"),
        -6: (UnifiedErrorCode.NETWORK_TIMEOUT, "接收响应失败"),
        -7: (UnifiedErrorCode.NETWORK_DISCONNECTED, "连接断开"),
        -8: (UnifiedErrorCode.NETWORK_TIMEOUT, "请求处理超时"),
        -9: (UnifiedErrorCode.NETWORK_TIMEOUT, "发送请求失败"),
        -10: (UnifiedErrorCode.INTERNAL_ERROR, "内部错误"),
        # ... 更多 CTP 错误码
    }

    @classmethod
    def translate(cls, raw_error: dict, venue: str) -> UnifiedError:
        error_id = raw_error.get("ErrorID", 0)
        error_msg = raw_error.get("ErrorMsg", "")

        if error_id == 0:
            # 不是错误
            return None

        code, default_msg = cls.ERROR_MAP.get(
            error_id,
            (UnifiedErrorCode.INTERNAL_ERROR, f"未知错误: {error_id}")
        )

        if code is None:
            code = UnifiedErrorCode.INTERNAL_ERROR

        return UnifiedError(
            code=code,
            category=cls._get_category(code),
            venue=venue,
            message=error_msg or default_msg,
            original_error=f"[{error_id}] {error_msg}",
            context={"error_id": error_id, "raw_error": raw_error}
        )
```

---

## 8. QMT 特殊处理

### 8.1 QMT 配置

```yaml
# configs/exchanges/qmt.yaml
exchange:
  id: QMT
  display_name: QMT
  venue_type: broker
  platform: windows_only  # 标记平台限制

connection:
  type: local_terminal
  exe_path: ""  # 从环境变量读取
  session_id: 0

# CI/CD 环境变量支持
environment:
  QMT_PATH: ""  # QMT 安装路径
  SKIP_QMT_TESTS: "true"  # CI 默认跳过
```

### 8.2 QMT 测试策略

```python
import os
import sys

def skip_qmt_tests():
    """检查是否应该跳过 QMT 测试"""
    # CI 环境跳过
    if os.getenv("CI") or os.getenv("SKIP_QMT_TESTS") == "true":
        return True

    # 非 Windows 跳过
    if sys.platform != "win32":
        return True

    # 检查 QMT 是否可用
    qmt_path = os.getenv("QMT_PATH")
    if qmt_path and not os.path.exists(qmt_path):
        return True

    return False

# pytest fixture
@pytest.fixture
def qmt_feed():
    if skip_qmt_tests():
        pytest.skip("QMT tests skipped (CI or non-Windows)")
    # ... 创建真实的 QMT Feed
```

---

## 9. 完整接口映射表

| 协议方法 | Binance | OKX | CTP | IB | QMT | 优先级 |
|---------|:-------:|:---:|:---:|:---:|:---:|:------:|
| **连接管理** ||||||||
| `connect` | no-op | no-op | 有 | 需实现 | 需实现 | P1 |
| `disconnect` | no-op | no-op | 需新增 | 需实现 | 需实现 | P1 |
| `is_connected` | 有 | 有 | 有 | 需实现 | 需实现 | P1 |
| **行情查询** ||||||||
| `get_tick` | 有 | 有 | 有 | 需新增 | 需新增 | P0 |
| `get_depth` | 有 | 有 | 有 | 需新增 | 需新增 | P0 |
| `get_kline` | 有 | 有 | 需新增 | 需新增 | 需新增 | P1 |
| `get_funding_rate` | 有 | 有 | 不适用 | 不适用 | 不适用 | P1 |
| `get_mark_price` | 有 | 有 | 不适用 | 不适用 | 不适用 | P2 |
| **交易操作** ||||||||
| `make_order` | 有 | 有 | 有 | 需新增 | 需新增 | P0 |
| `cancel_order` | 有 | 有 | 有 | 需新增 | 需新增 | P0 |
| `cancel_all` | 有 | 有 | 需新增 | 需新增 | 不适用 | P1 |
| `modify_order` | 不支持 | 有 | 不适用 | 需新增 | 需新增 | P2 |
| `query_order` | 有 | 有 | 有 | 需新增 | 需新增 | P0 |
| `get_open_orders` | 有 | 有 | 有 | 需新增 | 需新增 | P0 |
| `get_deals` | 有 | 有 | 有 | 需新增 | 需新增 | P2 |
| **账户查询** ||||||||
| `get_balance` | 有 | 有 | 有 | 需新增 | 需新增 | P0 |
| `get_account` | 有 | 有 | 有 | 需新增 | 需新增 | P1 |
| `get_position` | 有 | 有 | 有 | 需新增 | 不适用 | P1 |
| **异步接口** ||||||||
| `async_get_tick` | 需真实实现 | 需真实实现 | 不适用 | 需实现 | 不适用 | P1 |
| `async_make_order` | 需真实实现 | 需真实实现 | 不适用 | 需实现 | 不适用 | P1 |
| `async_cancel_order` | 需真实实现 | 需真实实现 | 不适用 | 需实现 | 不适用 | P1 |
| `async_get_balance` | 需真实实现 | 需真实实现 | 不适用 | 需实现 | 不适用 | P1 |
| **流相关** ||||||||
| `MARKET_STREAM` | 有 | 有 | 有 | 需新增 | 需新增 | P0 |
| `ACCOUNT_STREAM` | 有 | 有 | 有 | 需新增 | 需新增 | P1 |

---

## 10. 目录结构（目标形态）

```
bt_api_py/
├── feeds/
│   ├── __init__.py
│   ├── abstract_feed.py          # 统一协议定义
│   ├── feed.py                   # Feed 基类（重构）
│   ├── base_stream.py            # DataStream 基类（保持）
│   ├── http_client.py            # HTTP 客户端
│   ├── connection_mixin.py       # 连接管理混入
│   │
│   ├── live_binance/             # Binance（重构）
│   ├── live_okx/                 # OKX（重构）
│   ├── live_ctp_feed.py          # CTP（重构）
│   ├── live_ib_feed.py           # IB（重写）
│   ├── live_qmt_feed.py          # QMT（新增）
│   │
│   └── dex/                      # DEX
│       ├── uniswap/
│       ├── pancakeswap/
│       └── ...
│
├── containers/
│   ├── __init__.py
│   ├── instrument.py              # Instrument 模型
│   ├── exchanges/                # ExchangeData
│   ├── orders/
│   ├── trades/
│   ├── positions/
│   ├── balances/
│   └── ...
│
├── registry.py                   # ExchangeRegistry（扩展）
├── instrument_manager.py         # InstrumentManager（新增）
├── symbol_manager.py             # 符号管理器（整合）
├── config_loader.py              # 配置加载器
├── rate_limiter.py               # 限流器
├── error_framework.py            # 错误框架
│
├── configs/
│   └── exchanges/                # 交易所配置
│       ├── binance.yaml
│       ├── okx.yaml
│       ├── ctp.yaml
│       ├── ib.yaml
│       └── qmt.yaml
│
├── templates/
│   └── exchange/                 # 脚手架模板
│       ├── __init__.py.j2
│       ├── request_base.py.j2
│       └── ...
│
└── tests/
    ├── test_abstract_feed.py
    ├── test_instrument.py
    ├── test_rate_limiter.py
    ├── test_error_framework.py
    └── ...
```

---

## 11. 性能基准

### 11.1 性能目标

| 操作 | 目标 | 测量方式 |
|------|------|----------|
| Feed 初始化 | < 10ms | time.perf_counter |
| HTTP 请求（不含网络） | < 5ms | httpx benchmark |
| 符号转换 | < 0.1ms | 10万次调用平均 |
| 错误翻译 | < 0.1ms | 10万次调用平均 |
| 配置加载（单个） | < 50ms | 单个文件解析 |
| 配置加载（全部70个） | < 500ms | 批量加载 |
| Instrument 查询 | < 0.05ms | 100万次查询 |

### 11.2 资源限制

| 指标 | 目标 |
|------|------|
| 单 Feed 内存占用 | < 10MB |
| 50 个并发连接内存 | < 500MB |
| CPU 空闲时 | < 1% |
| 吞吐量 | > 1000 req/s |

---

## 12. 测试策略

### 12.1 测试分层

```
┌─────────────────────────────────┐
│   E2E 测试 (少量，关键路径)      │
│   - 使用测试网/沙盒              │
│   - 真实下单撤单（小额）         │
└─────────────────────────────────┘
           ↑
├─────────────────────────────────┤
│   集成测试 (中等)                │
│   - 组件协作验证                 │
│   - 使用 mock server             │
└─────────────────────────────────┘
           ↑
├─────────────────────────────────┤
│   单元测试 (大量)                │
│   - 每个 Feed 方法               │
│   - 每个 Translator              │
│   - 配置加载解析                 │
└─────────────────────────────────┘
```

### 12.2 Mock 策略

| 场所类型 | Mock 工具 | 说明 |
|---------|----------|------|
| CEX | respx / httpx MockTransport | HTTP 响应 mock |
| DEX (类CEX) | 同 CEX | - |
| DEX (链上) | eth-tester / anvil | 本地链 |
| CTP | 模拟行情/交易回报 | 回放数据 |
| IB | ib_insync paper mode | paper account |
| QMT | mock 数据 | 本地模拟 |

---

## 13. 改造顺序与文件级清单（更新版）

### 第一批：新增协议与模型（不破坏现有逻辑）

- `bt_api_py/feeds/abstract_feed.py` - 统一协议
- `bt_api_py/feeds/connection_mixin.py` - 连接管理
- `bt_api_py/containers/instrument.py` - Instrument 模型
- `bt_api_py/instrument_manager.py` - InstrumentManager

### 第二批：基础设施（独立组件）

- `bt_api_py/feeds/http_client.py` - HTTP 客户端
- `bt_api_py/rate_limiter.py` - 限流器
- `bt_api_py/error_framework.py` - 错误框架
- `bt_api_py/config_loader.py` - 配置加载器（含 pydantic 模型）

### 第三批：配置文件

- `bt_api_py/configs/exchanges/binance.yaml`
- `bt_api_py/configs/exchanges/okx.yaml`
- `bt_api_py/configs/exchanges/ctp.yaml`
- `bt_api_py/configs/exchanges/ib.yaml`
- `bt_api_py/configs/exchanges/qmt.yaml`

### 第四批：Feed 基类重构

- `bt_api_py/feeds/feed.py` - 引入 httpx、RateLimiter、ErrorTranslator
- `bt_api_py/feeds/base_stream.py` - 引入连接状态管理

### 第五批：现有交易所对齐

- `bt_api_py/feeds/live_binance/` - 对齐新协议
- `bt_api_py/feeds/live_okx/` - 对齐新协议
- `bt_api_py/feeds/live_ctp_feed.py` - 对齐新协议

### 第六批：新交易所/券商

- `bt_api_py/feeds/live_ib_feed.py` - 重写
- `bt_api_py/feeds/live_qmt_feed.py` - 新增

### 第七批：DEX 基础设施

- `bt_api_py/feeds/dex/base.py` - DEX 基类
- `bt_api_py/feeds/dex/evm.py` - EVM DEX 基类

### 第八批：工具与测试

- `bt_api_py/tools/generate_scaffold.py` - 脚手架工具
- `bt_api_py/tools/validate_exchange.py` - 验证工具
- `tests/benchmarks/` - 性能基准测试
