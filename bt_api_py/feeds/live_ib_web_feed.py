"""
Interactive Brokers Web API Feed 实现
基于 IBKR RESTful Web API，通过 HTTP/HTTPS 进行交易、行情、账户管理

支持两种认证方式:
  1. Client Portal Gateway (个人客户): 本地网关代理
  2. OAuth 2.0 (机构客户): JWT + access_token

依赖: pip install httpx pyjwt cryptography
"""
import time
import threading
from typing import Optional, Set

from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.base_stream import BaseDataStream, ConnectionState
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.http_client import HttpClient
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.containers.exchanges.ib_web_exchange_data import (
    IbWebExchangeDataStock,
    IbWebExchangeDataFuture,
)


class IbWebRequestData(Feed):
    """IB Web API REST 请求封装"""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue)
        self.data_queue = data_queue
        self.exchange_name = "IB_WEB"
        self.base_url = kwargs.get("base_url", "https://localhost:5000")
        self.account_id = kwargs.get("account_id", None)
        self.access_token = kwargs.get("access_token", None)
        self.verify_ssl = kwargs.get("verify_ssl", False)
        self.proxies = kwargs.get("proxies", None)
        self.timeout = kwargs.get("timeout", 10)
        self.asset_type = kwargs.get("asset_type", "STK")
        self._params = IbWebExchangeDataStock()
        self._params.rest_url = self.base_url
        self.logger_name = kwargs.get("logger_name", "ib_web_feed.log")
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "ib_web_request", 0, 0, False
        ).create_logger()
        self._http = HttpClient(
            venue="IB_WEB", timeout=self.timeout,
            verify=self.verify_ssl, proxies=self.proxies,
        )
        self._authenticated = False
        self._session_lock = threading.Lock()
        self._last_session_check = 0
        self._session_check_interval = 60
        self._subscribed_conids = set()

        # Cookie 支持
        self._cookies = kwargs.get("cookies", None)
        self._cookie_source = kwargs.get("cookie_source", None)
        self._cookie_browser = kwargs.get("cookie_browser", "chrome")
        self._cookie_path = kwargs.get("cookie_path", "/sso")  # IBKR Gateway 认证路径
        self._loaded_cookies = {}
        self._load_cookies()

    @classmethod
    def _capabilities(cls) -> Set[Capability]:
        return {
            Capability.GET_TICK, Capability.GET_DEPTH, Capability.GET_KLINE,
            Capability.MAKE_ORDER, Capability.CANCEL_ORDER, Capability.CANCEL_ALL,
            Capability.QUERY_ORDER, Capability.QUERY_OPEN_ORDERS, Capability.GET_DEALS,
            Capability.GET_BALANCE, Capability.GET_ACCOUNT, Capability.GET_POSITION,
            Capability.BATCH_ORDER,
        }

    def _build_headers(self):
        headers = {"Content-Type": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def _request(self, method, endpoint, params=None, json_data=None, **kwargs):
        url = f"{self.base_url}{endpoint}"
        headers = self._build_headers()
        self.request_logger.info(f"{method} {endpoint}")

        # 合并 cookies
        request_cookies = kwargs.get("cookies", {})
        if self._loaded_cookies:
            request_cookies = {**self._loaded_cookies, **request_cookies}
        kwargs["cookies"] = request_cookies

        return self._http.request(
            method=method, url=url, headers=headers,
            params=params, json_data=json_data, **kwargs,
        )

    def _get(self, endpoint, params=None, **kwargs):
        return self._request("GET", endpoint, params=params, **kwargs)

    def _post(self, endpoint, json_data=None, **kwargs):
        return self._request("POST", endpoint, json_data=json_data, **kwargs)

    def _delete(self, endpoint, **kwargs):
        return self._request("DELETE", endpoint, **kwargs)

    def _patch(self, endpoint, json_data=None, **kwargs):
        return self._request("PATCH", endpoint, json_data=json_data, **kwargs)

    def _put(self, endpoint, json_data=None, **kwargs):
        return self._request("PUT", endpoint, json_data=json_data, **kwargs)

    # ── Cookie 管理 ─────────────────────────────────────────────

    def _load_cookies(self):
        """加载浏览器 cookies"""
        if self._cookies:
            # 直接传入的 cookie 字典
            self._loaded_cookies = self._cookies
        elif self._cookie_source:
            from bt_api_py.functions.browser_cookies import get_ibkr_cookies
            self._loaded_cookies = get_ibkr_cookies(
                base_url=self.base_url,
                cookie_source=self._cookie_source,
                browser=self._cookie_browser,
                cookie_path=self._cookie_path,
            )
        else:
            self._loaded_cookies = {}

    def set_cookies(self, cookies):
        """动态设置 cookies"""
        self._loaded_cookies = cookies

    def get_cookies(self):
        """获取当前 cookies"""
        return self._loaded_cookies

    def has_cookies(self):
        """检查是否有可用的 cookies"""
        return bool(self._loaded_cookies)

    # ── 连接管理 ──────────────────────────────────────────────

    def connect(self):
        try:
            result = self.check_auth_status()
            self._authenticated = result.get("authenticated", False)
            if not self._authenticated:
                self.reauthenticate()
        except Exception as e:
            self.request_logger.warning(f"IB Web connect failed: {e}")
            self._authenticated = False

    def disconnect(self):
        self._authenticated = False
        self._http.close()

    def is_connected(self):
        return self._authenticated

    def _ensure_session(self):
        now = time.time()
        if now - self._last_session_check > self._session_check_interval:
            with self._session_lock:
                if now - self._last_session_check > self._session_check_interval:
                    try:
                        result = self.check_auth_status()
                        self._authenticated = result.get("authenticated", False)
                        if not self._authenticated:
                            self.reauthenticate()
                    except Exception as e:
                        self.request_logger.warning(f"Session check failed: {e}")
                    self._last_session_check = now

    # ── 会话管理 ──────────────────────────────────────────────

    def check_auth_status(self):
        """POST /iserver/auth/status"""
        return self._post("/iserver/auth/status")

    def reauthenticate(self):
        """POST /iserver/reauthenticate"""
        return self._post("/iserver/reauthenticate")

    def authenticate_oauth(self, client_id, private_key_path):
        """OAuth 2.0 认证"""
        try:
            import jwt as pyjwt
        except ImportError:
            raise ImportError("pyjwt required. Install: pip install pyjwt cryptography")
        with open(private_key_path, 'r') as f:
            private_key = f.read()
        now = int(time.time())
        payload = {
            "iss": client_id, "sub": client_id,
            "aud": f"{self.base_url}/oauth/token",
            "exp": now + 300, "iat": now,
        }
        assertion = pyjwt.encode(payload, private_key, algorithm="RS256")
        data = {
            "grant_type": "client_credentials", "client_id": client_id,
            "client_assertion": assertion,
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        }
        response = self._post("/oauth/token", json_data=data)
        self.access_token = response.get("access_token")
        self._authenticated = True
        return self.access_token

    # ── 合约搜索 ──────────────────────────────────────────────

    def search_stocks(self, symbols, extra_data=None, **kwargs):
        """GET /trsrv/stocks"""
        return self._get("/trsrv/stocks", params={"symbols": symbols})

    def search_futures(self, symbols, extra_data=None, **kwargs):
        """GET /trsrv/futures"""
        return self._get("/trsrv/futures", params={"symbols": symbols})

    def search_contract(self, symbol, sec_type="STK", extra_data=None, **kwargs):
        """GET /iserver/secdef/search"""
        return self._get("/iserver/secdef/search",
                         params={"symbol": symbol, "secType": sec_type})

    def get_option_strikes(self, conid, exchange="SMART", sec_type="OPT",
                           month=None, extra_data=None, **kwargs):
        """GET /iserver/secdef/strikes"""
        params = {"conid": conid, "exchange": exchange, "sectype": sec_type}
        if month:
            params["month"] = month
        return self._get("/iserver/secdef/strikes", params=params)

    def get_option_info(self, conid, exchange="SMART", sec_type="OPT",
                        month=None, strike=None, extra_data=None, **kwargs):
        """GET /iserver/secdef/info"""
        params = {"conid": conid, "exchange": exchange, "sectype": sec_type}
        if month:
            params["month"] = month
        if strike is not None:
            params["strike"] = strike
        return self._get("/iserver/secdef/info", params=params)

    def resolve_conid(self, symbol, sec_type="STK"):
        """搜索品种并返回 conid"""
        if sec_type == "STK":
            resp = self.search_stocks(symbol)
            if symbol in resp:
                entries = resp[symbol]
                if entries and isinstance(entries, list):
                    contracts = entries[0].get("contracts", [])
                    if contracts:
                        return contracts[0].get("conid")
        else:
            resp = self.search_contract(symbol, sec_type)
            if resp and isinstance(resp, list) and len(resp) > 0:
                return resp[0].get("conid")
        return None

    # ── 市场数据 ──────────────────────────────────────────────

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """获取市场快照"""
        conid = self._resolve_conid_param(symbol, extra_data)
        fields = kwargs.get("fields", self._params.default_snapshot_fields)
        field_str = ",".join(str(f) for f in fields)
        params = {"conids": str(conid), "fields": field_str}
        self._get("/iserver/marketdata/snapshot", params=params)
        time.sleep(0.5)
        response = self._get("/iserver/marketdata/snapshot", params=params)
        if isinstance(response, list) and len(response) > 0:
            return response[0]
        return response

    def get_depth(self, symbol, count=5, extra_data=None, **kwargs):
        """获取买卖盘"""
        return self.get_tick(symbol, extra_data=extra_data,
                             fields=['84', '85', '86', '88'], **kwargs)

    def get_kline(self, symbol, period, count=100, start_time=None, end_time=None,
                  extra_data=None, **kwargs):
        """获取历史K线"""
        conid = self._resolve_conid_param(symbol, extra_data)
        ib_period = self._params.kline_periods.get(period, period)
        params = {"conid": str(conid), "period": ib_period, "bar": str(count or 100)}
        if start_time:
            params["startTime"] = str(start_time)
        return self._get("/iserver/marketdata/history", params=params)

    def unsubscribe_market_data(self, conid):
        """取消市场数据订阅"""
        self._post("/iserver/marketdata/unsubscribe", json_data={"conid": int(conid)})
        self._subscribed_conids.discard(conid)

    # ── 订单管理 ──────────────────────────────────────────────

    def make_order(self, symbol, volume, price, order_type='buy-limit',
                   offset='open', post_only=False, client_order_id=None,
                   extra_data=None, **kwargs):
        """提交订单"""
        conid = self._resolve_conid_param(symbol, extra_data)
        account_id = self._get_account_id(extra_data)
        side, ib_order_type = self._parse_order_type(order_type)
        tif = extra_data.get("tif", "DAY") if isinstance(extra_data, dict) else "DAY"
        order = {
            "conid": conid, "side": side, "orderType": ib_order_type,
            "quantity": volume, "tif": tif,
        }
        if ib_order_type in ("LMT", "STP_LMT") and price:
            order["price"] = price
        if ib_order_type in ("STP", "STP_LMT") and isinstance(extra_data, dict):
            aux = extra_data.get("aux_price")
            if aux:
                order["auxPrice"] = aux
        if client_order_id:
            order["cOID"] = client_order_id
        endpoint = f"/iserver/account/{account_id}/orders"
        response = self._post(endpoint, json_data={"orders": [order]})
        return self._handle_order_reply(response)

    def place_bracket_order(self, account_id=None, orders=None,
                            extra_data=None, **kwargs):
        """提交括号订单"""
        account_id = account_id or self._get_account_id(extra_data)
        endpoint = f"/iserver/account/{account_id}/orders"
        return self._post(endpoint, json_data={"orders": orders})

    def modify_order(self, symbol, order_id, extra_data=None, **kwargs):
        """修改订单"""
        account_id = self._get_account_id(extra_data)
        modify_data = extra_data if isinstance(extra_data, dict) else {}
        endpoint = f"/iserver/account/{account_id}/order/{order_id}"
        response = self._post(endpoint, json_data=modify_data)
        return self._handle_order_reply(response)

    def cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """取消订单"""
        account_id = self._get_account_id(extra_data)
        return self._delete(f"/iserver/account/{account_id}/order/{order_id}")

    def cancel_all(self, symbol=None, extra_data=None, **kwargs):
        """取消所有订单"""
        orders = self.get_open_orders(symbol, extra_data=extra_data)
        order_list = orders.get("orders", []) if isinstance(orders, dict) else (
            orders if isinstance(orders, list) else [])
        results = []
        for o in order_list:
            oid = o.get("orderId")
            if oid:
                try:
                    results.append(self.cancel_order(symbol, oid, extra_data=extra_data))
                except Exception as e:
                    self.request_logger.warning(f"Failed to cancel order {oid}: {e}")
        return results

    def query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """查询特定订单"""
        all_orders = self.get_open_orders(symbol, extra_data=extra_data)
        order_list = all_orders.get("orders", []) if isinstance(all_orders, dict) else (
            all_orders if isinstance(all_orders, list) else [])
        for o in order_list:
            if str(o.get("orderId")) == str(order_id):
                return o
        return {}

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """GET /iserver/account/orders"""
        params = {"force": "true"}
        if self.account_id:
            params["accountId"] = self.account_id
        if isinstance(extra_data, dict) and "filters" in extra_data:
            params["filters"] = extra_data["filters"]
        return self._get("/iserver/account/orders", params=params)

    def get_deals(self, symbol=None, count=100, start_time=None, end_time=None,
                  extra_data=None, **kwargs):
        """GET /iserver/account/trades"""
        return self._get("/iserver/account/trades")

    def suppress_order_messages(self, message_ids):
        """POST /iserver/questions/suppress"""
        return self._post("/iserver/questions/suppress",
                          json_data={"messageIds": message_ids})

    def reset_suppress(self):
        """POST /iserver/questions/suppress/reset"""
        return self._post("/iserver/questions/suppress/reset")

    def _handle_order_reply(self, response):
        if isinstance(response, list) and len(response) > 0:
            first = response[0]
            if isinstance(first, dict) and "id" in first and "message" in first:
                mid = first["id"]
                self.request_logger.info(f"Order confirmation: {first.get('message')}")
                return self._post(f"/iserver/reply/{mid}",
                                  json_data={"confirmed": True})
        return response

    # ── 持仓和账户 ────────────────────────────────────────────

    def get_portfolio_accounts(self, extra_data=None, **kwargs):
        """GET /portfolio/accounts"""
        return self._get("/portfolio/accounts")

    def get_portfolio_subaccounts(self, extra_data=None, **kwargs):
        """GET /portfolio/subaccounts"""
        return self._get("/portfolio/subaccounts")

    def get_position(self, symbol=None, extra_data=None, **kwargs):
        """GET /portfolio/{accountId}/positions/{pageId}

        注意: 此端点需要浏览器会话认证。
        替代方案: 使用 /iserver/account/positions 或其他可用端点
        """
        account_id = self._get_account_id(extra_data)
        page_id = kwargs.get("page_id", 0)

        # 先尝试 portfolio 端点（需要浏览器会话）
        if self.has_cookies():
            try:
                return self._get(f"/portfolio/{account_id}/positions/{page_id}")
            except Exception:
                pass

        # 回退到尝试其他方式
        raise NotImplementedError(
            "get_position requires browser session authentication. "
            "Please set IB_WEB_COOKIE_SOURCE=browser or use alternative endpoints."
        )

    def get_account(self, symbol="ALL", extra_data=None, **kwargs):
        """GET /iserver/account/{accountId}/summary

        使用 iserver 端点替代 portfolio 端点，因为不需要浏览器会话
        """
        account_id = self._get_account_id(extra_data)
        return self._get(f"/iserver/account/{account_id}/summary")

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        """GET /iserver/account/{accountId}/summary

        使用 iserver/summary 端点，返回账户余额信息
        """
        account_id = self._get_account_id(extra_data)
        return self._get(f"/iserver/account/{account_id}/summary")

    # ── Account Management API (/gw/api/v1) ──────────────────

    def get_accounts_list(self, status=None, limit=None, offset=None,
                          extra_data=None, **kwargs):
        """GET /gw/api/v1/accounts"""
        params = {}
        if status:
            params["status"] = status
        if limit:
            params["limit"] = limit
        if offset:
            params["offset"] = offset
        return self._get("/gw/api/v1/accounts", params=params)

    def get_account_detail(self, account_id=None, extra_data=None, **kwargs):
        """GET /gw/api/v1/accounts/{accountId}"""
        account_id = account_id or self._get_account_id(extra_data)
        return self._get(f"/gw/api/v1/accounts/{account_id}")

    def update_account_info(self, account_id=None, update_data=None,
                            extra_data=None, **kwargs):
        """PATCH /gw/api/v1/accounts/{accountId}"""
        account_id = account_id or self._get_account_id(extra_data)
        return self._patch(f"/gw/api/v1/accounts/{account_id}", json_data=update_data)

    def close_account(self, account_id=None, reason="", extra_data=None, **kwargs):
        """POST /gw/api/v1/accounts/{accountId}/close"""
        account_id = account_id or self._get_account_id(extra_data)
        return self._post(f"/gw/api/v1/accounts/{account_id}/close",
                          json_data={"reason": reason})

    # ── 资金和银行 ────────────────────────────────────────────

    def get_bank_instructions(self, account_id=None, method=None,
                              extra_data=None, **kwargs):
        """GET /gw/api/v1/bank-instructions/query"""
        account_id = account_id or self._get_account_id(extra_data)
        params = {"accountId": account_id}
        if method:
            params["method"] = method
        return self._get("/gw/api/v1/bank-instructions/query", params=params)

    def create_withdraw_request(self, amount, currency="USD",
                                instruction_id=None, account_id=None,
                                notes=None, extra_data=None, **kwargs):
        """POST /gw/api/v1/withdraw-request"""
        account_id = account_id or self._get_account_id(extra_data)
        data = {"accountId": account_id, "amount": amount, "currency": currency}
        if instruction_id:
            data["instructionId"] = instruction_id
        if notes:
            data["notes"] = notes
        return self._post("/gw/api/v1/withdraw-request", json_data=data)

    def create_deposit_request(self, amount, currency="USD", method="CHECK",
                               account_id=None, notes=None,
                               extra_data=None, **kwargs):
        """POST /gw/api/v1/deposit-request"""
        account_id = account_id or self._get_account_id(extra_data)
        data = {"accountId": account_id, "amount": amount,
                "currency": currency, "method": method}
        if notes:
            data["notes"] = notes
        return self._post("/gw/api/v1/deposit-request", json_data=data)

    def internal_transfer_cash(self, to_account_id, amount, currency="USD",
                               from_account_id=None, extra_data=None, **kwargs):
        """POST /gw/api/v1/internal-transfer (现金)"""
        from_account_id = from_account_id or self._get_account_id(extra_data)
        data = {
            "fromAccountId": from_account_id, "toAccountId": to_account_id,
            "transferType": "CASH", "amount": amount, "currency": currency,
        }
        return self._post("/gw/api/v1/internal-transfer", json_data=data)

    def internal_transfer_position(self, to_account_id, transfers,
                                   from_account_id=None, extra_data=None, **kwargs):
        """POST /gw/api/v1/internal-transfer (持仓)"""
        from_account_id = from_account_id or self._get_account_id(extra_data)
        data = {
            "fromAccountId": from_account_id, "toAccountId": to_account_id,
            "transferType": "POSITION", "transfers": transfers,
        }
        return self._post("/gw/api/v1/internal-transfer", json_data=data)

    # ── 报告 ──────────────────────────────────────────────────

    def get_statements(self, start_date, end_date, account_id=None,
                       extra_data=None, **kwargs):
        """GET /gw/api/v1/statements"""
        account_id = account_id or self._get_account_id(extra_data)
        return self._get("/gw/api/v1/statements", params={
            "accountId": account_id, "startDate": start_date, "endDate": end_date})

    def get_tax_documents(self, tax_year, account_id=None,
                          extra_data=None, **kwargs):
        """GET /gw/api/v1/tax-documents/available"""
        account_id = account_id or self._get_account_id(extra_data)
        return self._get("/gw/api/v1/tax-documents/available",
                         params={"accountId": account_id, "taxYear": tax_year})

    def get_trade_confirmations(self, start_date, end_date, account_id=None,
                                extra_data=None, **kwargs):
        """GET /gw/api/v1/trade-confirmations"""
        account_id = account_id or self._get_account_id(extra_data)
        return self._get("/gw/api/v1/trade-confirmations", params={
            "accountId": account_id, "startDate": start_date, "endDate": end_date})

    # ── SSO ───────────────────────────────────────────────────

    def get_sso_url(self, target_url, account_id=None, show_nav_bar=None,
                    extra_data=None, **kwargs):
        """GET /gw/api/v1/sso/url"""
        account_id = account_id or self._get_account_id(extra_data)
        params = {"accountId": account_id, "targetUrl": target_url}
        if show_nav_bar is not None:
            params["showNavBar"] = str(show_nav_bar).lower()
        return self._get("/gw/api/v1/sso/url", params=params)

    # ── 通知 ──────────────────────────────────────────────────

    def get_fyi_unread_count(self, extra_data=None, **kwargs):
        """GET /fyi/unreadnumber"""
        return self._get("/fyi/unreadnumber")

    def get_fyi_notifications(self, extra_data=None, **kwargs):
        """GET /fyi/notifications"""
        return self._get("/fyi/notifications")

    def mark_fyi_read(self, notification_id, extra_data=None, **kwargs):
        """PUT /fyi/notifications/{notificationID}"""
        return self._put(f"/fyi/notifications/{notification_id}")

    def get_fyi_settings(self, extra_data=None, **kwargs):
        """GET /fyi/settings"""
        return self._get("/fyi/settings")

    # ── 辅助方法 ──────────────────────────────────────────────

    def _get_account_id(self, extra_data=None):
        if isinstance(extra_data, dict):
            aid = extra_data.get("account_id")
            if aid:
                return aid
        if self.account_id:
            return self.account_id
        raise ValueError("account_id is required.")

    def _resolve_conid_param(self, symbol, extra_data=None):
        if isinstance(symbol, int):
            return symbol
        if isinstance(extra_data, dict) and "conid" in extra_data:
            return extra_data["conid"]
        try:
            return int(symbol)
        except (ValueError, TypeError):
            pass
        conid = self.resolve_conid(symbol, self.asset_type)
        if conid is None:
            raise ValueError(f"Cannot resolve conid for symbol: {symbol}")
        return conid

    def _parse_order_type(self, order_type):
        ot = order_type.lower()
        side = "BUY" if ot.startswith("buy") else ("SELL" if ot.startswith("sell") else "BUY")
        if "market" in ot:
            ib_type = "MKT"
        elif "stop_limit" in ot or "stop-limit" in ot:
            ib_type = "STP_LMT"
        elif "stop" in ot:
            ib_type = "STP"
        elif "limit" in ot:
            ib_type = "LMT"
        else:
            ib_type = self._params.order_type_map.get(ot, ot.upper())
        return side, ib_type


# ── 资产类型子类 ──────────────────────────────────────────────

class IbWebRequestDataStock(IbWebRequestData):
    """IB Web API 股票 Feed"""
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "STK")
        self._params = IbWebExchangeDataStock()
        self._params.rest_url = self.base_url


class IbWebRequestDataFuture(IbWebRequestData):
    """IB Web API 期货 Feed"""
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "FUT")
        self._params = IbWebExchangeDataFuture()
        self._params.rest_url = self.base_url
