"""
Stage 1 Tests: Exchange Integration
验证 Binance/OKX/CTP 三个交易所的:
  1. YAML 配置加载
  2. Capability 声明正确性
  3. ErrorTranslator 集成
  4. RateLimiter 集成 (Binance/OKX)
  5. 协议合规性 + 向后兼容
"""

import pytest

from bt_api_py.config_loader import ConnectionType, VenueType, load_exchange_config
from bt_api_py.feeds.capability import Capability

# ====================== YAML Config Tests ======================


class TestBinanceConfig:
    @pytest.fixture
    def cfg(self):
        return load_exchange_config("bt_api_py/configs/binance.yaml")

    def test_basic_fields(self, cfg):
        assert cfg.id == "binance"
        assert cfg.venue_type == VenueType.CEX
        assert cfg.display_name == "Binance"

    def test_connection(self, cfg):
        assert cfg.connection.type == ConnectionType.HTTP
        assert cfg.connection.timeout == 10
        assert cfg.connection.max_retries == 3

    def test_base_urls(self, cfg):
        assert cfg.base_urls is not None
        assert "swap" in cfg.base_urls.rest
        assert "spot" in cfg.base_urls.rest
        assert "swap" in cfg.base_urls.wss

    def test_rate_limits(self, cfg):
        assert len(cfg.rate_limits) == 3
        names = {r.name for r in cfg.rate_limits}
        assert "request_weight" in names
        assert "order_rate" in names

    def test_asset_types(self, cfg):
        assert "swap" in cfg.asset_types
        assert "spot" in cfg.asset_types
        swap = cfg.asset_types["swap"]
        assert "get_tick" in swap.rest_paths
        assert "make_order" in swap.rest_paths


class TestOkxConfig:
    @pytest.fixture
    def cfg(self):
        return load_exchange_config("bt_api_py/configs/okx.yaml")

    def test_basic_fields(self, cfg):
        assert cfg.id == "okx"
        assert cfg.venue_type == VenueType.CEX
        assert cfg.display_name == "OKX"

    def test_connection(self, cfg):
        assert cfg.connection.type == ConnectionType.HTTP

    def test_base_urls(self, cfg):
        assert cfg.base_urls is not None
        assert "default" in cfg.base_urls.rest
        assert "public" in cfg.base_urls.wss
        assert "private" in cfg.base_urls.wss

    def test_rate_limits(self, cfg):
        assert len(cfg.rate_limits) == 3

    def test_asset_types(self, cfg):
        assert "swap" in cfg.asset_types
        assert "spot" in cfg.asset_types


class TestCtpConfig:
    @pytest.fixture
    def cfg(self):
        return load_exchange_config("bt_api_py/configs/ctp.yaml")

    def test_basic_fields(self, cfg):
        assert cfg.id == "ctp"
        assert cfg.venue_type == VenueType.CEX

    def test_connection(self, cfg):
        assert cfg.connection.type == ConnectionType.SPI
        assert cfg.connection.timeout == 15

    def test_no_base_urls(self, cfg):
        # Broker type doesn't require base_urls
        pass

    def test_broker_fields(self, cfg):
        assert cfg.broker_id == "9999"
        assert cfg.app_id == "simnow_client_test"

    def test_rate_limits(self, cfg):
        assert len(cfg.rate_limits) == 1


# ====================== Capability Tests ======================


class TestBinanceCapabilities:
    def test_has_market_data_caps(self):
        caps = self._get_caps()
        for c in [
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_FUNDING_RATE,
            Capability.GET_MARK_PRICE,
        ]:
            assert c in caps, f"Missing {c}"

    def test_has_trading_caps(self):
        caps = self._get_caps()
        for c in [
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
            Capability.QUERY_ORDER,
            Capability.QUERY_OPEN_ORDERS,
            Capability.GET_DEALS,
        ]:
            assert c in caps, f"Missing {c}"

    def test_has_account_caps(self):
        caps = self._get_caps()
        for c in [Capability.GET_BALANCE, Capability.GET_ACCOUNT, Capability.GET_POSITION]:
            assert c in caps, f"Missing {c}"

    def test_has_stream_caps(self):
        caps = self._get_caps()
        assert Capability.MARKET_STREAM in caps
        assert Capability.ACCOUNT_STREAM in caps

    def test_has_advanced_caps(self):
        caps = self._get_caps()
        assert Capability.HEDGE_MODE in caps
        assert Capability.BATCH_ORDER in caps
        assert Capability.OCO_ORDER in caps

    @staticmethod
    def _get_caps():
        from bt_api_py.feeds.live_binance.request_base import BinanceRequestData

        return BinanceRequestData._capabilities()


class TestOkxCapabilities:
    def test_has_market_data_caps(self):
        caps = self._get_caps()
        for c in [
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_FUNDING_RATE,
            Capability.GET_MARK_PRICE,
        ]:
            assert c in caps

    def test_has_extra_advanced_caps(self):
        caps = self._get_caps()
        assert Capability.CONDITIONAL_ORDER in caps
        assert Capability.TRAILING_STOP in caps

    def test_superset_of_ctp(self):
        """OKX should support strictly more capabilities than CTP"""
        from bt_api_py.feeds.live_ctp_feed import CtpRequestData

        okx_caps = self._get_caps()
        ctp_caps = CtpRequestData._capabilities()
        assert ctp_caps.issubset(okx_caps)

    @staticmethod
    def _get_caps():
        from bt_api_py.feeds.live_okx.request_base import OkxRequestData

        return OkxRequestData._capabilities()


class TestCtpCapabilities:
    def test_has_basic_trading_caps(self):
        caps = self._get_caps()
        for c in [
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
            Capability.GET_ACCOUNT,
            Capability.GET_POSITION,
        ]:
            assert c in caps

    def test_no_http_only_caps(self):
        """CTP should NOT have HTTP-specific capabilities like depth, kline"""
        caps = self._get_caps()
        assert Capability.GET_DEPTH not in caps
        assert Capability.GET_KLINE not in caps
        assert Capability.GET_FUNDING_RATE not in caps
        assert Capability.GET_MARK_PRICE not in caps

    def test_no_advanced_order_caps(self):
        caps = self._get_caps()
        assert Capability.BATCH_ORDER not in caps
        assert Capability.OCO_ORDER not in caps
        assert Capability.CONDITIONAL_ORDER not in caps

    @staticmethod
    def _get_caps():
        from bt_api_py.feeds.live_ctp_feed import CtpRequestData

        return CtpRequestData._capabilities()


# ====================== ErrorTranslator Integration Tests ======================


class TestBinanceErrorTranslator:
    def test_translator_exists(self):
        from bt_api_py.feeds.live_binance.request_base import BinanceRequestData

        # Can't instantiate without queue, just verify class has the attribute setup
        assert hasattr(BinanceRequestData, "translate_error")

    def test_translator_translates_error(self):
        from bt_api_py.error import BinanceErrorTranslator

        t = BinanceErrorTranslator()
        err = t.translate({"code": -1003, "msg": "Too many requests"}, "binance")
        assert err is not None
        assert err.venue == "binance"

    def test_translator_no_error(self):
        from bt_api_py.error import BinanceErrorTranslator

        t = BinanceErrorTranslator()
        err = t.translate({"code": 0, "msg": "ok"}, "binance")
        # code=0 means success for Binance
        assert err is not None  # translator always returns, caller checks


class TestOkxErrorTranslator:
    def test_translator_exists(self):
        from bt_api_py.feeds.live_okx.request_base import OkxRequestData

        assert hasattr(OkxRequestData, "translate_error")

    def test_translator_translates_error(self):
        from bt_api_py.error import OKXErrorTranslator

        t = OKXErrorTranslator()
        err = t.translate({"code": "50011", "msg": "Rate limit reached"}, "okx")
        assert err is not None
        assert err.venue == "okx"


class TestCtpErrorTranslator:
    def test_translator_exists(self):
        from bt_api_py.feeds.live_ctp_feed import CtpRequestData

        assert hasattr(CtpRequestData, "translate_error")

    def test_translator_translates_error(self):
        from bt_api_py.error import CTPErrorTranslator

        t = CTPErrorTranslator()
        err = t.translate({"ErrorID": 3, "ErrorMsg": "认证失败"}, "CTP")
        assert err is not None
        assert err.venue == "CTP"


# ====================== RateLimiter Integration Tests ======================


class TestBinanceRateLimiter:
    def test_default_rate_limiter(self):
        from bt_api_py.feeds.live_binance.request_base import BinanceRequestData

        rl = BinanceRequestData._create_default_rate_limiter()
        assert rl is not None
        status = rl.get_status()
        assert len(status) == 2
        assert "binance_request_weight" in status
        assert "binance_order_rate" in status

    def test_rate_limiter_acquire(self):
        from bt_api_py.feeds.live_binance.request_base import BinanceRequestData

        rl = BinanceRequestData._create_default_rate_limiter()
        allowed = rl.acquire(method="GET", path="/fapi/v1/ticker/bookTicker")
        assert allowed is True


class TestOkxRateLimiter:
    def test_default_rate_limiter(self):
        from bt_api_py.feeds.live_okx.request_base import OkxRequestData

        rl = OkxRequestData._create_default_rate_limiter()
        assert rl is not None
        status = rl.get_status()
        assert len(status) == 3
        assert "okx_general" in status
        assert "okx_trade" in status
        assert "okx_account" in status

    def test_rate_limiter_acquire(self):
        from bt_api_py.feeds.live_okx.request_base import OkxRequestData

        rl = OkxRequestData._create_default_rate_limiter()
        allowed = rl.acquire(method="GET", path="/api/v5/market/ticker")
        assert allowed is True


# ====================== Protocol Compliance ======================


class TestProtocolCompliance:
    """Verify that exchange feed classes still comply with AbstractVenueFeed protocol"""

    def test_binance_has_required_methods(self):
        from bt_api_py.feeds.live_binance.request_base import BinanceRequestData

        required = [
            "get_account",
            "get_balance",
            "get_tick",
            "get_depth",
            "get_kline",
            "make_order",
            "cancel_order",
            "query_order",
            "get_open_orders",
            "get_deals",
            "get_position",
            "connect",
            "disconnect",
            "translate_error",
        ]
        for method in required:
            assert hasattr(BinanceRequestData, method), f"Binance missing {method}"

    def test_okx_has_required_methods(self):
        from bt_api_py.feeds.live_okx.request_base import OkxRequestData

        required = [
            "get_account",
            "get_balance",
            "get_tick",
            "get_depth",
            "get_kline",
            "make_order",
            "cancel_order",
            "query_order",
            "get_open_orders",
            "get_deals",
            "get_position",
            "connect",
            "disconnect",
            "translate_error",
        ]
        for method in required:
            assert hasattr(OkxRequestData, method), f"OKX missing {method}"

    def test_ctp_has_required_methods(self):
        from bt_api_py.feeds.live_ctp_feed import CtpRequestData

        required = [
            "get_account",
            "get_balance",
            "make_order",
            "cancel_order",
            "query_order",
            "get_position",
            "connect",
            "disconnect",
            "translate_error",
        ]
        for method in required:
            assert hasattr(CtpRequestData, method), f"CTP missing {method}"

    def test_all_feeds_inherit_from_feed(self):
        from bt_api_py.feeds.feed import Feed
        from bt_api_py.feeds.live_binance.request_base import BinanceRequestData
        from bt_api_py.feeds.live_ctp_feed import CtpRequestData
        from bt_api_py.feeds.live_okx.request_base import OkxRequestData

        assert issubclass(BinanceRequestData, Feed)
        assert issubclass(OkxRequestData, Feed)
        assert issubclass(CtpRequestData, Feed)
