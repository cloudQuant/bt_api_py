# -*- coding: utf-8 -*-
import json
import time
from bt_api_py.feeds.live_binance.request_base import BinanceRequestData
from bt_api_py.feeds.live_binance.market_wss_base import BinanceMarketWssData
from bt_api_py.feeds.live_binance.account_wss_base import BinanceAccountWssData
from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataSpot
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.containers.orders.binance_order import (BinanceRequestOrderData,
                                                       BinanceSpotWssOrderData)
from bt_api_py.containers.accounts.binance_account import BinanceSpotWssAccountData
from bt_api_py.containers.trades.binance_trade import BinanceSpotWssTradeData


class BinanceRequestDataSpot(BinanceRequestData):
    def __init__(self, data_queue, **kwargs):
        super(BinanceRequestDataSpot, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "binance_spot_feed.log")
        self._params = BinanceExchangeDataSpot()
        self.request_logger = SpdLogManager("./logs/" + self.logger_name, "request",
                                            0, 0, False).create_logger()
        self.async_logger = SpdLogManager("./logs/" + self.logger_name, "async_request",
                                          0, 0, False).create_logger()

    def _make_order(self, symbol, vol, price=None, order_type='buy-limit',
                    offset='open', post_only=False, client_order_id=None,
                    extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        request_type = "make_order"
        path = self._params.get_rest_path(request_type)
        side, order_type = order_type.split('-')
        time_in_force = kwargs.get('time_in_force', "GTC")
        params = {
            'symbol': request_symbol,
            'side': side.upper(),
            'quantity': vol,
            'price': price,
            'type': order_type.upper(),
            'timeInForce': time_in_force,
        }
        if client_order_id is not None:
            params['newClientOrderId'] = client_order_id
        if order_type == 'market':
            params.pop("timeInForce", None)
            params.pop("price", None)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "post_only": post_only,
            "normalize_function": BinanceRequestDataSpot._make_order_normalize_function,
        })
        # if kwargs is not None:
        #     extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        status = True if input_data is not None else False
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]
        if isinstance(input_data, list):
            data = [BinanceRequestOrderData(i, symbol_name, asset_type, True)
                    for i in input_data]
        elif isinstance(input_data, dict):
            data = [BinanceRequestOrderData(input_data, symbol_name, asset_type, True)]
        else:
            data = []
        return data, status

    # noinspection PyBroadException
    def make_order(self, symbol, vol, price=None, order_type='buy-limit',
                   offset='open', post_only=False, client_order_id=None, extra_data=None, **kwargs):
        print("run spot make_order")
        path, params, extra_data = self._make_order(symbol, vol, price, order_type, offset,
                                                    post_only, client_order_id, extra_data,
                                                    **kwargs)
        # print("params = ", params)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data


class BinanceMarketWssDataSpot(BinanceMarketWssData):
    def __init__(self, data_queue, **kwargs):
        super(BinanceMarketWssDataSpot, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = BinanceExchangeDataSpot()


class BinanceAccountWssDataSpot(BinanceAccountWssData):
    def __init__(self, data_queue, **kwargs):
        kwargs.setdefault("exchange_data", BinanceExchangeDataSpot())
        super(BinanceAccountWssDataSpot, self).__init__(data_queue, **kwargs)
        self._params = BinanceExchangeDataSpot()

    def get_listen_key(self, max_retries=3):
        """Obtain listenKey via WebSocket API userDataStream.start.

        The old REST endpoint POST /api/v3/userDataStream is deprecated (410 Gone).
        The new approach connects to the WS API, sends userDataStream.start with
        the apiKey, and retrieves the listenKey from the response.
        """
        import ssl
        import threading
        import websocket as _ws

        result_holder = [None]
        error_holder = [None]
        done = threading.Event()

        def _on_open(ws):
            ws.send(json.dumps({
                "id": "start-listen-key",
                "method": "userDataStream.start",
                "params": {"apiKey": self.public_key}
            }))

        def _on_message(ws, message):
            rsp = json.loads(message)
            if rsp.get("status") == 200 and "listenKey" in rsp.get("result", {}):
                result_holder[0] = rsp["result"]
            else:
                error_holder[0] = rsp.get("error", rsp)
            done.set()
            ws.close()

        def _on_error(ws, error):
            error_holder[0] = str(error)
            done.set()

        last_err = None
        for attempt in range(max_retries):
            result_holder[0] = None
            error_holder[0] = None
            done.clear()
            try:
                tmp_ws = _ws.WebSocketApp(
                    self._params.acct_wss_url,
                    on_open=_on_open,
                    on_message=_on_message,
                    on_error=_on_error,
                )
                t = threading.Thread(
                    target=lambda: tmp_ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE}),
                    daemon=True,
                )
                t.start()
                done.wait(timeout=10)
                if result_holder[0] is not None:
                    return result_holder[0]
                last_err = error_holder[0]
                self.logger.warn(f"get_listen_key attempt {attempt + 1}/{max_retries} "
                                 f"unexpected response: {last_err}")
            except Exception as e:
                last_err = e
                self.logger.warn(f"get_listen_key attempt {attempt + 1}/{max_retries} error: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
        raise RuntimeError(f"Failed to get listen key after {max_retries} attempts: {last_err}")

    def wss_author(self):
        result = self.get_listen_key()
        self.listen_key = result["listenKey"]
        self.wss_url = f"{self._params.wss_url}"

    def open_rsp(self):
        self.wss_logger.info(
            f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} {self._params.exchange_name} Websocket Connected =====")
        subscribe_msg = json.dumps({
            "method": "SUBSCRIBE",
            "params": [self.listen_key],
            "id": 1
        })
        self.ws.send(subscribe_msg)

    def handle_data(self, content):
        event = content.get("e", None)
        if event is not None:
            # 现货账户事件类型
            if "executionReport" == event and content.get("x", None) != "TRADE":
                self.push_order(content)
            if "outboundAccountPosition" == event:
                self.push_account(content)
            if "executionReport" == event and content.get("x", None) == "TRADE":
                self.push_trade(content)
            # if "balanceUpdate" == event:
            #     self.push_balance(content)

    def push_account(self, content):
        # 推送account数据并添加到事件中
        # print("订阅到账户数据")
        symbol = "ALL"
        account_data = BinanceSpotWssAccountData(content, symbol, self.asset_type, True)
        self.data_queue.put(account_data)
        # print("获取account数据成功，当前账户净值为：", account_data.get_balances()[0].get_margin())

    def push_order(self, content):
        # print("订阅到order数据")
        symbol = content['s']
        order_data = BinanceSpotWssOrderData(content, symbol, self.asset_type, True)
        self.data_queue.put(order_data)
        # print("获取order成功，当前order_status 为：", order_data.get_order_status())

    def push_trade(self, content):
        # print("订阅到trade数据")
        symbol = content['s']
        trade_data = BinanceSpotWssTradeData(content, symbol, self.asset_type, True)
        self.data_queue.put(trade_data)
        # print("获取trade成功，当前trade_id 为：", trade_data.get_trade_id())
