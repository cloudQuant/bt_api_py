# bt_api_py 项目改进报告（第二轮迭代）

> 日期: 2026-03-08
> 项目版本: 0.15
> 前置: 基于第一轮报告验收后，通过深度代码扫描发现的新改进项
> 重点: 线程安全、架构一致性、运维可观测性、开发者体验

---

## 目录

1. [第一轮验收总结](#1-第一轮验收总结)
2. [线程安全与并发改进](#2-线程安全与并发改进)
3. [架构一致性改进](#3-架构一致性改进)
4. [API 设计与开发者体验](#4-api-设计与开发者体验)
5. [数据完整性与可靠性](#5-数据完整性与可靠性)
6. [运维与可观测性](#6-运维与可观测性)
7. [Python 兼容性与最佳实践](#7-python-兼容性与最佳实践)
8. [优先级排序与迭代计划](#8-优先级排序与迭代计划)

---

## 1. 第一轮验收总结

第一轮报告经代码验证后发现 **P0 四项全部有误**：

| 原始编号 | 声称的问题 | 验证结果 |
|----------|-----------|----------|
| P0-1 | MEXC 类名遮蔽 | 代码已使用别名导入 `as MexcMarketWssDataBase`，不存在 |
| P0-2 | coinbase_trade.py 使用 print() | 已使用 `_logger.error()`，已修复 |
| P0-3 | protection.py 加解密静默失败 | 已正确抛出 `DataProtectionError`，已修复 |
| P0-4 | 可变默认参数 | 已使用 `exchange_data=None` 模式，已修复 |

此外：
- `websocket_manager.py` 消息队列已设置 `maxsize=config.message_queue_size`（默认 10000），不存在无界增长
- `live_mexc_feed.py` 导入已全部在模块顶部，不存在延迟导入

**第一轮仍有效的待改进项：**
- 通用异常捕获（`except Exception`）普遍存在 — 已确认
- `security.py:199` 解密失败静默 pass — 已确认
- `connection_pool.py:175` 关闭连接静默 pass — 已确认
- `feeds/registry.py` 使用 print() — 已确认
- Container 重复代码、适配器 handler 重复 — 架构层面已确认
- 测试基础设施改进建议 — 仍然有效

---

## 2. 线程安全与并发改进

### 2.1 EventBus handler 列表并发修改 [HIGH]

**文件:** `bt_api_py/event_bus.py:27-37, 44`

**问题:** `emit()` 迭代 handler 列表时，`off()` 可以修改同一列表，导致 `RuntimeError: list changed size during iteration`。

```python
# emit() 在迭代 handlers 列表
for handler in self._handlers.get(event_type, []):  # line 44
    handler(data)

# 同时 off() 在修改同一列表
handlers.remove(handler)  # line 37 — 与 emit() 竞争
```

**建议:**
- `emit()` 中迭代列表的副本：`for handler in list(self._handlers.get(event_type, [])):`
- 或使用 `threading.Lock` 保护 `_handlers` 的读写
- 交易系统中事件总线是核心组件，线程安全至关重要

### 2.2 日志工厂全局缓存无锁保护 [MEDIUM]

**文件:** `bt_api_py/logging_factory.py:25, 37, 52`

**问题:** `_logger_cache` 字典的读写没有任何同步保护。多线程同时调用 `get_logger()` 可能创建重复 logger 或触发字典操作的竞争。

```python
_logger_cache: dict = {}  # 全局共享状态，无锁

def get_logger(module, print_info=False):
    cache_key = (module, print_info)
    if cache_key in _logger_cache:      # 检查（无锁）
        return _logger_cache[cache_key]
    ...
    _logger_cache[cache_key] = logger   # 写入（无锁）
```

**建议:**
- 使用 `threading.Lock` 保护缓存的读写
- 或使用 `functools.lru_cache` 替代手工缓存（自带线程安全）

### 2.3 SimpleCache 无线程同步 [MEDIUM]

**文件:** `bt_api_py/cache.py:36-56`

**问题:** `SimpleCache` 声称 "Thread-safe operations (basic)" 但实际没有任何锁保护。`get()` 中检查过期和删除操作不是原子的。

```python
def get(self, key):
    if key not in self._cache:       # 检查存在
        return None
    value, expiry = self._cache[key] # 读取（可能在此刻被另一线程删除）
    if time.time() > expiry:
        del self._cache[key]         # 删除（非原子）
```

**建议:**
- 添加 `threading.RLock` 保护所有缓存操作
- 修正 docstring 中的 "Thread-safe" 声明，或真正实现线程安全
- 全局实例 `_exchange_info_cache` 和 `_market_data_cache`（line 228-229）需要同样的保护

### 2.4 DIContainer 单例模式竞争条件 [MEDIUM]

**文件:** `bt_api_py/core/dependency_injection.py:190-195`

**问题:** `Container` 类的 `__new__` 实现了单例模式，但 `if cls._instance is None` 检查不是线程安全的（经典的 double-check locking 问题）。

```python
class Container:
    _instance = None
    def __new__(cls):
        if cls._instance is None:        # TOCTOU 竞争
            cls._instance = super().__new__(cls)
        return cls._instance
```

**建议:**
- 使用 `threading.Lock` 保护单例创建
- 或使用模块级别实例代替类级别单例

### 2.5 InstrumentManager 全局状态无保护 [HIGH]

**文件:** `bt_api_py/instrument_manager.py`

**问题:** 全局单例 `_instrument_manager` 的 `register()`、`find()`、`clear()` 方法操作共享字典但没有同步保护。在多交易所并发注册场景下可能损坏内部数据。

**建议:**
- 使用 `threading.RLock` 保护所有字典操作
- 考虑使用 `concurrent.futures` 中的线程安全容器

---

## 3. 架构一致性改进

### 3.1 双重注册系统并存 [HIGH]

**文件:**
- `bt_api_py/registry.py` — ExchangeRegistry（metaclass 单例，主注册系统）
- `bt_api_py/feeds/registry.py` — 模块级 `_registry` 字典（旧注册系统）

**问题:** 两套完全独立的注册系统共存：
- `ExchangeRegistry` 使用 metaclass `_RegistryMeta`，支持全局和实例两种用法
- `feeds/registry.py` 使用模块级字典和 `@register` 装饰器
- 两者注册的交易所名称格式不一致（`BINANCE___SPOT` vs `BINANCE_SPOT`）
- `feeds/registry.py` 中使用 `print()` 而非 logger

**建议:**
- 统一为一套注册系统（推荐保留 `ExchangeRegistry`）
- 废弃 `feeds/registry.py`，迁移其使用方到 `ExchangeRegistry`
- 如需保留向后兼容，在 `feeds/registry.py` 中做薄代理层指向 `ExchangeRegistry`

### 3.2 feeds/registry.py 导入时自动执行副作用 [MEDIUM]

**文件:** `bt_api_py/feeds/registry.py`（模块底部）

**问题:** 模块被导入时会自动执行 `initialize_default_feeds()`，导入 binance/okx/hitbtc 等模块。这属于导入副作用（import side effect），会导致：
- 导入顺序敏感，增加调试难度
- 无法在单元测试中控制哪些交易所被加载
- 延长首次导入时间

**建议:**
- 移除模块级自动执行，改为显式调用
- 参考 `bt_api.py` 中的做法（通过 `pkgutil.iter_modules` 在 BtApi 初始化时注册）

### 3.3 Container init_data() 重复模式 [MEDIUM]

**文件:** `bt_api_py/containers/trades/coinbase_trade.py`（典型案例）

**问题:** `CoinbaseTradeData`、`CoinbaseWssTradeData`、`CoinbaseRequestTradeData` 三个类的 `init_data()` 方法有 ~80% 重复代码。差异仅在于个别字段名（如 `entry_id` vs `trade_id`，`trade_time` vs `time`）。

```python
# CoinbaseTradeData.init_data() — line 42-72
# CoinbaseWssTradeData.init_data() — line 158-187
# CoinbaseRequestTradeData.init_data() — line 193-223
# 三个方法结构几乎完全相同，仅字段映射不同
```

**建议:**
- 在基类中实现通用的 `init_data()`，通过类变量 `_FIELD_MAP` 定义字段映射
- 子类只需覆盖 `_FIELD_MAP` 即可，例如：
```python
class CoinbaseTradeData(TradeData):
    _FIELD_MAP = {
        "trade_id": "entry_id",
        "trade_time": "trade_time",
    }

class CoinbaseWssTradeData(CoinbaseTradeData):
    _FIELD_MAP = {
        "trade_id": "trade_id",
        "trade_time": "time",
    }
```

### 3.4 AutoInitMixin.__getattribute__ 脆弱性 [LOW]

**文件:** `bt_api_py/containers/auto_init_mixin.py`

**问题:** `__getattribute__` 通过检查方法名是否以 `get_` 开头来触发懒初始化。维护一个排除列表来防止递归。这种机制：
- 继承链变更时可能引入无限递归
- 排除列表需要手动维护
- 新增 `get_*` 方法时可能忘记排除

**建议:**
- 考虑使用 `__getattr__`（仅在属性不存在时触发）替代 `__getattribute__`
- 或使用 `descriptor` 协议实现更精确的懒加载

---

## 4. API 设计与开发者体验

### 4.1 核心模块缺少 `__all__` 声明 [MEDIUM]

**涉及文件:**
- `bt_api_py/registry.py`
- `bt_api_py/auth_config.py`
- `bt_api_py/cache.py`
- `bt_api_py/event_bus.py`
- `bt_api_py/rate_limiter.py`
- `bt_api_py/logging_factory.py`
- `bt_api_py/config_loader.py`

**问题:** 这些核心公开模块没有定义 `__all__`，用户无法明确知道哪些是公开 API，哪些是内部实现。IDE 自动补全可能暴露不稳定的内部接口。

**建议:**
- 为每个公开模块添加 `__all__` 声明
- 遵循约定：`_` 前缀的为私有，`__all__` 中列出的为公开稳定 API

### 4.2 BtApi.log() 方法设计 [LOW]

**文件:** `bt_api_py/bt_api.py:60-70`

**问题:** `log()` 方法通过字符串匹配 level 来分发日志级别，使用了废弃的 `logger.warn()`，且 `else: pass` 静默忽略无效级别。

```python
def log(self, txt, level="info"):
    if level == "info":
        self.logger.info(txt)
    elif level == "warning":
        self.logger.warn(txt)  # 废弃方法
    ...
    else:
        pass  # 无效级别被静默忽略
```

**建议:**
- 使用 `getattr(self.logger, level, self.logger.info)(txt)` 简化
- 替换 `warn` 为 `warning`
- 对无效级别记录警告而非静默忽略

### 4.3 可选依赖处理模式不统一 [LOW]

**涉及文件:**
- `bt_api_py/security.py` — `CRYPTO_AVAILABLE = False` 模式
- `bt_api_py/config_loader.py` — `yaml = None` 模式
- `bt_api_py/feeds/http_client.py` — `httpx = None` 模式

**问题:** 三种不同的可选依赖处理模式，增加代码阅读和维护成本。

**建议:**
- 统一为一种模式，推荐 flag 模式（如 `CRYPTO_AVAILABLE`）或 lazy import 工具
- 在使用时统一通过辅助函数检查：`require_optional("cryptography", "pip install bt_api_py[ib_web]")`

---

## 5. 数据完整性与可靠性

### 5.1 config_loader 部分加载静默成功 [MEDIUM]

**文件:** `bt_api_py/config_loader.py`

**问题:** 配置加载失败时仅记录 warning 并继续，用户可能得到不完整的交易所列表而不自知。

```python
try:
    config = load_exchange_config(filepath)
    configs[config.id] = config
except Exception as e:
    logger.warn(f"Failed to load config {filepath}: {e}")
    # 继续加载其他配置，不抛出异常
```

**建议:**
- 区分"配置文件损坏"（应该警告）和"关键配置缺失"（应该报错）
- 返回结果中包含加载失败的列表，让调用者决定是否继续
- 至少使用 `logger.warning()` 替代废弃的 `logger.warn()`

### 5.2 ConnectionPool 同步/异步双实现维护成本 [LOW]

**文件:** `bt_api_py/connection_pool.py:18-186, 189-236`

**问题:** `ConnectionPool`（sync）和 `AsyncConnectionPool`（async）是两套独立实现，逻辑高度相似但需要分别维护。修复一个版本的 bug 可能忘记同步到另一个版本。

**建议:**
- 短期：添加测试确保两个版本行为一致
- 长期：考虑用 `anyio` 或 adapter 模式统一实现

---

## 6. 运维与可观测性

### 6.1 缓存命中率不可观测 [MEDIUM]

**文件:** `bt_api_py/cache.py`

**问题:** `SimpleCache` 没有记录命中率、miss 率等指标，无法判断缓存效果。

**建议:**
- 添加 `_hits`、`_misses` 计数器
- 提供 `get_stats() -> dict` 方法返回命中率
- 在 `@cached` 装饰器中也暴露统计信息

### 6.2 WebSocket 连接指标不够精细 [LOW]

**文件:** `bt_api_py/websocket_manager.py:79-86`

**问题:** 连接统计使用简单计数器，缺少延迟分位数、错误分类等精细指标。

**建议:**
- 添加消息延迟的 P50/P95/P99 统计
- 按错误类型分类统计（网络错误、解析错误、回调错误）
- 提供 Prometheus-compatible 的指标导出接口

### 6.3 logging_factory 日志文件路径硬编码 [LOW]

**文件:** `bt_api_py/logging_factory.py:15-22`

**问题:** `_MODULE_LOG_MAP` 中的日志文件名硬编码，无法通过配置修改日志目录或文件名。

**建议:**
- 支持通过环境变量或配置文件设置日志目录
- 添加日志级别的动态配置能力

---

## 7. Python 兼容性与最佳实践

### 7.1 废弃的 logger.warn() 调用 [MEDIUM]

**涉及文件:**
- `bt_api_py/event_bus.py:48` — `self.logger.warn(...)`
- `bt_api_py/bt_api.py:64` — `self.logger.warn(txt)`
- `bt_api_py/config_loader.py` — `logger.warn(...)`

**问题:** `Logger.warn()` 在 Python 2.6 起已废弃，Python 3.13 计划移除。项目声明支持 Python 3.11-3.13。

**建议:**
- 全局搜索替换 `logger.warn(` 为 `logger.warning(`
- 添加 ruff 规则或 grep CI 检查防止回退

### 7.2 启用 ruff T201 规则检测 print() [LOW]

**现状:** ruff 配置中未启用 `T` (flake8-print) 规则组。

**建议:**
- 在 `pyproject.toml` 的 `[tool.ruff.lint] select` 中添加 `"T"`
- 对确实需要 print 的地方使用 `# noqa: T201` 注释豁免

---

## 8. 优先级排序与迭代计划

### 第二轮 P0 — 立即修复

| 编号 | 改进项 | 类型 | 涉及文件 | 工作量 |
|------|--------|------|----------|--------|
| V2-P0-1 | EventBus handler 列表迭代安全 | 线程安全 | `event_bus.py:44` | 小 |
| V2-P0-2 | security.py 解密失败静默 pass | 安全 | `security.py:199` | 小 |
| V2-P0-3 | 替换全部 `logger.warn()` 为 `warning()` | 兼容性 | 3 个文件 | 小 |
| V2-P0-4 | feeds/registry.py print() 替换 | 代码质量 | `feeds/registry.py` | 小 |

### 第二轮 P1 — 短期改进

| 编号 | 改进项 | 类型 | 工作量 |
|------|--------|------|--------|
| V2-P1-1 | SimpleCache 添加线程同步 | 线程安全 | 小 |
| V2-P1-2 | logging_factory 缓存加锁 | 线程安全 | 小 |
| V2-P1-3 | InstrumentManager 全局状态加锁 | 线程安全 | 小 |
| V2-P1-4 | 统一注册系统（消除 feeds/registry.py） | 架构 | 中 |
| V2-P1-5 | 核心模块添加 `__all__` 声明 | API 设计 | 小 |
| V2-P1-6 | 启用 ruff T201 规则 | 工具链 | 小 |

### 第二轮 P2 — 中期改进

| 编号 | 改进项 | 类型 | 工作量 |
|------|--------|------|--------|
| V2-P2-1 | Container init_data() 字段映射重构 | 架构 | 大 |
| V2-P2-2 | feeds/registry.py 导入副作用消除 | 架构 | 中 |
| V2-P2-3 | DIContainer 单例线程安全 | 线程安全 | 小 |
| V2-P2-4 | 缓存命中率指标 | 可观测性 | 小 |
| V2-P2-5 | 配置加载失败报告机制 | 可靠性 | 中 |
| V2-P2-6 | 可选依赖处理模式统一 | 代码质量 | 小 |

### 第二轮 P3 — 长期改进

| 编号 | 改进项 | 类型 | 工作量 |
|------|--------|------|--------|
| V2-P3-1 | AutoInitMixin 懒加载机制重构 | 架构 | 中 |
| V2-P3-2 | ConnectionPool sync/async 统一 | 架构 | 中 |
| V2-P3-3 | WebSocket 精细化指标 | 可观测性 | 中 |
| V2-P3-4 | 日志目录可配置化 | 运维 | 小 |
| V2-P3-5 | BtApi.log() 方法简化 | API 设计 | 小 |

---

## 附录: 第二轮问题文件索引

| 文件路径 | 问题类型 | 严重级别 |
|----------|----------|----------|
| `bt_api_py/event_bus.py:44` | handler 列表并发迭代竞争 | HIGH |
| `bt_api_py/event_bus.py:48` | 废弃的 warn() 调用 | MEDIUM |
| `bt_api_py/instrument_manager.py` | 全局状态无锁保护 | HIGH |
| `bt_api_py/logging_factory.py:25,37,52` | 缓存无锁保护 | MEDIUM |
| `bt_api_py/cache.py:36-56` | 声称线程安全但无实现 | MEDIUM |
| `bt_api_py/cache.py:228-229` | 全局实例无保护 | MEDIUM |
| `bt_api_py/core/dependency_injection.py:192` | 单例 TOCTOU 竞争 | MEDIUM |
| `bt_api_py/feeds/registry.py` | 双重注册系统 + print() + 导入副作用 | HIGH |
| `bt_api_py/security.py:199-200` | 解密失败静默 pass | HIGH |
| `bt_api_py/bt_api.py:64` | 废弃的 warn() 调用 | LOW |
| `bt_api_py/config_loader.py` | 废弃的 warn() + 部分加载静默 | MEDIUM |
| `bt_api_py/connection_pool.py:175` | 关闭连接静默 pass | MEDIUM |
| `bt_api_py/containers/auto_init_mixin.py` | __getattribute__ 脆弱性 | LOW |
| 7 个核心模块 | 缺少 __all__ 声明 | MEDIUM |
