"""
Tests for IB Web API Exchange Data
测试 IB Web API 交易所配置数据类

验证所有配置数据均从 ib.yaml 加载, Python 代码中不残留硬编码值.
"""

from __future__ import annotations

import os

import pytest
import yaml

from bt_api_py.containers.exchanges.ib_web_exchange_data import (
    IbWebExchangeData,
    IbWebExchangeDataForex,
    IbWebExchangeDataFuture,
    IbWebExchangeDataOption,
    IbWebExchangeDataStock,
    _get_ib_config,
    _get_ib_raw_config,
    _get_ib_yaml_path,
)


# ─── helper: 直接读取 yaml 作为黄金基准 ─────────────────────────
@pytest.fixture(scope="module")
def yaml_data():
    """加载原始 ib.yaml 作为测试基准"""
    path = _get_ib_yaml_path()
    assert os.path.exists(path), f"ib.yaml not found at {path}"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ═══════════════════════════════════════════════════════════════
# 1. YAML 配置加载 (pydantic ExchangeConfig)
# ═══════════════════════════════════════════════════════════════


class TestYamlConfigLoading:
    """测试 ib.yaml 配置文件通过 pydantic 正确加载"""

    def test_config_loaded_successfully(self):
        config = _get_ib_config()
        assert config is not None, "ib.yaml config should load successfully"

    def test_config_id(self):
        assert _get_ib_config().id == "ib_web"

    def test_config_display_name(self):
        assert "Interactive Brokers" in _get_ib_config().display_name

    def test_config_venue_type(self):
        assert _get_ib_config().venue_type.value == "broker"

    def test_config_base_urls_rest(self):
        urls = _get_ib_config().base_urls
        assert urls is not None
        assert urls.rest["production"] == "https://api.interactivebrokers.com"
        assert urls.rest["testing"] == "https://api.test.interactivebrokers.com"
        assert urls.rest["gateway"] == "https://localhost:5000"
        assert urls.rest["default"] == "https://localhost:5000"

    def test_config_base_urls_wss(self):
        urls = _get_ib_config().base_urls
        assert urls.wss["default"] == "wss://localhost:5000/v1/api/ws"

    def test_config_connection(self):
        conn = _get_ib_config().connection
        assert conn.type.value == "http"
        assert conn.timeout == 10
        assert conn.max_retries == 3

    def test_config_authentication(self):
        auth = _get_ib_config().authentication
        assert auth is not None
        assert auth.type.value == "oauth"

    @pytest.mark.kline
    def test_config_kline_periods(self):
        kp = _get_ib_config().kline_periods
        assert kp is not None
        assert kp["1m"] == "1min"
        assert kp["1h"] == "1h"
        assert kp["1d"] == "1d"
        assert kp["1M"] == "1m"

    def test_config_status_dict(self):
        sd = _get_ib_config().status_dict
        assert sd["submitted"] == "submit"
        assert sd["filled"] == "filled"
        assert sd["cancelled"] == "cancel"
        assert sd["inactive"] == "inactive"
        assert sd["presubmitted"] == "presubmit"

    def test_config_rate_limits_count(self):
        assert len(_get_ib_config().rate_limits) >= 5

    def test_config_rate_limit_names(self):
        names = {r.name for r in _get_ib_config().rate_limits}
        for expected in (
            "global_trading",
            "cp_gateway",
            "account_management",
            "market_snapshot",
            "orders_query",
        ):
            assert expected in names

    def test_config_asset_types_keys(self):
        at = _get_ib_config().asset_types
        for key in ("stk", "fut", "opt", "cash"):
            assert key in at

    def test_config_asset_type_stk_name(self):
        assert _get_ib_config().asset_types["stk"].exchange_name == "IB_WEB_STK"

    def test_config_asset_type_fut_name(self):
        assert _get_ib_config().asset_types["fut"].exchange_name == "IB_WEB_FUT"

    def test_config_asset_type_opt_name(self):
        assert _get_ib_config().asset_types["opt"].exchange_name == "IB_WEB_OPT"

    def test_config_asset_type_cash_name(self):
        assert _get_ib_config().asset_types["cash"].exchange_name == "IB_WEB_CASH"

    def test_config_yaml_anchor_shared_paths(self):
        """YAML 锚点继承: 所有 asset_type 共享相同 rest_paths"""
        at = _get_ib_config().asset_types
        stk_paths = at["stk"].rest_paths
        for key in ("fut", "opt", "cash"):
            assert at[key].rest_paths == stk_paths, f"{key} rest_paths differs from stk"


# ═══════════════════════════════════════════════════════════════
# 2. 原始 YAML dict 加载 (pydantic 忽略的额外字段)
# ═══════════════════════════════════════════════════════════════


class TestRawYamlConfig:
    """测试 _get_ib_raw_config 返回完整 yaml dict (含 pydantic 忽略的字段)"""

    def test_raw_config_loaded(self):
        raw = _get_ib_raw_config()
        assert raw is not None
        assert isinstance(raw, dict)

    def test_raw_has_sec_type_map(self):
        raw = _get_ib_raw_config()
        assert "sec_type_map" in raw
        assert raw["sec_type_map"]["STK"] == "Stock"
        assert raw["sec_type_map"]["FUT"] == "Future"

    def test_raw_has_market_data_fields(self):
        raw = _get_ib_raw_config()
        assert "market_data_fields" in raw
        assert str(raw["market_data_fields"]["last_price"]) == "31"
        assert str(raw["market_data_fields"]["bid_price"]) == "84"

    def test_raw_has_default_snapshot_fields(self):
        raw = _get_ib_raw_config()
        assert "default_snapshot_fields" in raw
        assert "31" in [str(f) for f in raw["default_snapshot_fields"]]

    def test_raw_has_order_type_map(self):
        raw = _get_ib_raw_config()
        assert "order_type_map" in raw
        assert raw["order_type_map"]["market"] == "MKT"
        assert raw["order_type_map"]["limit"] == "LMT"

    def test_raw_has_tif_map(self):
        raw = _get_ib_raw_config()
        assert "tif_map" in raw
        assert raw["tif_map"]["day"] == "DAY"
        assert raw["tif_map"]["gtc"] == "GTC"


# ═══════════════════════════════════════════════════════════════
# 3. Python 值 == YAML 值 (验证无硬编码残留)
# ═══════════════════════════════════════════════════════════════


class TestAllFieldsFromYaml:
    """逐字段交叉校验: Python 对象的值 == ib.yaml 中的值"""

    @pytest.fixture
    def stk(self):
        return IbWebExchangeDataStock()

    @pytest.mark.kline
    def test_kline_periods_match_yaml(self, stk, yaml_data):
        for k, v in yaml_data["kline_periods"].items():
            assert stk.kline_periods[k] == v, f"kline_periods[{k}] mismatch"

    def test_status_dict_match_yaml(self, stk, yaml_data):
        for k, v in yaml_data["status_dict"].items():
            assert stk.status_dict[k] == v, f"status_dict[{k}] mismatch"

    def test_sec_type_map_match_yaml(self, stk, yaml_data):
        for k, v in yaml_data["sec_type_map"].items():
            assert stk.sec_type_map[k] == v, f"sec_type_map[{k}] mismatch"

    def test_market_data_fields_match_yaml(self, stk, yaml_data):
        for k, v in yaml_data["market_data_fields"].items():
            assert stk.market_data_fields[k] == str(v), f"market_data_fields[{k}] mismatch"

    def test_default_snapshot_fields_match_yaml(self, stk, yaml_data):
        expected = [str(f) for f in yaml_data["default_snapshot_fields"]]
        assert stk.default_snapshot_fields == expected

    def test_order_type_map_match_yaml(self, stk, yaml_data):
        for k, v in yaml_data["order_type_map"].items():
            assert stk.order_type_map[k] == v, f"order_type_map[{k}] mismatch"

    def test_tif_map_match_yaml(self, stk, yaml_data):
        for k, v in yaml_data["tif_map"].items():
            assert stk.tif_map[k] == v, f"tif_map[{k}] mismatch"

    def test_rest_paths_match_yaml(self, stk, yaml_data):
        yaml_stk_paths = yaml_data["asset_types"]["stk"]["rest_paths"]
        for k, v in yaml_stk_paths.items():
            assert stk.rest_paths[k] == v, f"rest_paths[{k}] mismatch"

    def test_wss_paths_match_yaml(self, stk, yaml_data):
        yaml_stk_wss = yaml_data["asset_types"]["stk"]["wss_paths"]
        for k, v in yaml_stk_wss.items():
            assert stk.wss_paths[k] == v, f"wss_paths[{k}] mismatch"

    def test_rest_url_match_yaml(self, stk, yaml_data):
        assert stk.rest_url == yaml_data["base_urls"]["rest"]["default"]

    def test_wss_url_match_yaml(self, stk, yaml_data):
        assert stk.wss_url == yaml_data["base_urls"]["wss"]["default"]

    def test_rate_limits_match_yaml(self, stk, yaml_data):
        """rate_limits_config 中的值 == yaml limit/interval"""
        for rule in yaml_data["rate_limits"]:
            name = rule["name"]
            expected = rule["limit"] / rule["interval"]
            assert stk.rate_limits_config[name] == pytest.approx(expected), (
                f"rate_limits_config[{name}] mismatch"
            )


# ═══════════════════════════════════════════════════════════════
# 4. 基类 IbWebExchangeData
# ═══════════════════════════════════════════════════════════════


class TestIbWebExchangeDataBase:
    """测试 IbWebExchangeData 基类属性和方法"""

    @pytest.fixture
    def base(self):
        return IbWebExchangeData()

    # 类常量
    def test_class_constants(self, base):
        assert base.PROD_REST_URL == "https://api.interactivebrokers.com"
        assert base.TEST_REST_URL == "https://api.test.interactivebrokers.com"
        assert base.GATEWAY_REST_URL == "https://localhost:5000"

    def test_rest_url_loaded(self, base):
        assert base.rest_url == "https://localhost:5000"

    # kline_periods
    @pytest.mark.kline
    def test_kline_periods_not_empty(self, base):
        assert len(base.kline_periods) > 0

    @pytest.mark.kline
    def test_kline_periods_keys(self, base):
        for k in ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w", "1M"):
            assert k in base.kline_periods

    @pytest.mark.kline
    def test_reverse_kline_periods(self, base):
        for v, k in base.reverse_kline_periods.items():
            assert base.kline_periods[k] == v

    def test_get_period_known(self, base):
        assert base.get_period("1h") == "1h"
        assert base.get_period("1m") == "1min"

    def test_get_period_unknown(self, base):
        assert base.get_period("unknown") == "unknown"

    # sec_type_map
    def test_sec_type_map_not_empty(self, base):
        assert len(base.sec_type_map) > 0

    def test_sec_type_map_values(self, base):
        assert base.sec_type_map["STK"] == "Stock"
        assert base.sec_type_map["FUT"] == "Future"
        assert base.sec_type_map["OPT"] == "Option"
        assert base.sec_type_map["FOP"] == "FutureOption"
        assert base.sec_type_map["CASH"] == "Forex"
        assert base.sec_type_map["CFD"] == "CFD"
        assert base.sec_type_map["BOND"] == "Bond"
        assert base.sec_type_map["CRYPTO"] == "Crypto"

    # market_data_fields
    def test_market_data_fields_not_empty(self, base):
        assert len(base.market_data_fields) > 0

    def test_market_data_fields_core(self, base):
        assert base.market_data_fields["last_price"] == "31"
        assert base.market_data_fields["bid_price"] == "84"
        assert base.market_data_fields["bid_size"] == "85"
        assert base.market_data_fields["ask_price"] == "86"
        assert base.market_data_fields["ask_size"] == "88"
        assert base.market_data_fields["volume"] == "7059"

    def test_market_data_fields_ohlc(self, base):
        assert base.market_data_fields["open"] == "7295"
        assert base.market_data_fields["high"] == "70"
        assert base.market_data_fields["low"] == "71"
        assert base.market_data_fields["close"] == "7291"

    def test_market_data_fields_change(self, base):
        assert base.market_data_fields["change"] == "82"
        assert base.market_data_fields["change_pct"] == "83"

    # default_snapshot_fields
    def test_default_snapshot_fields_not_empty(self, base):
        assert len(base.default_snapshot_fields) >= 6

    def test_default_snapshot_fields_content(self, base):
        for f in ("31", "84", "85", "86", "88", "7059"):
            assert f in base.default_snapshot_fields

    # order_type_map
    def test_order_type_map_not_empty(self, base):
        assert len(base.order_type_map) > 0

    def test_order_type_map_values(self, base):
        assert base.order_type_map["market"] == "MKT"
        assert base.order_type_map["limit"] == "LMT"
        assert base.order_type_map["stop"] == "STP"
        assert base.order_type_map["stop_limit"] == "STP_LMT"

    # tif_map
    def test_tif_map_not_empty(self, base):
        assert len(base.tif_map) > 0

    def test_tif_map_values(self, base):
        assert base.tif_map["day"] == "DAY"
        assert base.tif_map["gtc"] == "GTC"
        assert base.tif_map["ioc"] == "IOC"
        assert base.tif_map["gtd"] == "GTD"

    # status_dict
    def test_status_dict_not_empty(self, base):
        assert len(base.status_dict) > 0

    def test_status_dict_values(self, base):
        assert base.status_dict["submitted"] == "submit"
        assert base.status_dict["filled"] == "filled"
        assert base.status_dict["cancelled"] == "cancel"
        assert base.status_dict["inactive"] == "inactive"
        assert base.status_dict["presubmitted"] == "presubmit"

    # rate_limits_config
    def test_rate_limits_config_not_empty(self, base):
        assert len(base.rate_limits_config) > 0

    # get_symbol
    def test_get_symbol(self, base):
        assert base.get_symbol("AAPL") == "AAPL"
        assert base.get_symbol("EUR.USD") == "EUR.USD"

    # get_snapshot_fields_str
    def test_get_snapshot_fields_str_default(self, base):
        result = base.get_snapshot_fields_str()
        assert "31" in result
        assert "," in result

    def test_get_snapshot_fields_str_custom(self, base):
        assert base.get_snapshot_fields_str(fields=["31", "84"]) == "31,84"


# ═══════════════════════════════════════════════════════════════
# 5. REST 路径
# ═══════════════════════════════════════════════════════════════


class TestRestPaths:
    """测试 REST 端点路径"""

    @pytest.fixture
    def stk(self):
        return IbWebExchangeDataStock()

    def test_rest_paths_not_empty(self, stk):
        assert len(stk.rest_paths) >= 40

    # 会话管理
    def test_path_auth_status(self, stk):
        assert stk.rest_paths["auth_status"] == "POST /iserver/auth/status"

    def test_path_reauthenticate(self, stk):
        assert stk.rest_paths["reauthenticate"] == "POST /iserver/reauthenticate"

    def test_path_oauth_token(self, stk):
        assert stk.rest_paths["oauth_token"] == "POST /oauth/token"

    # 合约搜索
    def test_path_search_stocks(self, stk):
        assert stk.rest_paths["search_stocks"] == "GET /trsrv/stocks"

    def test_path_search_futures(self, stk):
        assert stk.rest_paths["search_futures"] == "GET /trsrv/futures"

    def test_path_secdef_search(self, stk):
        assert stk.rest_paths["secdef_search"] == "GET /iserver/secdef/search"

    def test_path_secdef_strikes(self, stk):
        assert stk.rest_paths["secdef_strikes"] == "GET /iserver/secdef/strikes"

    def test_path_secdef_info(self, stk):
        assert stk.rest_paths["secdef_info"] == "GET /iserver/secdef/info"

    # 市场数据
    def test_path_market_snapshot(self, stk):
        assert stk.rest_paths["market_snapshot"] == "GET /iserver/marketdata/snapshot"

    def test_path_market_history(self, stk):
        assert stk.rest_paths["market_history"] == "GET /iserver/marketdata/history"

    @pytest.mark.ticker
    def test_path_get_tick(self, stk):
        assert stk.rest_paths["get_tick"] == "GET /iserver/marketdata/snapshot"

    @pytest.mark.orderbook
    def test_path_get_depth(self, stk):
        assert stk.rest_paths["get_depth"] == "GET /iserver/marketdata/snapshot"

    @pytest.mark.kline
    def test_path_get_kline(self, stk):
        assert stk.rest_paths["get_kline"] == "GET /iserver/marketdata/history"

    # 订单管理
    def test_path_make_order(self, stk):
        assert stk.rest_paths["make_order"] == "POST /iserver/account/{accountId}/orders"

    def test_path_modify_order(self, stk):
        assert stk.rest_paths["modify_order"] == "POST /iserver/account/{accountId}/order/{orderId}"

    def test_path_cancel_order(self, stk):
        assert (
            stk.rest_paths["cancel_order"] == "DELETE /iserver/account/{accountId}/order/{orderId}"
        )

    def test_path_get_open_orders(self, stk):
        assert stk.rest_paths["get_open_orders"] == "GET /iserver/account/orders"

    def test_path_order_reply(self, stk):
        assert stk.rest_paths["order_reply"] == "POST /iserver/reply/{messageId}"

    def test_path_get_deals(self, stk):
        assert stk.rest_paths["get_deals"] == "GET /iserver/trades"

    # 持仓和账户
    def test_path_portfolio_accounts(self, stk):
        assert stk.rest_paths["portfolio_accounts"] == "GET /portfolio/accounts"

    def test_path_get_position(self, stk):
        assert stk.rest_paths["get_position"] == "GET /portfolio/{accountId}/positions"

    def test_path_get_account(self, stk):
        assert stk.rest_paths["get_account"] == "GET /portfolio/{accountId}/summary"

    def test_path_get_balance(self, stk):
        assert stk.rest_paths["get_balance"] == "GET /portfolio/{accountId}/ledger"

    # 账户管理 API
    def test_path_get_accounts_list(self, stk):
        assert stk.rest_paths["get_accounts_list"] == "GET /gw/api/v1/accounts"

    def test_path_get_account_detail(self, stk):
        assert stk.rest_paths["get_account_detail"] == "GET /gw/api/v1/accounts/{accountId}"

    def test_path_update_account(self, stk):
        assert stk.rest_paths["update_account"] == "PATCH /gw/api/v1/accounts/{accountId}"

    def test_path_close_account(self, stk):
        assert stk.rest_paths["close_account"] == "POST /gw/api/v1/accounts/{accountId}/close"

    def test_path_bank_instructions(self, stk):
        assert stk.rest_paths["bank_instructions"] == "GET /gw/api/v1/bank-instructions/query"

    def test_path_withdraw_request(self, stk):
        assert stk.rest_paths["withdraw_request"] == "POST /gw/api/v1/withdraw-request"

    def test_path_deposit_request(self, stk):
        assert stk.rest_paths["deposit_request"] == "POST /gw/api/v1/deposit-request"

    def test_path_internal_transfer(self, stk):
        assert stk.rest_paths["internal_transfer"] == "POST /gw/api/v1/internal-transfer"

    def test_path_statements(self, stk):
        assert stk.rest_paths["statements"] == "GET /gw/api/v1/statements"

    def test_path_tax_documents(self, stk):
        assert stk.rest_paths["tax_documents"] == "GET /gw/api/v1/tax-documents/available"

    def test_path_trade_confirmations(self, stk):
        assert stk.rest_paths["trade_confirmations"] == "GET /gw/api/v1/trade-confirmations"

    def test_path_sso_url(self, stk):
        assert stk.rest_paths["sso_url"] == "GET /gw/api/v1/sso/url"

    # 通知
    def test_path_fyi_unread(self, stk):
        assert stk.rest_paths["fyi_unread"] == "GET /fyi/unreadnumber"

    def test_path_fyi_notifications(self, stk):
        assert stk.rest_paths["fyi_notifications"] == "GET /fyi/notifications"

    def test_path_fyi_settings(self, stk):
        assert stk.rest_paths["fyi_settings"] == "GET /fyi/settings"

    # 不存在的路径
    def test_path_not_found_raises(self, stk):
        with pytest.raises(NotImplementedError):
            stk.get_rest_path("nonexistent_path")


# ═══════════════════════════════════════════════════════════════
# 6. get_rest_url 方法
# ═══════════════════════════════════════════════════════════════


class TestGetRestUrl:
    """测试 get_rest_url 解析 METHOD + 路径 + 参数替换"""

    @pytest.fixture
    def stk(self):
        return IbWebExchangeDataStock()

    def test_simple_get(self, stk):
        method, url = stk.get_rest_url("portfolio_accounts")
        assert method == "GET"
        assert url == "https://localhost:5000/portfolio/accounts"

    def test_get_with_account_param(self, stk):
        method, url = stk.get_rest_url("get_position", accountId="U1234567")
        assert method == "GET"
        assert url == "https://localhost:5000/portfolio/U1234567/positions"

    def test_delete_with_two_params(self, stk):
        method, url = stk.get_rest_url("cancel_order", accountId="U1234567", orderId="99999")
        assert method == "DELETE"
        assert url == "https://localhost:5000/iserver/account/U1234567/order/99999"

    def test_post_with_account_param(self, stk):
        method, url = stk.get_rest_url("make_order", accountId="U1234567")
        assert method == "POST"
        assert url == "https://localhost:5000/iserver/account/U1234567/orders"

    def test_post_modify_order(self, stk):
        method, url = stk.get_rest_url("modify_order", accountId="U1234567", orderId="12345")
        assert method == "POST"
        assert url == "https://localhost:5000/iserver/account/U1234567/order/12345"

    def test_patch_method(self, stk):
        method, url = stk.get_rest_url("update_account", accountId="U1234567")
        assert method == "PATCH"
        assert url == "https://localhost:5000/gw/api/v1/accounts/U1234567"

    def test_post_auth_status(self, stk):
        method, url = stk.get_rest_url("auth_status")
        assert method == "POST"
        assert url == "https://localhost:5000/iserver/auth/status"

    def test_post_order_reply(self, stk):
        method, url = stk.get_rest_url("order_reply", messageId="abc123")
        assert method == "POST"
        assert url == "https://localhost:5000/iserver/reply/abc123"

    def test_get_balance(self, stk):
        method, url = stk.get_rest_url("get_balance", accountId="U9999999")
        assert method == "GET"
        assert url == "https://localhost:5000/portfolio/U9999999/ledger"

    def test_get_account_detail(self, stk):
        method, url = stk.get_rest_url("get_account_detail", accountId="U1234567")
        assert method == "GET"
        assert url == "https://localhost:5000/gw/api/v1/accounts/U1234567"


# ═══════════════════════════════════════════════════════════════
# 7. WebSocket 路径
# ═══════════════════════════════════════════════════════════════


class TestWssPaths:
    """测试 WebSocket 路径模板"""

    @pytest.fixture
    def stk(self):
        return IbWebExchangeDataStock()

    def test_wss_paths_count(self, stk):
        assert len(stk.wss_paths) == 7

    def test_wss_market_data(self, stk):
        assert stk.wss_paths["market_data"] == "smd+{conid}+{fields_json}"

    def test_wss_unsubscribe(self, stk):
        assert stk.wss_paths["unsubscribe"] == "umd+{conid}+{}"

    def test_wss_account(self, stk):
        assert stk.wss_paths["account"] == "sacct"

    def test_wss_orders(self, stk):
        assert stk.wss_paths["orders"] == "sor"

    def test_wss_pnl(self, stk):
        assert stk.wss_paths["pnl"] == "spl+{accountId}"

    def test_wss_trades(self, stk):
        assert stk.wss_paths["trades"] == "str"

    def test_wss_heartbeat(self, stk):
        assert stk.wss_paths["heartbeat"] == "tic"

    def test_get_wss_path(self, stk):
        assert stk.get_wss_path() == "wss://localhost:5000/v1/api/ws"


# ═══════════════════════════════════════════════════════════════
# 8. 子类 exchange_name
# ═══════════════════════════════════════════════════════════════


class TestSubclassExchangeNames:
    """测试各子类正确设置 exchange_name"""

    def test_stock(self):
        assert IbWebExchangeDataStock().exchange_name == "IB_WEB_STK"

    def test_future(self):
        assert IbWebExchangeDataFuture().exchange_name == "IB_WEB_FUT"

    def test_option(self):
        assert IbWebExchangeDataOption().exchange_name == "IB_WEB_OPT"

    def test_forex(self):
        assert IbWebExchangeDataForex().exchange_name == "IB_WEB_CASH"


# ═══════════════════════════════════════════════════════════════
# 9. 子类共享配置一致性
# ═══════════════════════════════════════════════════════════════


class TestSubclassConsistency:
    """测试各子类共享相同的基础配置 (全部来自 yaml)"""

    @pytest.fixture
    def all_instances(self):
        return {
            "stk": IbWebExchangeDataStock(),
            "fut": IbWebExchangeDataFuture(),
            "opt": IbWebExchangeDataOption(),
            "cash": IbWebExchangeDataForex(),
        }

    def test_same_rest_url(self, all_instances):
        urls = {v.rest_url for v in all_instances.values()}
        assert len(urls) == 1

    def test_same_wss_url(self, all_instances):
        urls = {v.wss_url for v in all_instances.values()}
        assert len(urls) == 1

    @pytest.mark.kline
    def test_same_kline_periods(self, all_instances):
        ref = list(all_instances.values())[0].kline_periods
        for v in all_instances.values():
            assert v.kline_periods == ref

    def test_same_status_dict(self, all_instances):
        ref = list(all_instances.values())[0].status_dict
        for v in all_instances.values():
            assert v.status_dict == ref

    def test_same_sec_type_map(self, all_instances):
        ref = list(all_instances.values())[0].sec_type_map
        for v in all_instances.values():
            assert v.sec_type_map == ref

    def test_same_market_data_fields(self, all_instances):
        ref = list(all_instances.values())[0].market_data_fields
        for v in all_instances.values():
            assert v.market_data_fields == ref

    def test_same_default_snapshot_fields(self, all_instances):
        ref = list(all_instances.values())[0].default_snapshot_fields
        for v in all_instances.values():
            assert v.default_snapshot_fields == ref

    def test_same_order_type_map(self, all_instances):
        ref = list(all_instances.values())[0].order_type_map
        for v in all_instances.values():
            assert v.order_type_map == ref

    def test_same_tif_map(self, all_instances):
        ref = list(all_instances.values())[0].tif_map
        for v in all_instances.values():
            assert v.tif_map == ref

    def test_same_rate_limits_config(self, all_instances):
        ref = list(all_instances.values())[0].rate_limits_config
        for v in all_instances.values():
            assert v.rate_limits_config == ref

    def test_same_rest_paths_keys(self, all_instances):
        ref = set(list(all_instances.values())[0].rest_paths.keys())
        for v in all_instances.values():
            assert set(v.rest_paths.keys()) == ref

    def test_same_wss_paths_keys(self, all_instances):
        ref = set(list(all_instances.values())[0].wss_paths.keys())
        for v in all_instances.values():
            assert set(v.wss_paths.keys()) == ref

    def test_different_exchange_names(self, all_instances):
        names = [v.exchange_name for v in all_instances.values()]
        assert len(set(names)) == 4


# ═══════════════════════════════════════════════════════════════
# 10. 速率限制
# ═══════════════════════════════════════════════════════════════


class TestRateLimits:
    """测试速率限制配置 (从 yaml rate_limits 转换而来)"""

    @pytest.fixture
    def base(self):
        return IbWebExchangeData()

    def test_rate_limits_not_empty(self, base):
        assert len(base.rate_limits_config) >= 5

    def test_global_trading_limit(self, base):
        assert base.rate_limits_config["global_trading"] == pytest.approx(50.0)

    def test_cp_gateway_limit(self, base):
        assert base.rate_limits_config["cp_gateway"] == pytest.approx(10.0)

    def test_account_management_limit(self, base):
        assert base.rate_limits_config["account_management"] == pytest.approx(10.0)

    def test_market_snapshot_limit(self, base):
        assert base.rate_limits_config["market_snapshot"] == pytest.approx(10.0)

    def test_orders_query_limit(self, base):
        assert base.rate_limits_config["orders_query"] == pytest.approx(0.2)


# ═══════════════════════════════════════════════════════════════
# 11. ExchangeData 继承
# ═══════════════════════════════════════════════════════════════


class TestExchangeDataInheritance:
    """测试与 ExchangeData 基类的兼容性"""

    def test_is_exchange_data_instance(self):
        from bt_api_py.containers.exchanges.exchange_data import ExchangeData

        for cls in (
            IbWebExchangeDataStock,
            IbWebExchangeDataFuture,
            IbWebExchangeDataOption,
            IbWebExchangeDataForex,
        ):
            assert isinstance(cls(), ExchangeData)

    def test_to_dict(self):
        d = IbWebExchangeDataStock().to_dict()
        assert isinstance(d, dict)
        for key in ("exchange_name", "rest_url", "kline_periods"):
            assert key in d

    def test_raise_path_error(self):
        with pytest.raises(NotImplementedError):
            IbWebExchangeDataStock().raise_path_error("IB_WEB_STK", "nonexistent")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
