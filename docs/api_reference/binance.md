# Binance API 参考

Binance 交易所的 API 接口文档。

## 交易所标识

| 市场 | 标识 | 说明 |

|------|------|------|

| 现货 | `BINANCE___SPOT` | 加密货币现货交易 |

| 合约 | `BINANCE___SWAP` | USDT 本位永续合约 |

| 杠杆 | `BINANCE___MARGIN` | 杠杆交易 |

## 认证配置

```python
exchange_kwargs = {
    "BINANCE___SPOT": {
        "api_key": "your_api_key",
        "secret": "your_secret",
        "testnet": False,  # 是否使用测试网
    }
}

```bash

## API 端点

### 现货端点

| 环境 | REST Base URL | WebSocket |

|------|---------------|-----------|

| 生产环境 | `<https://api.binance.com`> | `wss://stream.binance.com:9443` |

| 测试环境 | `<https://testnet.binance.vision`> | `wss://testnet.binance.vision` |

### 合约端点

| 环境 | REST Base URL | WebSocket |

|------|---------------|-----------|

| 生产环境 | `<https://fapi.binance.com`> | `wss://fstream.binance.com` |

| 测试环境 | `<https://testnet.binancefuture.com`> | `wss://stream.binancefuture.com` |

## 特殊参数

### 交易对格式

现货使用大写格式：`BTCUSDT`
合约使用中划线格式：`BTC-USDT`

### 时间窗口

Binance 使用接收时间窗口（recvWindow）来防止重放攻击，默认值为 5000 毫秒。

### 签名

所有私有接口都需要签名验证，签名使用 HMAC SHA256 算法。

## 常用 API

### 获取交易规则

```python
api = api.get_request_api("BINANCE___SPOT")
exchange_info = api.get_exchange_info()

```bash

### 获取服务器时间

```python
server_time = api.get_server_time()

```bash

## 错误码

| 代码 | 说明 |

|------|------|

| -1000 | 未知错误 |

| -1001 | 断开连接 |

| -1002 | 未授权 |

| -1003 | 请求过多 |

| -1006 | 过于频繁 |

| -1007 | 已封禁 IP |

| -1013 | 无效的 API Key |

| -1014 | 无效的签名 |

| -1021 | 时间戳超出范围 |

| -1100 | 非法字符 |

| -1101 | 非法参数 |

| -1102 | 强制参数为空 |

| -1103 | 非法参数值 |

| -1104 | 未读的参数 |

| -1105 | 参数过长 |

| -1106 | 无效的参数 |

| -1112 | 无效的量 |

| -1114 | 不可用的 API |

| -1115 | taker 过多 |

| -1116 | 订单过多 |

| -1120 | 无效的 interval |

| -1121 | 无效的 K 线 |

| -1128 | 余额不足 |

| -1130 | 无效的订单类型 |

| -1131 | 无效的订单状态 |

## 更多信息

- [Binance 官方文档](<https://binance-docs.github.io/apidocs/)>
- [Binance API 覆盖率](../binance_api_implementation_status.md)
- [Binance 待实现 API](../binance_api_missing_apis.md)
