"""
Interactive Brokers 交易所配置数据
包含 IB TWS/Gateway 连接参数、合约类型映射等
"""
from bt_api_py.containers.exchanges.exchange_data import ExchangeData


class IbExchangeData(ExchangeData):
    """IB 交易所配置基类"""

    def __init__(self):
        super().__init__()
        self.exchange_name = 'IB'
        # IB 使用 TWS API，不走 REST/WebSocket
        self.rest_url = ''
        self.wss_url = ''
        # TWS/Gateway 连接参数（由用户配置传入）
        self.host = '127.0.0.1'
        self.port = 7497        # TWS=7497, Gateway=4001
        self.client_id = 1

        self.kline_periods = {
            '1m': '1 min',
            '5m': '5 mins',
            '15m': '15 mins',
            '30m': '30 mins',
            '1h': '1 hour',
            '1d': '1 day',
            '1w': '1 week',
            '1M': '1 month',
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        # IB 合约类型映射
        self.sec_type_map = {
            'STK': 'Stock',          # 股票
            'FUT': 'Future',         # 期货
            'OPT': 'Option',         # 期权
            'FOP': 'FutureOption',   # 期货期权
            'CASH': 'Forex',         # 外汇
            'CFD': 'CFD',            # 差价合约
            'BOND': 'Bond',          # 债券
            'CRYPTO': 'Crypto',      # 加密货币
        }

        # IB 常用交易所
        self.ib_exchanges = [
            'SMART',     # IB 智能路由
            'NYSE',      # 纽约证券交易所
            'NASDAQ',    # 纳斯达克
            'ARCA',      # NYSE Arca
            'GLOBEX',    # CME Globex (期货)
            'ECBOT',     # CBOT 电子交易
            'NYMEX',     # 纽约商品交易所
            'SEHK',      # 香港交易所
            'SEHKNTL',   # 港股通
            'IDEALPRO',  # 外汇
        ]

    def get_symbol(self, symbol):
        """IB 品种名称直接使用"""
        return symbol

    def get_rest_path(self, key):
        raise NotImplementedError("IB does not use REST API, use TWS API instead")

    def get_wss_path(self, **kwargs):
        raise NotImplementedError("IB does not use WebSocket, use TWS API instead")


class IbExchangeDataStock(IbExchangeData):
    """IB 股票配置"""

    def __init__(self):
        super().__init__()
        self.exchange_name = 'IB_STK'


class IbExchangeDataFuture(IbExchangeData):
    """IB 期货配置"""

    def __init__(self):
        super().__init__()
        self.exchange_name = 'IB_FUT'


class IbExchangeDataOption(IbExchangeData):
    """IB 期权配置"""

    def __init__(self):
        super().__init__()
        self.exchange_name = 'IB_OPT'


class IbExchangeDataForex(IbExchangeData):
    """IB 外汇配置"""

    def __init__(self):
        super().__init__()
        self.exchange_name = 'IB_CASH'
