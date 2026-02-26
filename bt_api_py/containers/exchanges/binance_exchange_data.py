import json
from enum import Enum
from bt_api_py.containers.exchanges.exchange_data import ExchangeData


class BinanceExchangeData(ExchangeData):
    """Base class for all Binance exchange types.

    Provides shared utility methods (get_symbol, get_period, get_rest_path,
    get_wss_path, account_wss_symbol) and default kline_periods.
    Subclasses MUST set exchange-specific: exchange_name, rest_url,
    acct_wss_url, wss_url, rest_paths, wss_paths, legal_currency.
    """

    def __init__(self):
        super(BinanceExchangeData, self).__init__()
        self.exchange_name = 'binance'
        self.rest_url = ''
        self.acct_wss_url = ''
        self.wss_url = ''
        self.rest_paths = {}
        self.wss_paths = {}

        self.kline_periods = {
            '1s': '1s',
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
        if 'pair' in kwargs:
            kwargs['pair'] = self.get_symbol(kwargs['pair'])
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
                new_value.append(value[0].replace(f"<symbol>", self.get_symbol(symbol).lower()))
            req[key] = new_value
        return json.dumps(req)


class BinanceExchangeDataSwap(BinanceExchangeData):
    """Binance USDT-M Futures (fapi)."""

    def __init__(self):
        super(BinanceExchangeDataSwap, self).__init__()
        self.exchange_name = 'binance_swap'

        self.rest_url = 'https://fapi.binance.com'
        self.acct_wss_url = 'wss://fstream.binance.com/ws'
        self.wss_url = 'wss://fstream.binance.com/ws'

        self.rest_paths = {
            # --- General ---
            'ping': 'GET /fapi/v1/ping',
            'get_server_time': 'GET /fapi/v1/time',
            'get_contract': 'GET /fapi/v1/exchangeInfo',
            # --- Market Data ---
            'get_tick': 'GET /fapi/v1/ticker/bookTicker',
            'get_info': 'GET /fapi/v1/ticker/24hr',
            'get_new_price': 'GET /fapi/v1/trades',
            'get_historical_trades': 'GET /fapi/v1/historicalTrades',
            'get_depth': 'GET /fapi/v1/depth',
            'get_incre_depth': 'GET /fapi/v1/depth',
            'get_kline': 'GET /fapi/v1/klines',
            'get_ui_klines': 'GET /fapi/v1/uiKlines',
            'get_agg_trades': 'GET /fapi/v1/aggTrades',
            'get_funding_rate': 'GET /fapi/v1/premiumIndex',
            'get_clear_price': 'GET /fapi/v1/premiumIndex',
            'get_mark_price': 'GET /fapi/v1/premiumIndex',
            'get_history_funding_rate': 'GET /fapi/v1/fundingRate',
            'get_market_rate': 'GET /fapi/v1/premiumIndex',
            'get_funding_info': 'GET /fapi/v1/fundingInfo',
            'get_continuous_kline': 'GET /fapi/v1/continuousKlines',
            'get_index_price_kline': 'GET /fapi/v1/indexPriceKlines',
            'get_mark_price_kline': 'GET /fapi/v1/markPriceKlines',
            'get_price_ticker': 'GET /fapi/v2/ticker/price',
            'get_avg_price': 'GET /fapi/v1/avgPrice',
            'get_ticker': 'GET /fapi/v1/ticker',
            'get_open_interest': 'GET /fapi/v1/openInterest',
            'get_open_interest_interval': 'GET /fapi/v1/openInterestInterval',
            'get_liquidation_orders': 'GET /fapi/v1/allForceOrder',
            'get_delivery_price': 'GET /fapi/v1/deliveryPrice',
            # --- Market Data (Futures Data Endpoints) ---
            'get_long_short_ratio': 'GET /futures/data/globalLongShortAccountRatio',
            'get_top_long_short_account_ratio': 'GET /futures/data/topLongShortAccountRatio',
            'get_top_long_short_position_ratio': 'GET /futures/data/topLongShortPositionRatio',
            'get_taker_buy_sell_volume': 'GET /futures/data/takerlongshortRatio',
            'get_open_interest_hist': 'GET /futures/data/openInterestHist',
            'get_index_constituents': 'GET /fapi/v1/constituents',
            # --- Account ---
            'get_account': 'GET /fapi/v2/account',
            'get_account_v3': 'GET /fapi/v3/account',
            'get_balance': 'GET /fapi/v2/balance',
            'get_balance_v3': 'GET /fapi/v3/balance',
            'get_position': 'GET /fapi/v2/positionRisk',
            'get_position_v3': 'GET /fapi/v3/positionRisk',
            'get_fee': 'GET /fapi/v1/commissionRate',
            'get_income': 'GET /fapi/v1/income',
            'get_adl_quantile': 'GET /fapi/v1/adlQuantile',
            'get_leverage_bracket': 'GET /fapi/v1/leverageBracket',
            'get_position_mode': 'GET /fapi/v1/positionSide/dual',
            'get_multi_assets_mode': 'GET /fapi/v1/multiAssetsMargin',
            'get_api_trading_status': 'GET /fapi/v1/apiTradingStatus',
            'get_api_key_permission': 'GET /fapi/v1/apiKeyPermission',
            'get_order_rate_limit': 'GET /fapi/v1/rateLimit/order',
            'get_user_force_orders': 'GET /fapi/v1/userForceOrders',
            'get_symbol_config': 'GET /fapi/v1/symbolConfig',
            'get_account_config': 'GET /fapi/v1/accountConfig',
            # --- Trade ---
            'make_order': 'POST /fapi/v1/order',
            'make_order_test': 'POST /fapi/v1/order/test',
            'make_orders': 'POST /fapi/v1/batchOrders',
            'modify_order': 'PUT /fapi/v1/order',
            'modify_orders': 'PUT /fapi/v1/batchOrders',
            'cancel_order': 'DELETE /fapi/v1/order',
            'cancel_orders': 'DELETE /fapi/v1/batchOrders',
            'cancel_all': 'DELETE /fapi/v1/allOpenOrders',
            'auto_cancel_all': 'POST /fapi/v1/countdownCancelAll',
            'query_order': 'GET /fapi/v1/order',
            'get_open_orders': 'GET /fapi/v1/openOrders',
            'get_all_orders': 'GET /fapi/v1/allOrders',
            'get_deals': 'GET /fapi/v1/userTrades',
            'get_force_orders': 'GET /fapi/v1/forceOrders',
            'change_leverage': 'POST /fapi/v1/leverage',
            'change_margin_type': 'POST /fapi/v1/marginType',
            'change_position_mode': 'POST /fapi/v1/positionSide/dual',
            'change_multi_assets_mode': 'POST /fapi/v1/multiAssetsMargin',
            'modify_isolated_position_margin': 'POST /fapi/v1/positionMargin',
            'get_position_margin_history': 'GET /fapi/v1/positionMargin/history',
            # --- Order Lists (OCO) ---
            'make_oco_order': 'POST /fapi/v1/order/oco',
            'get_order_list': 'GET /fapi/v1/orderList',
            'get_all_order_lists': 'GET /fapi/v1/allOrderList',
            'get_open_order_lists': 'GET /fapi/v1/openOrderList',
            'cancel_order_list': 'DELETE /fapi/v1/orderList',
            # --- Listen Key ---
            'get_listen_key': 'POST /fapi/v1/listenKey',
            'refresh_listen_key': 'PUT /fapi/v1/listenKey',
            'close_listen_key': 'DELETE /fapi/v1/listenKey',
            # --- Legacy aliases (kept for backward compatibility) ---
            'update_leverage': 'POST /fapi/v1/leverage',
            'get_his_trans': 'POST /fapi/v1/positionSide/dual',
            'set_lever': 'POST /fapi/v1/leverage',
        }

        self.wss_paths = {
            # --- Market Streams ---
            'agg_trade': {'params': ['<symbol>@aggTrade'], 'method': 'SUBSCRIBE', 'id': 1},
            'trade': {'params': ['<symbol>@trade'], 'method': 'SUBSCRIBE', 'id': 1},
            'kline': {'params': ['<symbol>@kline_<period>'], 'method': 'SUBSCRIBE', 'id': 1},
            'continuous_kline': {'params': ['<pair>_perpetual@continuousKline_<period>'], 'method': 'SUBSCRIBE', 'id': 1},
            'mini_ticker': {'params': ['<symbol>@miniTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            'ticker': {'params': ['<symbol>@ticker'], 'method': 'SUBSCRIBE', 'id': 1},
            'ticker_window': {'params': ['<symbol>@ticker_<window>'], 'method': 'SUBSCRIBE', 'id': 1},
            'book_ticker': {'params': ['<symbol>@bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            'depth': {'params': ['<symbol>@depth20@100ms'], 'method': 'SUBSCRIBE', 'id': 1},
            'depth500': {'params': ['<symbol>@depth5@500ms'], 'method': 'SUBSCRIBE', 'id': 1},
            'depth_partial': {'params': ['<symbol>@depth<level>@100ms'], 'method': 'SUBSCRIBE', 'id': 1},
            'increDepthFlow': {'params': ['<symbol>@depth@100ms'], 'method': 'SUBSCRIBE', 'id': 1},
            'mark_price': {'params': ['<symbol>@markPrice@1s'], 'method': 'SUBSCRIBE', 'id': 1},
            'funding_rate': {'params': ['<symbol>@markPrice@1s'], 'method': 'SUBSCRIBE', 'id': 1},
            'force_order': {'params': ['<symbol>@forceOrder'], 'method': 'SUBSCRIBE', 'id': 1},
            # --- All Market Streams ---
            'all_force_order': {'params': ['!forceOrder@arr'], 'method': 'SUBSCRIBE', 'id': 1},
            'all_mini_ticker': {'params': ['!miniTicker@arr'], 'method': 'SUBSCRIBE', 'id': 1},
            'all_ticker': {'params': ['!ticker@arr'], 'method': 'SUBSCRIBE', 'id': 1},
            'all_ticker_window': {'params': ['!ticker_<window>@arr'], 'method': 'SUBSCRIBE', 'id': 1},
            'all_mark_price': {'params': ['!markPrice@arr@1s'], 'method': 'SUBSCRIBE', 'id': 1},
            'all_book_ticker': {'params': ['!bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            # --- Account & Position Streams ---
            'listen_key': '',
            # --- Aliases for compatibility ---
            'tick': {'params': ['<symbol>@bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            'tick_all': {'params': ['!bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            'ticks': {'params': ['!ticker@arr'], 'method': 'SUBSCRIBE', 'id': 1},
            'tickers': {'params': ['!ticker@arr'], 'method': 'SUBSCRIBE', 'id': 1},
            'market': {'params': ['!bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            'bidAsk': {'params': ['<symbol>@bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            'clearPrice': {'params': ['<symbol>@markPrice@1s'], 'method': 'SUBSCRIBE', 'id': 1},
            'liquidation': {'params': ['<symbol>@forceOrder'], 'method': 'SUBSCRIBE', 'id': 1},
            'contract_info': {'params': ['!contractInfo'], 'method': 'SUBSCRIBE', 'id': 1},
            # --- User Data Streams (listen key based) ---
            'orders': '',
            'deals': '',
            'balance': '',
            'position': '',
            'account': '',
            'portfolio': '',
        }

        self.symbol_leverage_dict = {'BTC-USDT': 100,
                                     'ETH-USDT': 10,
                                     'BCH-USDT': 10,
                                     'DOGE-USDT': 0.001,
                                     "BNB-USDT": 10,
                                     "OP-USDT": 1, }


class BinanceExchangeDataSpot(BinanceExchangeData):
    def __init__(self):
        super(BinanceExchangeDataSpot, self).__init__()
        self.exchange_name = 'binanceSpot'
        self.rest_url = 'https://api.binance.com'
        self.acct_wss_url = 'wss://stream.binance.com/ws'
        self.wss_url = 'wss://stream.binance.com/ws'

        self.rest_paths = {
            # --- General ---
            'ping': 'GET /api/v3/ping',
            'get_server_time': 'GET /api/v3/time',
            'get_contract': 'GET /api/v3/exchangeInfo',
            # --- Market Data ---
            'get_tick': 'GET /api/v3/ticker/bookTicker',
            'get_depth': 'GET /api/v3/depth',
            'get_incre_depth': 'GET /api/v1/depth',
            'get_kline': 'GET /api/v3/klines',
            'get_ui_klines': 'GET /api/v3/uiKlines',
            'get_avg_price': 'GET /api/v3/avgPrice',
            'get_info': 'GET /api/v3/ticker/24hr',
            'get_market': 'GET /api/v3/ticker/price',
            'get_ticker': 'GET /api/v3/ticker',
            'get_new_price': 'GET /api/v3/trades',
            'get_historical_trades': 'GET /api/v3/historicalTrades',
            'get_agg_trades': 'GET /api/v3/aggTrades',
            # --- Account ---
            'get_account': 'GET /api/v3/account',
            'get_balance': 'GET /api/v3/account',
            'get_fee': 'GET /sapi/v1/asset/tradeFee',
            'get_commission': 'GET /api/v3/account/commission',
            'get_order_rate_limit': 'GET /api/v3/rateLimit/order',
            # --- Trade ---
            'make_order': 'POST /api/v3/order',
            'make_order_test': 'POST /api/v3/order/test',
            'cancel_order': 'DELETE /api/v3/order',
            'cancel_all': 'DELETE /api/v3/openOrders',
            'cancel_replace_order': 'POST /api/v3/order/cancelReplace',
            'amend_keep_priority': 'PUT /api/v3/order/amend/keepPriority',
            'query_order': 'GET /api/v3/order',
            'get_open_orders': 'GET /api/v3/openOrders',
            'get_all_orders': 'GET /api/v3/allOrders',
            'get_deals': 'GET /api/v3/myTrades',
            # --- Order Lists (OCO/OTO/OTOCO) ---
            'make_oco_order': 'POST /api/v3/order/oco',
            'get_order_list': 'GET /api/v3/orderList',
            'get_all_order_lists': 'GET /api/v3/allOrderList',
            'get_open_order_lists': 'GET /api/v3/openOrderList',
            'cancel_order_list': 'DELETE /api/v3/orderList',
            # --- SOR (Smart Order Routing) ---
            'sor_make_order': 'POST /api/v3/sor/order',
            'sor_make_order_test': 'POST /api/v3/sor/order/test',
            'get_sor_allocations': 'GET /api/v3/myAllocations',
            # --- Prevented Matches ---
            'get_prevented_matches': 'GET /api/v3/myPreventedMatches',
            # --- Order Amendments ---
            'get_order_amendments': 'GET /api/v3/order/amendments',
            # --- Filters ---
            'get_my_filters': 'GET /api/v3/myFilters',
            # --- Listen Key ---
            'get_listen_key': 'POST /sapi/v1/userDataStream',
            'refresh_listen_key': 'PUT /sapi/v1/userDataStream',
            # --- Sub-account ---
            'query_referral': 'GET /sapi/v1/apiReferral/ifNewUser',
            'universal_transfer': 'POST /sapi/v1/sub-account/universalTransfer',
            'account_summary': 'GET /sapi/v2/sub-account/futures/account',
            # --- Transfer (Futures) ---
            'transfer': 'POST /sapi/v1/futures/transfer',
        }

        self.wss_paths = {
            # --- Market Streams ---
            'agg_trade': {'params': ['<symbol>@aggTrade'], 'method': 'SUBSCRIBE', 'id': 1},
            'trade': {'params': ['<symbol>@trade'], 'method': 'SUBSCRIBE', 'id': 1},
            'kline': {'params': ['<symbol>@kline_<period>'], 'method': 'SUBSCRIBE', 'id': 1},
            'mini_ticker': {'params': ['<symbol>@miniTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            'ticker': {'params': ['<symbol>@ticker'], 'method': 'SUBSCRIBE', 'id': 1},
            'ticker_window': {'params': ['<symbol>@ticker_<window>'], 'method': 'SUBSCRIBE', 'id': 1},
            'book_ticker': {'params': ['<symbol>@bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            'avg_price': {'params': ['<symbol>@avgPrice'], 'method': 'SUBSCRIBE', 'id': 1},
            'depth': {'params': ['<symbol>@depth20@100ms'], 'method': 'SUBSCRIBE', 'id': 1},
            'depth_partial': {'params': ['<symbol>@depth<level>@100ms'], 'method': 'SUBSCRIBE', 'id': 1},
            'increDepthFlow': {'params': ['<symbol>@depth@100ms'], 'method': 'SUBSCRIBE', 'id': 1},
            'force_order': {'params': ['<symbol>@forceOrder'], 'method': 'SUBSCRIBE', 'id': 1},
            # --- All Market Streams ---
            'all_mini_ticker': {'params': ['!miniTicker@arr'], 'method': 'SUBSCRIBE', 'id': 1},
            'all_ticker': {'params': ['!ticker@arr'], 'method': 'SUBSCRIBE', 'id': 1},
            'all_ticker_window': {'params': ['!ticker_<window>@arr'], 'method': 'SUBSCRIBE', 'id': 1},
            'all_book_ticker': {'params': ['!bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            # --- Aliases for compatibility ---
            'tick': {'params': ['<symbol>@bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            'tick_all': {'params': ['!bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            'ticks': {'params': ['!ticker@arr'], 'method': 'SUBSCRIBE', 'id': 1},
            'market': {'params': ['!bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            'bidAsk': {'params': ['<symbol>@bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            'liquidation': {'params': ['<symbol>@forceOrder'], 'method': 'SUBSCRIBE', 'id': 1},
            # --- User Data Streams (listen key based) ---
            'orders': '',
            'deals': '',
            'balance': '',
            'position': '',
        }

        self.kline_periods = {
            '1s': '1s',
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
            if isinstance(v, str):
                req[key] = [req[key][0].replace(f"<{k}>", v.lower())]
        new_value = []
        if "symbol_list" in kwargs:
            for symbol in kwargs['symbol_list']:
                value = req[key]
                new_value.append(value[0].replace(f"<symbol>", self.get_symbol(symbol).lower()))
            req[key] = new_value
        return json.dumps(req)


class BinanceExchangeDataCoinM(BinanceExchangeData):
    def __init__(self):
        super(BinanceExchangeDataCoinM, self).__init__()
        self.exchange_name = 'binance_coin_m'

        self.rest_url = 'https://dapi.binance.com'
        self.acct_wss_url = 'wss://dstream.binance.com/ws'
        self.wss_url = 'wss://dstream.binance.com/ws'

        self.rest_paths = {
            # --- General ---
            'ping': 'GET /dapi/v1/ping',
            'get_server_time': 'GET /dapi/v1/time',
            'get_contract': 'GET /dapi/v1/exchangeInfo',
            # --- Market Data ---
            'get_tick': 'GET /dapi/v1/ticker/bookTicker',
            'get_info': 'GET /dapi/v1/ticker/24hr',
            'get_new_price': 'GET /dapi/v1/trades',
            'get_historical_trades': 'GET /dapi/v1/historicalTrades',
            'get_depth': 'GET /dapi/v1/depth',
            'get_incre_depth': 'GET /dapi/v1/depth',
            'get_kline': 'GET /dapi/v1/klines',
            'get_ui_klines': 'GET /dapi/v1/uiKlines',
            'get_agg_trades': 'GET /dapi/v1/aggTrades',
            'get_funding_rate': 'GET /dapi/v1/premiumIndex',
            'get_clear_price': 'GET /dapi/v1/premiumIndex',
            'get_mark_price': 'GET /dapi/v1/premiumIndex',
            'get_history_funding_rate': 'GET /dapi/v1/fundingRate',
            'get_market_rate': 'GET /dapi/v1/premiumIndex',
            'get_funding_info': 'GET /dapi/v1/fundingInfo',
            'get_continuous_kline': 'GET /dapi/v1/continuousKlines',
            'get_index_price_kline': 'GET /dapi/v1/indexPriceKlines',
            'get_mark_price_kline': 'GET /dapi/v1/markPriceKlines',
            'get_price_ticker': 'GET /dapi/v1/ticker/price',
            'get_avg_price': 'GET /dapi/v1/avgPrice',
            'get_ticker': 'GET /dapi/v1/ticker',
            'get_open_interest': 'GET /dapi/v1/openInterest',
            'get_open_interest_interval': 'GET /dapi/v1/openInterestInterval',
            'get_liquidation_orders': 'GET /dapi/v1/allForceOrder',
            'get_delivery_price': 'GET /dapi/v1/deliveryPrice',
            # --- Market Data (Futures Data Endpoints) ---
            'get_long_short_ratio': 'GET /futures/data/globalLongShortAccountRatio',
            'get_top_long_short_account_ratio': 'GET /futures/data/topLongShortAccountRatio',
            'get_top_long_short_position_ratio': 'GET /futures/data/topLongShortPositionRatio',
            'get_taker_buy_sell_volume': 'GET /futures/data/takerlongshortRatio',
            'get_open_interest_hist': 'GET /futures/data/openInterestHist',
            'get_index_constituents': 'GET /dapi/v1/constituents',
            # --- Account ---
            'get_account': 'GET /dapi/v1/account',
            'get_balance': 'GET /dapi/v1/balance',
            'get_position': 'GET /dapi/v1/positionRisk',
            'get_fee': 'GET /dapi/v1/commissionRate',
            'get_income': 'GET /dapi/v1/income',
            'get_adl_quantile': 'GET /dapi/v1/adlQuantile',
            'get_leverage_bracket': 'GET /dapi/v1/leverageBracket',
            'get_leverage_bracket_v2': 'GET /dapi/v2/leverageBracket',
            'get_position_mode': 'GET /dapi/v1/positionSide/dual',
            'get_api_key_permission': 'GET /dapi/v1/apiKeyPermission',
            'get_user_force_orders': 'GET /dapi/v1/userForceOrders',
            # --- Trade ---
            'make_order': 'POST /dapi/v1/order',
            'make_order_test': 'POST /dapi/v1/order/test',
            'make_orders': 'POST /dapi/v1/batchOrders',
            'modify_order': 'PUT /dapi/v1/order',
            'modify_orders': 'PUT /dapi/v1/batchOrders',
            'cancel_order': 'DELETE /dapi/v1/order',
            'cancel_orders': 'DELETE /dapi/v1/batchOrders',
            'cancel_all': 'DELETE /dapi/v1/allOpenOrders',
            'auto_cancel_all': 'POST /dapi/v1/countdownCancelAll',
            'query_order': 'GET /dapi/v1/order',
            'get_open_orders': 'GET /dapi/v1/openOrders',
            'get_all_orders': 'GET /dapi/v1/allOrders',
            'get_deals': 'GET /dapi/v1/userTrades',
            'get_force_orders': 'GET /dapi/v1/forceOrders',
            'change_leverage': 'POST /dapi/v1/leverage',
            'change_margin_type': 'POST /dapi/v1/marginType',
            'change_position_mode': 'POST /dapi/v1/positionSide/dual',
            'modify_isolated_position_margin': 'POST /dapi/v1/positionMargin',
            'get_position_margin_history': 'GET /dapi/v1/positionMargin/history',
            # --- Order Lists (OCO) ---
            'make_oco_order': 'POST /dapi/v1/order/oco',
            'get_order_list': 'GET /dapi/v1/orderList',
            'get_all_order_lists': 'GET /dapi/v1/allOrderList',
            'get_open_order_lists': 'GET /dapi/v1/openOrderList',
            'cancel_order_list': 'DELETE /dapi/v1/orderList',
            # --- Listen Key ---
            'get_listen_key': 'POST /dapi/v1/listenKey',
            'refresh_listen_key': 'PUT /dapi/v1/listenKey',
            'close_listen_key': 'DELETE /dapi/v1/listenKey',
            # --- Legacy aliases ---
            'update_leverage': 'POST /dapi/v1/leverage',
            'set_lever': 'POST /dapi/v1/leverage',
        }

        self.wss_paths = {
            # --- Market Streams ---
            'agg_trade': {'params': ['<symbol>@aggTrade'], 'method': 'SUBSCRIBE', 'id': 1},
            'trade': {'params': ['<symbol>@trade'], 'method': 'SUBSCRIBE', 'id': 1},
            'kline': {'params': ['<symbol>@kline_<period>'], 'method': 'SUBSCRIBE', 'id': 1},
            'continuous_kline': {'params': ['<pair>_perpetual@continuousKline_<period>'], 'method': 'SUBSCRIBE', 'id': 1},
            'mini_ticker': {'params': ['<symbol>@miniTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            'ticker': {'params': ['<symbol>@ticker'], 'method': 'SUBSCRIBE', 'id': 1},
            'ticker_window': {'params': ['<symbol>@ticker_<window>'], 'method': 'SUBSCRIBE', 'id': 1},
            'book_ticker': {'params': ['<symbol>@bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            'depth': {'params': ['<symbol>@depth20@100ms'], 'method': 'SUBSCRIBE', 'id': 1},
            'depth500': {'params': ['<symbol>@depth5@500ms'], 'method': 'SUBSCRIBE', 'id': 1},
            'depth_partial': {'params': ['<symbol>@depth<level>@100ms'], 'method': 'SUBSCRIBE', 'id': 1},
            'increDepthFlow': {'params': ['<symbol>@depth@100ms'], 'method': 'SUBSCRIBE', 'id': 1},
            'mark_price': {'params': ['<symbol>@markPrice@1s'], 'method': 'SUBSCRIBE', 'id': 1},
            'funding_rate': {'params': ['<symbol>@markPrice@1s'], 'method': 'SUBSCRIBE', 'id': 1},
            'force_order': {'params': ['<symbol>@forceOrder'], 'method': 'SUBSCRIBE', 'id': 1},
            # --- All Market Streams ---
            'all_force_order': {'params': ['!forceOrder@arr'], 'method': 'SUBSCRIBE', 'id': 1},
            'all_mini_ticker': {'params': ['!miniTicker@arr'], 'method': 'SUBSCRIBE', 'id': 1},
            'all_ticker': {'params': ['!ticker@arr'], 'method': 'SUBSCRIBE', 'id': 1},
            'all_ticker_window': {'params': ['!ticker_<window>@arr'], 'method': 'SUBSCRIBE', 'id': 1},
            'all_mark_price': {'params': ['!markPrice@arr@1s'], 'method': 'SUBSCRIBE', 'id': 1},
            'all_book_ticker': {'params': ['!bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            # --- Aliases for compatibility ---
            'tick': {'params': ['<symbol>@bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            'tick_all': {'params': ['!bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            'ticks': {'params': ['!ticker@arr'], 'method': 'SUBSCRIBE', 'id': 1},
            'tickers': {'params': ['!ticker@arr'], 'method': 'SUBSCRIBE', 'id': 1},
            'market': {'params': ['!bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            'bidAsk': {'params': ['<symbol>@bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            'clearPrice': {'params': ['<symbol>@markPrice@1s'], 'method': 'SUBSCRIBE', 'id': 1},
            'liquidation': {'params': ['<symbol>@forceOrder'], 'method': 'SUBSCRIBE', 'id': 1},
            'contract_info': {'params': ['!contractInfo'], 'method': 'SUBSCRIBE', 'id': 1},
            # --- User Data Streams (listen key based) ---
            'orders': '',
            'deals': '',
            'balance': '',
            'position': '',
            'account': '',
        }

        self.legal_currency = [
            'USD', 'BTC', 'ETH', 'BNB', 'DOT', 'ADA',
        ]


class BinanceExchangeDataOption(BinanceExchangeData):
    def __init__(self):
        super(BinanceExchangeDataOption, self).__init__()
        self.exchange_name = 'binance_option'

        self.rest_url = 'https://eapi.binance.com'
        self.acct_wss_url = 'wss://nbstream.binance.com/eoptions/ws'
        self.wss_url = 'wss://nbstream.binance.com/eoptions/ws'

        self.rest_paths = {
            # --- General ---
            'ping': 'GET /eapi/v1/ping',
            'get_server_time': 'GET /eapi/v1/time',
            'get_contract': 'GET /eapi/v1/exchangeInfo',
            # --- Market Data ---
            'get_tick': 'GET /eapi/v1/ticker',
            'get_depth': 'GET /eapi/v1/depth',
            'get_kline': 'GET /eapi/v1/klines',
            'get_new_price': 'GET /eapi/v1/trades',
            'get_mark_price': 'GET /eapi/v1/mark',
            'get_index_price': 'GET /eapi/v1/index',
            'get_open_interest': 'GET /eapi/v1/openInterest',
            'get_exercise_history': 'GET /eapi/v1/exerciseHistory',
            # --- Account ---
            'get_account': 'GET /eapi/v1/account',
            'get_income': 'GET /eapi/v1/bill',
            'get_position': 'GET /eapi/v1/position',
            'get_exercise_record': 'GET /eapi/v1/exerciseRecord',
            'get_user_trades': 'GET /eapi/v1/userTrades',
            # --- Trade ---
            'make_order': 'POST /eapi/v1/order',
            'make_order_test': 'POST /eapi/v1/order/test',
            'make_orders': 'POST /eapi/v1/batchOrders',
            'cancel_order': 'DELETE /eapi/v1/order',
            'cancel_orders': 'DELETE /eapi/v1/batchOrders',
            'cancel_all': 'DELETE /eapi/v1/allOpenOrders',
            'cancel_all_by_underlying': 'DELETE /eapi/v1/allOpenOrdersByUnderlying',
            'query_order': 'GET /eapi/v1/order',
            'get_open_orders': 'GET /eapi/v1/openOrders',
            'get_all_orders': 'GET /eapi/v1/historyOrders',
            # --- Listen Key ---
            'get_listen_key': 'POST /eapi/v1/listenKey',
            'refresh_listen_key': 'PUT /eapi/v1/listenKey',
            'close_listen_key': 'DELETE /eapi/v1/listenKey',
        }

        self.wss_paths = {
            # --- Market Streams ---
            'agg_trade': {'params': ['<symbol>@trade'], 'method': 'SUBSCRIBE', 'id': 1},
            'tick': {'params': ['<symbol>@ticker'], 'method': 'SUBSCRIBE', 'id': 1},
            'ticker': {'params': ['<symbol>@ticker'], 'method': 'SUBSCRIBE', 'id': 1},
            'all_ticker': {'params': ['!ticker@arr'], 'method': 'SUBSCRIBE', 'id': 1},
            'depth': {'params': ['<symbol>@depth20@100ms'], 'method': 'SUBSCRIBE', 'id': 1},
            'depth_partial': {'params': ['<symbol>@depth<level>@100ms'], 'method': 'SUBSCRIBE', 'id': 1},
            'kline': {'params': ['<symbol>@kline_<period>'], 'method': 'SUBSCRIBE', 'id': 1},
            'mark_price': {'params': ['<symbol>@markPrice'], 'method': 'SUBSCRIBE', 'id': 1},
            'index_price': {'params': ['<underlying>@index'], 'method': 'SUBSCRIBE', 'id': 1},
            'open_interest': {'params': ['<underlying>@openInterest@<expiration>'], 'method': 'SUBSCRIBE', 'id': 1},
            # --- Aliases ---
            'book_ticker': {'params': ['<symbol>@ticker'], 'method': 'SUBSCRIBE', 'id': 1},
            # --- User Data Streams (listen key based) ---
            'orders': '',
            'deals': '',
            'balance': '',
            'position': '',
            'account': '',
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
            '12h': '12h',
            '1d': '1d',
            '3d': '3d',
            '1w': '1w',
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        self.legal_currency = [
            'USDT', 'USD', 'BTC', 'ETH',
        ]


class BinanceExchangeDataMargin(BinanceExchangeData):
    def __init__(self):
        super(BinanceExchangeDataMargin, self).__init__()
        self.exchange_name = 'binance_margin'

        self.rest_url = 'https://api.binance.com'
        self.acct_wss_url = 'wss://stream.binance.com/ws'
        self.wss_url = 'wss://stream.binance.com/ws'

        self.rest_paths = {
            # --- General (shared with Spot /api/v3) ---
            'ping': 'GET /api/v3/ping',
            'get_server_time': 'GET /api/v3/time',
            'get_contract': 'GET /api/v3/exchangeInfo',
            # --- Market Data (shared with Spot /api/v3) ---
            'get_tick': 'GET /api/v3/ticker/bookTicker',
            'get_info': 'GET /api/v3/ticker/24hr',
            'get_new_price': 'GET /api/v3/trades',
            'get_historical_trades': 'GET /api/v3/historicalTrades',
            'get_depth': 'GET /api/v3/depth',
            'get_incre_depth': 'GET /api/v1/depth',
            'get_kline': 'GET /api/v3/klines',
            'get_ui_klines': 'GET /api/v3/uiKlines',
            'get_agg_trades': 'GET /api/v3/aggTrades',
            # --- Margin-specific Market Data ---
            'get_all_assets': 'GET /sapi/v1/margin/allAssets',
            'get_all_pairs': 'GET /sapi/v1/margin/allPairs',
            'get_isolated_all_pairs': 'GET /sapi/v1/margin/isolated/allPairs',
            'get_price_index': 'GET /sapi/v1/margin/priceIndex',
            'get_cross_margin_collateral_ratio': 'GET /sapi/v1/margin/crossMarginCollateralRatio',
            'get_leverage_bracket': 'GET /sapi/v1/margin/leverageBracket',
            'get_isolated_margin_tier': 'GET /sapi/v1/margin/isolatedMarginTier',
            'get_margin_manual_liquidation': 'GET /sapi/v1/margin/liquidationBureau/assets',
            # --- Account ---
            'get_account': 'GET /sapi/v1/margin/account',
            'get_isolated_account': 'GET /sapi/v1/margin/isolated/account',
            'get_isolated_account_limit': 'GET /sapi/v1/margin/isolated/accountLimit',
            'get_max_borrowable': 'GET /sapi/v1/margin/maxBorrowable',
            'get_max_transferable': 'GET /sapi/v1/margin/maxTransferable',
            'get_interest_history': 'GET /sapi/v1/margin/interestHistory',
            'get_interest_rate_history': 'GET /sapi/v1/margin/interestRateHistory',
            'get_next_hourly_interest_rate': 'GET /sapi/v1/margin/next-hourly-interest-rate',
            'get_cross_margin_data': 'GET /sapi/v1/margin/crossMarginData',
            'get_isolated_margin_data': 'GET /sapi/v1/margin/isolatedMarginData',
            'get_capital_flow': 'GET /sapi/v1/margin/capital-flow',
            'get_bnb_burn': 'GET /sapi/v1/bnbBurn',
            'get_bnb_burn_status': 'GET /sapi/v1/bnbBurn/status',
            'toggle_bnb_burn': 'POST /sapi/v1/bnbBurn',
            'get_trade_coeff': 'GET /sapi/v1/margin/tradeCoeff',
            # --- Borrow & Repay ---
            'borrow_repay': 'POST /sapi/v1/margin/borrow-repay',
            'get_borrow_repay_records': 'GET /sapi/v1/margin/borrow-repay',
            'borrow': 'POST /sapi/v1/margin/loan',
            'repay': 'POST /sapi/v1/margin/repay',
            # --- Transfer ---
            'get_transfer_history': 'GET /sapi/v1/margin/transfer',
            'transfer_to_margin': 'POST /sapi/v1/margin/transfer',
            'transfer_to_spot': 'POST /sapi/v1/margin/transfer',
            # --- Trade ---
            'make_order': 'POST /sapi/v1/margin/order',
            'make_order_test': 'POST /sapi/v1/margin/order/test',
            'make_order_oto': 'POST /sapi/v1/margin/order/oto',
            'make_order_otoco': 'POST /sapi/v1/margin/order/otoco',
            'cancel_order': 'DELETE /sapi/v1/margin/order',
            'cancel_all': 'DELETE /sapi/v1/margin/openOrders',
            'query_order': 'GET /sapi/v1/margin/order',
            'get_open_orders': 'GET /sapi/v1/margin/openOrders',
            'get_all_orders': 'GET /sapi/v1/margin/allOrders',
            'get_deals': 'GET /sapi/v1/margin/myTrades',
            'get_order_rate_limit': 'GET /sapi/v1/margin/rateLimit/order',
            'manual_liquidation': 'POST /sapi/v1/margin/manual-liquidation',
            'exchange_small_liability': 'POST /sapi/v1/margin/exchange-small-liability',
            'get_small_liability_history': 'GET /sapi/v1/margin/exchange-small-liability-history',
            'set_max_leverage': 'POST /sapi/v1/margin/max-leverage',
            # --- Listen Key ---
            'get_listen_key': 'POST /sapi/v1/margin/listen-key',
            'refresh_listen_key': 'PUT /sapi/v1/margin/listen-key',
            'close_listen_key': 'DELETE /sapi/v1/margin/listen-key',
        }

        self.wss_paths = {
            # --- Market Streams ---
            'agg_trade': {'params': ['<symbol>@aggTrade'], 'method': 'SUBSCRIBE', 'id': 1},
            'trade': {'params': ['<symbol>@trade'], 'method': 'SUBSCRIBE', 'id': 1},
            'kline': {'params': ['<symbol>@kline_<period>'], 'method': 'SUBSCRIBE', 'id': 1},
            'mini_ticker': {'params': ['<symbol>@miniTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            'ticker': {'params': ['<symbol>@ticker'], 'method': 'SUBSCRIBE', 'id': 1},
            'ticker_window': {'params': ['<symbol>@ticker_<window>'], 'method': 'SUBSCRIBE', 'id': 1},
            'book_ticker': {'params': ['<symbol>@bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            'depth': {'params': ['<symbol>@depth20@100ms'], 'method': 'SUBSCRIBE', 'id': 1},
            'depth_partial': {'params': ['<symbol>@depth<level>@100ms'], 'method': 'SUBSCRIBE', 'id': 1},
            'increDepthFlow': {'params': ['<symbol>@depth@100ms'], 'method': 'SUBSCRIBE', 'id': 1},
            # --- All Market Streams ---
            'all_mini_ticker': {'params': ['!miniTicker@arr'], 'method': 'SUBSCRIBE', 'id': 1},
            'all_ticker': {'params': ['!ticker@arr'], 'method': 'SUBSCRIBE', 'id': 1},
            'all_ticker_window': {'params': ['!ticker_<window>@arr'], 'method': 'SUBSCRIBE', 'id': 1},
            # --- Aliases for compatibility ---
            'tick': {'params': ['<symbol>@bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            'tick_all': {'params': ['!bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            'ticks': {'params': ['!ticker@arr'], 'method': 'SUBSCRIBE', 'id': 1},
            'bidAsk': {'params': ['<symbol>@bookTicker'], 'method': 'SUBSCRIBE', 'id': 1},
            # --- User Data Streams (listen key based) ---
            'orders': '',
            'deals': '',
            'balance': '',
            'position': '',
            'account': '',
        }

        self.legal_currency = [
            'USDT', 'USD', 'BTC', 'ETH', 'BNB', 'BUSD',
        ]

    def get_symbol(self, symbol):
        return symbol.replace('-', '')


class BinanceExchangeDataAlgo(BinanceExchangeData):
    def __init__(self):
        super(BinanceExchangeDataAlgo, self).__init__()
        self.exchange_name = 'binance_algo'

        self.rest_url = 'https://api.binance.com'
        self.acct_wss_url = 'wss://stream.binance.com/ws'
        self.wss_url = 'wss://stream.binance.com/ws'

        self.rest_paths = {
            # --- General ---
            'ping': 'GET /api/v3/ping',
            'get_server_time': 'GET /api/v3/time',
            # --- Spot Algo (TWAP/VWAP) ---
            'spot_twap_new_order': 'POST /sapi/v1/algo/spot/newOrderTwap',
            'spot_vwap_new_order': 'POST /sapi/v1/algo/spot/newOrderVwap',
            'spot_cancel_order': 'DELETE /sapi/v1/algo/spot/order',
            'spot_get_open_orders': 'GET /sapi/v1/algo/spot/openOrders',
            'spot_get_history_orders': 'GET /sapi/v1/algo/spot/historicalOrders',
            'spot_get_sub_orders': 'GET /sapi/v1/algo/spot/subOrders',
            # --- Futures Algo (TWAP/VP) ---
            'futures_twap_new_order': 'POST /sapi/v1/algo/futures/newOrderTwap',
            'futures_vp_new_order': 'POST /sapi/v1/algo/futures/newOrderVp',
            'futures_cancel_order': 'DELETE /sapi/v1/algo/futures/order',
            'futures_get_open_orders': 'GET /sapi/v1/algo/futures/openOrders',
            'futures_get_history_orders': 'GET /sapi/v1/algo/futures/historicalOrders',
            'futures_get_sub_orders': 'GET /sapi/v1/algo/futures/subOrders',
        }

        self.wss_paths = {}

        self.legal_currency = [
            'USDT', 'USD', 'BTC', 'ETH', 'BNB', 'BUSD',
        ]

    def get_symbol(self, symbol):
        return symbol.replace('-', '')
