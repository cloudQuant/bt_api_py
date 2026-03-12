import time

from bt_api_py.containers.trades.trade import TradeData


class KorbitSpotWssTradeData(TradeData):
    """保存 Korbit WebSocket 成交信息."""

    def __init__(
        self, trade_info, symbol_name, asset_type, has_been_json_encoded: bool = False
    ) -> None:
        super().__init__(
            trade_info, has_been_json_encoded, symbol_name=symbol_name, asset_type=asset_type
        )
        self.exchange_name = "KORBIT"
        self.local_update_time = time.time()
