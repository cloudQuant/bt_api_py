import json
from enum import Enum
from bt_api_py.containers.exchanges.exchange_data import ExchangeData


class BinanceOrderStatus(Enum):
    NEW = 'submit'
    PARTIALLY_FILLED = 'partial_filled'
    FILLED = 'filled'
    CANCELED = 'cancel'
    REJECTED = 'rejected'
    EXPIRED = 'expired'


class NormalizedBinanceOrderStatus(Enum):
    submit = "NEW"
    partial_filled = "PARTIALLY_FILLED"
    filled = "FILLED"
    canceled = "CANCELED"
    rejected = "REJECTED"
    expired = "EXPIRED"


class BinanceExchangeData(ExchangeData):
    def __init__(self):
        super(BinanceExchangeData, self).__init__()
        self.exchange_name = 'binance_swap'

        self.rest_url = 'https://fapi.binance.com'
        self.acct_wss_url = 'wss://fstream.binance.com/ws'
        self.wss_url = 'wss://fstream.binance.com/ws'

        self.rest_paths = {
            'get_contract': 'GET /fapi/v1/exchangeInfo',
            'get_account': 'GET /fapi/v2/account',
            'get_balance': 'GET /fapi/v2/balance',
            'get_position': 'GET /fapi/v2/positionRisk',
            'get_fee': 'GET /fapi/v1/commissionRate',
            'get_tick': 'GET /fapi/v1/ticker/bookTicker',
            'get_info': 'GET /fapi/v1/ticker/24hr',
            'get_new_price': 'GET /fapi/v1/trades',
            'get_depth': 'GET /fapi/v1/depth',
            'get_kline': 'GET /fapi/v1/klines',
            'get_funding_rate': 'GET /fapi/v1/premiumIndex',
            'get_clear_price': 'GET /fapi/v1/premiumIndex',
            'get_mark_price': 'GET /fapi/v1/premiumIndex',
            'make_order': 'POST /fapi/v1/order',
            'make_orders': 'POST /fapi/v1/batchOrders',
            'cancel_order': 'DELETE /fapi/v1/order',
            'cancel_all': 'DELETE /fapi/v1/allOpenOrders',
            'query_order': 'GET /fapi/v1/order',
            'get_open_orders': 'GET /fapi/v1/openOrders',
            'get_deals': 'GET /fapi/v1/userTrades',
            'get_force_orders': 'GET /fapi/v1/forceOrders',
            'get_incre_depth': 'GET /fapi/v1/depth',
            'get_listen_key': 'POST /fapi/v1/listenKey',
            'refresh_listen_key': 'PUT /fapi/v1/listenKey',
            'update_leverage': 'POST /fapi/v1/leverage',
            'get_market_rate': 'GET /fapi/v1/premiumIndex',
            'get_his_trans': "POST /fapi/v1/positionSide/dual",
            'set_lever': 'POST /fapi/v1/leverage',
            'get_income': 'GET /fapi/v1/income',
            "get_server_time": 'GET /fapi/v1/time',
        }

        self.wss_paths = {
            'all_force_order': {'params': ['!forceOrder@arr'], 'method': 'SUBSCRIBE', 'id': 1},  # 全市场强平订单
            'all_ticker': {'params': ['!ticker@arr'], 'method': 'SUBSCRIBE', 'id': 1},  # 全市场的精简ticker
            'all_mark_price': {'params': ['!markPrice@arr@1s'], 'method': 'SUBSCRIBE', 'id': 1},  # 全市场的mark_price
            'force_order': {'params': ['<symbol>@forceOrder'], 'method': 'SUBSCRIBE', 'id': 1},
            'agg_trade': {'params': ['<symbol>@aggTrade'], 'method': 'SUBSCRIBE', 'id': 1},
            'tick': {'params': ['<symbol>@bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            'tick_all': {'params': ['!bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            'depth': {'params': ['<symbol>@depth20@100ms'], 'method': 'SUBSCRIBE', 'id': 1},
            'depth500': {'params': ['<symbol>@depth5@500ms'], 'method': 'SUBSCRIBE', 'id': 1},
            'increDepthFlow': {'params': ['<symbol>@depth@100ms'], 'method': 'SUBSCRIBE', 'id': 1},
            'kline': {'params': ['<symbol>@kline_<period>'], 'method': 'SUBSCRIBE', 'id': 1},
            'clearPrice': {'params': ['<symbol>@markPrice@1s'], 'method': 'SUBSCRIBE', 'id': 1},
            'bidAsk': {'params': ['<symbol>@bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            "funding_rate": {"params": ["<symbol>@markPrice@1s"], 'method': 'SUBSCRIBE', 'id': 1},
            "mark_price": {"params": ["<symbol>@markPrice@1s"], 'method': 'SUBSCRIBE', 'id': 1},
            'tickers': {'params': ['!ticker@arr'], 'method': 'SUBSCRIBE', 'id': 1},
            'orders': '',
            'deals': '',
            'balance': '',
            'position': '',

        }
        self.symbol_leverage_dict = {'BTC-USDT': 100,
                                     'ETH-USDT': 10,
                                     'BCH-USDT': 10,
                                     'DOGE-USDT': 0.001,
                                     "BNB-USDT": 10,
                                     "OP-USDT": 1, }

        self.kline_periods = {
            '1m': '1m',
            '3m': '3m',
            '5m': '5m',
            '15m': '15m',
            '30m': '30m',
            '1h': '1h',
            '2h': '2h',
            '4h': '4h',
            '6h': '6h',
            '8h': '8h',
            '12h': '12h',
            '1d': '1d',
            '3d': '3d',
            '1w': '1w',
            '1M': '1M',
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        # self.order_status = {
        #     'NEW': 'submit',
        #     'PARTIALLY_FILLED': 'partial-filled',
        #     'FILLED': 'filled',
        #     'CANCELED': 'cancel',
        #     'REJECTED': 'rejected',
        #     'EXPIRED': 'expired',
        # }

        self.order_status = BinanceOrderStatus
        self.normalized_order_status = NormalizedBinanceOrderStatus

        self.legal_currency = [
            'USDT', 'USD', 'BTC', 'ETH',
        ]

    # noinspection PyMethodMayBeStatic
    def get_symbol(self, symbol):
        return symbol.replace("-", "")

    def account_wss_symbol(self, symbol):
        for lc in self.legal_currency:
            if lc in symbol:
                symbol = f"{symbol.split(lc)[0]}/{lc}".lower()
                break
        return symbol

    # noinspection PyMethodMayBeStatic
    def get_period(self, key):
        return key

    def get_rest_path(self, key):
        if key not in self.rest_paths or self.rest_paths[key] == '':
            self.raise_path_error(self.exchange_name, key)
        return self.rest_paths[key]

    def get_wss_path(self, **kwargs):
        """
        get wss key path
        :param kwargs: kwargs params
        :return: path
        """
        # 'depth': {'params': ['<symbol>@depth20@100ms'], 'method': 'SUBSCRIBE', 'id': 1},
        key = kwargs['topic']
        if 'symbol' in kwargs:
            kwargs['symbol'] = self.get_symbol(kwargs['symbol'])
        if 'period' in kwargs:
            kwargs['period'] = self.get_period(kwargs['period'])

        if key not in self.wss_paths or self.wss_paths[key] == '':
            self.raise_path_error(self.exchange_name, key)
        req = self.wss_paths[key].copy()
        key = list(req.keys())[0]
        for k, v in kwargs.items():
            if isinstance(v, str):
                req[key] = [req[key][0].replace(f"<{k}>", v.lower())]
        new_value = []
        if "symbol_list" in kwargs:
            for symbol in kwargs['symbol_list']:
                value = req[key]
                new_value.append(value[0].replace(f"<symbol>", symbol.lower()))
            req[key] = new_value
        return json.dumps(req)


class BinanceExchangeDataSwap(BinanceExchangeData):
    pass


class BinanceExchangeDataSpot(BinanceExchangeData):
    def __init__(self):
        super(BinanceExchangeDataSpot, self).__init__()
        self.exchange_name = 'binanceSpot'
        self.rest_url = 'https://api.binance.com'
        self.acct_wss_url = 'wss://stream.binance.com/ws'
        self.wss_url = 'wss://stream.binance.com/ws'

        self.rest_paths = {
            'transfer': 'POST /sapi/v1/futures/transfer',
            'get_account': 'GET /api/v3/account',
            'get_balance': 'GET /api/v3/account',
            'get_contract': 'GET /api/v3/exchangeInfo',
            'get_fee': 'GET /sapi/v1/asset/tradeFee',
            'get_tick': 'GET /api/v3/ticker/bookTicker',
            'get_depth': 'GET /api/v3/depth',
            'get_kline': 'GET /api/v3/klines',
            'get_funding_rate': 'GET /fapi/v1/fundingRate',
            'get_clear_price': 'GET /fapi/v1/premiumIndex',
            'get_mark_price': 'GET /fapi/v1/premiumIndex',
            'get_market': 'GET /api/v3/ticker/price',
            'make_order': 'POST /api/v3/order',
            'cancel_order': 'DELETE /api/v3/order',
            'cancel_all': 'DELETE /api/v3/openOrders',
            'query_order': 'GET /api/v3/order',
            'get_open_orders': 'GET /api/v3/openOrders',
            'get_deals': 'GET /api/v3/myTrades',
            'get_incre_depth': 'GET /api/v1/depth',
            'get_listen_key': 'POST /api/v3/userDataStream',
            'refresh_listen_key': 'PUT /api/v3/userDataStream',
            'query_referral': 'GET /sapi/v1/apiReferral/ifNewUser',
            'universal_transfer': "POST /sapi/v1/sub-account/universalTransfer",
            "account_summary": "GET /sapi/v2/sub-account/futures/account",
            "get_server_time": 'GET /api/v3/time',
        }

        self.wss_paths = {
            'force_order': {'params': ['<symbol>@forceOrder'], 'method': 'SUBSCRIBE', 'id': 1},
            'agg_trade': {'params': ['<symbol>@aggTrade'], 'method': 'SUBSCRIBE', 'id': 1},
            'tick': {'params': ['<symbol>@bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            'depth': {'params': ['<symbol>@depth20@100ms'], 'method': 'SUBSCRIBE', 'id': 1},
            'increDepthFlow': {'params': ['<symbol>@depth@100ms'], 'method': 'SUBSCRIBE', 'id': 1},
            'kline': {'params': ['<symbol>@kline_<period>'], 'method': 'SUBSCRIBE', 'id': 1},
            'market': {'params': ['!bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            'bidAsk': {'params': ['<symbol>@bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},

            'ticks': {'params': ['!ticker@arr'], 'method': 'SUBSCRIBE', 'id': 1},

            'orders': '',
            'deals': '',
            'balance': '',
            'position': '',
        }

        self.kline_periods = {
            '1m': '1m',
            '3m': '3m',
            '5m': '5m',
            '15m': '15m',
            '30m': '30m',
            '1h': '1h',
            '2h': '2h',
            '4h': '4h',
            '6h': '6h',
            '8h': '8h',
            '12h': '12h',
            '1d': '1d',
            '3d': '3d',
            '1w': '1w',
            '1M': '1M',
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        # self.status_dict = {
        #     'NEW': 'submit',
        #     'PARTIALLY_FILLED': 'partial-filled',
        #     'FILLED': 'filled',
        #     'CANCELED': 'cancel',
        #     'REJECTED': 'rejected',
        #     'EXPIRED': 'expired',
        # }

        self.legal_currency = [
            'USDT', 'USD', 'BTC', 'ETH', 'BUSD'
        ]

        # noinspection PyMethodMayBeStatic

    def get_symbol(self, symbol):
        return symbol.replace('-', '')

    def account_wss_symbol(self, symbol):
        for lc in self.legal_currency:
            if lc in symbol[-4:]:
                symbol = f"{symbol.split(lc)[0]}/{lc}".lower()
        return symbol

        # noinspection PyMethodMayBeStatic

    def get_wss_path(self, **kwargs):
        """
        get wss key path
        :param kwargs: kwargs params
        :return: path
        """
        key = kwargs['topic']
        if 'symbol' in kwargs:
            kwargs['symbol'] = self.get_symbol(kwargs['symbol'])
        if 'period' in kwargs:
            kwargs['period'] = self.get_period(kwargs['period'])

        if key not in self.wss_paths or self.wss_paths[key] == '':
            self.raise_path_error(self.exchange_name, key)
        req = self.wss_paths[key].copy()
        key = list(req.keys())[0]
        for k, v in kwargs.items():
            req[key] = [req[key][0].replace(f"<{k}>", v.lower())]
        return json.dumps(req)
