"""
品种管理器 — 统一品种命名与跨交易所映射
不同交易所对同一品种有不同的命名规则:
  - Binance: "BTCUSDT"
  - OKX: "BTC-USDT-SWAP"
  - CTP: "IF2506"
  - IB: Contract(symbol="AAPL", secType="STK", exchange="SMART", currency="USD")
SymbolManager 提供统一的内部名称与交易所名称之间的双向映射
"""


class SymbolInfo:
    """品种元信息"""

    def __init__(self, internal_name, exchange, exchange_symbol, **meta):
        self.internal_name = internal_name  # 内部统一名称, 如 "BTC-USDT"
        self.exchange = exchange  # 交易所标识, 如 "BINANCE___SWAP"
        self.exchange_symbol = exchange_symbol  # 交易所原始品种名, 如 "BTCUSDT"
        self.meta = meta  # 额外元信息

    def __repr__(self):
        return (
            f"SymbolInfo(internal={self.internal_name}, exchange={self.exchange}, "
            f"symbol={self.exchange_symbol})"
        )


class SymbolManager:
    """品种管理器，管理内部名称与交易所名称之间的映射"""

    def __init__(self):
        # internal_name -> {exchange_name: SymbolInfo}
        self._to_exchange = {}
        # (exchange_name, exchange_symbol) -> internal_name
        self._from_exchange = {}

    def register(self, internal_name, exchange, exchange_symbol, **meta):
        """注册品种映射
        :param internal_name: 内部统一名称, 如 "BTC-USDT"
        :param exchange: 交易所标识, 如 "BINANCE___SWAP"
        :param exchange_symbol: 交易所原始品种名, 如 "BTCUSDT"
        :param meta: 额外元信息, 如 exchange_id="CFFEX", product_type="future"
        """
        info = SymbolInfo(internal_name, exchange, exchange_symbol, **meta)
        self._to_exchange.setdefault(internal_name, {})[exchange] = info
        self._from_exchange[(exchange, exchange_symbol)] = internal_name

    def to_exchange(self, internal_name, exchange):
        """内部名称 -> 交易所名称
        :param internal_name: 内部统一名称
        :param exchange: 交易所标识
        :return: 交易所原始品种名, 如果未注册则返回 internal_name 本身
        """
        mapping = self._to_exchange.get(internal_name, {})
        info = mapping.get(exchange)
        if info is not None:
            return info.exchange_symbol
        return internal_name

    def from_exchange(self, exchange_symbol, exchange):
        """交易所名称 -> 内部名称
        :param exchange_symbol: 交易所原始品种名
        :param exchange: 交易所标识
        :return: 内部统一名称, 如果未注册则返回 exchange_symbol 本身
        """
        return self._from_exchange.get((exchange, exchange_symbol), exchange_symbol)

    def get_symbol_info(self, internal_name, exchange):
        """获取品种元信息
        :param internal_name: 内部统一名称
        :param exchange: 交易所标识
        :return: SymbolInfo or None
        """
        return self._to_exchange.get(internal_name, {}).get(exchange)

    def list_symbols(self, exchange=None):
        """列出所有已注册品种
        :param exchange: 如果指定，只列出该交易所的品种
        :return: list of internal_name
        """
        if exchange is None:
            return list(self._to_exchange.keys())
        return [name for name, mapping in self._to_exchange.items() if exchange in mapping]

    def clear(self):
        """清空所有映射（主要用于测试）"""
        self._to_exchange.clear()
        self._from_exchange.clear()
