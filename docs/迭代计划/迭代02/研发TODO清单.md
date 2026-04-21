# 迭代02：研发TODO清单

> 日期：2026-04-20
> 状态：规划中

---

## Phase 1：清理遗留 + 插件切换

> 目标：bt_api_py 中不再包含任何交易所特定代码

### T101 删除 exchange_registers/ 目录

- [ ] 备份当前 exchange_registers/ 内容（git 已有记录）
- [ ] 删除 `bt_api_py/exchange_registers/` 整个目录（68 个文件）
- [ ] 删除 bt_api.py 中的 pkgutil 扫描代码（第 79-85 行）
- [ ] 验证：`import bt_api_py` 不报 ImportError

### T102 重构 BtApi 启动加载

- [ ] 在 BtApi.__init__() 中集成 PluginLoader.load_all()
- [ ] 添加 lazy loading 选项：仅在首次使用交易所时加载对应插件
- [ ] 添加插件加载失败的优雅降级（log warning，不 crash）
- [ ] 移除 bt_api.py 中所有对 `bt_api_py.feeds.*` 和 `bt_api_py.containers.exchanges.*` 的 import
- [ ] 验证：安装 bt_api_binance 后 `BtApi({"BINANCE___SPOT": {...}})` 正常工作

### T103 清理 feed_registry.py

- [ ] 移除 `initialize_default_feeds()` 中对 `bt_api_py.gateway.registrar` 的引用
- [ ] 确认所有方法都正确委托到 `bt_api_base.registry.ExchangeRegistry`
- [ ] 评估：此文件是否还有存在必要，或可直接用 bt_api_base.registry 替代

### T104 清理 __init__.py

- [ ] 确保所有 re-export 来源正确（bt_api_base 或 bt_api_py 自身模块）
- [ ] 移除任何对已删除模块的引用
- [ ] 添加 `__version__` 独立管理（不再依赖 bt_api_base._version）

### T105 清理 websocket_manager.py

- [ ] 检查 `.core.async_context`、`.core.dependency_injection`、`.core.interfaces` 的引用
- [ ] 确认这些模块是 bt_api_py 本地的还是应该引用 bt_api_base
- [ ] 如果是 bt_api_base 的，更新 import 路径

### T106 核心包零交易所代码检查

- [ ] `grep -r "binance\|okx\|ctp\|kucoin\|bybit" bt_api_py/` 确认无硬编码交易所逻辑
- [ ] 检查 error.py 中是否有交易所特定错误码
- [ ] 检查 auth_config.py 中的交易所特定配置类是否应保留

---

## Phase 2：bt_api_xx DirectClient 完善

> 目标：每个 bt_api_xx 可独立安装使用，提供完整的直连能力

### T201 定义 AbstractDirectClient 基类

- [ ] 在 `bt_api_base/src/bt_api_base/client.py` 中定义
- [ ] 定义标准方法签名（行情/交易/账户/WebSocket）
- [ ] 使用 ABC + abstractmethod 约束子类实现
- [ ] 提供默认的 HTTP session 管理（aiohttp）
- [ ] 提供默认的错误处理和重试逻辑
- [ ] 支持 async context manager（`__aenter__` / `__aexit__`）

### T202 完善 BinanceDirectClient

- [ ] 检查现有 `bt_api_binance/client.py` 实现
- [ ] 补齐 REST API：ticker / depth / kline / trades / place_order / cancel_order / balance / positions
- [ ] 补齐 WebSocket：ticker 订阅 / depth 订阅 / 用户数据流
- [ ] 实现签名（HMAC-SHA256）
- [ ] 实现限速（rate limiter）
- [ ] 编写单元测试（mock 交易所响应）
- [ ] 编写使用示例

### T203 完善 OkxDirectClient

- [ ] 检查现有实现状态
- [ ] 补齐 REST API（同 T202）
- [ ] 补齐 WebSocket（公共频道 + 私有频道）
- [ ] 实现签名（HMAC-SHA256 + timestamp）
- [ ] 编写单元测试和使用示例

### T204 完善 CtpDirectClient

- [ ] 检查现有 CTP 封装状态
- [ ] 封装行情接口（MdApi）为 async 调用
- [ ] 封装交易接口（TraderApi）为 async 调用
- [ ] 处理 CTP 回调 → asyncio 事件的桥接
- [ ] 编写单元测试（mock CTP 回调）

### T205 完善 IbWebDirectClient

- [ ] 检查现有 IB Web 实现
- [ ] 封装 REST API + SSE 事件流
- [ ] 处理 OAuth / Cookie 认证
- [ ] 编写单元测试

### T206 补齐缺失的 pyproject.toml

- [ ] bt_api_bydfi/pyproject.toml
- [ ] bt_api_coinbase/pyproject.toml
- [ ] bt_api_coinone/pyproject.toml
- [ ] bt_api_gmx/pyproject.toml
- [ ] 格式参照 bt_api_binance 的 pyproject.toml

### T207 DirectClient 使用示例

- [ ] `examples/direct_client/binance_example.py`
- [ ] `examples/direct_client/okx_example.py`
- [ ] `examples/direct_client/ctp_example.py`
- [ ] 每个示例包含：连接 → 获取行情 → 下单 → 查询 → 断开

---

## Phase 3：ZMQ 行情交易分发

> 目标：bt_api_py 支持通过 ZMQ 将行情推送给多个策略进程，并接收交易指令

### T301 设计 ZMQ 消息协议

- [ ] 定义 Topic 格式规范：`{exchange}.{asset_type}.{symbol}.{data_type}`
- [ ] 定义 Message dataclass（topic, timestamp, data, sequence）
- [ ] 选择序列化方式：msgpack（默认）/ json（调试用）
- [ ] 定义交易请求/响应格式
- [ ] 编写协议文档

### T302 实现 ZmqPublisher

- [ ] 创建 `bt_api_py/zmq/publisher.py`
- [ ] PUB socket 绑定配置地址
- [ ] 支持按 topic 发布消息
- [ ] 集成到 EventBus：EventBus 收到行情 → ZmqPublisher 自动推送
- [ ] 发布计数和统计
- [ ] 高水位（HWM）设置防止内存溢出

### T303 实现 ZmqSubscriber

- [ ] 创建 `bt_api_py/zmq/subscriber.py`
- [ ] SUB socket 连接 Publisher
- [ ] 支持 topic 模式匹配（前缀匹配）
- [ ] 支持多个 callback 注册
- [ ] 消息反序列化 + 回调分发
- [ ] 支持 asyncio 和 threading 两种消费模式

### T304 实现 ZmqRequestReply

- [ ] 创建 `bt_api_py/zmq/request_reply.py`
- [ ] Server 端（REP socket）：绑定在 BtApi 侧，接收交易指令
- [ ] Client 端（REQ socket）：策略侧发送交易指令
- [ ] 请求 → BtApi.make_order/cancel_order → 响应
- [ ] 超时处理
- [ ] 请求 ID 去重

### T305 BtApi 集成 ZMQ

- [ ] BtApi.__init__() 添加 zmq_config 参数
- [ ] `enable_zmq_publisher(pub_addr, rep_addr)` 方法
- [ ] 行情 callback → ZmqPublisher.publish()
- [ ] ZmqRequestReply.server → BtApi 交易方法映射
- [ ] 关闭时优雅断开 ZMQ 连接

### T306 ZMQ 心跳与监控

- [ ] Publisher 定期发送心跳消息（topic: `_heartbeat`）
- [ ] Subscriber 检测心跳超时 → 触发重连
- [ ] 统计指标：消息发送/接收数量、延迟、丢包率
- [ ] 可选集成到 monitoring 模块

### T307 ZMQ 端到端测试

- [ ] 测试 PUB/SUB：模拟行情 → 推送 → 多个 Subscriber 接收
- [ ] 测试 REQ/REP：模拟下单 → 收到响应
- [ ] 测试重连：模拟 Publisher 重启 → Subscriber 自动恢复
- [ ] 测试背压：高频消息场景下的 HWM 行为
- [ ] 性能基线：单线程 PUB/SUB 消息吞吐量

---

## Phase 4：独立安装验证

> 目标：端到端验证三层架构的独立安装能力

### T401 bt_api_base 独立安装测试

- [ ] 创建纯净虚拟环境
- [ ] `pip install ./bt_api/bt_api_base`
- [ ] 验证：`from bt_api_base import ExchangeRegistry` 成功
- [ ] 验证：`from bt_api_base.containers.tickers.ticker import Ticker` 成功
- [ ] 验证：`from bt_api_base.plugins.loader import PluginLoader` 成功

### T402 bt_api_binance 独立安装测试

- [ ] `pip install ./bt_api/bt_api_base ./bt_api/bt_api_binance`
- [ ] 验证：`from bt_api_binance import BinanceDirectClient` 成功
- [ ] 验证：DirectClient 可连接 Binance testnet
- [ ] 验证：不依赖 bt_api_py 的任何模块

### T403 bt_api_py + plugin 联合测试

- [ ] `pip install ./bt_api/bt_api_base ./bt_api/bt_api_binance .`（当前目录为 bt_api_py）
- [ ] 验证：`from bt_api_py import BtApi` 成功
- [ ] 验证：PluginLoader 自动发现 bt_api_binance
- [ ] 验证：`BtApi({"BINANCE___SPOT": {...}}).get_tick(...)` 正常

### T404 bt_api_py 无插件模式

- [ ] 仅 `pip install ./bt_api/bt_api_base .`
- [ ] 验证：`BtApi()` 构造不报错
- [ ] 验证：调用交易方法时给出清晰的 "未安装对应交易所插件" 提示

### T405 完善 install_and_test_all.py

- [ ] 支持选择性安装（按交易所名称过滤）
- [ ] 添加冒烟测试（import + plugin 注册检查）
- [ ] 输出测试报告（安装结果 + 测试结果矩阵）

### T406 依赖冲突检测

- [ ] 检查所有包的 bt_api_base 版本约束一致
- [ ] 检查第三方依赖版本范围不冲突（numpy、pandas、aiohttp 等）
- [ ] 编写脚本自动检测版本约束冲突

---

## Phase 5：测试体系建设

### T501 bt_api_base 单元测试

- [ ] `tests/test_registry.py` - ExchangeRegistry 注册/查询/隔离
- [ ] `tests/test_plugins.py` - PluginLoader 发现/加载/版本检查
- [ ] `tests/test_containers.py` - 数据容器创建/序列化
- [ ] `tests/test_exceptions.py` - 异常层级
- [ ] `tests/test_rate_limiter.py` - 限速器
- [ ] 覆盖率 ≥ 80%

### T502 bt_api_py 核心测试

- [ ] `tests/test_bt_api.py` - BtApi 构造/插件加载/方法调用
- [ ] `tests/test_event_bus.py` - EventBus 发布/订阅/错误处理
- [ ] `tests/test_instrument_manager.py` - 符号注册/查询/映射
- [ ] `tests/test_zmq_publisher.py` - ZMQ 发布
- [ ] `tests/test_zmq_subscriber.py` - ZMQ 订阅
- [ ] `tests/test_zmq_request_reply.py` - ZMQ 交易指令
- [ ] 覆盖率 ≥ 70%

### T503 plugin 注册测试

- [ ] 为每个 bt_api_xx 编写 `test_plugin_registration.py`
- [ ] 验证 register_plugin 返回正确的 PluginInfo
- [ ] 验证注册后 ExchangeRegistry 包含正确的 feed/stream/exchange_data

### T504 集成测试框架

- [ ] 定义集成测试 fixture（安装 base + py + 一个 plugin）
- [ ] 端到端：BtApi → PluginLoader → DirectClient → mock server → 响应
- [ ] CI 中使用 pytest-xdist 并行运行

### T505 mock 交易所服务

- [ ] 创建 `bt_api_base/testing/mock_server.py`
- [ ] 支持模拟 REST API 响应
- [ ] 支持模拟 WebSocket 行情推送
- [ ] 提供标准化的测试数据（ticker、depth、order 等）

---

## Phase 6：文档与示例

### T601 插件开发指南

- [ ] 文件：`docs/plugins/developer-guide.md`
- [ ] 内容：目录结构模板、pyproject.toml 配置、plugin.py 编写、注册流程、测试方法

### T602 用户迁移指南

- [ ] 文件：`docs/migration/v2-migration.md`
- [ ] 内容：旧 API → 新 API 对照表、安装方式变化、import 路径变化

### T603 ZMQ 分发使用说明

- [ ] 文件：`docs/zmq/usage-guide.md`
- [ ] 内容：架构图、配置参数、Publisher 启动、Subscriber 使用、交易指令

### T604 独立直连示例

- [ ] `examples/direct_client/binance_ticker.py` - 获取行情
- [ ] `examples/direct_client/binance_trade.py` - 下单交易
- [ ] `examples/direct_client/okx_websocket.py` - WebSocket 订阅

### T605 统一接口示例

- [ ] `examples/unified_api/multi_exchange.py` - 多交易所同时使用
- [ ] `examples/unified_api/zmq_publisher.py` - 启动 ZMQ 行情分发
- [ ] `examples/unified_api/zmq_subscriber.py` - 策略端订阅行情
