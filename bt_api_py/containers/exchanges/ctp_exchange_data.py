"""
CTP 交易所配置数据
包含 CTP 行情和交易的前置地址、REST path 映射、品种信息等
"""
from bt_api_py.containers.exchanges.exchange_data import ExchangeData


class CtpExchangeData(ExchangeData):
    """CTP 交易所配置基类"""

    def __init__(self):
        super().__init__()
        self.exchange_name = 'CTP'
        # CTP 没有 REST API，使用 SPI 回调模式
        self.rest_url = ''
        self.wss_url = ''
        # CTP 前置地址（由用户配置传入）
        self.md_front = ''     # 行情前置, 如 "tcp://180.168.146.187:10131"
        self.td_front = ''     # 交易前置, 如 "tcp://180.168.146.187:10130"

        self.kline_periods = {
            '1m': '1',
            '5m': '5',
            '15m': '15',
            '30m': '30',
            '1h': '60',
            '1d': 'D',
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        # CTP 交易所代码映射
        self.exchange_id_map = {
            'CFFEX': '中金所',    # 中国金融期货交易所 (股指期货、国债期货)
            'SHFE': '上期所',     # 上海期货交易所
            'DCE': '大商所',      # 大连商品交易所
            'CZCE': '郑商所',     # 郑州商品交易所
            'INE': '能源中心',    # 上海国际能源交易中心
            'GFEX': '广期所',     # 广州期货交易所
        }

    def get_symbol(self, symbol):
        """CTP 品种名称直接使用, 如 'IF2506', 'rb2510'"""
        return symbol

    def get_rest_path(self, key):
        raise NotImplementedError("CTP does not use REST API, use SPI callback instead")

    def get_wss_path(self, **kwargs):
        raise NotImplementedError("CTP does not use WebSocket, use SPI callback instead")


class CtpExchangeDataFuture(CtpExchangeData):
    """CTP 期货配置"""

    def __init__(self):
        super().__init__()
        self.exchange_name = 'CTP_FUTURE'


class CtpExchangeDataOption(CtpExchangeData):
    """CTP 期权配置"""

    def __init__(self):
        super().__init__()
        self.exchange_name = 'CTP_OPTION'
