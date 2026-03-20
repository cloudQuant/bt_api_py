"""MEXC Exchange Data Configuration.

Provides REST API URLs, path mappings, and configuration for MEXC exchange.
"""

import json
import os
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("mexc_exchange_data")

# ── 配置加载缓存 ──────────────────────────────────────────────
_mexc_config = None
_mexc_config_loaded = False


def _get_mexc_config() -> Any | None:
    """延迟加载并缓存 MEXC YAML 配置."""
    global _mexc_config, _mexc_config_loaded
    if _mexc_config_loaded:
        return _mexc_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "mexc.yaml",
        )
        if os.path.exists(config_path):
            _mexc_config = load_exchange_config(config_path)
        _mexc_config_loaded = True
    except (OSError, ValueError, KeyError, ImportError) as e:
        logger.warning(f"Failed to load mexc.yaml config: {e}")
    return _mexc_config


class MexcExchangeData(ExchangeData):
    """Base class for all MEXC exchange types.

    Provides shared utility methods (get_symbol, get_period, get_rest_path,
    get_wss_path, account_wss_symbol) and default kline_periods.
    Subclasses MUST set exchange-specific: exchange_name, rest_url,
    acct_wss_url, wss_url, rest_paths, wss_paths, legal_currency.
    """

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "mexc"
        self.rest_url = ""
        self.acct_wss_url = ""
        self.wss_url = ""
        self.um_rest_url = ""
        self.um_wss_Url = ""
        self.rest_paths = {}
        self.wss_paths = {}

        self.kline_periods = {
            "1s": "1s",
            "1m": "1m",
            "3m": "3m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1h",
            "2h": "2h",
            "4h": "4h",
            "6h": "6h",
            "8h": "8h",
            "12h": "12h",
            "1d": "1d",
            "3d": "3d",
            "1w": "1w",
            "1M": "1M",
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        self.legal_currency = [
            "USDT",
            "USD",
            "BTC",
            "ETH",
        ]

    def _load_from_config(self, asset_type) -> bool:
        """从 YAML 配置文件加载交易所参数.

        Args:
            asset_type: 资产类型 key, 如 'spot', 'swap', 'coin_m' 等
        Returns:
            bool: 是否加载成功

        """
        config = _get_mexc_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        # exchange_name
        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name

        # URLs
        if config.base_urls:
            self.rest_url = config.base_urls.rest.get(asset_type, self.rest_url)
            self.wss_url = config.base_urls.wss.get(asset_type, self.wss_url)
            self.acct_wss_url = config.base_urls.acct_wss.get(asset_type, self.acct_wss_url)

        # rest_paths (直接使用, 格式一致)
        if asset_cfg.rest_paths:
            self.rest_paths = dict(asset_cfg.rest_paths)

        # wss_paths: YAML 模板字符串 → {'params': [template], 'method': 'SUBSCRIBE', 'id': 1}
        if asset_cfg.wss_paths:
            converted: dict[str, Any] = {}
            for key, value in asset_cfg.wss_paths.items():
                if isinstance(value, str):
                    if value:
                        converted[key] = {"params": [value], "method": "SUBSCRIBE", "id": 1}
                    else:
                        converted[key] = ""
                else:
                    converted[key] = value
            self.wss_paths = converted

        # kline_periods (asset-level 优先, 否则用 exchange-level)
        kp = asset_cfg.kline_periods or (config.kline_periods if config.kline_periods else None)
        if kp:
            self.kline_periods = dict(kp)
            self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        # legal_currency (asset-level 优先, 否则用 exchange-level)
        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)

        return True

    # noinspection PyMethodMayBeStatic
    def get_symbol(self, symbol: str) -> str:
        # Remove common separators to get MEXC format (BASEQUOTE)
        return symbol.replace("-", "").replace("/", "")

    def account_wss_symbol(self, symbol: str) -> str:
        for lc in self.legal_currency:
            if lc in symbol:
                symbol = f"{symbol.split(lc)[0]}/{lc}".lower()
                break
        return symbol

    # noinspection PyMethodMayBeStatic
    def get_period(self, key: str) -> str:
        if key in self.kline_periods:
            return self.kline_periods[key]
        return key

    def get_rest_path(self, key: str, **kwargs: Any) -> str:
        if key not in self.rest_paths or self.rest_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)
        return str(self.rest_paths[key])

    def get_wss_path(self, **kwargs) -> str:
        """Get wss key path
        :param kwargs: kwargs params
        :return: path.
        """
        # 'depth': {'params': ['<symbol>@depth20@100ms'], 'method': 'SUBSCRIBE', 'id': 1},
        key = kwargs["topic"]
        if "symbol" in kwargs:
            kwargs["symbol"] = self.get_symbol(kwargs["symbol"])
        if "pair" in kwargs:
            kwargs["pair"] = self.get_symbol(kwargs["pair"])
        if "period" in kwargs:
            kwargs["period"] = self.get_period(kwargs["period"])

        if key not in self.wss_paths or self.wss_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)
        req = self.wss_paths[key].copy()
        key = list(req.keys())[0]
        for k, v in kwargs.items():
            if isinstance(v, str):
                req[key] = [req[key][0].replace(f"<{k}>", v.lower())]
        new_value = []
        if "symbol_list" in kwargs:
            for symbol in kwargs["symbol_list"]:
                value = req[key]
                new_value.append(value[0].replace("<symbol>", self.get_symbol(symbol).lower()))
            req[key] = new_value
        return json.dumps(req)

    def to_json(self):
        content = self.to_dict()
        return json.dumps(content, indent=2)


class MexcExchangeDataSwap(MexcExchangeData):
    """MEXC USDT-M Futures (swap) Data Configuration."""

    def __init__(self) -> None:
        super().__init__()
        # Load configuration from YAML file
        config_loaded = self._load_from_config("swap")

        # Fallback values if config loading fails
        if not config_loaded:
            self.rest_url = "https://api.mexc.com"
            self.wss_url = "wss://wbs.mexc.com/ws"
            self.acct_wss_url = "wss://wbs.mexc.com/ws"

            # REST API paths
            self.rest_paths = {
                # General
                "ping": "GET /api/v1/ping",
                "get_server_time": "GET /api/v1/time",
                "get_contract": "GET /api/v1/exchangeInfo",
                # Market Data
                "get_tick": "GET /api/v1/ticker/bookTicker",
                "get_info": "GET /api/v1/ticker/24hr",
                "get_new_price": "GET /api/v1/trades",
                "get_historical_trades": "GET /api/v1/historicalTrades",
                "get_depth": "GET /api/v1/depth",
                "get_incre_depth": "GET /api/v1/depth",
                "get_kline": "GET /api/v1/klines",
                "get_agg_trades": "GET /api/v1/aggTrades",
                "get_funding_rate": "GET /api/v1/premiumIndex",
                "get_clear_price": "GET /api/v1/premiumIndex",
                "get_mark_price": "GET /api/v1/premiumIndex",
                "get_history_funding_rate": "GET /api/v1/fundingRate",
                "get_market_rate": "GET /api/v1/premiumIndex",
                "get_funding_info": "GET /api/v1/fundingInfo",
                "get_continuous_kline": "GET /api/v1/continuousKlines",
                "get_index_price_kline": "GET /api/v1/indexPriceKlines",
                "get_mark_price_kline": "GET /api/v1/markPriceKlines",
                "get_price_ticker": "GET /api/v2/ticker/price",
                "get_avg_price": "GET /api/v1/avgPrice",
                "get_ticker": "GET /api/v1/ticker",
                "get_open_interest": "GET /api/v1/openInterest",
                "get_delivery_price": "GET /api/v1/deliveryPrice",
                # Futures Data Endpoints
                "get_long_short_ratio": "GET /futures/data/globalLongShortAccountRatio",
                "get_top_long_short_account_ratio": "GET /futures/data/topLongShortAccountRatio",
                "get_top_long_short_position_ratio": "GET /futures/data/topLongShortPositionRatio",
                "get_taker_buy_sell_volume": "GET /futures/data/takerlongshortRatio",
                "get_open_interest_hist": "GET /futures/data/openInterestHist",
                "get_index_constituents": "GET /api/v1/constituents",
                # Account
                "get_account": "GET /api/v2/account",
                "get_balance": "GET /api/v2/balance",
                "get_position": "GET /api/v2/positionRisk",
                "get_fee": "GET /api/v1/commissionRate",
                "get_income": "GET /api/v1/income",
                "get_adl_quantile": "GET /api/v1/adlQuantile",
                "get_leverage_bracket": "GET /api/v1/leverageBracket",
                "get_position_mode": "GET /api/v1/positionSide/dual",
                "get_multi_assets_mode": "GET /api/v1/multiAssetsMargin",
                "get_api_trading_status": "GET /api/v1/apiTradingStatus",
                "get_api_key_permission": "GET /api/v1/apiKeyPermission",
                "get_order_rate_limit": "GET /api/v1/rateLimit/order",
                "get_force_orders": "GET /api/v1/forceOrders",
                "get_symbol_config": "GET /api/v1/symbolConfig",
                "get_account_config": "GET /api/v1/accountConfig",
                # Trade
                "make_order": "POST /api/v1/order",
                "make_order_test": "POST /api/v1/order/test",
                "make_orders": "POST /api/v1/batchOrders",
                "modify_order": "PUT /api/v1/order",
                "modify_orders": "PUT /api/v1/batchOrders",
                "cancel_order": "DELETE /api/v1/order",
                "cancel_orders": "DELETE /api/v1/batchOrders",
                "cancel_all": "DELETE /api/v1/allOpenOrders",
                "auto_cancel_all": "POST /api/v1/countdownCancelAll",
                "query_order": "GET /api/v1/order",
                "get_open_orders": "GET /api/v1/openOrders",
                "get_all_orders": "GET /api/v1/allOrders",
                "get_deals": "GET /api/v1/userTrades",
                "change_leverage": "POST /api/v1/leverage",
                "change_margin_type": "POST /api/v1/marginType",
                "change_position_mode": "POST /api/v1/positionSide/dual",
                "change_multi_assets_mode": "POST /api/v1/multiAssetsMargin",
                "modify_isolated_position_margin": "POST /api/v1/positionMargin",
                "get_position_margin_history": "GET /api/v1/positionMargin/history",
                # OCO
                "make_oco_order": "POST /api/v1/order/oco",
                "get_order_list": "GET /api/v1/orderList",
                "get_all_order_lists": "GET /api/v1/allOrderList",
                "get_open_order_lists": "GET /api/v1/openOrderList",
                "cancel_order_list": "DELETE /api/v1/orderList",
                # Listen Key
                "get_listen_key": "POST /api/v1/listenKey",
                "refresh_listen_key": "PUT /api/v1/listenKey",
                "close_listen_key": "DELETE /api/v1/listenKey",
            }

            # WebSocket paths
            self.wss_paths = {
                # Market Streams
                "agg_trade": "<symbol>@aggTrade",
                "trade": "<symbol>@trade",
                "kline": "<symbol>@kline_<period>",
                "continuous_kline": "<pair>_perpetual@continuousKline_<period>",
                "mini_ticker": "<symbol>@miniTicker",
                "ticker": "<symbol>@ticker",
                "ticker_window": "<symbol>@ticker_<window>",
                "book_ticker": "<symbol>@bookTicker",
                "depth": "<symbol>@depth20@100ms",
                "depth500": "<symbol>@depth5@500ms",
                "depth_partial": "<symbol>@depth<level>@100ms",
                "increDepthFlow": "<symbol>@depth@100ms",
                "mark_price": "<symbol>@markPrice@1s",
                "funding_rate": "<symbol>@markPrice@1s",
                "force_order": "<symbol>@forceOrder",
                # All Market Streams
                "all_force_order": "!forceOrder@arr",
                "all_mini_ticker": "!miniTicker@arr",
                "all_ticker": "!ticker@arr",
                "all_ticker_window": "!ticker_<window>@arr",
                "all_mark_price": "!markPrice@arr@1s",
                "all_book_ticker": "!bookTicker",
                # Aliases
                "tick": "<symbol>@bookTicker",
                "tick_all": "!bookTicker",
                "ticks": "!ticker@arr",
                "tickers": "!ticker@arr",
                "market": "!bookTicker",
                "bidAsk": "<symbol>@bookTicker",
                "clearPrice": "<symbol>@markPrice@1s",
                "liquidation": "<symbol>@forceOrder",
                "liquidation_order": "<symbol>@forceOrder",
                "contract_info": "!contractInfo",
                # User Data Streams
                "listen_key": "",
                "orders": "",
                "deals": "",
                "balance": "",
                "position": "",
                "account": "",
                "portfolio": "",
            }


class MexcExchangeDataMargin(MexcExchangeData):
    """MEXC Margin Trading Data Configuration."""

    def __init__(self) -> None:
        super().__init__()
        # Load configuration from YAML file
        config_loaded = self._load_from_config("margin")

        # Fallback values if config loading fails
        if not config_loaded:
            self.rest_url = "https://api.mexc.com"
            self.wss_url = "wss://wbs.mexc.com/ws"
            self.acct_wss_url = "wss://wbs.mexc.com/ws"

            # REST API paths
            self.rest_paths = {
                # General
                "ping": "GET /api/v3/ping",
                "get_server_time": "GET /api/v3/time",
                "get_contract": "GET /api/v3/exchangeInfo",
                "get_exchange_info": "GET /api/v3/exchangeInfo",
                # Market Data
                "get_tick": "GET /api/v3/ticker/bookTicker",
                "get_info": "GET /api/v3/ticker/24hr",
                "get_new_price": "GET /api/v3/trades",
                "get_historical_trades": "GET /api/v3/historicalTrades",
                "get_depth": "GET /api/v3/depth",
                "get_incre_depth": "GET /api/v1/depth",
                "get_kline": "GET /api/v3/klines",
                "get_ui_klines": "GET /api/v3/uiKlines",
                "get_agg_trades": "GET /api/v3/aggTrades",
                # Margin-specific
                "get_all_assets": "GET /sapi/v1/margin/allAssets",
                "get_all_pairs": "GET /sapi/v1/margin/allPairs",
                "get_isolated_all_pairs": "GET /sapi/v1/margin/isolated/allPairs",
                "get_price_index": "GET /sapi/v1/margin/priceIndex",
                "get_cross_margin_collateral_ratio": "GET /sapi/v1/margin/crossMarginCollateralRatio",
                "get_leverage_bracket": "GET /sapi/v1/margin/leverageBracket",
                "get_isolated_margin_tier": "GET /sapi/v1/margin/isolatedMarginTier",
                "get_margin_manual_liquidation": "GET /sapi/v1/margin/liquidationBureau/assets",
                # Account
                "get_account": "GET /sapi/v1/margin/account",
                "get_isolated_account": "GET /sapi/v1/margin/isolated/account",
                "get_isolated_account_limit": "GET /sapi/v1/margin/isolated/accountLimit",
                "get_max_borrowable": "GET /sapi/v1/margin/maxBorrowable",
                "get_max_transferable": "GET /sapi/v1/margin/maxTransferable",
                "get_interest_history": "GET /sapi/v1/margin/interestHistory",
                "get_interest_rate_history": "GET /sapi/v1/margin/interestRateHistory",
                "get_next_hourly_interest_rate": "GET /sapi/v1/margin/next-hourly-interest-rate",
                "get_cross_margin_data": "GET /sapi/v1/margin/crossMarginData",
                "get_isolated_margin_data": "GET /sapi/v1/margin/isolatedMarginData",
                "get_capital_flow": "GET /sapi/v1/margin/capital-flow",
                "get_bnb_burn": "GET /sapi/v1/bnbBurn",
                "get_bnb_burn_status": "GET /sapi/v1/bnbBurn/status",
                "toggle_bnb_burn": "POST /sapi/v1/bnbBurn",
                "get_trade_coeff": "GET /sapi/v1/margin/tradeCoeff",
                # Borrow & Repay
                "borrow_repay": "POST /sapi/v1/margin/borrow-repay",
                "get_borrow_repay_records": "GET /sapi/v1/margin/borrow-repay",
                "borrow": "POST /sapi/v1/margin/loan",
                "repay": "POST /sapi/v1/margin/repay",
                # Transfer
                "get_transfer_history": "GET /sapi/v1/margin/transfer",
                "transfer_to_margin": "POST /sapi/v1/margin/transfer",
                "transfer_to_spot": "POST /sapi/v1/margin/transfer",
                # Trade
                "make_order": "POST /sapi/v1/margin/order",
                "make_order_test": "POST /sapi/v1/margin/order/test",
                "make_order_oto": "POST /sapi/v1/margin/order/oto",
                "make_order_otoco": "POST /sapi/v1/margin/order/otoco",
                "cancel_order": "DELETE /sapi/v1/margin/order",
                "cancel_all": "DELETE /sapi/v1/margin/openOrders",
                "query_order": "GET /sapi/v1/margin/order",
                "get_open_orders": "GET /sapi/v1/margin/openOrders",
                "get_all_orders": "GET /sapi/v1/margin/allOrders",
                "get_deals": "GET /sapi/v1/margin/myTrades",
                "get_order_rate_limit": "GET /sapi/v1/margin/rateLimit/order",
                "manual_liquidation": "POST /sapi/v1/margin/manual-liquidation",
                "exchange_small_liability": "POST /sapi/v1/margin/exchange-small-liability",
                "get_small_liability_history": "GET /sapi/v1/margin/exchange-small-liability-history",
                "set_max_leverage": "POST /sapi/v1/margin/max-leverage",
                # Listen Key
                "get_listen_key": "POST /sapi/v1/margin/listen-key",
                "refresh_listen_key": "PUT /sapi/v1/margin/listen-key",
                "close_listen_key": "DELETE /sapi/v1/margin/listen-key",
            }

            # WebSocket paths
            self.wss_paths = {
                "agg_trade": "<symbol>@aggTrade",
                "trade": "<symbol>@trade",
                "kline": "<symbol>@kline_<period>",
                "mini_ticker": "<symbol>@miniTicker",
                "ticker": "<symbol>@ticker",
                "ticker_window": "<symbol>@ticker_<window>",
                "book_ticker": "<symbol>@bookTicker",
                "depth": "<symbol>@depth20@100ms",
                "depth_partial": "<symbol>@depth<level>@100ms",
                "increDepthFlow": "<symbol>@depth@100ms",
                "all_mini_ticker": "!miniTicker@arr",
                "all_ticker": "!ticker@arr",
                "all_ticker_window": "!ticker_<window>@arr",
                "tick": "<symbol>@bookTicker",
                "tick_all": "!bookTicker",
                "ticks": "!ticker@arr",
                "bidAsk": "<symbol>@bookTicker",
                "orders": "",
                "deals": "",
                "balance": "",
                "position": "",
                "account": "",
            }


class MexcExchangeDataSpot(MexcExchangeData):
    """MEXC Spot Trading Data Configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.asset_type = "SPOT"
        # Load configuration from YAML file
        config_loaded = self._load_from_config("spot")

        # Fallback values if config loading fails
        if not config_loaded:
            self.exchange_name = "MEXC___SPOT"
            self.rest_url = "https://api.mexc.com"
            self.wss_url = "wss://wbs.mexc.com/ws"
            self.acct_wss_url = "wss://wbs.mexc.com/ws"

            # REST API paths
            self.rest_paths = {
                # General
                "ping": "GET /api/v3/ping",
                "get_server_time": "GET /api/v3/time",
                "get_contract": "GET /api/v3/exchangeInfo",
                "get_exchange_info": "GET /api/v3/exchangeInfo",
                # Market Data
                "get_tick": "GET /api/v3/ticker/bookTicker",
                "get_depth": "GET /api/v3/depth",
                "get_order_book": "GET /api/v3/depth",
                "get_incre_depth": "GET /api/v1/depth",
                "get_kline": "GET /api/v3/klines",
                "get_klines": "GET /api/v3/klines",
                "get_avg_price": "GET /api/v3/avgPrice",
                "get_info": "GET /api/v3/ticker/24hr",
                "get_24hr_ticker": "GET /api/v3/ticker/24hr",
                "get_market": "GET /api/v3/ticker/price",
                "get_ticker": "GET /api/v3/ticker",
                "get_new_price": "GET /api/v3/trades",
                "get_recent_trades": "GET /api/v3/trades",
                "get_historical_trades": "GET /api/v3/historicalTrades",
                "get_agg_trades": "GET /api/v3/aggTrades",
                # Account
                "get_account": "GET /api/v3/account",
                "get_balance": "GET /api/v3/account",
                "get_fee": "GET /sapi/v1/asset/tradeFee",
                "get_commission": "GET /api/v3/account/commission",
                "get_order_rate_limit": "GET /api/v3/rateLimit/order",
                # Trade
                "make_order": "POST /api/v3/order",
                "make_order_test": "POST /api/v3/order/test",
                "cancel_order": "DELETE /api/v3/order",
                "cancel_all": "DELETE /api/v3/openOrders",
                "cancel_replace_order": "POST /api/v3/order/cancelReplace",
                "amend_keep_priority": "PUT /api/v3/order/amend/keepPriority",
                "query_order": "GET /api/v3/order",
                "get_order": "GET /api/v3/order",
                "get_open_orders": "GET /api/v3/openOrders",
                "get_all_orders": "GET /api/v3/allOrders",
                "get_deals": "GET /api/v3/myTrades",
                # OCO / OTO / OTOCO
                "make_oco_order": "POST /api/v3/order/oco",
                "get_order_list": "GET /api/v3/orderList",
                "get_all_order_lists": "GET /api/v3/allOrderList",
                "get_open_order_lists": "GET /api/v3/openOrderList",
                "cancel_order_list": "DELETE /api/v3/orderList",
                # Listen Key
                "get_listen_key": "POST /sapi/v1/userListenToken",
                "refresh_listen_key": "POST /sapi/v1/userListenToken",
            }

            # WebSocket paths
            self.wss_paths = {
                # Market Streams
                "agg_trade": "<symbol>@aggTrade",
                "trade": "<symbol>@trade",
                "kline": "<symbol>@kline_<period>",
                "mini_ticker": "<symbol>@miniTicker",
                "ticker": "<symbol>@ticker",
                "ticker_window": "<symbol>@ticker_<window>",
                "book_ticker": "<symbol>@bookTicker",
                "avg_price": "<symbol>@avgPrice",
                "depth": "<symbol>@depth20@100ms",
                "depth_partial": "<symbol>@depth<level>@100ms",
                "increDepthFlow": "<symbol>@depth@100ms",
                "force_order": "<symbol>@forceOrder",
                "kline_timezone": "<symbol>@kline_<period>@+08:00",
                # All Market Streams
                "all_mini_ticker": "!miniTicker@arr",
                "all_ticker": "!ticker@arr",
                "all_ticker_window": "!ticker_<window>@arr",
                "all_book_ticker": "!bookTicker",
                # Aliases
                "tick": "<symbol>@bookTicker",
                "tick_all": "!bookTicker",
                "ticks": "!ticker@arr",
                "market": "!bookTicker",
                "bidAsk": "<symbol>@bookTicker",
                "liquidation": "<symbol>@forceOrder",
                "liquidation_order": "<symbol>@forceOrder",
                # User Data Streams
                "orders": "",
                "deals": "",
                "balance": "",
                "position": "",
            }
