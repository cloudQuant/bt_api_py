# IBKR Web API 实现指南

> 最后更新: 2026-02-26

本文档提供在 bt_api_py 框架中集成 IBKR Web API 的实现指南。

---

## 前置要求

### 账户要求

1. **个人客户**:
   - IBKR Pro 账户（已开户并注资）
   - 使用 Client Portal Gateway 进行认证

1. **机构客户**:
   - 联系 api-solutions@interactivebrokers.com
   - 获取 OAuth 2.0 凭证
   - 完成合规审批（约 8-14 周）

### 技术要求

- Python 3.8+
- 网络连接（HTTPS）
- 对于个人客户：Java 运行环境（用于 Client Portal Gateway）

---

## 认证配置

### 方式 1: Client Portal Gateway（个人客户）

- *步骤**:

1. 下载 Client Portal Gateway

   ```bash

# 从 IBKR 官网下载

# <https://www.interactivebrokers.com/en/trading/ibgateway-stable.php>
   ```

1. 启动 Gateway

   ```bash
   cd clientportal.gw
   bin/run.sh root/conf.yaml
   ```

1. 访问本地端点

   ```

   <https://localhost:5000>
   ```

1. 在代码中配置

   ```python
   IB_BASE_URL = "<https://localhost:5000">
   IB_VERIFY_SSL = False  # 本地开发环境
   ```

### 方式 2: OAuth 2.0（机构客户）

- *步骤**:

1. 生成 JWT Token

   ```python
   import jwt
   import time

   def generate_client_assertion(client_id, private_key):
       payload = {
           "iss": client_id,
           "sub": client_id,
           "aud": "<https://api.interactivebrokers.com/oauth/token",>
           "exp": int(time.time()) + 300,
           "iat": int(time.time())
       }

       token = jwt.encode(payload, private_key, algorithm="RS256")
       return token
   ```

1. 获取 Access Token

   ```python
   import requests

   def get_access_token(client_id, client_assertion):
       url = "<https://api.interactivebrokers.com/oauth/token">

       data = {
           "grant_type": "client_credentials",
           "client_id": client_id,
           "client_assertion": client_assertion,
           "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
       }

       response = requests.post(url, json=data)
       return response.json()["access_token"]
   ```

1. 使用 Access Token

   ```python
   headers = {
       "Authorization": f"Bearer {access_token}",
       "Content-Type": "application/json"
   }
   ```

---

## 基础实现步骤

### 1. 创建 API 客户端类

```python
import requests
from typing import Optional, Dict, Any

class IBKRClient:
    def __init__(self, base_url: str, access_token: Optional[str] = None):
        self.base_url = base_url
        self.access_token = access_token
        self.session = requests.Session()

        if access_token:
            self.session.headers.update({
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            })

    def _request(self, method: str, endpoint: str, **kwargs) -> Dict[Any, Any]:
        url = f"{self.base_url}{endpoint}"
        response = self.session.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()

    def get(self, endpoint: str, **kwargs) -> Dict[Any, Any]:
        return self._request("GET", endpoint, **kwargs)

    def post(self, endpoint: str, **kwargs) -> Dict[Any, Any]:
        return self._request("POST", endpoint, **kwargs)

    def delete(self, endpoint: str, **kwargs) -> Dict[Any, Any]:
        return self._request("DELETE", endpoint, **kwargs)

```

### 2. 验证连接

```python
def verify_connection(client: IBKRClient) -> bool:
    try:
        response = client.post("/iserver/auth/status")
        return response.get("authenticated", False)
    except Exception as e:
        print(f"Connection failed: {e}")
        return False

```

### 3. 获取账户信息

```python
def get_accounts(client: IBKRClient) -> list:
    response = client.get("/portfolio/accounts")
    return response

```

---

## 核心功能实现

### 合约搜索

```python
class ContractSearch:
    def __init__(self, client: IBKRClient):
        self.client = client

    def search_stock(self, symbol: str) -> Optional[int]:
        """搜索股票并返回 conid"""
        response = self.client.get(f"/trsrv/stocks?symbols={symbol}")

        if symbol in response:
            contracts = response[symbol][0].get("contracts", [])
            if contracts:
                return contracts[0]["conid"]
        return None

    def search_contract(self, symbol: str, sec_type: str = "STK") -> list:
        """通用合约搜索"""
        params = {"symbol": symbol, "secType": sec_type}
        response = self.client.get("/iserver/secdef/search", params=params)
        return response

```

### 市场数据

```python
class MarketData:
    def __init__(self, client: IBKRClient):
        self.client = client
        self.subscribed_conids = set()

    def subscribe(self, conid: int, fields: list = None) -> None:
        """订阅市场数据"""
        if fields is None:
            fields = [31, 84, 85, 86, 88, 7059]  # 默认字段

        field_str = ",".join(map(str, fields))
        params = {"conids": conid, "fields": field_str}

# 首次请求（预检）
        self.client.get("/iserver/marketdata/snapshot", params=params)
        self.subscribed_conids.add(conid)

    def get_snapshot(self, conid: int) -> Dict[str, Any]:
        """获取快照数据"""
        if conid not in self.subscribed_conids:
            self.subscribe(conid)

        params = {"conids": conid, "fields": "31,84,85,86,88,7059"}
        response = self.client.get("/iserver/marketdata/snapshot", params=params)

        if response and len(response) > 0:
            return response[0]
        return {}

    def unsubscribe(self, conid: int) -> None:
        """取消订阅"""
        self.client.get(f"/iserver/marketdata/unsubscribe?conid={conid}")
        self.subscribed_conids.discard(conid)

```

### 订单管理

```python
class OrderManager:
    def __init__(self, client: IBKRClient, account_id: str):
        self.client = client
        self.account_id = account_id

    def place_order(self, conid: int, side: str, quantity: int,
                   order_type: str = "MKT", price: float = None) -> Dict[str, Any]:
        """提交订单"""
        order = {
            "conid": conid,
            "side": side.upper(),
            "orderType": order_type,
            "quantity": quantity,
            "tif": "DAY"
        }

        if order_type == "LMT" and price:
            order["price"] = price

        endpoint = f"/iserver/account/{self.account_id}/orders"
        response = self.client.post(endpoint, json={"orders": [order]})

# 处理确认消息
        if isinstance(response, list) and "id" in response[0]:
            return self._confirm_order(response[0]["id"])

        return response

    def _confirm_order(self, message_id: str) -> Dict[str, Any]:
        """确认订单"""
        endpoint = f"/iserver/reply/{message_id}"
        return self.client.post(endpoint, json={"confirmed": True})

    def modify_order(self, order_id: str, **kwargs) -> Dict[str, Any]:
        """修改订单"""
        endpoint = f"/iserver/account/{self.account_id}/order/{order_id}"
        return self.client.post(endpoint, json=kwargs)

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """取消订单"""
        endpoint = f"/iserver/account/{self.account_id}/order/{order_id}"
        return self.client.delete(endpoint)

    def get_orders(self, filters: str = None) -> list:
        """查询订单"""
        params = {"accountId": self.account_id, "force": True}
        if filters:
            params["filters"] = filters

        return self.client.get("/iserver/account/orders", params=params)

```

### 持仓管理

```python
class PositionManager:
    def __init__(self, client: IBKRClient, account_id: str):
        self.client = client
        self.account_id = account_id

    def get_positions(self) -> list:
        """获取持仓"""
        endpoint = f"/portfolio/{self.account_id}/positions"
        return self.client.get(endpoint)

    def get_summary(self) -> Dict[str, Any]:
        """获取账户摘要"""
        endpoint = f"/portfolio/{self.account_id}/summary"
        return self.client.get(endpoint)

    def get_ledger(self) -> Dict[str, Any]:
        """获取账户余额"""
        endpoint = f"/portfolio/{self.account_id}/ledger"
        return self.client.get(endpoint)

```

---

## 错误处理

### 实现重试机制

```python
import time
from functools import wraps

def retry_on_rate_limit(max_retries: int = 3, backoff: float = 1.0):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except requests.HTTPError as e:
                    if e.response.status_code == 429:
                        if attempt < max_retries - 1:
                            wait_time = backoff * (2 **attempt)
                            print(f"Rate limit hit, waiting {wait_time}s...")
                            time.sleep(wait_time)
                            continue
                    raise
            return None
        return wrapper
    return decorator

```

### 错误处理示例

```python
class IBKRClientWithErrorHandling(IBKRClient):
    @retry_on_rate_limit(max_retries=3)
    def _request(self, method: str, endpoint: str,**kwargs):
        try:
            return super()._request(method, endpoint, **kwargs)
        except requests.HTTPError as e:
            error_data = e.response.json() if e.response.text else {}

            if e.response.status_code == 401:
                raise AuthenticationError("Authentication failed")
            elif e.response.status_code == 403:
                raise PermissionError("Insufficient permissions")
            elif e.response.status_code == 404:
                raise NotFoundError(f"Resource not found: {endpoint}")
            elif e.response.status_code == 429:
                raise RateLimitError("Rate limit exceeded")
            else:
                raise APIError(f"API error: {error_data}")

```

---

## 最佳实践

### 1. 速率限制管理

```python
import threading
from collections import deque
from datetime import datetime, timedelta

class RateLimiter:
    def __init__(self, max_requests: int, time_window: int):
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests = deque()
        self.lock = threading.Lock()

    def acquire(self):
        with self.lock:
            now = datetime.now()

# 移除过期的请求记录
            while self.requests and self.requests[0] < now - timedelta(seconds=self.time_window):
                self.requests.popleft()

# 检查是否超过限制
            if len(self.requests) >= self.max_requests:
                wait_time = (self.requests[0] + timedelta(seconds=self.time_window) - now).total_seconds()
                if wait_time > 0:
                    time.sleep(wait_time)

            self.requests.append(now)

```

### 2. 会话管理

```python
class SessionManager:
    def __init__(self, client: IBKRClient):
        self.client = client
        self.last_check = None
        self.check_interval = 60  # 秒

    def ensure_session(self):
        """确保会话有效"""
        now = time.time()

        if self.last_check is None or (now - self.last_check) > self.check_interval:
            try:
                response = self.client.post("/iserver/auth/status")
                if not response.get("authenticated"):
                    self.client.post("/iserver/reauthenticate")
                self.last_check = now
            except Exception as e:
                print(f"Session check failed: {e}")

```

### 3. 数据缓存

```python
from functools import lru_cache
from datetime import datetime, timedelta

class DataCache:
    def __init__(self, ttl: int = 300):
        self.cache = {}
        self.ttl = ttl

    def get(self, key: str):
        if key in self.cache:
            data, timestamp = self.cache[key]
            if datetime.now() - timestamp < timedelta(seconds=self.ttl):
                return data
            else:
                del self.cache[key]
        return None

    def set(self, key: str, value):
        self.cache[key] = (value, datetime.now())

```

### 4. 日志记录

```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger('ibkr_client')

class LoggingIBKRClient(IBKRClient):
    def _request(self, method: str, endpoint: str, **kwargs):
        logger.info(f"{method} {endpoint}")
        try:
            response = super()._request(method, endpoint, **kwargs)
            logger.debug(f"Response: {response}")
            return response
        except Exception as e:
            logger.error(f"Request failed: {e}")
            raise

```

---

## 完整使用示例

```python

# 初始化客户端

client = IBKRClient(base_url="<https://localhost:5000")>

# 验证连接

if not verify_connection(client):
    print("Failed to connect")
    exit(1)

# 获取账户

accounts = get_accounts(client)
account_id = accounts[0]

# 搜索合约

contract_search = ContractSearch(client)
aapl_conid = contract_search.search_stock("AAPL")

# 获取市场数据

market_data = MarketData(client)
snapshot = market_data.get_snapshot(aapl_conid)
print(f"AAPL Last Price: {snapshot.get('31')}")

# 下单

order_manager = OrderManager(client, account_id)
order_response = order_manager.place_order(
    conid=aapl_conid,
    side="BUY",
    quantity=100,
    order_type="LMT",
    price=150.00
)
print(f"Order placed: {order_response}")

# 查询持仓

position_manager = PositionManager(client, account_id)
positions = position_manager.get_positions()
print(f"Positions: {positions}")

```

---

## 相关文档

- [API 快速参考](./api_reference_quick.md) - 端点快速查询
- [交易 API 详细文档](./trading.md) - 完整 API 说明
- [账户管理 API](./account_management.md) - 账户管理功能

---

## 支持

如有问题，请联系：

- **Email**: api@interactivebrokers.com
- **文档**: <https://www.interactivebrokers.com/campus/ibkr-api-page/>
