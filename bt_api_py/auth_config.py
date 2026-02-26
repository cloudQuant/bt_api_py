"""
认证配置 — 统一管理不同交易所的认证方式
加密货币交易所使用 API Key，CTP 使用 Broker/User/Password，IB 使用 TWS 连接参数
"""


class AuthConfig:
    """认证配置基类"""

    def __init__(self, exchange, asset_type="SWAP", **kwargs):
        self.exchange = exchange
        self.asset_type = asset_type

    def get_exchange_name(self):
        """返回交易所标识，如 'BINANCE___SWAP'"""
        return f"{self.exchange}___{self.asset_type}"

    def to_dict(self):
        """转为字典，用于传递给 feed 构造函数"""
        return {k: v for k, v in self.__dict__.items() if not k.startswith('_')}


class CryptoAuthConfig(AuthConfig):
    """加密货币交易所认证配置（Binance, OKX 等）"""

    def __init__(self, exchange, asset_type="SWAP",
                 public_key=None, private_key=None, passphrase=None, **kwargs):
        super().__init__(exchange, asset_type, **kwargs)
        self.public_key = public_key
        self.private_key = private_key
        self.passphrase = passphrase  # OKX 需要


class CtpAuthConfig(AuthConfig):
    """CTP 认证配置"""

    def __init__(self, exchange="CTP", asset_type="FUTURE",
                 broker_id="", user_id="", password="",
                 auth_code="", app_id="",
                 md_front="", td_front="",
                 product_info="", **kwargs):
        super().__init__(exchange, asset_type, **kwargs)
        self.broker_id = broker_id
        self.user_id = user_id
        self.password = password
        self.auth_code = auth_code
        self.app_id = app_id
        self.md_front = md_front      # 行情前置地址, 如 "tcp://180.168.146.187:10131"
        self.td_front = td_front      # 交易前置地址, 如 "tcp://180.168.146.187:10130"
        self.product_info = product_info


class IbAuthConfig(AuthConfig):
    """Interactive Brokers 认证配置"""

    def __init__(self, exchange="IB", asset_type="STK",
                 host="127.0.0.1", port=7497, client_id=1, **kwargs):
        super().__init__(exchange, asset_type, **kwargs)
        self.host = host
        self.port = port              # TWS=7497, Gateway=4001
        self.client_id = client_id
