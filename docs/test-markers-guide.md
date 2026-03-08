# 测试标记使用指南

本文档说明如何使用细粒度的pytest标记来运行不同类型的测试。

## 背景

bt_api_py项目包含大量测试，其中很多是公开行情数据测试（不需要鉴权），, 有些是需要鉴权的测试。为了方便测试和CI/CD流程
, 我们引入了细粒度的测试标记系统。

## 标记分类

### 1. 公开行情数据测试（不需要鉴权）

这些测试访问公开市场数据, 不需要API密钥或账户配置:

- `ticker`: Ticker/Tick 行情数据测试
- `kline`: K线/Bar数据测试
- `orderbook`: 订单簿/深度数据测试
- `public_trade`: 公开成交记录测试

### 2. 需要鉴权的测试

这些测试需要真实的API密钥和账户配置:

- `auth_account`: 账户和余额测试
- `auth_order`: 订单管理测试
- `auth_position`: 持仓管理测试
- `auth_private_trade`: 私有成交记录测试

### 3. 传统标记（仍然支持）

- `network`: 需要网络访问的测试
- `integration`: 集成测试
- `slow`: 慢速测试（> 1s）

## 使用方法

### 运行公开行情数据测试

```bash
# 运行所有ticker测试
pytest tests -m ticker

# 运行所有kline测试
pytest tests -m kline

# 运行所有orderbook测试
pytest tests -m orderbook

# 运行所有public_trade测试
pytest tests -m public_trade

# 运行所有公开行情数据测试
pytest tests -m "ticker or kline or orderbook or public_trade"

# 排除需要鉴权的测试
pytest tests -m "not (auth_account or auth_order or auth_position or auth_private_trade)"
```

### 运行需要鉴权的测试

```bash
# 运行账户测试（需要配置.env文件）
pytest tests -m auth_account

# 运行订单测试
pytest tests -m auth_order

# 运行持仓测试
pytest tests -m auth_position

# 运行所有鉴权测试
pytest tests -m "auth_account or auth_order or auth_position or auth_private_trade"
```

### 使用便捷脚本

我们提供了便捷脚本来运行测试:

#### 公开行情数据测试

```bash
# 运行所有行情数据测试
./scripts/run_market_tests.sh all

# 只运行ticker测试
./scripts/run_market_tests.sh ticker

# 只运行kline测试（详细输出）
./scripts/run_market_tests.sh kline -v

# 运行所有行情测试（8个worker，生成HTML报告）
./scripts/run_market_tests.sh all -n 8 --html
```

#### 鉴权测试

```bash
# 运行所有鉴权测试（需要配置.env）
./scripts/run_auth_tests.sh all

# 只运行订单测试
./scripts/run_auth_tests.sh order

# 只运行账户测试（详细输出）
./scripts/run_auth_tests.sh account -v

# 运行所有鉴权测试（生成HTML报告）
./scripts/run_auth_tests.sh all --html
```

## CI/CD 集成

### GitHub Actions 配置示例

```yaml
name: Tests

on: [push, pull_request]

jobs:
  public-market-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: pip install -e ".[dev]"
      - name: Run public market data tests
        run: pytest tests -m "ticker or kline or orderbook or public_trade" -n 4

  auth-tests:
    runs-on: ubuntu-latest
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: pip install -e ".[dev]"
      - name: Run auth tests
        env:
          BINANCE_API_KEY: ${{ secrets.BINANCE_API_KEY }}
          BINANCE_SECRET_KEY: ${{ secrets.BINANCE_SECRET_KEY }}
          OKX_API_KEY: ${{ secrets.OKX_API_KEY }}
          OKX_SECRET_KEY: ${{ secrets.OKX_SECRET_KEY }}
          OKX_PASSPHRASE: ${{ secrets.OKX_PASSPHRASE }}
        run: pytest tests -m "auth_account or auth_order" -n 2
```

## 测试统计

当前标记统计（2026-03-08）：

- `auth_account`: 67 个测试
- `auth_order`: 36 个测试
- `auth_position`: 30 个测试
- `auth_private_trade`: 8 个测试
- `ticker`: 4 个测试
- `kline`: 1 个测试
- `orderbook`: 1 个测试
- `public_trade`: 7 个测试

## 最佳实践

### 1. 本地开发

```bash
# 快速验证（只跑不需要鉴权的测试）
pytest tests -m "ticker or kline" -n 4

# 完整验证（包括鉴权测试）
pytest tests -m "not network" -n 4
```

### 2. PR 检查

```bash
# PR 应该至少跑通公开行情测试
pytest tests -m "ticker or kline or orderbook or public_trade" -n 4
```

### 3. 发布前验证

```bash
# 发布前应该运行所有测试（包括鉴权测试）
pytest tests -m "not network" -n 4
pytest tests -m "auth_account or auth_order or auth_position" -n 2
```

## 故障排查

### 问题：测试被跳过

**原因**: 测试被标记为 `network` 但没有运行network测试

**解决**:
```bash
# 确保包含network标记
pytest tests -m "network and ticker"
```

### 问题：鉴权测试失败

**原因**: 没有配置 `.env` 文件或API密钥无效

**解决**:
1. 创建 `.env` 文件
2. 配置正确的API密钥
3. 确保账户有足够余额
4. 确保IP在白名单中

## 更新日志

- **2026-03-08**: 初始版本，添加细粒度测试标记
- **2026-03-08**: 添加便捷测试运行脚本
