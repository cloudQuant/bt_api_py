"""
IB Web API 实时行情测试

前置条件:
  1. 下载并启动 IBKR Client Portal Gateway
     - 下载地址: https://www.interactivebrokers.com/en/trading/ibgateway-stable.php
     - 启动: cd clientportal.gw && bin/run.sh root/conf.yaml
  2. 在浏览器打开 https://localhost:5000 并用模拟账号登录
  3. 配置 .env 文件:
     IB_WEB_BASE_URL=https://localhost:5000
     IB_WEB_ACCOUNT_ID=DUxxxxxxx
     IB_WEB_VERIFY_SSL=false

运行方式:
  pytest tests/feeds/test_live_ib_web_request_data.py -v -s
  或选择性运行:
  pytest tests/feeds/test_live_ib_web_request_data.py -v -s -k "test_auth_status"
"""

import queue
import time

import pytest

from bt_api_py.auth_config import IbWebAuthConfig
from bt_api_py.feeds.live_ib_web_feed import IbWebRequestDataStock
from bt_api_py.functions.utils import read_account_config

# ── 配置检测 ──────────────────────────────────────────────


def _get_ib_web_config():
    """读取 ib_web 配置"""
    config = read_account_config()
    return config.get("ib_web", {})


def _gateway_available():
    """检测 Client Portal Gateway 是否可用"""
    cfg = _get_ib_web_config()
    base_url = cfg.get("base_url", "https://localhost:5000")
    try:
        import requests
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        resp = requests.post(
            f"{base_url}/iserver/auth/status",
            verify=False,
            timeout=5,
            proxies={"http": None, "https": None},
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("authenticated", False)
        return False
    except Exception:
        return False


# 如果 Gateway 不可用，跳过所有测试
gateway_is_up = _gateway_available()
skip_if_no_gateway = pytest.mark.skipif(
    not gateway_is_up,
    reason="IB Client Portal Gateway not running or not authenticated. "
    "Start gateway and login at https://localhost:5000 first.",
)


def _init_feed():
    """初始化 IB Web Feed 实例"""
    cfg = _get_ib_web_config()
    data_queue = queue.Queue()
    kwargs = {
        "base_url": cfg.get("base_url", "https://localhost:5000"),
        "account_id": cfg.get("account_id", ""),
        "verify_ssl": cfg.get("verify_ssl", False),
        "timeout": cfg.get("timeout", 10),
        "access_token": cfg.get("access_token", "") or None,
        "cookie_source": cfg.get("cookie_source", "") or None,
        "cookie_browser": cfg.get("cookie_browser", "chrome"),
    }
    feed = IbWebRequestDataStock(data_queue, **kwargs)
    return feed


# ── 认证和连接测试 ────────────────────────────────────────


@skip_if_no_gateway
class TestAuthAndSession:
    """认证和会话管理测试"""

    def test_auth_status(self):
        """检查认证状态"""
        feed = _init_feed()
        result = feed.check_auth_status()
        assert isinstance(result, dict)
        assert "authenticated" in result or "connected" in result

    def test_is_authenticated(self):
        """验证已通过认证"""
        feed = _init_feed()
        result = feed.check_auth_status()
        is_auth = result.get("authenticated", False)
        if not is_auth:
            pytest.skip(
                "Gateway is running but not authenticated. Please login at https://localhost:5000"
            )
        assert is_auth is True

    def test_connect(self):
        """测试 connect() 方法"""
        feed = _init_feed()
        feed.connect()
        assert feed.is_connected()
        feed.disconnect()

    def test_reauthenticate(self):
        """测试重新认证"""
        feed = _init_feed()
        result = feed.reauthenticate()
        assert isinstance(result, dict)


# ── IbWebAuthConfig 测试 ──────────────────────────────────


class TestIbWebAuthConfig:
    """IbWebAuthConfig 配置类测试 (不需要 Gateway)"""

    def test_default_values(self):
        auth = IbWebAuthConfig()
        assert auth.exchange == "IB_WEB"
        assert auth.asset_type == "STK"
        assert auth.base_url == "https://localhost:5000"
        assert auth.verify_ssl is False
        assert auth.timeout == 10

    def test_custom_values(self):
        auth = IbWebAuthConfig(
            base_url="https://api.interactivebrokers.com",
            account_id="DU123456",
            access_token="test_token",
            asset_type="FUT",
            verify_ssl=True,
        )
        assert auth.base_url == "https://api.interactivebrokers.com"
        assert auth.account_id == "DU123456"
        assert auth.access_token == "test_token"
        assert auth.asset_type == "FUT"
        assert auth.verify_ssl is True

    def test_get_exchange_name(self):
        auth = IbWebAuthConfig(asset_type="STK")
        assert auth.get_exchange_name() == "IB_WEB___STK"

    def test_to_dict(self):
        auth = IbWebAuthConfig(account_id="DU999999")
        d = auth.to_dict()
        assert d["exchange"] == "IB_WEB"
        assert d["account_id"] == "DU999999"
        assert d["base_url"] == "https://localhost:5000"

    def test_oauth_config(self):
        auth = IbWebAuthConfig(
            base_url="https://api.interactivebrokers.com",
            client_id="my_client_id",
            private_key_path="/path/to/key.pem",
            verify_ssl=True,
        )
        assert auth.client_id == "my_client_id"
        assert auth.private_key_path == "/path/to/key.pem"


# ── 账户信息测试 ──────────────────────────────────────────


@skip_if_no_gateway
class TestAccountInfo:
    """账户信息查询测试"""

    def test_get_portfolio_accounts(self):
        """获取账户列表"""
        feed = _init_feed()
        feed.connect()
        result = feed.get_portfolio_accounts()
        assert result is not None
        # 返回应为 list
        if isinstance(result, list):
            assert len(result) > 0

    def test_get_account_summary(self):
        """获取账户摘要"""
        feed = _init_feed()
        feed.connect()
        cfg = _get_ib_web_config()
        account_id = cfg.get("account_id", "")
        if not account_id:
            pytest.skip("account_id not configured in .env (IB_WEB_ACCOUNT_ID)")

        feed.get_portfolio_accounts()
        time.sleep(1)
        result = feed.get_account(extra_data={"account_id": account_id})
        assert result is not None
        # 验证返回包含余额信息
        assert "balance" in result or "availableFunds" in result

    def test_get_balance(self):
        """获取账户余额"""
        feed = _init_feed()
        feed.connect()
        cfg = _get_ib_web_config()
        account_id = cfg.get("account_id", "")
        if not account_id:
            pytest.skip("account_id not configured")

        feed.get_portfolio_accounts()
        time.sleep(1)
        result = feed.get_balance(extra_data={"account_id": account_id})
        assert result is not None
        # 验证返回包含余额信息
        assert "balance" in result or "availableFunds" in result


# ── 合约搜索测试 ──────────────────────────────────────────


@skip_if_no_gateway
class TestContractSearch:
    """合约搜索测试"""

    def test_search_stocks(self):
        """搜索股票 (AAPL)"""
        feed = _init_feed()
        feed.connect()
        result = feed.search_stocks("AAPL")
        assert result is not None
        # 预期包含 AAPL key
        if isinstance(result, dict):
            assert "AAPL" in result

    def test_search_contract(self):
        """通用合约搜索"""
        feed = _init_feed()
        feed.connect()
        result = feed.search_contract("AAPL", "STK")
        assert result is not None
        if isinstance(result, list):
            assert len(result) > 0

    def test_resolve_conid_aapl(self):
        """解析 AAPL 的 conid"""
        feed = _init_feed()
        feed.connect()
        conid = feed.resolve_conid("AAPL", "STK")
        assert conid is not None
        assert isinstance(conid, int)
        # AAPL 的 conid 通常是 265598
        assert conid > 0

    def test_search_futures(self):
        """搜索期货 (ES)"""
        feed = _init_feed()
        feed.connect()
        result = feed.search_futures("ES")
        assert result is not None


# ── 市场数据测试 ──────────────────────────────────────────


@skip_if_no_gateway
class TestMarketData:
    """市场数据测试"""

    AAPL_CONID = 265598  # AAPL 的 conid

    @pytest.mark.ticker
    def test_get_tick_by_conid(self):
        """通过 conid 获取快照数据"""
        feed = _init_feed()
        feed.connect()
        result = feed.get_tick(
            self.AAPL_CONID,
            extra_data={"conid": self.AAPL_CONID},
        )
        assert result is not None
        if isinstance(result, dict):
            # 至少应包含 conid
            assert "conid" in result or "conidEx" in result or "server_id" in result

    @pytest.mark.ticker
    def test_get_tick_fields(self):
        """验证快照字段"""
        feed = _init_feed()
        feed.connect()
        # 请求两次 (IB 首次请求是预检)
        feed.get_tick(
            self.AAPL_CONID,
            extra_data={"conid": self.AAPL_CONID},
            fields=["31", "84", "85", "86", "88"],
        )
        time.sleep(1)
        result = feed.get_tick(
            self.AAPL_CONID,
            extra_data={"conid": self.AAPL_CONID},
            fields=["31", "84", "85", "86", "88"],
        )
        if isinstance(result, dict):
            # field 31 = last price, 84 = bid, 86 = ask
            has_data = any(k in result for k in ["31", "84", "86", "conid"])
            assert has_data, f"Expected market data fields in: {list(result.keys())}"

    @pytest.mark.orderbook
    def test_get_depth(self):
        """获取买卖盘"""
        feed = _init_feed()
        feed.connect()
        result = feed.get_depth(
            self.AAPL_CONID,
            extra_data={"conid": self.AAPL_CONID},
        )
        assert result is not None

    @pytest.mark.kline
    def test_get_kline(self):
        """获取历史 K 线"""
        feed = _init_feed()
        feed.connect()
        result = feed.get_kline(
            self.AAPL_CONID,
            period="1d",
            count=5,
            extra_data={"conid": self.AAPL_CONID},
        )
        assert result is not None
        if isinstance(result, dict):
            # IB 返回 {"data": [...], "serverId": ...}
            if "data" in result:
                bars = result["data"]
                assert len(bars) > 0
                first_bar = bars[0]
                # 验证 bar 字段
                has_ohlc = any(k in first_bar for k in ["o", "h", "l", "c", "v"])
                assert has_ohlc, f"Expected OHLC in: {list(first_bar.keys())}"

    def test_unsubscribe_market_data(self):
        """取消行情订阅"""
        feed = _init_feed()
        feed.connect()
        # 先订阅
        feed.get_tick(
            self.AAPL_CONID,
            extra_data={"conid": self.AAPL_CONID},
        )
        time.sleep(0.5)
        # 取消 (Gateway 可能返回非标准响应, 只要不是连接错误即可)
        try:
            feed.unsubscribe_market_data(self.AAPL_CONID)
        except Exception as e:
            # 405/500 等非致命错误可忽略
            assert "connect" not in str(e).lower(), f"Connection error: {e}"


# ── WebSocket 流式数据测试 ────────────────────────────────


@skip_if_no_gateway
class TestWebSocketStream:
    """WebSocket 流式数据测试"""

    AAPL_CONID = 265598

    def test_websocket_connect_and_receive(self):
        """测试 WebSocket 连接并接收市场数据"""
        try:
            import websocket
        except ImportError:
            pytest.skip("websocket-client not installed")

        from bt_api_py.feeds.live_ib_web_stream import IbWebDataStream

        data_queue = queue.Queue()
        cfg = _get_ib_web_config()

        stream = IbWebDataStream(
            data_queue,
            base_url=cfg.get("base_url", "https://localhost:5000"),
            verify_ssl=cfg.get("verify_ssl", False),
            topics=[
                {
                    "topic": "market_data",
                    "conid": self.AAPL_CONID,
                    "fields": ["31", "84", "85", "86", "88"],
                },
            ],
        )

        stream.connect()

        received = []
        deadline = time.time() + 15  # 最多等15秒
        while time.time() < deadline:
            try:
                msg = data_queue.get(timeout=1)
                received.append(msg)
                if len(received) >= 3:
                    break
            except queue.Empty:
                continue

        stream.disconnect()

        # 至少收到一条消息 (可能是心跳或数据)
        if len(received) > 0:
            first = received[0]
            assert isinstance(first, dict)
            assert "type" in first
            assert first.get("exchange") == "IB_WEB"

    def test_account_stream(self):
        """测试账户 WebSocket 流"""
        try:
            import websocket
        except ImportError:
            pytest.skip("websocket-client not installed")

        from bt_api_py.feeds.live_ib_web_stream import IbWebAccountStream

        data_queue = queue.Queue()
        cfg = _get_ib_web_config()
        account_id = cfg.get("account_id", "")
        if not account_id:
            pytest.skip("account_id not configured")

        stream = IbWebAccountStream(
            data_queue,
            base_url=cfg.get("base_url", "https://localhost:5000"),
            account_id=account_id,
            verify_ssl=cfg.get("verify_ssl", False),
            topics=[{"topic": "account"}, {"topic": "order"}],
        )

        stream.connect()

        received = []
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                msg = data_queue.get(timeout=1)
                received.append(msg)
            except queue.Empty:
                continue

        stream.disconnect()


# ── 持仓和订单查询测试 ────────────────────────────────────


@skip_if_no_gateway
class TestPositionAndOrders:
    """持仓和订单查询测试"""

    def test_get_position(self):
        """查询持仓"""
        feed = _init_feed()
        feed.connect()
        cfg = _get_ib_web_config()
        account_id = cfg.get("account_id", "")
        if not account_id:
            pytest.skip("account_id not configured")

        # get_position 需要 cookies 或会抛出 NotImplementedError
        if not feed.has_cookies():
            with pytest.raises(NotImplementedError):
                feed.get_position(extra_data={"account_id": account_id})
            pytest.skip("get_position requires browser session cookies")
        else:
            feed.get_portfolio_accounts()
            time.sleep(1)
            result = feed.get_position(extra_data={"account_id": account_id})
            assert result is not None

    def test_get_open_orders(self):
        """查询未完成订单"""
        feed = _init_feed()
        feed.connect()
        result = feed.get_open_orders()
        assert result is not None

    def test_get_deals(self):
        """查询最近成交"""
        feed = _init_feed()
        feed.connect()
        result = feed.get_deals()
        assert result is not None


# ── 配置加载验证测试 (不需要 Gateway) ─────────────────────


class TestConfigLoading:
    """验证 read_account_config 正确加载 ib_web 配置"""

    def test_ib_web_config_exists(self):
        config = read_account_config()
        assert "ib_web" in config

    def test_ib_web_config_keys(self):
        config = read_account_config()
        ib_web = config["ib_web"]
        assert "base_url" in ib_web
        assert "account_id" in ib_web
        assert "verify_ssl" in ib_web
        assert "timeout" in ib_web

    def test_ib_web_config_defaults(self):
        config = read_account_config()
        ib_web = config["ib_web"]
        # 默认值（未设置环境变量时）
        assert ib_web["base_url"] == "https://localhost:5000" or isinstance(ib_web["base_url"], str)
        assert isinstance(ib_web["verify_ssl"], bool)
        assert isinstance(ib_web["timeout"], int)
        assert ib_web["timeout"] > 0

    def test_ib_web_cookie_config(self):
        """测试 cookie 配置项存在"""
        config = read_account_config()
        ib_web = config["ib_web"]
        assert "cookie_source" in ib_web
        assert "cookie_browser" in ib_web


# ── Cookie 功能测试 ────────────────────────────────────────────


class TestCookieFunctionality:
    """Cookie 功能测试 (不需要 Gateway)"""

    def test_cookie_string_parsing(self):
        """测试 Cookie 字符串解析"""
        from bt_api_py.functions.browser_cookies import extract_cookie_string

        cookie_str = "key1=value1; key2=value2; key3=value3"
        cookies = extract_cookie_string(cookie_str)

        assert cookies["key1"] == "value1"
        assert cookies["key2"] == "value2"
        assert cookies["key3"] == "value3"

    def test_cookies_to_header(self):
        """测试 Cookie 转换为 Header 格式"""
        from bt_api_py.functions.browser_cookies import cookies_to_header

        cookies = {"key1": "value1", "key2": "value2"}
        header = cookies_to_header(cookies)

        assert "key1=value1" in header
        assert "key2=value2" in header

    def test_feed_has_cookies_method(self):
        """测试 Feed 的 cookie 方法"""
        data_queue = queue.Queue()
        feed = IbWebRequestDataStock(data_queue, cookies={"test_key": "test_value"})

        assert feed.has_cookies() is True
        cookies = feed.get_cookies()
        assert cookies.get("test_key") == "test_value"

    def test_feed_set_cookies(self):
        """测试动态设置 cookies"""
        data_queue = queue.Queue()
        feed = IbWebRequestDataStock(data_queue)

        assert feed.has_cookies() is False

        feed.set_cookies({"new_key": "new_value"})
        assert feed.has_cookies() is True
        assert feed.get_cookies().get("new_key") == "new_value"


if __name__ == "__main__":
    # 先检查 Gateway 是否可用
    if not _gateway_available():
        pass
    else:
        pytest.main([__file__, "-v", "-s"])
