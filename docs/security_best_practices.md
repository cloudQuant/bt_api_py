# 安全最佳实践

## API 密钥管理

### 1. 永远不要硬编码 API 密钥

❌ ***错误做法**:
```python
api_key = "abcd1234efgh5678"  # 不要这样做！
secret = "xyz9876543210"
```

✅ ***正确做法**:
```python
import os
api_key = os.getenv("BINANCE_API_KEY")
secret = os.getenv("BINANCE_SECRET")
```

### 2. 使用 .env 文件

创建 `.env` 文件存储密钥：

```bash
# .env
BINANCE_API_KEY=your_api_key_here
BINANCE_SECRET=your_secret_here
```

***重要**: 将 `.env` 添加到 `.gitignore`：

```bash
echo ".env" >> .gitignore
```

### 3. 使用加密存储

对于敏感环境，使用加密存储：

```python
from bt_api_py.security import SecureCredentialManager

# 使用加密密钥
manager = SecureCredentialManager(encryption_key="your_master_password")

# 加密存储
encrypted = manager.encrypt_credential("your_api_key")

# 解密使用
api_key = manager.decrypt_credential(encrypted)
```

### 4. 限制 API 密钥权限

在交易所设置中：

- ✅ 只启用必需的权限（交易、查询等）
- ❌ 不要启用提现权限（除非必需）
- ✅ 设置 IP 白名单
- ✅ 使用子账户进行测试

### 5. 定期轮换密钥

- 每 3-6 个月更换一次 API 密钥
- 发现泄露立即更换
- 保留旧密钥的备份（加密存储）

## 网络安全

### 1. 使用 HTTPS

所有 API 请求都应使用 HTTPS：

```python
# bt_api_py 默认使用 HTTPS
api = BtApi(exchange_kwargs=exchange_kwargs)
```

### 2. 验证 SSL 证书

```python
exchange_kwargs = {
    "BINANCE___SPOT": {
        "api_key": api_key,
        "secret": secret,
        "verify_ssl": True,  # 验证 SSL 证书
    }
}
```

### 3. 使用代理（可选）

在需要时使用代理：

```python
exchange_kwargs = {
    "BINANCE___SPOT": {
        "api_key": api_key,
        "secret": secret,
        "proxy": "http://proxy.example.com:8080",
    }
}
```

## 日志安全

### 1. 不要记录敏感信息

❌ ***错误做法**:
```python
logger.info(f"API Key: {api_key}")  # 不要记录密钥！
```

✅ ***正确做法**:
```python
from bt_api_py.security import SecureCredentialManager

masked = SecureCredentialManager.mask_credential(api_key)
logger.info(f"API Key: {masked}")  # abcd****wxyz
```

### 2. 安全的日志配置

```python
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="trading.log",
    logger_name="trading",
    print_info=False  # 生产环境不打印到控制台
).create_logger()
```

### 3. 定期清理日志

```bash
# 定期删除旧日志
find ./logs -name "*.log" -mtime +30 -delete
```

## 代码安全

### 1. 输入验证

```python
from bt_api_py.security import SecureCredentialManager

manager = SecureCredentialManager()

# 验证 API 密钥格式
if not manager.validate_api_key(api_key):
    raise ValueError("Invalid API key format")
```

### 2. 异常处理

不要在异常信息中暴露敏感数据：

```python
from bt_api_py.exceptions import AuthenticationError

try:
    api.get_ticker("BINANCE___SPOT", "BTCUSDT")
except AuthenticationError as e:
    # 不要记录完整的错误信息（可能包含密钥）
    logger.error("Authentication failed")
```

### 3. 使用类型检查

```python
from typing import Optional

def get_api_key(key_name: str) -> Optional[str]:
    """安全地获取 API 密钥"""
    key = os.getenv(key_name)
    if key and SecureCredentialManager.validate_api_key(key):
        return key
    return None
```

## 部署安全

### 1. 环境隔离

```bash
# 开发环境
export BINANCE_TESTNET=true
export BINANCE_API_KEY=dev_key

# 生产环境
export BINANCE_TESTNET=false
export BINANCE_API_KEY=prod_key
```

### 2. 使用密钥管理服务

在生产环境中使用专业的密钥管理：

- AWS Secrets Manager
- Azure Key Vault
- HashiCorp Vault
- Google Cloud Secret Manager

### 3. 容器化部署

使用 Docker secrets：

```dockerfile
# docker-compose.yml
version: '3.8'
services:
  trading_bot:
    image: bt_api_py:latest
    secrets:
      - binance_api_key
      - binance_secret

secrets:
  binance_api_key:
    external: true
  binance_secret:
    external: true
```

## 监控和审计

### 1. 监控异常活动

```python
from bt_api_py.exceptions import RateLimitError, AuthenticationError

# 记录异常登录尝试
try:
    api.get_ticker("BINANCE___SPOT", "BTCUSDT")
except AuthenticationError:
    logger.warning("Authentication failed - possible security breach")
    # 发送告警
```

### 2. 审计日志

记录所有重要操作：

```python
logger.info(f"Order placed: {order.symbol} {order.side} {order.quantity}")
logger.info(f"Order cancelled: {order_id}")
```

### 3. 设置告警

```python
# 监控大额交易
if order.quantity * order.price > 10000:
    logger.warning(f"Large order detected: ${order.quantity * order.price}")
    # 发送告警邮件/短信
```

## 测试安全

### 1. 使用测试网

```python
exchange_kwargs = {
    "BINANCE___SPOT": {
        "api_key": test_api_key,
        "secret": test_secret,
        "testnet": True,  # 使用测试网
    }
}
```

### 2. 模拟测试

```python
# 使用 mock 进行测试，不需要真实 API 密钥
from unittest.mock import Mock

mock_api = Mock()
mock_api.get_ticker.return_value = Mock(last_price=50000)
```

### 3. CI/CD 中跳过实时测试

```bash
# 在 CI 环境中设置
export SKIP_LIVE_TESTS=true
pytest tests
```

## 应急响应

### 1. API 密钥泄露处理流程

1. **立即撤销**泄露的 API 密钥
2. **生成新密钥**并更新配置
3. **检查账户**是否有异常交易
4. **审查日志**查找可疑活动
5. **通知相关人员**

### 2. 安全事件记录

```python
# 记录安全事件
logger.critical(f"Security incident: API key compromised at {datetime.now()}")
```

### 3. 紧急联系方式

保存交易所客服联系方式：

- Binance: support@binance.com
- OKX: support@okx.com
- 紧急电话号码

## 合规要求

### 1. 数据保护

- 遵守 GDPR、CCPA 等数据保护法规
- 不要存储不必要的用户数据
- 定期删除过期数据

### 2. 交易记录

- 保留完整的交易记录
- 定期备份重要数据
- 确保数据完整性

### 3. 访问控制

- 实施最小权限原则
- 使用多因素认证
- 定期审查访问权限

## 安全检查清单

- [ ] API 密钥存储在环境变量中
- [ ] `.env` 文件已添加到 `.gitignore`
- [ ] 启用了 IP 白名单
- [ ] 限制了 API 权限
- [ ] 使用 HTTPS 连接
- [ ] 启用了 SSL 证书验证
- [ ] 日志中不包含敏感信息
- [ ] 实施了异常处理
- [ ] 设置了监控和告警
- [ ] 定期轮换 API 密钥
- [ ] 使用测试网进行开发
- [ ] CI/CD 中跳过实时测试
- [ ] 制定了应急响应计划

## 参考资源

- [OWASP API Security Top 10](https://owasp.org/www-project-api-security/)
- [CWE Top 25 Most Dangerous Software Weaknesses](https://cwe.mitre.org/top25/)
- [NIST Cybersecurity Framework](https://www.nist.gov/cyberframework)
