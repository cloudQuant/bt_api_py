"""
统一 Instrument 模型

用于表示所有交易标的（现货、永续、交割、期权、股票、外汇等），
提供内部符号 ↔ 场所符号双向映射能力。
"""

import dataclasses
import enum
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import unique
from typing import Any


@unique
class AssetType(enum.StrEnum):
    """资产类型枚举"""

    SPOT = "spot"
    SWAP = "swap"
    FUTURE = "future"
    OPTION = "option"
    STK = "stk"
    FUND = "fund"
    BOND = "bond"
    FX = "fx"
    INDEX = "index"


@dataclass(frozen=True)
class Instrument:
    """统一交易标的模型（不可变，线程安全）"""

    # === 基础标识 ===
    internal: str  # 内部统一符号，如 BTC-USDT, IF2506, AAPL
    venue: str  # BINANCE___SWAP, CTP___FUTURE, IB___STK
    venue_symbol: str  # 交易所/券商原始符号，如 BTCUSDT, IF2506, AAPL
    asset_type: AssetType  # 资产类型

    # === 标的属性 ===
    underlying: str | None = None  # 标的：BTC、沪深300、Apple
    base_currency: str | None = None  # 基础货币（现货/合约）
    quote_currency: str | None = None  # 计价货币

    # === 合约属性（FUTURE/OPTION） ===
    expiry: datetime | None = None
    strike: Decimal | None = None
    contract_size: Decimal | None = None
    option_type: str | None = None  # CALL / PUT

    # === 交易属性 ===
    tick_size: Decimal | None = None
    min_qty: Decimal | None = None
    max_qty: Decimal | None = None
    qty_step: Decimal | None = None
    min_notional: Decimal | None = None

    # === 状态信息 ===
    status: str = "active"  # active / suspend / expire / delist
    list_time: datetime | None = None
    delist_time: datetime | None = None

    # === 扩展信息 ===
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        if self.expiry is None:
            return False
        return datetime.now() > self.expiry

    @property
    def is_listed(self) -> bool:
        if self.status != "active":
            return False
        return not (self.delist_time and datetime.now() > self.delist_time)

    def with_params(self, **kwargs: Any) -> "Instrument":
        """Create a copy with updated parameters.

        Args:
            **kwargs: Field names and values to update.

        Returns:
            A new Instrument instance with updated fields.
        """
        return dataclasses.replace(self, **kwargs)


# ── InstrumentFactory ─────────────────────────────────────────

# 已知的 quote 货币列表（按长度降序排列，优先匹配长的）
KNOWN_QUOTES = [
    "USDT",
    "USDC",
    "BUSD",
    "TUSD",
    "FDUSD",
    "USD",
    "BTC",
    "ETH",
    "BNB",
    "EUR",
    "GBP",
    "AUD",
    "TRY",
    "BRL",
]


class InstrumentFactory:
    """Instrument 工厂类"""

    @staticmethod
    def from_venue(
        venue: str,
        venue_symbol: str,
        asset_type: AssetType,
        **kwargs: Any,
    ) -> Instrument:
        """Create an Instrument from exchange symbol.

        Args:
            venue: Exchange identifier (e.g., "BINANCE___SPOT", "CTP___FUTURE").
            venue_symbol: Original exchange symbol (e.g., "BTCUSDT", "IF2506").
            asset_type: Type of asset (spot, swap, future, etc.).
            **kwargs: Additional instrument attributes.

        Returns:
            A new Instrument instance.
        """
        internal = InstrumentFactory._make_internal(venue, venue_symbol, asset_type)
        return Instrument(
            internal=internal,
            venue=venue,
            venue_symbol=venue_symbol,
            asset_type=asset_type,
            **kwargs,
        )

    @staticmethod
    def _make_internal(venue: str, venue_symbol: str, asset_type: AssetType) -> str:
        """Generate internal unified symbol.

        Parsing strategy:
        1. If symbol contains separators (-/_.), split by separator and join with '-'.
        2. Otherwise, match known quote currencies from the end.
        3. Return original symbol if matching fails.

        Args:
            venue: Exchange identifier.
            venue_symbol: Original exchange symbol.
            asset_type: Type of asset.

        Returns:
            Internal unified symbol (e.g., "BTC-USDT").
        """
        # 已含分隔符的交易所（OKX, Bitget, KuCoin 等）
        for sep in ["-", "/", "_", "."]:
            if sep in venue_symbol:
                parts = venue_symbol.split(sep)
                return "-".join(parts)

        # 无分隔符（Binance: BTCUSDT, DOGEUSDT, SHIBUSDT）
        upper = venue_symbol.upper()
        for quote in KNOWN_QUOTES:
            if upper.endswith(quote) and len(upper) > len(quote):
                base = upper[: -len(quote)]
                return f"{base}-{quote}"

        # 非 crypto 场所（CTP: IF2506, IB: AAPL）直接返回
        return venue_symbol
