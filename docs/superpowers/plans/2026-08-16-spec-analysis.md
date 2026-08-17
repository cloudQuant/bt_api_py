# bt_api_py 全量分析结论(Spec)

> 2026-08-16,由 4 个并行只读分析 agent 产出(核心包架构 / 交易所适配器 / 打包·CI·发布 / 测试·监控·安全),关键 P0 已由主会话抽查验证。本文档是整改迭代计划的事实依据(Spec),整改计划见 `2026-08-16-remediation-iteration-plan.md`。

## 背景事实

- 仓库根:`/Users/yunjinqi/Documents/new_projects/bt_api_py`,~10.5 万行 Python。
- `bt_api_py/` 核心包(~1 万行):基础设施从 `bt_api_base` re-export,含 brokers/、forwarding/、backtrader/、risk_management/、monitoring/、security_compliance/、certification/、gateway/、utils/、configs/。
- `bt_api/` 下 61 个交易所适配器 git 子模块(~9.5 万行源码 + ~5 万行测试),feeds 实现在各子模块 `src/bt_api_xxx/feeds/` 中;插件发现走 `bt_api_base.plugins.loader` + entry points(`bt_api.plugins`)。
- 442 个测试文件;母仓库 CI 只跑根 `tests/`(28 文件,384 测试),子模块测试不在母仓库 CI。

## Findings 注册表

### A — 订单链路 / 核心包正确性

| ID | 严重度 | 位置 | 问题 | 状态 |
|----|--------|------|------|------|
| A-01 | P0 | `bt_api_py/forwarding/router.py:133-135` | 非法 `side` 静默变 `"buy"`、非法 `order_type` 静默变 `"market"`,拼写错误可致市价买单直发交易所;`client.py:298-300` 默认值同为 `side="buy"/order_type="market"` | ✅已抽查验证 |
| A-02 | P0 | `router.py:144-153,213-223` | 幂等缓存缓存了瞬时错误(NETWORK_ERROR/RATE_LIMITED)的拒绝 ack,重试永远拿到旧拒绝;`brokers/errors.py:26-33` 的 `retryable` 字段未被使用 | |
| A-03 | P0 | `forwarding/transport.py:236-245` | ZMQ `send` 超时抛 TimeoutError 后不回读残留应答,下一次 send 读到上一条命令的 ack(响应错配) | |
| A-04 | P0 | `forwarding/memory.py:215-242` | 超时≠取消:超时后线程继续执行下单逻辑产生幽灵订单;线程内 `asyncio.run` 新 loop 执行绑定原 loop 的 async handler → RuntimeError | |
| A-05 | P0 | `forwarding/service.py:84-108,140` + `transport.py:259-267` | `start_sync` 用 asyncio.run 反复创建/销毁事件循环,与 adapter 连接绑定 loop 冲突;已在 loop 内调用同样崩溃 | |
| A-06 | P0 | `bt_api.py:390-395` | kline 重试耗尽后仅 log 后 `return`,历史数据静默缺失无异常 | |
| A-07 | P0 | `bt_api.py:57-71` | `_parse_time` naive ISO 字符串按本地时间、naive datetime 按 UTC,同一函数两种语义,历史数据错位 | |
| A-08 | P1 | `forwarding/client.py:267` | cancel_order 幂等 key 含 `uuid.uuid4()`,重试 key 不命中幂等表,重复撤单直达交易所 | |
| A-09 | P1 | `bt_api.py:784-801` | `__getattr__` 动态代理:`hasattr(api,"async_xxx")` 恒真、忘 await 静默丢协程、无类型 | |
| A-10 | P1 | `bt_api.py:110-111` | import 时全量加载 60+ 插件,任一插件坏则整个包 import 失败 | |
| A-11 | P1 | `bt_api.py:534-545` | `close()` 只关 `feed._http_client`,不关 WebSocket/订阅流 | |
| A-12 | P1 | `bt_api.py:289-291` | `get_async_request_api` 返回同步 feed,名实不符 | |
| A-13 | P1 | `bt_api.py:319-321` | subscribe 交易所不存在时仅 log + return,且先累加了 `subscribe_bar_num` | |
| A-14 | P2 | `forwarding/state.py:22-24` | `sqlite3.connect("memory:")` 在磁盘创建名为 `memory:` 的文件而非内存库 | |
| A-15 | P2 | `router.py:45` | `_acks_by_idempotency_key` 无界内存增长 | |
| A-16 | P2 | `brokers/mock.py:112` | 多次成交 `average_price` 覆盖为最新价而非加权平均 | |
| A-17 | P2 | `bt_api.py:473,481-498` | `update_balance` 未注册交易所抛裸 KeyError(与 `get_cash` 的 ExchangeNotFoundError 不一致) | |
| A-18 | P2 | `bt_api.py:866` | 核心入口超 800 行,含 17 空格异常缩进(生成代码痕迹);`KLINE_PERIOD_DELTAS` 大小写混用 | |
| A-19 | P2 | `forwarding/client.py:235-240` | fetch_open_orders 只过滤 `status=="submitted"`,漏掉 `"new"` | |
| A-20 | P2 | `forwarding/client.py:392-406` | tick/bar timestamp 秒/毫秒单位混用 | |

### B — 交易所适配器体系

| ID | 严重度 | 位置 | 问题 | 状态 |
|----|--------|------|------|------|
| B-01 | P0 | 59/61 子模块工作区 | 未提交的真实代码改动(binance 96 文件 +2941/-2603、ctp 41、htx 38……),`git submodule update --force`/新克隆会整体丢失 | ✅binance 96 文件已验证 |
| B-02 | P0 | `bt_api_okx/.../request_base.py:216,250`;`market_wss_base.py:86` | OK-ACCESS-TIMESTAMP 用 `round(time.time(),3)` epoch 浮点,OKX V5 要求 ISO 8601,私有请求大概率被拒 | ✅已抽查验证 |
| B-03 | P0 | 5 个子模块(bequant/bigone/bingx/bitbank/bitflyer) | 删除已 staged 未 commit;`.gitmodules` 已改但 `_generate_docs.py:452`、`docs/CODE_QUALITY.md:55` 仍引用死仓库 | |
| B-04 | P0 | binance/okx/gateio/hyperliquid/bybit `request_base.py` | `translate_error` 定义但全仓零调用,API 层错误(如 Binance -2019)被当正常数据返回 | |
| B-05 | P0 | 29 个适配器 pyproject | 无 `[project.entry-points."bt_api.plugins"]` 注册入口,运行时不可被发现;bybit、gmx 连 plugin.py 都没有 | |
| B-06 | P1 | binance/okx/hyperliquid `request()` | 同名方法签名漂移(有无 `is_sign`);目录布局漂移(binance 平铺 vs okx `live_*` 子目录) | |
| B-07 | P1 | `bt_api_binance/.../request_base.py:166` | `sign()` 缺 key 时 `pk = self.private_key or ""` 空串参与 HMAC,静默降级 | |
| B-08 | P1 | bybit | 完全没有 WSS 实现,能力矩阵不一致 | |
| B-09 | P1 | 40+ 子模块 git 仓库 | 构建产物入库:bybit 29 个 .pyc、hyperliquid 34、mexc 30、bitget 40;okx 66 个 build/lib 文件;bybit 6 个 egg-info | |
| B-10 | P1 | `bt_api_binance/tests/test_binance_sign.py:11-22` | 签名测试自指空壳(测试体内重实现 hmac 自比较);zebpay 唯一测试只断言 exchange_name;bithumb/giottus 测试跨仓复制粘贴 | |
| B-11 | P1 | okx/hyperliquid `exchange_data` | URL 硬编码生产地址,无统一测试网切换入口;yaml 端表手写重复 | |
| B-12 | P1 | `bt_api_hyperliquid/.../request_base.py:80,87-124` | 私钥加载后无任何 EIP-712/sign_message 调用,`is_sign` 参数被忽略,下单签名链路缺失(死代码或半成品) | |
| B-13 | P1 | `bt_api_base/src/bt_api_base/feeds/feed.py:194-251` | HTTP 重试仅对异常生效,无 429/Retry-After 处理;RateLimiter 接入方式不统一 | |
| B-14 | P1 | `bt_api_okx/.../market_wss_base.py:108` | 登录后固定 `time.sleep(0.3)` 再订阅,时序脆弱;断线重连后无重订阅逻辑 | |
| B-15 | P1 | 39 个 request_base.py | 跨仓重复代码:39 处 hmac 签名、15+ 处时间戳转换、6 处 rate_limiter 工厂几乎逐字相同 | |
| B-16 | P1 | `bt_api_gateio/src/bt_api_ctp/` | gateio 仓混入 ctp 适配器残留包;`bt_api_btbns` 是空仓库(0 源文件)仍被 pin | |
| B-17 | P1 | `bt_api_hyperliquid/.../request_base.py:117` | `async_request` 直接 f-string 打原始 URL 到日志,绕过脱敏 | |
| B-18 | P1 | `bt_api_binance/.../request_base.py`(2581 行) | 头号适配器 request_base 是 2581 行巨型单文件 | |
| B-19 | P1 | `bt_api/install_and_test_all.py:114` | 无 tests/ 目录的包直接 success=True,"没测"当"PASS";串行无并行、无报告产物 | |
| B-20 | P2 | `bt_api_base/.../connection_mixin.py` 等 | 中文 docstring 编码损坏(mojibake) | |
| B-21 | P2 | `bt_api_binance/tests/test_binance_sign.py:15` | 测试硬编码 64 位 hex 私钥字符串 | |
| B-22 | P2 | `bt_api_binance/.../request_base.py:200-210` | 注释掉的 `print(self.public_key/private_key)` 调试代码 | |
| B-23 | P2 | bybit/gateio spot.py | 共享 logger 名 `get_logger("request")`,多交易所同进程无法按仓隔离 | |
| B-24 | P2 | 各仓版本号 | 无统一发布节奏:base 0.15.1、binance 2.0.1、45 仓停留 0.1.x | |

### C — 测试诚信

| ID | 严重度 | 位置 | 问题 | 状态 |
|----|--------|------|------|------|
| C-01 | P0 | `conftest.py:308-323` | `pytest_runtest_makereport` 把消息含 "ssl"/"timeout"/"not found"/"api_key"/"404" 等子串的真实失败改写为 skipped,CI 可全绿 | ✅已抽查验证 |
| C-02 | P0 | `conftest.py:180-212` | 按 fspath 自动打 network 标记,从仓库根跑的子模块测试失败全部被 C-01 吞掉 | |
| C-03 | P0 | `bt_api_py/security_compliance/core/encryption_manager.py:37-47` | 可选依赖守卫只捕 ImportError,boto3 实际抛 AttributeError 逃逸 → 3 个测试文件(120+ 测试)收集失败静默消失 | |
| C-04 | P1 | `tests/test_quality_batch_v3/v5/v6.py` | `inspect.getsource()` 断言源码文本的形式检查,合理重构即破坏、不防回归 | |
| C-05 | P1 | `bt_api/bt_api_binance/tests/test_monitoring.py` 等 3 文件 | 根仓库测试文件被复制进 binance 子模块,两副本已分叉 | |
| C-06 | P1 | `tests/test_oauth2_provider*` 3 份 | 同模块三份测试且互不为超集,无权威版本 | |
| C-07 | P2 | `conftest.py:354-368` | `reset_environment` autouse fixture 每测试拷贝/恢复整个 os.environ | |
| C-08 | P2 | `pyproject.toml:53-58` | `addopts` 硬编码 `--dist=loadgroup`,未装 xdist 时裸 pytest 报错 | |
| C-09 | P2 | `tests/test_security_compliance.py:1133` | 相对路径 "./tmp_keys" 写密钥材料,依赖 CWD | |

### D — 发布 / CI / 工程化

| ID | 严重度 | 位置 | 问题 | 状态 |
|----|--------|------|------|------|
| D-01 | P0 | 仓库根 | 无 LICENSE 文件,但 pyproject 声明 MIT、README 引用 LICENSE | ✅已抽查验证 |
| D-02 | P0 | `_version.py:5` vs `__init__.py:12` | 版本双源:0.15.0 vs 从 bt_api_base 导入 0.15.2;发布 tag↔版本无一致性校验 | ✅已抽查验证 |
| D-03 | P0 | `publish.yml:22-49` | 9 个矩阵 job 产出同名 py3-none-any.whl,merge-multiple 报错;构建不存在的 CTP/Cython 扩展 | ✅已抽查验证 |
| D-04 | P0 | 全部 workflow | 60+ 子模块 CI 零覆盖,checkout 无 submodules | |
| D-05 | P1 | `mkdocs.yml:71` | mkdocstrings paths 指向不存在的 src 布局(近期连续修 docs 提交的根因) | |
| D-06 | P1 | `docs/reference/registry.md` 等 | 引用不存在的 bt_api_base.registry/event_bus/auth_config,靠注释 directive 止血 | |
| D-07 | P1 | `setup.py:60-63` + `MANIFEST.in:2-24` | 一半死代码指向已删的 CTP 源码;MANIFEST 引用不存在的 requirements.txt | |
| D-08 | P1 | `pyproject.toml:213-220` | mypy 关闭 arg-type/attr-defined 等 20 个核心错误码,类型检查空转 | |
| D-09 | P1 | pyproject/setup/publish 三处 | Python 版本互相矛盾(>=3.9 vs classifiers 到 3.14 vs 只构建 3.11-3.13) | |
| D-10 | P1 | CHANGELOG.md + docs/getting-started/change_log.md | 双 changelog 并存且停更;0.15.0 条目宣称的功能(Registry/EventBus/CTP)当前不存在 | |
| D-11 | P2 | `optimized-tests.yml` vs `tests.yml` | 18 job 兼容矩阵完全重复 | |
| D-12 | P2 | `tests.yml:61-63` | pip-audit `\|\| true` + continue-on-error 双保险,依赖审计永不失败 | |
| D-13 | P2 | `_generate_docs.py:212-220` | `"x" in containers or True` 恒真,60+ 交易所 README 的 Supported Operations 全虚假 | |
| D-14 | P2 | `_generate_docs.py:451-469,565,588` | 硬编码 64 仓清单与 .gitmodules 漂移;生成的 CI 模板含 `pytest tests/ \|\| true` | |
| D-15 | P2 | `.readthedocs.yaml` | RTD 与 GH Pages 双通道部署;fail_on_warning: false | |

### E — 安全

| ID | 严重度 | 位置 | 问题 | 状态 |
|----|--------|------|------|------|
| E-01 | P1 | `bt_api_py/security_compliance/`(~4700 行)+ certification/ | 全库无任何生产调用方,审计日志不记录真实下单(合规假象) | |
| E-02 | P1 | `security_compliance/core/audit_logger.py:145,158,198` | `encryption_key` 参数从未使用,details 明文 JSON 落盘;`_write_event_atomic` 的临时文件是死代码且写入非原子 | |
| E-03 | P1 | `security_compliance/core/encryption_manager.py:133-134,166-175` | PBKDF2 salt 由 sha256(口令) 派生(确定性盐);密钥文件无 chmod | |
| E-04 | P2 | `security_compliance/network/tls_manager.py:50-52` | `certificate_validation="none"` 后门(CERT_NONE + check_hostname=False) | |
| E-05 | P2 | `bt_api_py/monitoring/prometheus.py:218-219` | 默认绑定 0.0.0.0:8080 无鉴权 | |
| E-06 | P2 | `risk_management/ml_models/ml_base.py:9` | pickle 加载模型文件(B403 未缓解),不可信模型可 RCE | |
| E-07 | P2 | `certification/audit.py:44` | `mask_sensitive` 保留后 4 位明文 | |

### F — 死代码 / 架构收敛

| ID | 严重度 | 位置 | 问题 | 状态 |
|----|--------|------|------|------|
| F-01 | P1 | `bt_api_py/feed_registry.py`(145 行) | 全仓零引用死代码;:137 引用不存在的 `bt_api_py.plugins.loader`;真正的加载走 `bt_api_base.plugins.loader` | |
| F-02 | P1 | `risk_management/__init__.py:38-70` | `__all__` 定义两次,第二个引用不存在的 4 个类,`import *` 抛 AttributeError | |
| F-03 | P1 | `risk_management/ml_models/*.py:12-14` | 顶层硬 import sklearn,但 sklearn 仅在 dev extra,常规安装 import 即 ImportError | |
| F-04 | P1 | `backtrader/btapibroker.py`(17 行 stub)+ mapping.py(空) | 名为 backtrader 的包无任何 backtrader 集成代码,却导出顶层 `BtApiBroker` | |
| F-05 | P1 | `_plugin_shims.py`、`utils/time.py`、`_version.py` | 零引用双源副本,与 bt_api_base 版本漂移(如废弃的 utcfromtimestamp) | |
| F-06 | P1 | risk_management/ + monitoring/ + security_compliance/ 约 1.2 万行 | 未接入主链路,与 forwarding/router.py 自带 RiskRuleSet 构成两套互不通的风控 | |
| F-07 | P2 | `brokers/gateway_bridge.py:72-82` | GatewayBridgeAdapter 所有方法只抛 NOT_SUPPORTED 占位 | |
| F-08 | P2 | `forwarding/hub.py:18-34` | MarketDataHub.subscribe 的 refcount 与 bus 实际投递无关(死簿记) | |
| F-09 | P2 | `monitoring/config.py:14-20` | 从自身包 `bt_api_py.monitoring` 导入(反向依赖环味) | |
| F-10 | P2 | `ctp_env_selector.py:88-89` | 硬编码 SimNow 公网 IP 默认 front;通过改 os.environ 传递配置 | |
| F-11 | P2 | 巨型文件 | binance request_base 2581 行、risk_management 4 个 800-960 行文件、bt_api.py 866 行 | |
| F-12 | P2 | `gateway/client.py:18-62` | GatewayClient 7 个别名 endpoint 参数 + **kwargs 兜底 | |

## 正面结论(经核实无问题,不整改)

- 无密钥文件提交进 git(keys/、tmp_keys/、.env 均正确 ignore 且为空);母仓库构建产物(build/、dist/、site/、egg-info)未跟踪。
- 全库无 `verify=False`、`shell=True`、`pickle/eval` 不安全用法(ml_base 除外)、无硬编码 16+ 字符密钥。
- bt_api_base 底子好:统一 Feed 基类、指数退避重连、日志脱敏、RateLimiter、identity_manager 用 bcrypt、TLSManager 默认 TLS1.3。
- 根 tests/ 对 monitoring/metrics、forwarding、containers、risk_management 有较扎实的行为测试。

## 依赖关系(决定迭代顺序)

```
迭代0(数据止血) → 迭代1(订单链路) ─┐
                                  ├→ 迭代3(发布工程化) → 迭代4(适配器) → 迭代5(架构收敛)
迭代2(测试诚信,可与1并行) ────────┘
```
- 迭代 0 必须先做:`git submodule update --force` 随时可能发生,59 仓未提交改动是最大丢失风险。
- 迭代 2 先于 1/3/4 的价值:恢复"红灯真实"后,后续所有修复的验收才可信。
- 迭代 4 依赖迭代 0(改子模块必须先能提交)与迭代 2(子模块测试不被 conftest 污染)。
