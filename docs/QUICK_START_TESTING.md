# 测试快速上手指南

## 一分钟快速开始

### 运行公开行情数据测试（不需要API密钥）

```bash
# 运行所有公开行情测试
./scripts/run_market_tests.sh all

# 或使用pytest
pytest tests -m "ticker or kline or orderbook or public_trade" -n 4
```

## 常用命令

### 公开数据测试

```bash
# 只测ticker数据
pytest tests -m ticker -v

# 只测kline数据
pytest tests -m kline -v

# 只测orderbook数据
pytest tests -m orderbook -v

# 只测public_trade数据
pytest tests -m public_trade -v

# 所有公开数据
pytest tests -m "ticker or kline or orderbook or public_trade" -n 4
```

### 鉴权测试（需要API密钥）

```bash
# 配置.env文件后
./scripts/run_auth_tests.sh all

# 或使用pytest
pytest tests -m "auth_account or auth_order or auth_position" -n 2
```

### 快速验证

```bash
# 快速检查（排除慢速和网络测试）
pytest tests -m "not slow and not network" -n 4

# 完整验证（包括鉴权测试）
pytest tests -m "not network" -n 4
./scripts/run_auth_tests.sh all -n 2
```

## 测试标记速查表

| 标记 | 说明 | 是否需要鉴权 | 测试数量 |
|------|------|-------------|---------|
| `ticker` | Ticker/Tick数据 | ❌ 否 | 556 |
| `kline` | K线/Bar数据 | ❌ 否 | 404 |
| `orderbook` | 订单簿/深度 | ❌ 否 | 366 |
| `public_trade` | 公开成交 | ❌ 否 | 49 |
| `auth_account` | 账户余额 | ✅ 是 | 90 |
| `auth_order` | 订单管理 | ✅ 是 | 58 |
| `auth_position` | 持仓管理 | ✅ 是 | 30 |
| `auth_private_trade` | 私有成交 | ✅ 是 | 8 |

## 便捷脚本

### run_market_tests.sh

```bash
# 查看帮助
./scripts/run_market_tests.sh --help

# 运行测试
./scripts/run_market_tests.sh ticker      # 只测ticker
./scripts/run_market_tests.sh all         # 所有行情数据
./scripts/run_market_tests.sh all -n 8    # 8个并行worker
./scripts/run_market_tests.sh all --html  # 生成HTML报告
```

### run_auth_tests.sh

```bash
# 查看帮助
./scripts/run_auth_tests.sh --help

# 运行测试
./scripts/run_auth_tests.sh account       # 只测账户
./scripts/run_auth_tests.sh all           # 所有鉴权测试
./scripts/run_auth_tests.sh all -n 4      # 4个并行worker
./scripts/run_auth_tests.sh all --html    # 生成HTML报告
```

## 使用场景

### 本地开发
```bash
# 快速验证代码修改
./scripts/run_market_tests.sh all -n 8
```

### PR检查
```bash
# 确保公开数据功能正常
pytest tests -m "ticker or kline or orderbook or public_trade" -n 4
```

### 发布前验证
```bash
# 完整验证
pytest tests -m "not network" -n 4
./scripts/run_auth_tests.sh all -n 2
```

## 环境配置

### 公开数据测试
无需配置，直接运行即可。

### 鉴权测试

1. 创建 `.env` 文件：
```bash
BINANCE_API_KEY=your_api_key
BINANCE_SECRET_KEY=your_secret_key
OKX_API_KEY=your_api_key
OKX_SECRET_KEY=your_secret_key
OKX_PASSPHRASE=your_passphrase
```

2. 或创建 `account_config.json`：
```json
{
  "binance": {
    "public_key": "your_api_key",
    "private_key": "your_secret_key"
  },
  "okx": {
    "public_key": "your_api_key",
    "private_key": "your_secret_key",
    "passphrase": "your_passphrase"
  }
}
```

## 故障排查

### 问题：测试被跳过
**原因**: 测试需要网络连接或API密钥
**解决**: 
```bash
# 确保运行正确的标记
pytest tests -m ticker  # 而不是 pytest tests -m network
```

### 问题：鉴权测试失败
**原因**: API密钥无效或IP不在白名单
**解决**: 检查 `.env` 配置和IP白名单设置

## 更多信息

- [详细使用指南](test-markers-guide.md)
- [完整优化报告](test-optimization-complete.md)
- [AGENTS.md](../AGENTS.md)

## 需要帮助？

```bash
# 查看所有可用标记
pytest --markers

# 查看测试统计
pytest tests --co -q | wc -l

# 查看特定标记的测试
pytest tests -m ticker --co -q
```
