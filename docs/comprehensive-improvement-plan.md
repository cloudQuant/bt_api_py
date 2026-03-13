# bt_api_py 综合改进优化方案

**分析日期**: 2026-03-13
**项目版本**: v0.15.0+
**代码规模**: ~202,000 行 Python 代码, 1,762 个文件, 72 个交易所适配器

---

## 目录

1. [架构改进](#1-架构改进)
2. [代码质量改进](#2-代码质量改进)
3. [性能优化](#3-性能优化)
4. [测试改进](#4-测试改进)
5. [安全加固](#5-安全加固)
6. [文档完善](#6-文档完善)
7. [CI/CD 改进](#7-cicd-改进)
8. [优先级与执行计划](#8-优先级与执行计划)

---

## 1. 架构改进

### 1.1 BtApi 类拆分 (关键)

**问题**: `bt_api.py` 的 `BtApi` 类承担了 20+ 项职责（行情查询、下单、账户管理、数据下载、事件分发等），违反单一职责原则，循环复杂度 > 15。

**现状**:
```python
# bt_api.py - 556 行, 20+ 方法全部在一个类中
class BtApi:
    def get_tick(...)      # 行情
    def make_order(...)    # 交易
    def get_balance(...)   # 账户
    def download_history_bars(...)  # 数据下载
    def subscribe(...)     # WebSocket 订阅
    def update_total_balance(...)   # 余额汇总
```

**建议**: 拆分为以下子组件:
- `ExchangeManager` — 交易所连接/断开、feed 管理
- `MarketDataService` — 行情查询 (get_tick, get_depth, get_kline)
- `OrderService` — 下单/撤单/查单
- `AccountService` — 余额/持仓/账户查询
- `DataDownloadService` — 历史数据下载
- `BtApi` 仅作为门面(Facade)委托调用

**文件位置**: `bt_api_py/bt_api.py`
**优先级**: 高

---

### 1.2 异步模式统一 (关键)

**问题**: 当前项目混合使用 `run_in_executor` 模拟异步和真正的 `async/await`，`__getattr__` 中的 async 代理实际上是同步调用的包装。

**现状** (`bt_api.py:483-496`):
```python
def __getattr__(self, name):
    if name.startswith("async_"):
        def _async_proxy(exchange_name, *args, **kwargs):
            feed = self._get_feed(exchange_name)
            # 实际调用的是同步方法，并非真正异步
            feed_method = getattr(feed, name, None)
            return feed_method(*args, **kwargs)
        return _async_proxy
```

**建议**:
- 为主要交易所实现原生 `async` HTTP 客户端 (基于 `httpx.AsyncClient` 或 `aiohttp`)
- 将 `__getattr__` 动态代理替换为显式的 `async` 方法声明
- 保留同步接口，但内部通过 `asyncio.run()` 桥接异步实现

**优先级**: 高

---

### 1.3 全局单例 Registry 改进

**问题**: `ExchangeRegistry` 使用 `_RegistryMeta` 元类和 `__new__` 实现单例，测试隔离困难，全局状态可能导致内存泄漏。

**现状** (`registry.py:80-94`):
```python
def __new__(cls):
    """确保 ExchangeRegistry() 返回全局单例实例"""
    if cls._default is not None:
        return cls._default
    with cls._default_lock:
        if cls._default is None:
            instance = super().__new__(cls)
            # ... 初始化
            cls._default = instance
        return cls._default
```

**建议**:
- 添加 `ExchangeRegistry.create_isolated()` 工厂方法，用于测试隔离
- 在 `_create_feed()` 中添加实例缓存和弱引用清理
- 移除 `_RegistryMeta` 元类魔法，改用标准依赖注入

**文件位置**: `bt_api_py/registry.py`
**优先级**: 中

---

### 1.4 Gateway 架构改进

**问题**: `RuntimeGateway` 包含 CTP 特定初始化逻辑，命令分发使用字符串匹配。

**现状** (`gateway/runtime.py`):
- CTP 特定逻辑嵌入通用 Gateway（`runtime.py:62-81`）
- 命令分发使用 56 行字符串匹配，无类型安全
- 使用 `time.sleep(0.2)` 进行非确定性等待
- `_flush_adapter_output()` 存在忙等待循环

**建议**:
- 将 CTP 初始化抽取为独立适配器
- 命令分发改用 `Enum + dict` 映射模式
- 使用 `threading.Event` 替代 `time.sleep()`
- 在 flush 循环中添加退避策略

**优先级**: 中

---

### 1.5 依赖注入容器改进

**问题**: `core/dependency_injection.py` 使用运行时 `inspect.signature()` 解析依赖，性能开销大且无缓存。

**现状** (`core/dependency_injection.py:92-112`):
- `_create_instance()` 每次调用都做反射解析
- 未解析的依赖静默回退到默认值
- 全局 `_global_container` 实例造成隐式耦合
- 不可递归锁 (`threading.RLock`) 在无嵌套场景下不必要

**建议**:
- 缓存 `inspect.signature()` 结果
- 未解析依赖时记录日志或抛出异常
- 添加容器诊断方法 (`list_services()`, `resolve_trace()`)
- 添加生命周期钩子 (before_resolve, after_resolve)

**优先级**: 低

---

## 2. 代码质量改进

### 2.1 类型注解补全 (关键)

**问题**: 3,595 个类型注解问题（2,505 个缺失类型, 1,090 个缺失文档字符串），61/62 个文件待改进。MyPy 配置中对大量模块设置了 `ignore_errors = true`。

**现状** (`pyproject.toml:184-336`):
- 24 个 `[[tool.mypy.overrides]]` 块忽略了 feeds、containers、websocket、security_compliance 等核心模块的类型错误
- `disallow_untyped_defs = false` 允许无类型定义
- 仅 `exceptions.py` 和 `event_bus.py` 启用了严格类型检查

**建议执行顺序**:
1. `registry.py` → `bt_api.py` → `logging_factory.py` (核心模块)
2. `cache.py` → `rate_limiter.py` → `connection_pool.py` (基础设施)
3. `auth_config.py` → `security.py` (安全模块)
4. `containers/` 子模块 (数据容器)
5. `feeds/` 适配器 (交易所适配器)

**优先级**: 高

---

### 2.2 不可变性违反修复

**问题**: 多处代码直接修改字典/列表，违反不可变性原则。

**示例**:
- `gateway/client.py:107` — 直接修改 order 响应 dict
- `gateway/client.py:175-181` — `_remember_order()` 使用 `.update()` 修改共享状态
- `cache.py` — 缓存清理直接修改内部集合
- `rate_limiter.py` — 请求队列原地修改

**建议**: 统一使用以下模式:
```python
# 错误：直接修改
order_data.update(new_fields)

# 正确：创建副本
updated_order = {**order_data, **new_fields}
```

**优先级**: 高

---

### 2.3 Feed 基类改进

**问题**: `feeds/feed.py` 中 59 个方法全部 `raise NotImplementedError`，没有使用 `ABC + @abstractmethod`。

**现状** (`feeds/feed.py:198-516`):
```python
def get_tick(self, symbol, **kwargs):
    raise NotImplementedError

def get_depth(self, symbol, **kwargs):
    raise NotImplementedError

# ... 57 个类似方法
```

**其他问题**:
- `http_request()` 使用字符串匹配检测错误码 (`"404" in msg`)
- 重试逻辑中硬编码 `time.sleep(1)`，无指数退避
- 日志中可能泄露 API Key 和敏感查询参数
- 继承 3 个 mixin，耦合度高

**建议**:
- 使用 `ABC + @abstractmethod` 替代 `NotImplementedError`
- 错误检测改用 HTTP 状态码匹配
- 重试逻辑改用指数退避 + 最大重试次数
- 日志中过滤敏感参数

**文件位置**: `bt_api_py/feeds/feed.py`
**优先级**: 高

---

### 2.4 WebSocket 管理器中的硬编码

**问题**: `websocket_manager.py` 中交易所特定的订阅格式硬编码在通用代码中。

**现状** (`websocket_manager.py:165-186`):
```python
async def _send_subscription(self, subscription):
    if self.config.exchange_name.startswith("BINANCE"):
        message = {"method": "SUBSCRIBE", ...}
    elif self.config.exchange_name.startswith("OKX"):
        message = {"op": "subscribe", ...}
    else:
        message = {"action": "subscribe", ...}
```

**建议**:
- 定义 `WebSocketProtocol` 接口，每个交易所实现自己的协议
- 使用策略模式替代 `if/elif` 分支
- 将协议实现注册到 `ExchangeRegistry`

**优先级**: 中

---

### 2.5 错误处理一致性

**问题**: 错误处理模式不一致 — 混用异常、assert、返回码和静默忽略。

**关键问题点**:
- `security.py:194-200` — 解密失败静默记录 warning，不抛异常
- `auth_config.py` — 无参数校验（空字符串、None）
- `connection_pool.py:198` — 捕获宽泛异常 `except Exception`
- `rate_limiter.py:94-99` — 访问 `_requests[0]` 前无边界检查（竞态条件）
- `dependency_injection.py:105-110` — 未解析依赖静默回退

**建议**: 建立统一的错误处理策略:
1. 系统边界（用户输入、API 响应）使用显式校验 + 自定义异常
2. 内部方法使用 assert（仅开发模式）
3. 禁止 `except Exception` 宽泛捕获
4. 所有静默忽略添加日志记录

**优先级**: 高

---

## 3. 性能优化

### 3.1 速率限制器性能

**问题**: `SlidingWindowLimiter` 在每次 `acquire()` 调用时执行 O(n) 清理循环。

**现状** (`rate_limiter.py:85-86`):
```python
# 每次 acquire() 都遍历整个请求列表清理过期条目
def acquire(self):
    # O(n) cleanup
    while self._requests and ...:
        self._requests.popleft()
```

**其他问题**:
- `wait_and_acquire()` 使用 `time.sleep()` 轮询，CPU 效率低 (`rate_limiter.py:212-233`)
- `async_acquire()` 同样使用轮询而非 `asyncio.Event`
- 上下文管理器 `__enter__/__exit__` 实现为空操作

**建议**:
- 使用 `deque` 旋转替代逐元素清理
- `async_acquire()` 改用 `asyncio.Event` 事件驱动
- 同步等待改用 `threading.Event.wait(timeout=...)`

**优先级**: 高（实盘交易中频繁调用）

---

### 3.2 缓存层优化

**问题**: `cache.py` 存在多项性能和正确性问题。

**现状**:
- `cache.py:121` — `cleanup()` 执行两次遍历（keys + pop）
- `cache.py:362-363` — `cached()` 装饰器无法缓存 `None`/falsy 值（bug!）
- `cache.py:245-247` — `ExchangeInfoCache.clear_exchange()` 使用字符串前缀匹配，大规模缓存下效率低
- `cache.py:136-138` — `__iter__` 复制所有 key，内存开销大

**建议**:
- 使用惰性过期策略替代主动清理
- 引入 sentinel 值区分 "缓存了 None" 和 "未缓存"
- 为 `ExchangeInfoCache` 实现分层键结构
- `cached()` 装饰器添加 `maxsize` 和 `ttl` 参数

**优先级**: 中

---

### 3.3 连接池优化

**问题**: `connection_pool.py` 缺少连接预热、自适应池大小和单连接统计。

**现状**:
- `connection_pool.py:120-140` — `acquire()` 无等待重试机制
- `connection_pool.py:106-108` — 清理过程中迭代 `_in_use` 集合无防御性拷贝（并发风险）
- `connection_pool.py:96-97` — 清理线程硬编码 5 秒超时

**建议**:
- 添加 `warm_up(n)` 方法预创建连接
- 实现基于负载的自适应池大小调整
- 添加每连接指标（使用次数、错误次数、延迟）
- 使用 `threading.Condition` 实现等待获取机制

**优先级**: 中

---

### 3.4 导入优化

**问题**: 启动时加载所有 72 个交易所注册模块，即使只使用 1-2 个交易所。

**现状** (`bt_api.py:44-50`):
```python
import bt_api_py.exchange_registers as _exchange_reg_pkg
for _finder, _name, _ispkg in pkgutil.iter_modules(_exchange_reg_pkg.__path__):
    try:
        importlib.import_module(f"bt_api_py.exchange_registers.{_name}")
    except ImportError as e:
        _reg_logger.debug(f"{_name} register skipped: {e}")
```

**建议**:
- 改用延迟加载（lazy import），仅在 `create_feed()` 时加载对应交易所模块
- 或提供 `bt_api_py.exchanges.binance` 等细粒度导入入口

**优先级**: 低（影响启动时间，非运行时性能）

---

### 3.5 JSON 解析优化

**问题**: WebSocket 消息处理使用标准 `json.loads()`，对高频数据流可能成为瓶颈。

**建议**:
- 项目已依赖 `python-rapidjson`，可在 WebSocket 消息处理热路径中使用
- 对已知结构的消息使用流式解析
- 考虑 `orjson` 作为更高性能的替代方案

**优先级**: 低

---

## 4. 测试改进

### 4.1 零测试关键模块 (紧急)

**问题**: 多个核心基础设施模块完全没有单元测试。

| 模块 | 行数 | 风险级别 | 说明 |
|------|------|----------|------|
| `cache.py` | 402 | **紧急** | 缓存 TTL、淘汰策略、并发安全未经测试 |
| `connection_pool.py` | 318 | **紧急** | 连接池大小、复用、超时未经测试 |
| `rate_limiter.py` | 254 | **紧急** | 速率限制正确性对实盘交易至关重要 |
| `websocket_manager.py` | 529 | **高** | WebSocket 生命周期管理未经测试 |
| `instrument_manager.py` | 109 | **高** | 合约信息管理准确性影响交易 |
| `auth_config.py` | 220 | **中** | 认证配置安全性 |
| `core/dependency_injection.py` | ~120 | **中** | 依赖注入容器正确性 |
| `core/async_context.py` | ~80 | **中** | 异步上下文管理 |
| `core/interfaces.py` | ~250 | **低** | 协议定义（纯声明性） |

**建议**: 按风险级别从高到低依次补充测试，优先覆盖以下场景:
- cache: TTL 过期、LRU 淘汰、并发读写、缓存 None 值
- connection_pool: 池满、连接超时、并发获取/释放、连接健康检查
- rate_limiter: 滑动窗口计算、令牌桶填充、突发请求、并发获取

**优先级**: 紧急

---

### 4.2 交易所适配器单元测试

**问题**: 72 个交易所的 100+ feed 实现文件没有独立的单元测试，仅通过集成测试间接覆盖。

**建议**:
- 为每个主要交易所创建 mock API 响应的单元测试
- 测试重点: ticker 解析、order 解析、balance 解析、position 解析
- 使用参数化测试覆盖多交易所同类操作
- 优先覆盖: Binance, OKX, HTX, Bybit, KuCoin（按使用量排序）

```python
# 示例: 参数化交易所 ticker 解析测试
@pytest.mark.parametrize("exchange,mock_response,expected", [
    ("BINANCE___SPOT", BINANCE_TICKER_RESP, {"last_price": 50000.0, ...}),
    ("OKX___SWAP", OKX_TICKER_RESP, {"last_price": 50000.0, ...}),
])
def test_ticker_parsing(exchange, mock_response, expected):
    ...
```

**优先级**: 高

---

### 4.3 CI 覆盖率阈值不一致

**问题**: `pyproject.toml` 设置 `fail_under = 80`，但 CI 中实际使用 60% 阈值。

**现状**:
- `pyproject.toml:102` — `fail_under = 80`
- `.github/workflows/tests.yml` — Unit test stage uses 60% threshold
- 两者不一致，导致本地和 CI 行为不同

**建议**:
- 统一为 80% 阈值
- CI 中使用 `--cov-fail-under=80` 参数
- 添加覆盖率趋势追踪（Codecov badge 或 GitHub Action）
- 阻止降低覆盖率的 PR 合并

**优先级**: 高

---

### 4.4 缺失的测试类型

| 测试类型 | 现状 | 建议 |
|----------|------|------|
| **性能回归测试** | 仅有 `test_performance.py` 基准测试 | 添加 API 延迟基准线 (<100ms)，内存使用基准线 |
| **并发/压力测试** | `test_thread_safety.py` 仅覆盖 Registry/EventBus | 扩展到网络调用、连接池、速率限制器 |
| **错误恢复测试** | 几乎没有 | 添加重连测试、超时恢复测试、服务降级测试 |
| **内存泄漏测试** | 没有 | 添加长时间运行稳定性测试，使用 `tracemalloc` |
| **安全测试** | `test_security_compliance.py` 非常有限 | 添加加密/解密、Token 轮换、审计日志测试 |
| **变异测试** | 没有 | 引入 `mutmut` 或 `cosmic-ray` 检测弱测试 |

**优先级**: 中

---

### 4.5 测试 Fixtures 补全

**问题**: `conftest_test_data.py` 仅包含 Binance/OKX 的 fixture 数据。

**建议补充**:
- 速率限制场景 fixtures
- 连接错误场景 fixtures
- 超时场景 fixtures
- 畸形响应 fixtures
- 认证失败 fixtures
- 更多交易所的标准化 mock 数据

**优先级**: 中

---

## 5. 安全加固

### 5.1 API Key 安全

**问题**: API Key 以明文存储在内存中，无加密保护。

**现状** (`auth_config.py:50-76`):
```python
class CryptoAuthConfig(AuthConfig):
    def __init__(self, api_key, api_secret, ...):
        self.api_key = api_key          # 明文存储
        self.api_secret = api_secret    # 明文存储
```

**其他问题**:
- `security.py:194-200` — 解密失败静默处理
- `security.py:130` — `validate_api_key()` 仅检查长度 >= 16，不验证格式
- `security.py:228-233` — `.env` 文件解析无畸形行错误处理

**建议**:
- 使用 `keyring` 或操作系统密钥链存储 API Key
- 实现内存中加密存储（使用后立即清除明文）
- 添加 API Key 格式验证（每个交易所有特定格式）
- `.env` 解析添加错误处理和格式验证

**优先级**: 高

---

### 5.2 日志安全

**问题**: 日志中可能泄露 API Key 和敏感查询参数。

**现状** (`feeds/feed.py`):
- `feed.py:81` — 日志记录完整请求体（可能包含 API Key）
- `feed.py:48-49` — 日志记录完整 URL（可能包含敏感查询参数）

**建议**:
- 实现日志过滤器，自动掩码敏感字段
- 定义敏感字段列表: `api_key`, `api_secret`, `password`, `token`, `signature`
- 在日志输出前执行过滤

```python
SENSITIVE_KEYS = {"api_key", "apiKey", "secret", "password", "token", "signature"}

def mask_sensitive(data: dict) -> dict:
    return {
        k: "***" if k.lower() in {s.lower() for s in SENSITIVE_KEYS} else v
        for k, v in data.items()
    }
```

**优先级**: 高

---

### 5.3 输入验证

**问题**: 多个入口点缺少输入验证。

**需要补充验证的位置**:
- `BtApi.make_order()` — volume <= 0, price < 0, order_type 非法值
- `BtApi.subscribe()` — dataname 格式验证 (需包含 `___` 分隔符)
- `AuthConfig` 子类 — URL 格式、端口范围、必填字段
- `WebSocketConfig` — 连接参数范围检查

**优先级**: 高

---

### 5.4 依赖安全

**问题**: 50+ 依赖项缺少系统化的漏洞扫描。

**现状**: CI 中有 `pip-audit` 和 `bandit` 扫描，但:
- `pip-audit` 仅在特定 workflow 中运行
- 无自动化依赖更新机制
- `S301` (pickle) 被忽略 — ML 模型序列化使用 pickle

**建议**:
- 启用 Dependabot 或 Renovate Bot 自动依赖更新
- 在所有 PR 上运行 `pip-audit`
- 将 ML 模型序列化从 pickle 迁移到 `joblib` 或 `safetensors`
- 添加依赖许可证审查

**优先级**: 中

---

## 6. 文档完善

### 6.1 API 参考文档

**问题**: ~40% 的公共 API 缺少文档字符串，1,090 个缺失文档问题。

**建议**:
- 使用 `mkdocstrings` 自动从类型注解和 docstring 生成 API 参考
- 优先为以下模块添加 Google 风格 docstring:
  1. `bt_api.py` 所有公开方法
  2. `registry.py` 所有公开方法
  3. `containers/` 所有数据容器类
  4. `exceptions.py` 所有异常类（已完成）
  5. `event_bus.py` 所有方法（已完成）

**优先级**: 中

---

### 6.2 架构设计文档

**问题**: 缺少深度架构设计文档、设计决策记录 (ADR)、性能调优指南。

**建议创建的文档**:
1. **架构决策记录 (ADR)**
   - ADR-001: 为什么选择 Registry 模式而非工厂模式
   - ADR-002: 为什么使用 Queue + WebSocket 的混合模式
   - ADR-003: 为什么 Container 使用 AutoInitMixin
   - ADR-004: 为什么 Gateway 使用 ZMQ
2. **性能调优指南**
   - 连接池配置建议
   - 速率限制器参数调整
   - WebSocket 连接数优化
   - 内存使用优化
3. **错误处理指南**
   - 异常层次结构说明
   - 各交易所常见错误码映射
   - 重连和降级策略

**优先级**: 中

---

### 6.3 交易所适配指南

**问题**: 新增交易所的适配文档不够详细，缺少步骤模板。

**建议**: 创建标准化的交易所适配模板文档:
1. 需要实现的接口列表和方法签名
2. 注册模块模板代码
3. 数据容器映射规范
4. 测试用例模板
5. WebSocket 订阅协议规范

**优先级**: 低

---

## 7. CI/CD 改进

### 7.1 缺失的 CI 脚本

**问题**: Makefile 引用的脚本文件不存在。

| 脚本 | 状态 | 作用 |
|------|------|------|
| `scripts/run_optimized_tests.sh` | **缺失** | 优化测试执行 |
| `scripts/analyze_coverage.py` | **缺失** | 覆盖率分析 |

**建议**: 创建缺失脚本或从 Makefile 中移除引用。

**优先级**: 中

---

### 7.2 Pre-commit Hook 强化

**问题**: Pre-commit hooks 已定义但未在 CI 中强制执行。

**建议**:
- 在 CI pipeline 中添加 `pre-commit run --all-files` 步骤
- 确保本地和 CI 行为一致

**优先级**: 低

---

### 7.3 集成测试可靠性

**问题**: 集成测试标记为 `continue-on-error: true`，`pytest-rerunfailures` 可用但 CI 中未启用。

**建议**:
- 启用 `pytest-rerunfailures`，配置 `--reruns 3 --reruns-delay 5`
- 分离 "必须通过的集成测试" 和 "尽力而为的网络测试"
- 添加 flaky test 追踪仪表板

**优先级**: 中

---

### 7.4 发布流程

**建议补充**:
- 添加自动化 CHANGELOG 生成
- 添加语义化版本 (semver) 验证
- 添加发布前回归测试 gate

**优先级**: 低

---

## 8. 优先级与执行计划

### 紧急 (立即执行)

| 编号 | 改进项 | 风险 | 工作量 |
|------|--------|------|--------|
| T-1 | 补充 cache/connection_pool/rate_limiter 单元测试 | 实盘交易安全 | 中 |
| T-2 | 统一 CI 覆盖率阈值 (80%) | CI 可靠性 | 小 |
| T-3 | 日志敏感信息过滤 | API Key 泄露 | 小 |

### 高优先级 (1-4 周)

| 编号 | 改进项 | 收益 | 工作量 |
|------|--------|------|--------|
| H-1 | BtApi 类拆分 | 可维护性 | 大 |
| H-2 | 统一异步模式 | 性能/正确性 | 大 |
| H-3 | 类型注解补全 (核心模块) | 代码质量 | 中 |
| H-4 | Feed 基类改用 ABC | 接口规范 | 中 |
| H-5 | 交易所适配器单元测试 | 可靠性 | 大 |
| H-6 | 输入验证完善 | 安全性 | 中 |
| H-7 | API Key 加密存储 | 安全性 | 中 |
| H-8 | 不可变性修复 | 数据一致性 | 中 |
| H-9 | 错误处理统一 | 可维护性 | 中 |

### 中优先级 (1-3 个月)

| 编号 | 改进项 | 收益 | 工作量 |
|------|--------|------|--------|
| M-1 | Registry 单例改进 | 测试隔离 | 中 |
| M-2 | WebSocket 协议抽象 | 扩展性 | 中 |
| M-3 | Gateway 命令分发重构 | 可维护性 | 中 |
| M-4 | 速率限制器性能优化 | 运行效率 | 小 |
| M-5 | 缓存层优化 | 性能 | 中 |
| M-6 | 连接池优化 | 稳定性 | 中 |
| M-7 | API 参考文档生成 | 开发体验 | 中 |
| M-8 | 架构设计文档 | 知识沉淀 | 中 |
| M-9 | 缺失 CI 脚本补全 | CI 完整性 | 小 |
| M-10 | 集成测试可靠性 | CI 信心 | 小 |
| M-11 | 依赖安全扫描 | 安全性 | 小 |
| M-12 | 缺失测试类型补充 | 全面覆盖 | 大 |

### 低优先级 (3-6 个月)

| 编号 | 改进项 | 收益 | 工作量 |
|------|--------|------|--------|
| L-1 | 依赖注入容器改进 | 架构清晰 | 中 |
| L-2 | 导入延迟加载 | 启动性能 | 中 |
| L-3 | JSON 解析优化 | 运行性能 | 小 |
| L-4 | 交易所适配指南 | 开发文档 | 小 |
| L-5 | Pre-commit CI 强化 | 代码质量 | 小 |
| L-6 | 发布流程自动化 | 效率 | 中 |

---

## 附录: 关键文件清单

| 文件路径 | 问题数 | 主要问题 |
|----------|--------|----------|
| `bt_api_py/bt_api.py` | 5 | 类过大、假异步、无输入验证 |
| `bt_api_py/registry.py` | 3 | 全局单例、元类魔法 |
| `bt_api_py/feeds/feed.py` | 6 | 无 ABC、日志泄露、硬编码重试 |
| `bt_api_py/websocket_manager.py` | 4 | 交易所逻辑硬编码、无策略模式 |
| `bt_api_py/cache.py` | 4 | 无测试、缓存 None 值 bug、O(n) 清理 |
| `bt_api_py/rate_limiter.py` | 5 | 无测试、竞态条件、轮询等待 |
| `bt_api_py/connection_pool.py` | 4 | 无测试、并发风险、无自适应 |
| `bt_api_py/security.py` | 5 | 静默失败、弱验证、硬编码交易所名 |
| `bt_api_py/auth_config.py` | 4 | 明文存储、无验证、参数过多 |
| `bt_api_py/gateway/runtime.py` | 5 | CTP 耦合、字符串分发、忙等待 |
| `bt_api_py/gateway/client.py` | 5 | 可变性违反、无重试、无安全存储 |
| `bt_api_py/core/dependency_injection.py` | 4 | 运行时反射、静默回退、全局状态 |
| `bt_api_py/core/interfaces.py` | 3 | 接口过大、**kwargs、重复定义 |
