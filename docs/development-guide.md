# bt_api_py 开发指南

生成日期：2026-03-07  
项目类型：Python Library  
Python版本：3.9-3.14

---

## 快速开始

### 1. 环境要求

**必需：**
- Python 3.9+ (当前兼容目标: 3.9, 3.10, 3.11, 3.12, 3.13, 3.14)
- pip 或 uv (包管理器)
- Git

**可选：**
- C++编译器 (用于CTP扩展)
- Cython (用于性能优化)

### 2. 克隆和安装

```bash
# 克隆仓库
git clone https://github.com/cloudQuant/bt_api_py.git
cd bt_api_py

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# 或
.venv\Scripts\activate     # Windows

# 安装开发依赖
make install-dev
# 或手动安装
pip install -e ".[dev]"
```

### 3. 验证安装

```bash
# 运行测试
make test

# 检查代码质量
make check

# 验证导入
python -c "from bt_api_py import BtApi; print('✓ 安装成功')"
```

---

## 开发工作流

### 代码质量检查

#### 1. 自动格式化

```bash
# 格式化代码
make format

# 或手动
ruff format bt_api_py/ tests/
ruff check --fix bt_api_py/ tests/
```

**自动修复：**
- 代码格式（100字符行宽）
- Import排序（isort风格）
- 移除未使用的import
- 简化代码结构

#### 2. Lint检查

```bash
# 运行linter
make lint

# 或手动
ruff check bt_api_py/ tests/
```

**检查规则：**
- E: pycodestyle错误
- W: pycodestyle警告
- F: pyflakes
- I: isort
- N: pep8-naming
- UP: pyupgrade
- B: flake8-bugbear
- C4: flake8-comprehensions
- SIM: flake8-simplify
- TCH: flake8-type-checking

**例外：**
- CTP模块允许CamelCase命名（N802, N803, N806）
- `__init__.py` 允许未使用的import（F401, F403）

#### 3. 类型检查

```bash
# 运行mypy
make type-check

# 或手动
mypy bt_api_py/
```

**配置：**
- 目标版本：Python 3.11
- 严格模式：部分启用
- 忽略缺失的import
- CTP模块忽略类型错误

#### 4. 完整检查

```bash
# 运行所有检查
make check
```

等同于：`lint` + `type-check`

---

## 测试

### 测试命令

#### 基础测试

```bash
# 运行所有测试（排除CTP）
make test
# 或
./scripts/run_tests.sh

# 快速测试（排除slow和network）
make test-fast
# 或
./scripts/run_tests.sh -m "not slow and not network"

# 单元测试
make test-unit
# 或
./scripts/run_tests.sh -m "unit"

# 集成测试
make test-integration
# 或
./scripts/run_tests.sh -m "integration"
```

#### 测试覆盖率

```bash
# 带覆盖率报告
make test-cov
# 或
./scripts/run_tests.sh --cov

# 查看HTML报告
open htmlcov/index.html
```

#### 测试报告

```bash
# 生成HTML报告
make test-html
# 或
./scripts/run_tests.sh --html --cov

# 报告位置
# logs/test_YYYYMMDD_HHMMSS.log
# logs/test_YYYYMMDD_HHMMSS.html
```

#### 并行测试

```bash
# 使用8个worker（默认）
./scripts/run_tests.sh

# 自定义并行数
./scripts/run_tests.sh -p 16

# 禁用并行
./scripts/run_tests.sh -p 0
```

#### 特定测试

```bash
# 运行单个文件
pytest tests/test_registry.py -v

# 运行单个测试类
pytest tests/test_registry.py::TestRegistryInstance -v

# 运行单个测试函数
pytest tests/test_registry.py::TestRegistryInstance::test_register_and_create_feed -v

# 运行特定标记
pytest tests -m binance -v
pytest tests -m "not slow and not network" -v
```

#### CTP测试

```bash
# 运行CTP测试（需要CTP环境）
make test-ctp
# 或
./scripts/run_tests.sh --ctp
```

### 测试标记

项目使用pytest标记分类测试：

```python
import pytest

@pytest.mark.unit
def test_fast_calculation():
    """快速单元测试，无外部依赖"""
    pass

@pytest.mark.integration
@pytest.mark.network
def test_api_call():
    """集成测试，需要网络"""
    pass

@pytest.mark.slow
def test_long_running():
    """慢速测试（>1秒）"""
    pass

@pytest.mark.binance
def test_binance_specific():
    """Binance特定测试"""
    pass

# 其他标记：ctp, okx, ib
```

### 测试最佳实践

#### 1. AAA模式

```python
def test_order_creation():
    # Arrange（准备）
    account = Account(balance=10000)
    symbol = "BTCUSDT"
    quantity = 0.1
    
    # Act（执行）
    order = account.create_order(symbol=symbol, quantity=quantity)
    
    # Assert（断言）
    assert order.symbol == symbol
    assert order.quantity == quantity
    assert order.status == "pending"
```

#### 2. 使用Fixtures

```python
@pytest.fixture
def mock_registry():
    """创建干净的注册表"""
    registry = ExchangeRegistry()
    yield registry
    registry.clear()

def test_with_fixture(mock_registry):
    mock_registry.register_feed("TEST", MockFeed)
    assert mock_registry.has_exchange("TEST")
```

#### 3. 测试异常

```python
def test_exchange_not_found():
    registry = ExchangeRegistry()
    
    with pytest.raises(ExchangeNotFoundError) as exc_info:
        registry.create_feed("NONEXISTENT", queue)
    
    assert "NONEXISTENT" in str(exc_info.value)
    assert exc_info.value.exchange_name == "NONEXISTENT"
```

#### 4. 参数化测试

```python
@pytest.mark.parametrize("exchange,symbol", [
    ("BINANCE___SPOT", "BTCUSDT"),
    ("OKX___SWAP", "BTC-USDT-SWAP"),
    ("HTX___SPOT", "btcusdt"),
])
def test_get_ticker(exchange, symbol):
    api = BtApi()
    ticker = api.get_tick(exchange, symbol)
    assert ticker.get_last_price() > 0
```

### 测试覆盖率目标

- **新代码**：>80%
- **关键路径**：100%（Registry, EventBus, BtApi）
- **整体项目**：>60%

查看覆盖率：
```bash
make test-cov
open htmlcov/index.html
```

---

## Pre-commit钩子

### 安装

```bash
# 安装pre-commit钩子
make pre-commit
# 或
pre-commit install
```

### 配置

`.pre-commit-config.yaml` 包含：

```yaml
repos:
  - repo: local
    hooks:
      - id: ruff-format
        name: ruff format
        entry: ruff format
        language: system
        types: [python]
        
      - id: ruff-check
        name: ruff check
        entry: ruff check --fix
        language: system
        types: [python]
        
      - id: mypy
        name: mypy
        entry: mypy
        language: system
        types: [python]
```

### 手动运行

```bash
# 对所有文件运行
make pre-commit-run
# 或
pre-commit run --all-files

# 对暂存文件运行
pre-commit run
```

---

## 代码风格指南

### 1. 命名约定

**模块和包：**
```python
# snake_case
live_binance.py
binance_order.py
```

**类：**
```python
# PascalCase
class BinanceSpotFeed:
    pass

class BinanceOrderData:
    pass
```

**函数和方法：**
```python
# snake_case
def get_ticker(symbol: str) -> TickerData:
    pass

def calculate_position_size() -> float:
    pass
```

**常量：**
```python
# UPPER_SNAKE_CASE
DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3
```

**私有属性：**
```python
# 前缀下划线
class ExchangeRegistry:
    def __init__(self):
        self._feed_classes: dict[str, type] = {}
        self._default = None
```

### 2. 类型提示

**公共API必须添加类型提示：**

```python
def calculate_position_size(
    account_balance: float,
    risk_percent: float,
    stop_loss_distance: float
) -> float:
    """计算仓位大小"""
    risk_amount = account_balance * (risk_percent / 100)
    return risk_amount / stop_loss_distance

# 使用现代Python 3.11+语法
def get_exchange_names(self) -> list[str]:
    return list(self._feed_classes.keys())

# 使用联合类型
def get_stream_class(
    self,
    exchange_name: str,
    stream_type: str
) -> Any | None:
    return self._stream_classes.get(exchange_name, {}).get(stream_type)
```

### 3. Docstring

**使用Google风格：**

```python
def register_feed(self, exchange_name: str, feed_class: type) -> None:
    """注册REST feed类
    
    Args:
        exchange_name: 交易所标识，如 "BINANCE___SPOT", "CTP___FUTURE"
        feed_class: Feed子类
        
    Raises:
        ValueError: 如果exchange_name为空
        
    Example:
        >>> registry = ExchangeRegistry()
        >>> registry.register_feed("BINANCE___SPOT", BinanceSpotFeed)
    """
    if not exchange_name:
        raise ValueError("exchange_name不能为空")
    self._feed_classes[exchange_name] = feed_class
```

### 4. 错误处理

**使用自定义异常：**

```python
# ✓ 正确
from bt_api_py.exceptions import (
    BtApiError,
    ExchangeNotFoundError,
    OrderError,
    InsufficientBalanceError,
)

def create_order(self, symbol: str, quantity: float) -> Order:
    if not self.has_balance(quantity):
        raise InsufficientBalanceError(
            exchange_name=self.exchange_name,
            symbol=symbol,
            required=quantity,
            available=self.balance
        )
    # 订单创建逻辑

# ✗ 错误
def create_order(self, symbol: str, quantity: float):
    assert quantity > 0  # 不要使用assert
    if not self.has_balance(quantity):
        raise Exception("Not enough balance")  # 不要使用通用异常
```

### 5. Import组织

**自动排序（ruff处理）：**

```python
# 标准库
import asyncio
from typing import Any, Protocol

# 第三方
import pytest
from aiohttp import ClientSession

# 本地导入
from bt_api_py.exceptions import BtApiError, ExchangeNotFoundError
from bt_api_py.registry import ExchangeRegistry
```

**规则：**
- 未使用的import自动移除
- Import自动排序和分组
- 使用 `from module import Item` 导入特定项
- 避免通配符导入（`from module import *`）

---

## 添加新交易所

### 1. 创建Feed类

```python
# bt_api_py/feeds/live_newexchange/spot.py

from bt_api_py.feeds.abstract_feed import AbstractVenueFeed

class NewExchangeSpotFeed(AbstractVenueFeed):
    """NewExchange现货Feed实现"""
    
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.api_key = kwargs.get("api_key")
        self.secret = kwargs.get("secret")
    
    def connect(self):
        """连接到交易所"""
        # 实现连接逻辑
        pass
    
    def subscribe_ticker(self, symbol: str):
        """订阅行情"""
        # 实现订阅逻辑
        pass
    
    # ... 其他必需方法
```

### 2. 创建数据容器（如需要）

```python
# bt_api_py/containers/orders/newexchange_order.py

from bt_api_py.containers.orders.order import OrderData

class NewExchangeOrderData(OrderData):
    """NewExchange订单数据容器"""
    
    def get_symbol(self) -> str:
        """获取交易对"""
        # 实现特定逻辑
        pass
    
    # ... 其他方法
```

### 3. 注册交易所

```python
# bt_api_py/exchange_registers/register_newexchange.py

from bt_api_py.registry import ExchangeRegistry
from bt_api_py.feeds.live_newexchange.spot import NewExchangeSpotFeed

# 注册Feed
ExchangeRegistry.register_feed("NEWEXCHANGE___SPOT", NewExchangeSpotFeed)

# 注册其他组件（如需要）
# ExchangeRegistry.register_stream("NEWEXCHANGE___SPOT", "market", NewExchangeMarketStream)
# ExchangeRegistry.register_exchange_data("NEWEXCHANGE___SPOT", NewExchangeData)
```

### 4. 编写测试

```python
# tests/feeds/test_live_newexchange_spot.py

import pytest
from bt_api_py import ExchangeRegistry
from bt_api_py.feeds.live_newexchange.spot import NewExchangeSpotFeed

@pytest.mark.newexchange
class TestNewExchangeSpotFeed:
    """NewExchange现货Feed测试"""
    
    @pytest.fixture
    def mock_feed(self):
        """创建mock feed"""
        return NewExchangeSpotFeed(
            data_queue=None,
            api_key="test_key",
            secret="test_secret"
        )
    
    def test_feed_registration(self):
        """测试Feed注册"""
        assert ExchangeRegistry.has_exchange("NEWEXCHANGE___SPOT")
    
    def test_connect(self, mock_feed):
        """测试连接"""
        mock_feed.connect()
        assert mock_feed.is_connected
    
    # ... 更多测试
```

### 5. 更新文档

在以下位置添加文档：
- `README.md` - 支持的交易所列表
- `docs/exchanges/newexchange/` - 交易所特定文档
- `CHANGELOG.md` - 变更日志

---

## 调试技巧

### 1. 使用调试器

**VS Code：**
```json
// .vscode/launch.json
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Debug Tests",
            "type": "python",
            "request": "launch",
            "module": "pytest",
            "args": ["tests/test_file.py", "-v", "-s"],
            "console": "integratedTerminal"
        }
    ]
}
```

**PyCharm：**
- 右键测试 → Debug 'test_function'

### 2. 添加断点

```python
def test_debugging():
    value = calculate_something()
    pytest.set_trace()  # 调试器断点
    assert value > 0
```

### 3. 查看日志

```bash
# 测试日志
tail -f logs/test_*.log

# 应用日志（如果配置）
tail -f logs/bt_api.log
```

### 4. 单步调试

```bash
# 运行单个测试并打印输出
pytest tests/test_file.py::test_function -v -s

# 详细模式
pytest tests/test_file.py::test_function -vv -s --tb=long
```

---

## 性能优化

### 1. 使用Cython

```python
# bt_api_py/functions/calculate_number.pyx

def calculate_numbers_by_cython(double[:] data):
    """Cython优化的数值计算"""
    cdef double result = 0.0
    cdef int i
    for i in range(len(data)):
        result += data[i]
    return result
```

编译：
```bash
python setup.py build_ext --inplace
```

### 2. 使用异步

```python
import asyncio
from bt_api_py import BtApi

async def get_multiple_tickers():
    api = BtApi()
    
    # 并发获取多个交易所的行情
    tasks = [
        api.get_tick_async("BINANCE___SPOT", "BTCUSDT"),
        api.get_tick_async("OKX___SWAP", "BTC-USDT-SWAP"),
        api.get_tick_async("HTX___SPOT", "btcusdt"),
    ]
    
    results = await asyncio.gather(*tasks)
    return results
```

### 3. 批量操作

```python
# ✓ 正确：批量获取
all_tickers = api.get_all_ticks("BTCUSDT")

# ✗ 低效：循环单个获取
for exchange in exchanges:
    ticker = api.get_tick(exchange, "BTCUSDT")
```

---

## 常见问题

### Q: 测试失败怎么办？

```bash
# 1. 查看详细错误
pytest tests/test_file.py -vv --tb=long

# 2. 运行单个测试
pytest tests/test_file.py::test_function -v -s

# 3. 检查日志
tail -f logs/test_*.log

# 4. 清理缓存
make clean-test
```

### Q: Import错误？

```bash
# 1. 确认安装
pip list | grep bt_api_py

# 2. 重新安装
pip install -e ".[dev]"

# 3. 检查Python路径
python -c "import sys; print('\n'.join(sys.path))"
```

### Q: 类型检查失败？

```bash
# 查看详细错误
mypy bt_api_py/ --show-error-codes

# 忽略特定行
# type: ignore
```

### Q: CTP编译失败？

```bash
# 1. 检查C++编译器
g++ --version  # Linux/macOS
cl             # Windows

# 2. 检查SWIG
swig -version

# 3. 查看编译日志
python setup.py build_ext --inplace 2>&1 | tee build.log
```

---

## 清理

```bash
# 清理构建产物
make clean

# 清理测试产物
make clean-test

# 清理所有
make clean-all
```

---

## 获取帮助

- **文档**：https://cloudquant.github.io/bt_api_py/
- **Issues**：https://github.com/cloudQuant/bt_api_py/issues
- **Email**：yunjinqi@gmail.com

---

## 下一步

1. ✓ 阅读 `CONTRIBUTING.md` 了解贡献流程
2. ✓ 运行 `make test` 确保测试通过
3. ✓ 运行 `make check` 确保代码质量
4. ✓ 查看 `docs/` 了解项目架构
5. ✓ 选择一个交易所实现作为参考
6. ✓ 开始贡献代码！
