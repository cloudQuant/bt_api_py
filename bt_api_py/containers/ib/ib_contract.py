"""
IB 合约描述容器
IB 的合约描述比加密货币和 CTP 都复杂，需要多个字段组合才能唯一确定一个合约
"""

from __future__ import annotations

from typing import Any


class IbContract:
    """IB 合约描述，对应 ibapi.contract.Contract"""

    def __init__(
        self,
        symbol: Any = "",
        sec_type: Any = "STK",
        exchange: Any = "SMART",
        currency: Any = "USD",
        con_id: Any = 0,
        last_trade_date: Any = "",
        strike: Any = 0.0,
        right: Any = "",
        multiplier: Any = "",
        primary_exchange: Any = "",
        local_symbol: Any = "",
        trading_class: Any = "",
    ) -> None:
        self.symbol = symbol  # 品种代码, 如 "AAPL"
        self.sec_type = sec_type  # 合约类型: STK/FUT/OPT/FOP/CASH/CFD/BOND/CRYPTO
        self.exchange = exchange  # 交易所: SMART/NYSE/GLOBEX/SEHK 等
        self.currency = currency  # 货币: USD/HKD/CNH/EUR 等
        self.con_id = con_id  # IB 合约ID (唯一)
        self.last_trade_date = last_trade_date  # 到期日, 如 "20250620"
        self.strike = strike  # 期权行权价
        self.right = right  # 期权类型: C(Call) / P(Put)
        self.multiplier = multiplier  # 乘数
        self.primary_exchange = primary_exchange  # 主交易所
        self.local_symbol = local_symbol  # 本地品种代码
        self.trading_class = trading_class  # 交易类别

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v}

    def __str__(self) -> str:
        parts = [self.symbol, self.sec_type, self.exchange, self.currency]
        if self.last_trade_date:
            parts.append(self.last_trade_date)
        if self.strike:
            parts.append(str(self.strike))
        if self.right:
            parts.append(self.right)
        return " ".join(parts)

    def __repr__(self) -> str:
        return f"IbContract({self.__str__()})"

    @classmethod
    def stock(
        cls: type[IbContract], symbol: Any, exchange: Any = "SMART", currency: Any = "USD"
    ) -> IbContract:
        """快速创建股票合约"""
        return cls(symbol=symbol, sec_type="STK", exchange=exchange, currency=currency)

    @classmethod
    def future(
        cls: type[IbContract],
        symbol: Any,
        exchange: Any = "GLOBEX",
        currency: Any = "USD",
        last_trade_date: Any = "",
    ) -> IbContract:
        """快速创建期货合约"""
        return cls(
            symbol=symbol,
            sec_type="FUT",
            exchange=exchange,
            currency=currency,
            last_trade_date=last_trade_date,
        )

    @classmethod
    def option(
        cls: type[IbContract],
        symbol: Any,
        last_trade_date: Any,
        strike: Any,
        right: Any,
        exchange: Any = "SMART",
        currency: Any = "USD",
    ) -> IbContract:
        """快速创建期权合约"""
        return cls(
            symbol=symbol,
            sec_type="OPT",
            exchange=exchange,
            currency=currency,
            last_trade_date=last_trade_date,
            strike=strike,
            right=right,
        )

    @classmethod
    def forex(
        cls: type[IbContract], symbol: Any, exchange: Any = "IDEALPRO", currency: Any = "USD"
    ) -> IbContract:
        """快速创建外汇合约"""
        return cls(symbol=symbol, sec_type="CASH", exchange=exchange, currency=currency)
