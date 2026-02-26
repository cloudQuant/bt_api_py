"""
IB 合约描述容器
IB 的合约描述比加密货币和 CTP 都复杂，需要多个字段组合才能唯一确定一个合约
"""


class IbContract:
    """IB 合约描述，对应 ibapi.contract.Contract"""

    def __init__(self, symbol="", sec_type="STK", exchange="SMART",
                 currency="USD", con_id=0, last_trade_date="",
                 strike=0.0, right="", multiplier="", primary_exchange="",
                 local_symbol="", trading_class=""):
        self.symbol = symbol                       # 品种代码, 如 "AAPL"
        self.sec_type = sec_type                   # 合约类型: STK/FUT/OPT/FOP/CASH/CFD/BOND/CRYPTO
        self.exchange = exchange                   # 交易所: SMART/NYSE/GLOBEX/SEHK 等
        self.currency = currency                   # 货币: USD/HKD/CNH/EUR 等
        self.con_id = con_id                       # IB 合约ID (唯一)
        self.last_trade_date = last_trade_date     # 到期日, 如 "20250620"
        self.strike = strike                       # 期权行权价
        self.right = right                         # 期权类型: C(Call) / P(Put)
        self.multiplier = multiplier               # 乘数
        self.primary_exchange = primary_exchange   # 主交易所
        self.local_symbol = local_symbol           # 本地品种代码
        self.trading_class = trading_class         # 交易类别

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items() if v}

    def __str__(self):
        parts = [self.symbol, self.sec_type, self.exchange, self.currency]
        if self.last_trade_date:
            parts.append(self.last_trade_date)
        if self.strike:
            parts.append(str(self.strike))
        if self.right:
            parts.append(self.right)
        return " ".join(parts)

    def __repr__(self):
        return f"IbContract({self.__str__()})"

    @classmethod
    def stock(cls, symbol, exchange="SMART", currency="USD"):
        """快速创建股票合约"""
        return cls(symbol=symbol, sec_type="STK", exchange=exchange, currency=currency)

    @classmethod
    def future(cls, symbol, exchange="GLOBEX", currency="USD", last_trade_date=""):
        """快速创建期货合约"""
        return cls(symbol=symbol, sec_type="FUT", exchange=exchange,
                   currency=currency, last_trade_date=last_trade_date)

    @classmethod
    def option(cls, symbol, last_trade_date, strike, right,
               exchange="SMART", currency="USD"):
        """快速创建期权合约"""
        return cls(symbol=symbol, sec_type="OPT", exchange=exchange,
                   currency=currency, last_trade_date=last_trade_date,
                   strike=strike, right=right)

    @classmethod
    def forex(cls, symbol, exchange="IDEALPRO", currency="USD"):
        """快速创建外汇合约"""
        return cls(symbol=symbol, sec_type="CASH", exchange=exchange, currency=currency)
