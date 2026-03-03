import json
import time

from bt_api_py.containers.trades.trade import TradeData


class LocalBitcoinsSpotWssTradeData(TradeData):
    """保存 LocalBitcoins WebSocket 成交信息"""

    def __init__(self, trade_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(trade_info, symbol_name, asset_type, has_been_json_encoded)
        self.exchange_name = "LOCALBITCOINS"
        self.local_update_time = time.time()
