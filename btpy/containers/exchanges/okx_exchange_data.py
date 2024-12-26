import json
import time
import copy
import datetime
from btpy.containers.exchanges.exchange_data import ExchangeData


class OkxExchangeData(ExchangeData):
    def __init__(self):
        """这个类存放一些交易所用到的参数
        """
        super().__init__()
        self.exchange_name = 'OkxSwap'
        self.rest_url = 'https://www.okx.com'
        self.account_wss_url = 'wss://ws.okx.com:8443/ws/v5/private'
        self.wss_url = 'wss://ws.okx.com:8443/ws/v5/public'
        self.kline_wss_url = 'wss://ws.okx.com:8443/ws/v5/business'
        self.symbol_leverage_dict = {'BTC-USDT': 100,
                                     'ETH-USDT': 10,
                                     'BCH-USDT': 10,
                                     'DOGE-USDT': 0.001,
                                     "BNB-USDT": 10,
                                     "OP-USDT": 1, }

        self.rest_paths = {
            'get_account': 'GET /api/v5/account/balance',
            'get_balance': 'GET /api/futures/v3/accounts/<underlying>',
            # 'getBalance_assert': 'GET /api/v5/users/subaccount/list',
            'get_balance_assert': 'GET /api/v5/asset/balances',
            'get_position': 'GET /api/v5/account/positions',
            'get_positions': 'GET /api/v5/account/positions',
            'get_fee': 'GET /api/v5/account/trade-fee',
            'get_tick': 'GET /api/v5/market/ticker',
            'get_depth': 'GET /api/v5/market/books',
            'get_kline_his': 'GET /api/v5/market/history-candles',
            'get_kline': 'GET /api/v5/market/candles',
            'get_funding_rate': 'GET /api/v5/public/funding-rate',
            'get_mark_price': 'GET /api/v5/public/mark-price',
            "set_lever": "POST /api/v5/account/set-leverage",
            'make_order': 'POST /api/v5/trade/order',  # 限速： 60次 / 2s 限速规则：衍生品：UserID + (instrument_type、underlying)
            'make_orders': 'POST /api/v5/trade/batch-orders',
            'cancel_order': 'POST /api/v5/trade/cancel-order',
            'cancel_orders': 'POST /api/v5/trade/cancel-batch-orders',
            'cancel_all': 'POST /api/v5/trade/cancel-batch-orders',
            'query_order': 'GET /api/v5/trade/order',
            'get_open_orders': 'GET /api/v5/trade/orders-pending',
            'get_deals': 'GET /api/v5/trade/fills-history',
            "sub_account": "GET /api/v5/account/subaccount/balances",
            "transfer": "POST /api/v5/asset/transfer",
            "set_mode": "POST /api/v5/account/set-position-mode",
            "get_config": "GET /api/v5/account/config",
            "get_index_price": "GET /api/v5/market/index-tickers",
        }

        self.wss_paths = {
            'tick': {'args': [{"channel": "tickers", "instType": "SWAP", "instId": '<symbol>'}], 'op': 'subscribe'},
            'depth': {'args': [{"channel": "books5", "instType": "SWAP", "instId": '<symbol>'}], 'op': 'subscribe'},
            'books': {'args': [{"channel": "books", "instType": "SWAP", "instId": '<symbol>'}], 'op': 'subscribe'},
            "bidAsk": {'args': [{"channel": "bbo-tbt", "instType": "SWAP", "instId": '<symbol>'}], 'op': 'subscribe'},
            'increDepthFlow': {'args': [{"channel": "books50-l2-tbt", "instId": '<symbol>'}], 'op': 'subscribe'},
            'funding_rate': {'args': [{"channel": "funding-rate", "instId": '<symbol>'}], 'op': 'subscribe'},
            'mark_price': {'args': [{"channel": "mark-price", "instId": '<symbol>'}], 'op': 'subscribe'},
            # 'increDepth': {'args': ['futures/depth:<symbol>'], 'op': 'subscribe'},
            # 'increDepthFlow': {'args': ['futures/depth_l2_tbt:<symbol>'], 'op': 'subscribe'},
            # 'kline': {'args': ['futures/candle<period>s:<symbol>'], 'op': 'subscribe'},
            'orders': {"args": [{"channel": "orders", "instType": "SWAP", "instId": '<symbol>'}], "op": "subscribe"},
            'account': {"args": [{"channel": "account", "instType": "SWAP", "instId": '<symbol>'}], "op": "subscribe"},
            # 'positions': {"args": [{"channel": "positions", "instType": "SWAP", "instId": '<symbol>'}],
            #               "op": "subscribe"},
            'positions': {"args": [{"channel": "positions", "instType": "SWAP", "instId": '<symbol>'}],
                          "op": "subscribe"},
            'balance_position': {"args": [{"channel": "balance_and_position"}], "op": "subscribe"},
            'kline': {'args': [{"channel": "candle<period>", "instId": '<symbol>'}], 'op': 'subscribe'},
        }

        self.kline_periods = {
            '1m': '1m',
            '3m': '3m',
            '5m': '5m',
            '15m': '15m',
            '30m': '30m',
            '1h': '1H',
            '2h': '2H',
            '4h': '4H',
            '6h': '6H',
            '12h': '12H',
            '1d': '1D',
            '1w': '1W',
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}
        self.status_dict = {
            'live': 'submit',
            'partially_filled': 'partial-filled',
            'filled': 'filled',
            "canceled": 'cancel',
            # 'PARTIALLY_FILLED': 'partial-filled',
            # 'FILLED': 'filled',
            # 'CANCELED': 'cancel',
            # 'REJECTED': 'rejected',
            # 'EXPIRED': 'expired',
        }

    # noinspection PyMethodMayBeStatic
    def get_symbol(self, symbol):
        return symbol.replace('/', '-').upper() + "-SWAP"

    # noinspection PyMethodMayBeStatic
    def get_symbol_re(self, symbol):
        return symbol.replace('-', '/').lower().rsplit("/", 1)[0]

    # noinspection PyMethodMayBeStatic
    def get_period(self, key):
        if key not in self.kline_periods:
            return key
        return self.kline_periods[key]

    def get_rest_path(self, key):
        if key not in self.rest_paths or self.rest_paths[key] == '':
            self.raise_path_error(self.exchange_name, key)
        return self.rest_paths[key]

    # noinspection PyMethodMayBeStatic
    def str2int(self, time_str):
        dt = datetime.datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S.%fZ")
        timestamp = int((time.mktime(dt.timetuple()) + dt.microsecond / 1000000) * 1000)
        return timestamp

    def get_wss_path(self, **kwargs):
        """拿wss订阅字段
        Returns:
            TYPE: Description
        """
        key = kwargs['topic']
        if key == "mark_price" or key == "positions":
            if 'symbol' in kwargs:
                kwargs['symbol'] = kwargs['symbol']
        else:
            if 'symbol' in kwargs:
                kwargs['symbol'] = self.get_symbol(kwargs['symbol'])
        if 'period' in kwargs:
            kwargs['period'] = self.get_period(kwargs['period'])
        if key not in self.wss_paths or self.wss_paths[key] == '':
            self.raise_path_error(self.exchange_name, key)
        # print("kwargs", kwargs)
        req = copy.deepcopy(self.wss_paths[key])
        for k, v in req["args"][0].items():
            symbol = kwargs.get('symbol', '')
            # print("symbol", symbol, "k = ", k, "v = ", v)
            req["args"][0][k] = req["args"][0][k].replace("<symbol>", symbol)
            if "USDT" in symbol:
                currency = symbol.split("-")[1]  # self.symbol.split("-")[0] + "-" +
            else:
                currency = symbol.split("-")[0]
            req["args"][0][k] = req["args"][0][k].replace("<currency>", currency)
            req["args"][0][k] = req["args"][0][k].replace("<period>", kwargs.get('period', ''))
        req = json.dumps(req)
        # print("req_1", req)
        return req


class OkxExchangeDataSwap(OkxExchangeData):
    pass


class OkxExchangeDataSpot(OkxExchangeData):
    def __init__(self):
        super(OkxExchangeDataSpot, self).__init__()
        self.exchange_name = 'OkxSpot'
        self.rest_paths = {
            "get_instruments": "GET /api/v5/public/instruments",
            'get_account': 'GET /api/v5/account/balance',
            'get_balance': 'GET /api/futures/v3/accounts/<underlying>',
            'get_position': 'GET /api/v5/account/positions',
            'get_positions': 'GET /api/v5/account/positions',
            'get_fee': 'GET /api/v5/account/trade-fee',
            'get_tick': 'GET /api/v5/market/ticker',
            'get_depth': 'GET /api/v5/market/books',
            'get_kline': 'GET /api/v5/market/history-candles',
            # 'getFundingRate': 'GET /swap-api/v1/swap_funding_rate',
            # 'get_funding_rate': 'GET /api/v5/public/funding-rate',
            'get_mark_price': 'GET /api/v5/public/mark-price',
            'make_order': 'POST /api/v5/trade/order',
            "set_leverage": "POST /api/v5/account/set-leverage",
            # 限速：60次 / 2s 限速规则：衍生品：UserID + (instrument_type、underlying)
            'make_orders': 'POST /api/v5/trade/batch-orders',
            'cancel_order': 'POST /api/v5/trade/cancel-order',
            'cancel_all': 'POST /api/v5/trade/cancel-batch-orders',
            'query_order': 'GET /api/v5/trade/order',
            'get_open_orders': 'GET /api/v5/trade/orders-pending',
            'get_deals': 'GET /api/v5/trade/fills-history',
            "sub_account": "GET /api/v5/account/subaccount/balances",
            "transfer": "POST /api/v5/asset/transfer",
            "get_interest_rate": "GET /api/v5/account/interest-rate",
            "get_index_price": "GET /api/v5/market/index-tickers",
            "get_config": "GET /api/v5/account/config",
        }

        self.wss_paths = {
            'tick': {'args': [{"channel": "tickers", "instId": '<symbol>'}], 'op': 'subscribe'},
            'depth': {'args': [{"channel": "books5", "instId": '<symbol>'}], 'op': 'subscribe'},

            'increDepthFlow': {'args': [{"channel": "books50-l2-tbt", "instId": '<symbol>'}], 'op': 'subscribe'},
            # 'increDepth': {'args': ['futures/depth:<symbol>'], 'op': 'subscribe'},
            # 'increDepthFlow': {'args': ['futures/depth_l2_tbt:<symbol>'], 'op': 'subscribe'},
            # 'kline': {'args': ['futures/candle<period>s:<symbol>'], 'op': 'subscribe'},
            'mark_price': {'args': [{"channel": "mark-price", "instId": '<symbol>'}], 'op': 'subscribe'},
            'orders': {"args": [{"channel": "orders", "instType": "MARGIN", "instId": '<symbol>'}], "op": "subscribe"},
            'balance': {"args": [{"channel": "account", "ccy": '<currency>'}], "op": "subscribe"},
            'account': {"args": [{"channel": "account", "instType": "SPOT", "instId": '<symbol>'}], "op": "subscribe"},
            # 'positions': {"args": [{"channel": "positions", "instType": "SWAP", "instId": '<symbol>'}],
            #               "op": "subscribe"},
            'positions': {"args": [{"channel": "positions", "instType": "SPOT", "instId": '<symbol>'}],
                          "op": "subscribe"},
            'position': {"args": [{"channel": "positions", "instType": "SPOT", "instId": '<symbol>'}],
                         "op": "subscribe"},
            'balance_position': {"args": [{"channel": "balance_and_position"}], "op": "subscribe"},
            'kline': {'args': [{"channel": "candle<period>", "instId": '<symbol>'}], 'op': 'subscribe'},
        }

    def get_symbol(self, symbol):
        return symbol.replace('/', '-').upper()

    # noinspection PyMethodMayBeStatic
    def get_symbol_re(self, symbol):
        return symbol.replace('-', '/').lower()

    def get_wss_path(self, **kwargs):
        """拿wss订阅字段
        Returns:
            TYPE: Description
        """
        key = kwargs['topic']
        if 'symbol' in kwargs:
            kwargs['symbol'] = self.get_symbol(kwargs['symbol'])
        if 'period' in kwargs:
            kwargs['period'] = self.get_period(kwargs['period'])

        if key not in self.wss_paths or self.wss_paths[key] == '':
            self.raise_path_error(self.exchange_name, key)
        req = copy.deepcopy(self.wss_paths[key])
        for k, v in req["args"][0].items():
            symbol = kwargs.get('symbol', '')
            req["args"][0][k] = req["args"][0][k].replace("<symbol>", symbol)
            req["args"][0][k] = req["args"][0][k].replace("<currency>", symbol.split("-")[0])
            req["args"][0][k] = req["args"][0][k].replace("<period>", kwargs.get('period', ''))
        return json.dumps(req)
