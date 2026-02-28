# 常见问题 (FAQ)

## 安装和配置

### Q: 如何安装 bt_api_py？

**A:** 有两种安装方式：

```bash
# 从 PyPI 安装（推荐）
pip install bt_api_py

# 从源码安装
git clone https://github.com/cloudQuant/bt_api_py
cd bt_api_py
pip install -e .
```

### Q: 安装后导入失败，提示缺少依赖？

**A:** 某些交易所需要额外的依赖包：

```bash
# 安装所有可选依赖
pip install bt_api_py[all]

# 或按需安装
pip install bt_api_py[ib_web]  # IB Web API
pip install bt_api_py[visualization]  # 图表功能
```

### Q: CTP 扩展编译失败怎么办？

**A:** 确保已安装编译工具：

```bash
# Linux
sudo apt-get install build-essential

# macOS
xcode-select --install

# Windows
# 安装 Visual Studio Build Tools
```

## API 密钥和认证

### Q: 如何安全地存储 API 密钥？

**A:** 推荐使用环境变量或 `.env` 文件：

```bash
# 创建 .env 文件
BINANCE_API_KEY=your_api_key
BINANCE_SECRET=your_secret

# 在代码中使用
from bt_api_py.security import SecureCredentialManager

manager = SecureCredentialManager()
credentials = manager.get_exchange_credentials("BINANCE")
```

**重要**: 永远不要将 API 密钥硬编码在代码中或提交到 Git！

### Q: 测试时如何跳过需要 API 密钥的测试？

**A:** 设置环境变量：

```bash
export SKIP_LIVE_TESTS=true
pytest tests
```

### Q: Binance 报错 "Invalid API-key, IP, or permissions"？

**A:** 检查以下几点：

1. API 密钥是否正确
2. IP 地址是否在白名单中
3. API 权限是否足够（现货/合约/提现等）
4. 是否使用了正确的网络（主网/测试网）

## 交易相关

### Q: 如何在多个交易所之间切换？

**A:** 使用统一的 `BtApi` 接口：

```python
from bt_api_py import BtApi

exchange_kwargs = {
    "BINANCE___SPOT": {"api_key": "...", "secret": "..."},
    "OKX___SPOT": {"api_key": "...", "secret": "...", "passphrase": "..."},
}

api = BtApi(exchange_kwargs=exchange_kwargs)

# 在不同交易所下单
api.limit_order("BINANCE___SPOT", "BTCUSDT", "buy", 0.001, 50000)
api.limit_order("OKX___SPOT", "BTC-USDT", "buy", 0.001, 50000)
```

### Q: 如何订阅实时行情？

**A:** 使用 WebSocket 订阅：

```python
def on_ticker(ticker):
    print(f"价格: {ticker.last_price}")

api.subscribe_ticker("BINANCE___SPOT", "BTCUSDT", on_ticker)
api.run()  # 启动事件循环
```

### Q: 下单失败，提示余额不足？

**A:** 检查：

1. 账户余额是否充足
2. 是否考虑了手续费
3. 最小下单量是否满足
4. 是否有足够的可用余额（未冻结）

### Q: 如何处理订单被拒绝的情况？

**A:** 使用异常处理：

```python
from bt_api_py.exceptions import OrderError, InsufficientBalanceError

try:
    order = api.limit_order("BINANCE___SPOT", "BTCUSDT", "buy", 0.001, 50000)
except InsufficientBalanceError as e:
    print(f"余额不足: {e}")
except OrderError as e:
    print(f"下单失败: {e}")
```

## 性能优化

### Q: 如何提高 API 请求速度？

**A:** 使用异步接口：

```python
import asyncio

async def main():
    # 并发请求多个交易对
    tasks = [
        api.get_ticker_async("BINANCE___SPOT", "BTCUSDT"),
        api.get_ticker_async("BINANCE___SPOT", "ETHUSDT"),
        api.get_ticker_async("BINANCE___SPOT", "BNBUSDT"),
    ]
    results = await asyncio.gather(*tasks)
    return results

asyncio.run(main())
```

### Q: WebSocket 连接频繁断开怎么办？

**A:** 启用自动重连：

```python
# 在交易所配置中启用重连
exchange_kwargs = {
    "BINANCE___SPOT": {
        "api_key": "...",
        "secret": "...",
        "auto_reconnect": True,  # 自动重连
        "reconnect_delay": 5,    # 重连延迟（秒）
    }
}
```

### Q: 如何避免触发速率限制？

**A:** 使用内置的速率限制器：

```python
from bt_api_py.rate_limiter import RateLimiter

# 创建速率限制器（每秒最多 10 个请求）
limiter = RateLimiter(max_requests=10, time_window=1.0)

# 在请求前检查
with limiter:
    ticker = api.get_ticker("BINANCE___SPOT", "BTCUSDT")
```

## 数据处理

### Q: 如何获取历史 K 线数据？

**A:**

```python
from datetime import datetime, timedelta

end_time = datetime.now()
start_time = end_time - timedelta(days=7)

bars = api.get_kline(
    "BINANCE___SPOT",
    "BTCUSDT",
    period="1h",
    start_time=start_time,
    end_time=end_time
)

# 转换为 DataFrame
import pandas as pd
df = pd.DataFrame([bar.__dict__ for bar in bars])
```

### Q: 如何处理不同交易所的数据格式差异？

**A:** bt_api_py 已经统一了数据格式，所有交易所返回相同的数据容器：

```python
# 所有交易所返回相同的 Ticker 对象
ticker_binance = api.get_ticker("BINANCE___SPOT", "BTCUSDT")
ticker_okx = api.get_ticker("OKX___SPOT", "BTC-USDT")

# 访问方式完全相同
print(ticker_binance.last_price)
print(ticker_okx.last_price)
```

## 错误处理

### Q: 遇到网络错误如何处理？

**A:** 使用重试机制：

```python
from bt_api_py.exceptions import NetworkError
import time

max_retries = 3
for attempt in range(max_retries):
    try:
        ticker = api.get_ticker("BINANCE___SPOT", "BTCUSDT")
        break
    except NetworkError as e:
        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)  # 指数退避
        else:
            raise
```

### Q: 如何调试 API 调用？

**A:** 启用调试日志：

```python
api = BtApi(exchange_kwargs=exchange_kwargs, debug=True)

# 或使用日志系统
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="debug.log",
    logger_name="debug",
    print_info=True  # 打印到控制台
).create_logger()
```

## CTP 特定问题

### Q: CTP 连接失败，提示 "CTP3:不合法的登录"？

**A:** 检查：

1. 用户名、密码是否正确
2. 经纪商 ID 是否正确
3. 是否在交易时间内
4. 账户是否已激活

### Q: CTP 如何查询持仓？

**A:**

```python
positions = api.get_position("CTP___FUTURE", "IF2506")
for pos in positions:
    print(f"合约: {pos.symbol}, 持仓: {pos.quantity}, 方向: {pos.side}")
```

## IB 特定问题

### Q: IB Web API 认证失败？

**A:** 确保：

1. 已生成 JWT 密钥对
2. 密钥文件路径正确
3. 账户 ID 正确
4. IB Gateway 或 TWS 已启动

### Q: IB 如何订阅美股行情？

**A:**

```python
def on_ticker(ticker):
    print(f"{ticker.symbol}: ${ticker.last_price}")

api.subscribe_ticker("IB_WEB___STK", "AAPL", on_ticker)
api.run()
```

## 其他问题

### Q: 支持哪些 Python 版本？

**A:** Python 3.11, 3.12, 3.13

### Q: 支持哪些操作系统？

**A:** Linux (x86_64), Windows (x64), macOS (arm64/x86_64)

### Q: 如何贡献代码？

**A:** 

1. Fork 项目
2. 创建功能分支
3. 提交 Pull Request
4. 参考 [开发者指南](developer_guide.md)

### Q: 在哪里报告 Bug？

**A:** 在 [GitHub Issues](https://github.com/cloudQuant/bt_api_py/issues) 提交问题

### Q: 如何获取帮助？

**A:**

- 查看 [在线文档](https://cloudquant.github.io/bt_api_py/)
- 阅读 [示例代码](examples/)
- 提交 [GitHub Issue](https://github.com/cloudQuant/bt_api_py/issues)
- 发送邮件至 yunjinqi@gmail.com
