import json
import time

from bt_api_py.containers.accounts.binance_account import BinanceSpotWssAccountData
from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataSpot
from bt_api_py.containers.orders.binance_order import (
    BinanceRequestOrderData,
    BinanceSpotWssOrderData,
)
from bt_api_py.containers.trades.binance_trade import BinanceSpotWssTradeData
from bt_api_py.feeds.live_binance.account_wss_base import BinanceAccountWssData
from bt_api_py.feeds.live_binance.market_wss_base import BinanceMarketWssData
from bt_api_py.feeds.live_binance.request_base import BinanceRequestData
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class BinanceRequestDataSpot(BinanceRequestData):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "binance_spot_feed.log")
        self._params = BinanceExchangeDataSpot()
        self.request_logger = get_logger("binance_spot_feed")
        self.async_logger = get_logger("binance_spot_feed")

    def _make_order(
        self,
        symbol,
        vol,
        price=None,
        order_type="buy-limit",
        offset="open",
        post_only=False,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        request_symbol = self._params.get_symbol(symbol)
        request_type = "make_order"
        path = self._params.get_rest_path(request_type)
        side, order_type = order_type.split("-")
        time_in_force = kwargs.get("time_in_force", "GTC")
        params = {
            "symbol": request_symbol,
            "side": side.upper(),
            "quantity": vol,
            "price": price,
            "type": order_type.upper(),
            "timeInForce": time_in_force,
        }
        if client_order_id is not None:
            params["newClientOrderId"] = client_order_id
        if order_type == "market":
            params.pop("timeInForce", None)
            params.pop("price", None)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "post_only": post_only,
                "normalize_function": BinanceRequestDataSpot._make_order_normalize_function,
            },
        )
        # if kwargs is not None:
        #     extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]
        if isinstance(input_data, list):
            data = [BinanceRequestOrderData(i, symbol_name, asset_type, True) for i in input_data]
        elif isinstance(input_data, dict):
            data = [BinanceRequestOrderData(input_data, symbol_name, asset_type, True)]
        else:
            data = []
        return data, status

    # ==================== 账户快照接口 ====================

    def _get_account_snapshot(self, account_type="SPOT", extra_data=None, **kwargs):
        """获取账户快照

        Args:
            account_type: 账户类型 (SPOT, MARGIN, FUTURES)
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_account_snapshot"
        path = self._params.get_rest_path(request_type)
        params = {
            "type": account_type,
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def get_account_snapshot(self, account_type="SPOT", extra_data=None, **kwargs):
        """获取账户快照

        Args:
            account_type: 账户类型 (SPOT, MARGIN, FUTURES)

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._get_account_snapshot(
            account_type=account_type, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    # ==================== 交易日统计数据接口 ====================

    def _get_ticker_trading_day(self, symbol=None, extra_data=None, **kwargs):
        """获取交易日统计数据

        Args:
            symbol: 交易对
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_ticker_trading_day"
        path = self._params.get_rest_path(request_type)
        params = {}
        if symbol is not None:
            request_symbol = self._params.get_symbol(symbol)
            params["symbol"] = request_symbol
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def get_ticker_trading_day(self, symbol=None, extra_data=None, **kwargs):
        """获取交易日统计数据

        Args:
            symbol: 交易对

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._get_ticker_trading_day(
            symbol=symbol, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=False)
        return data

    # ==================== 合约划转接口 ====================

    def _futures_transfer(self, asset, amount, transfer_type, extra_data=None, **kwargs):
        """合约账户划转

        Args:
            asset: 资产名称 (如 USDT)
            amount: 划转数量
            transfer_type: 划转类型 (1: 现货转U本位合约, 2: U本位合约转现货,
                                        3: 现货转币本位合约, 4: 币本位合约转现货)
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "futures_transfer"
        path = self._params.get_rest_path("transfer")
        params = {
            "asset": asset,
            "amount": amount,
            "type": transfer_type,
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": asset,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def futures_transfer(self, asset, amount, transfer_type, extra_data=None, **kwargs):
        """合约账户划转

        Args:
            asset: 资产名称 (如 USDT)
            amount: 划转数量
            transfer_type: 划转类型 (1: 现货转U本位合约, 2: U本位合约转现货,
                                        3: 现货转币本位合约, 4: 币本位合约转现货)

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._futures_transfer(
            asset=asset, amount=amount, transfer_type=transfer_type, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_futures_transfer_history(
        self,
        asset=None,
        start_time=None,
        end_time=None,
        limit=None,
        page=None,
        extra_data=None,
        **kwargs,
    ):
        """查询合约划转历史

        Args:
            asset: 资产名称
            start_time: 开始时间戳
            end_time: 结束时间戳
            limit: 每页数量 (默认100, 最大1000)
            page: 页码 (默认1)
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_futures_transfer_history"
        path = self._params.get_rest_path(request_type)
        params = {}
        if asset is not None:
            params["asset"] = asset
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        if limit is not None:
            params["limit"] = limit
        if page is not None:
            params["page"] = page
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": asset or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def get_futures_transfer_history(
        self,
        asset=None,
        start_time=None,
        end_time=None,
        limit=None,
        page=None,
        extra_data=None,
        **kwargs,
    ):
        """查询合约划转历史

        Args:
            asset: 资产名称
            start_time: 开始时间戳
            end_time: 结束时间戳
            limit: 每页数量 (默认100, 最大1000)
            page: 页码 (默认1)

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._get_futures_transfer_history(
            asset=asset,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            page=page,
            extra_data=extra_data,
            **kwargs,
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    # noinspection PyBroadException
    def make_order(
        self,
        symbol,
        vol,
        price=None,
        order_type="buy-limit",
        offset="open",
        post_only=False,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        print("run spot make_order")
        path, params, extra_data = self._make_order(
            symbol, vol, price, order_type, offset, post_only, client_order_id, extra_data, **kwargs
        )
        # print("params = ", params)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    # ==================== 高级订单接口 ====================

    def _cancel_replace_order(
        self,
        symbol,
        cancel_order_id=None,
        cancel_client_order_id=None,
        side=None,
        order_type=None,
        quantity=None,
        price=None,
        stop_price=None,
        new_client_order_id=None,
        cancel_restrictions=None,
        extra_data=None,
        **kwargs,
    ):
        """取消并替换订单 (Cancel Replace Order)

        Args:
            symbol: 交易对
            cancel_order_id: 要取消的订单ID
            cancel_client_order_id: 要取消的客户端订单ID (与cancel_order_id二选一)
            side: 订单方向 (BUY/SELL)
            order_type: 订单类型 (LIMIT, MARKET, STOP_LOSS, STOP_LOSS_LIMIT, TAKE_PROFIT, TAKE_PROFIT_LIMIT)
            quantity: 订单数量
            price: 订单价格 (LIMIT订单必需)
            stop_price: 触发价格 (止损/止盈订单必需)
            new_client_order_id: 新订单的客户端ID
            cancel_restrictions: 取消限制 (ONLY_NEW, ONLY_PARTIALLY_FILLED)
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "cancel_replace_order"
        request_symbol = self._params.get_symbol(symbol)
        path = self._params.get_rest_path(request_type)
        params = {"symbol": request_symbol}

        if cancel_order_id is not None:
            params["cancelOrderId"] = cancel_order_id
        if cancel_client_order_id is not None:
            params["cancelOrigClientOrderId"] = cancel_client_order_id
        if side is not None:
            params["side"] = side.upper()
        if order_type is not None:
            params["type"] = order_type.upper()
        if quantity is not None:
            params["quantity"] = quantity
        if price is not None:
            params["price"] = price
        if stop_price is not None:
            params["stopPrice"] = stop_price
        if new_client_order_id is not None:
            params["newClientOrderId"] = new_client_order_id
        if cancel_restrictions is not None:
            params["cancelRestrictions"] = cancel_restrictions

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": BinanceRequestDataSpot._make_order_normalize_function,
            },
        )
        return path, params, extra_data

    def cancel_replace_order(
        self,
        symbol,
        cancel_order_id=None,
        cancel_client_order_id=None,
        side=None,
        order_type=None,
        quantity=None,
        price=None,
        stop_price=None,
        new_client_order_id=None,
        cancel_restrictions=None,
        extra_data=None,
        **kwargs,
    ):
        """取消并替换订单

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._cancel_replace_order(
            symbol=symbol,
            cancel_order_id=cancel_order_id,
            cancel_client_order_id=cancel_client_order_id,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            new_client_order_id=new_client_order_id,
            cancel_restrictions=cancel_restrictions,
            extra_data=extra_data,
            **kwargs,
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _cancel_all_orders(self, symbol=None, extra_data=None, **kwargs):
        """取消所有订单

        Args:
            symbol: 交易对，如果不指定则取消所有现货订单
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "cancel_all"
        path = self._params.get_rest_path(request_type)
        params = {}
        if symbol is not None:
            request_symbol = self._params.get_symbol(symbol)
            params["symbol"] = request_symbol

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def cancel_all_orders(self, symbol=None, extra_data=None, **kwargs):
        """取消所有订单

        Args:
            symbol: 交易对，如果不指定则取消所有现货订单

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._cancel_all_orders(
            symbol=symbol, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _amend_keep_priority(
        self,
        symbol,
        order_id=None,
        client_order_id=None,
        quantity=None,
        price=None,
        extra_data=None,
        **kwargs,
    ):
        """修改订单并保持队列优先级

        Args:
            symbol: 交易对
            order_id: 订单ID (与client_order_id二选一)
            client_order_id: 客户端订单ID
            quantity: 新数量
            price: 新价格
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "amend_keep_priority"
        request_symbol = self._params.get_symbol(symbol)
        path = self._params.get_rest_path(request_type)
        params = {"symbol": request_symbol}

        if order_id is not None:
            params["orderId"] = order_id
        if client_order_id is not None:
            params["origClientOrderId"] = client_order_id
        if quantity is not None:
            params["quantity"] = quantity
        if price is not None:
            params["price"] = price

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": BinanceRequestDataSpot._make_order_normalize_function,
            },
        )
        return path, params, extra_data

    def amend_keep_priority(
        self,
        symbol,
        order_id=None,
        client_order_id=None,
        quantity=None,
        price=None,
        extra_data=None,
        **kwargs,
    ):
        """修改订单并保持队列优先级

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._amend_keep_priority(
            symbol=symbol,
            order_id=order_id,
            client_order_id=client_order_id,
            quantity=quantity,
            price=price,
            extra_data=extra_data,
            **kwargs,
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data


class BinanceMarketWssDataSpot(BinanceMarketWssData):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = BinanceExchangeDataSpot()


class BinanceAccountWssDataSpot(BinanceAccountWssData):
    def __init__(self, data_queue, **kwargs):
        kwargs.setdefault("exchange_data", BinanceExchangeDataSpot())
        super().__init__(data_queue, **kwargs)
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
            ws.send(
                json.dumps(
                    {
                        "id": "start-listen-key",
                        "method": "userDataStream.start",
                        "params": {"apiKey": self.public_key},
                    }
                )
            )

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
                self.logger.warn(
                    f"get_listen_key attempt {attempt + 1}/{max_retries} "
                    f"unexpected response: {last_err}"
                )
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
            f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} {self._params.exchange_name} Websocket Connected ====="
        )
        subscribe_msg = json.dumps({"method": "SUBSCRIBE", "params": [self.listen_key], "id": 1})
        self.ws.send(subscribe_msg)

    def handle_data(self, content):
        event = content.get("e", None)
        if event is not None:
            # 现货账户事件类型
            if event == "executionReport" and content.get("x", None) != "TRADE":
                self.push_order(content)
            if event == "outboundAccountPosition":
                self.push_account(content)
            if event == "executionReport" and content.get("x", None) == "TRADE":
                self.push_trade(content)
            # 余额更新事件 (分红等)
            if event == "balanceUpdate":
                self.push_balance(content)

    def push_account(self, content):
        # 推送account数据并添加到事件中
        # print("订阅到账户数据")
        symbol = "ALL"
        account_data = BinanceSpotWssAccountData(content, symbol, self.asset_type, True)
        self.data_queue.put(account_data)
        # print("获取account数据成功，当前账户净值为：", account_data.get_balances()[0].get_margin())

    def push_order(self, content):
        # print("订阅到order数据")
        symbol = content["s"]
        order_data = BinanceSpotWssOrderData(content, symbol, self.asset_type, True)
        self.data_queue.put(order_data)
        # print("获取order成功，当前order_status 为：", order_data.get_order_status())

    def push_trade(self, content):
        # print("订阅到trade数据")
        symbol = content["s"]
        trade_data = BinanceSpotWssTradeData(content, symbol, self.asset_type, True)
        self.data_queue.put(trade_data)
        # print("获取trade成功，当前trade_id 为：", trade_data.get_trade_id())

    def push_balance(self, content):
        """推送余额更新数据 (分红等)

        balanceUpdate 事件结构:
        {
            "e": "balanceUpdate",
            "E": 1573200697114,
            "s": "BTC",
            "u": "15896533547050558808",
            "B": "500.00000000"
        }
        """
        symbol = content.get("s", "ALL")
        balance_data = BinanceSpotWssAccountData(content, symbol, self.asset_type, True)
        self.data_queue.put(balance_data)
