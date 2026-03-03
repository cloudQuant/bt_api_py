# Exchange Integration Skill v2.0

自动生成 bt_api_py 框架的标准化交易所集成代码，基于 20+ 交易所实现经验总结的模式与规范。

## 功能特性

- 快速生成完整的交易所适配器代码（Feed 基类 + 资产类型子类）
- 支持 CEX（REST API）和 DEX（GraphQL）
- 自动生成 YAML 配置文件
- 自动生成 ExchangeData 类（带配置加载）
- 自动生成数据容器（Ticker / Order / OrderBook 等）
- 生成 ExchangeRegistry 注册模块
- 遵循项目既定模式与命名规范

## 安装依赖

```bash
pip install jinja2 pyyaml
```

## 使用方法

### 1. 最简模式（推荐新手）

```bash
python .claude-skills/exchange-integration/scripts/generator.py phemex
```

使用默认配置生成基础代码框架。

### 2. 交互式模式

```bash
python .claude-skills/exchange-integration/scripts/generator.py coinex -i
```

逐步输入配置信息。

### 3. DEX（GraphQL）模式

```bash
python .claude-skills/exchange-integration/scripts/generator.py uniswap \
  --api-type graphql \
  --graphql-url https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3
```

### 4. 配置文件模式

```bash
python .claude-skills/exchange-integration/scripts/generator.py kucoin -c kucoin_config.json
```

## 核心模式规范

### 交易所标识命名

格式：`{交易所大写}___{资产类型大写}`

```python
# 正确示例
"BINANCE___SPOT"
"BINANCE___SWAP"
"OKX___SPOT"
"COINEX___SPOT"
"UNISWAP___DEX"

# 错误示例 - 不要使用
"BinanceSpot"
"binance-spot"
"BINANCE_SPOT"
```

### 文件结构

```
bt_api_py/
├── configs/
│   └── {exchange}.yaml                  # YAML 配置（必需）
├── feeds/
│   ├── live_{exchange}/
│   │   ├── __init__.py
│   │   ├── request_base.py              # 基础请求类
│   │   └── spot.py                      # Spot 实现
│   └── register_{exchange}.py           # 注册模块
├── containers/
│   ├── exchanges/
│   │   └── {exchange}_exchange_data.py  # 配置类（带 YAML 加载）
│   └── tickers/
│       └── {exchange}_ticker.py         # Ticker 容器
```

### YAML 配置规范

每个交易所必须创建 YAML 配置文件：

```yaml
id: {exchange_id}
display_name: {Display Name}
venue_type: cex  # 或 dex
website: https://www.exchange.com
api_doc: https://docs.exchange.com

base_urls:
  rest: https://api.exchange.com
  wss: wss://stream.exchange.com

authentication:
  type: hmac_sha256  # 或 none, api_key, hmac_sha512
  header_name: X-API-KEY

kline_periods:
  1m: "1min"
  5m: "5min"
  1h: "1hour"
  1d: "1day"

legal_currency: [USDT, USD, BTC]

rate_limits:
  - name: public_api
    type: sliding_window
    interval: 60
    limit: 1200

asset_types:
  spot:
    exchange_name: {exchange}Spot
    rest_paths:
      get_server_time: "GET /api/v1/time"
      get_ticker: "GET /api/v1/ticker"
      get_depth: "GET /api/v1/depth"
      get_kline: "GET /api/v1/kline"
```

### ExchangeData 配置加载模式

```python
_config = None
_config_loaded = False

def _get_{exchange}_config():
    """延迟加载并缓存 YAML 配置"""
    global _config, _config_loaded
    if _config_loaded:
        return _config
    _config_loaded = True
    try:
        from bt_api_py.config_loader import load_exchange_config
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "{exchange}.yaml",
        )
        if os.path.exists(config_path):
            _config = load_exchange_config(config_path)
    except Exception as e:
        logger.warning("Failed to load config: %s", e)
    return _config

class {Exchange}ExchangeDataSpot({Exchange}ExchangeData):
    def __init__(self):
        super().__init__()
        self.asset_type = "spot"
        self._load_from_config("spot")
```

### 注册模式

```python
from bt_api_py.registry import ExchangeRegistry

def register_{exchange}():
    """注册到全局 ExchangeRegistry"""
    ExchangeRegistry.register_feed(
        "{EXCHANGE}___SPOT",
        {Exchange}RequestDataSpot
    )
    ExchangeRegistry.register_exchange_data(
        "{EXCHANGE}___SPOT",
        {Exchange}ExchangeDataSpot
    )
    ExchangeRegistry.register_balance_handler(
        "{EXCHANGE}___SPOT",
        _{exchange}_balance_handler
    )

# 自动注册
register_{exchange}()
```

## 常见问题解决

### 问题 1：配置加载失败

**错误**：`'ExchangeConfig' object has no attribute 'symbol_mappings'`

**解决**：访问原始配置而不是 pydantic 模型

```python
# 错误
mappings = config.symbol_mappings

# 正确
raw_mappings = getattr(config, '_raw_config', {}).get('symbol_mappings', {})
```

### 问题 2：GraphQL 查询错误

**错误**：`Cannot query field "X" on type "Y"`

**解决**：验证 GraphQL schema，移除无效字段

```python
# 错误 - 无效字段
query = "{ pools { tokens { id } liquidity } }"

# 正确 - 仅使用有效字段
query = "{ pools { id token0 { symbol } token1 { symbol } } }"
```

### 问题 3：路径未找到

**错误**：`raise_path_error` 被调用

**解决**：确保 YAML 配置包含所有必需的 `rest_paths`

```yaml
asset_types:
  spot:
    rest_paths:
      get_tick: "GET /api/v1/ticker"  # 必需
      get_depth: "GET /api/v1/depth"   # 必需
```

## 认证类型

| 类型 | 说明 | Headers |
|------|------|---------|
| `none` | 无需认证 | 无 |
| `api_key` | 仅 API Key | `X-API-KEY: {key}` |
| `hmac_sha256` | HMAC-SHA256 签名 | `X-API-KEY`, 签名在查询参数 |
| `hmac_sha256_passphrase` | OKX 风格 | API Key, Signature, Timestamp, Passphrase |
| `hmac_sha512` | Kraken 风格 | API-Key, API-Sign (base64) |

## 生成后检查清单

- [ ] 创建 `configs/{exchange}.yaml` 并配置正确的 API 端点
- [ ] 创建 `containers/exchanges/{exchange}_exchange_data.py` 并实现 `_load_from_config()`
- [ ] 创建 `feeds/live_{exchange}/` 目录及 `request_base.py` 和 `spot.py`
- [ ] 创建数据容器（至少 `{exchange}_ticker.py`）
- [ ] 创建 `feeds/register_{exchange}.py` 并实现自动注册
- [ ] 在 `bt_api_py/feeds/__init__.py` 或启动模块中导入注册
- [ ] 测试公开接口（get_tick, get_depth）
- [ ] 验证 YAML 配置路径与交易所 API 文档一致

## DEX 特定模式

对于 DEX（Uniswap, PancakeSwap, Balancer 等）：

```python
# GraphQL 查询执行
def _execute_graphql(self, query: str, variables: dict = None):
    response = requests.post(
        self.graphql_url,
        json={"query": query, "variables": variables},
        headers={"Content-Type": "application/json"}
    )
    return response.json()

# 获取池数据
def get_pools(self, extra_data=None, **kwargs):
    query = "{ pools(first: 100) { id token0 { symbol } token1 { symbol } } }"
    extra_data = extra_data or {}
    extra_data.update({
        "normalize_function": self._get_pools_normalize_function,
    })
    return self.request("GRAPHQL", query=query, extra_data=extra_data)
```

## 参考实现

- **Binance** — `bt_api_py/feeds/live_binance/` — HMAC-SHA256, 完整实现
- **OKX** — `bt_api_py/feeds/live_okx/` — Passphrase 认证, Mixin 架构
- **CoinEx** — `bt_api_py/feeds/live_coinex/` — 简单 HMAC-SHA256, 最小化实现
- **Uniswap** — `bt_api_py/feeds/live_uniswap/` — GraphQL DEX 实现
- **PancakeSwap** — `bt_api_py/feeds/live_pancakeswap/` — BSC DEX with GraphQL

## 版本历史

### v2.0.0
- 基于 20+ 交易所实现经验更新
- 新增 DEX/GraphQL 支持
- 明确 YAML 配置要求
- 修复配置加载模式
- 添加常见问题解决方案

### v1.1.0
- 初始模板生成功能
