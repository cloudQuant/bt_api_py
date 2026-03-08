# bt_api_py 初级工程师执行版迭代计划（Phase 2）

日期：2026-03-08  
适用对象：初级工程师 / 初中级工程师  
计划周期：4 周  
前置条件：上一轮 [2026-03-08-junior-engineering-iteration-plan.md](/Users/yunjinqi/Documents/source_code/bt_api_py/docs/plans/2026-03-08-junior-engineering-iteration-plan.md) 已完成或至少完成 P0 任务

## 1. 这轮计划为什么存在

上一轮计划主要解决第一批 websocket、monitoring、async pytest 和文档治理问题。继续做下一轮时，最值得投入的已经不是“再补几个零散 warning”，而是把第二批高成本工程债拆掉。

这次代码审查最明确的结论有 6 条：

1. [bt_api_py/core/services.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/core/services.py) 单文件约 `557` 行，存在 `9` 处 `except Exception`，但当前没有对应的直接测试文件。
2. [bt_api_py/security_compliance/core/encryption_manager.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/security_compliance/core/encryption_manager.py) 约 `623` 行、`9` 处 `except Exception`；[bt_api_py/security_compliance/core/audit_logger.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/security_compliance/core/audit_logger.py) 约 `548` 行、`8` 处 `except Exception`，而 [tests/test_security_compliance.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/test_security_compliance.py) 目前几乎只有正向路径，没有 `pytest.raises(...)` 负路径。
3. [bt_api_py/risk_management/core/policy_engine.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/risk_management/core/policy_engine.py) 约 `845` 行、`7` 处 `except Exception`；[tests/test_risk_management.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/test_risk_management.py) 同样几乎没有异常路径测试。
4. 运行时 feed 路径还有明显残留：[bt_api_py/feeds/live_binance/request_base.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/feeds/live_binance/request_base.py) 仍有 `16` 个 `print()`；[bt_api_py/feeds/live_ib_web_stream.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/feeds/live_ib_web_stream.py) 仍有 `9` 处 `except Exception`。
5. `27` 个测试文件调用了 `read_account_config()`，但没有显式 `@pytest.mark.network`；这会让 `not network` 回归和真实 live 测试边界继续混在一起。
6. 一批 request-data / OKX / Binance / IB 测试仍保持脚本风格，例如 [tests/feeds/test_okx_swap_req_funding.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/feeds/test_okx_swap_req_funding.py) 有 `102` 个 `print()`，这不是稳定测试应有的形态。

## 2. 本轮目标

- 把 `core/services` 从“只有实现、缺少直接验证”变成“有生命周期测试的核心模块”
- 给 `security_compliance` 和 `risk_management` 补上第一批负路径测试
- 清理第二批运行时 feed 热点
- 收敛一组高度相似的 spot request base，实现小范围模板化
- 把最常跑的一批 live/request-data 测试明确标成 network，并去掉脚本化写法

## 3. 初级工程师执行规则

1. 每个任务只做一个清晰目标，不要跨太多子系统。
2. 不要修改公开 API 签名，除非高级工程师先确认。
3. 不要把 live/network 失败硬修成“本地通过”；网络或 IP 白名单问题允许保留。
4. 当前仓库仍然很脏；每次只 `git add` 本任务文件，提交前先看 `git diff -- <files>`。
5. 新增测试优先放在现有 `tests/` 结构下；没有必要不要引入新测试插件。

## 4. 任务总览

| 任务ID | 任务名 | 优先级 | 预计工期 | 适合级别 |
|---|---|---:|---:|---|
| N1 | Core Services 生命周期测试补齐 | P0 | 1.5 天 | 初中级 |
| N2 | Security Compliance 失败契约第一批 | P0 | 2 天 | 初中级 |
| N3 | Risk Policy Engine 失败上下文治理 | P1 | 2 天 | 初中级 |
| N4 | Binance/IB 运行时日志与吞错清理 | P0 | 1.5 天 | 初级 |
| N5 | Spot Request Base 模板化试点 | P1 | 2 天 | 初中级 |
| N6 | Live Request 测试标记与脚本化清理 | P0 | 2 天 | 初级 |

## 5. 推荐顺序

### 第 1 周

- N1 Core Services 生命周期测试补齐
- N4 Binance/IB 运行时日志与吞错清理

### 第 2 周

- N2 Security Compliance 失败契约第一批
- N6 Live Request 测试标记与脚本化清理

### 第 3 周

- N3 Risk Policy Engine 失败上下文治理
- N5 Spot Request Base 模板化试点

### 第 4 周

- 统一回归
- 补剩余负路径测试
- 汇总可继续扩展的第二批候选文件

## 6. 任务依赖

- N6 最好在 N4 合入后开始，因为它会使用 [tests/feeds/test_live_binance_spot_request_data.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/feeds/test_live_binance_spot_request_data.py) 和 [tests/feeds/test_live_ib_web_request_data.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/feeds/test_live_ib_web_request_data.py) 做验证。
- N3 不要和 N2 放到同一个 PR；两者都在做“失败契约”相关治理，但对象不同，混在一起 review 成本过高。
- N5 只允许挑 `2~3` 个适配器做试点，不允许顺手扩到十几个交易所。

## 7. 详细任务卡

### N1 Core Services 生命周期测试补齐

目标：给 [bt_api_py/core/services.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/core/services.py) 的关键生命周期补上直接测试，先让这个核心模块可验证。

建议修改文件：

- [bt_api_py/core/services.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/core/services.py)
- `tests/core/test_services.py`（新增）

具体动作：

1. 给 `EventService` 增加 `start()` / `stop()` / `publish_async()` / `unsubscribe()` 的单元测试
2. 给 `ConnectionService` 增加 `get_connection()` / `release_connection()` / `close_all()` 的单元测试
3. 给 `CacheService` 增加“Redis 不可用时回退本地缓存”的测试
4. 如果发现 `pass` 或吞错分支无法测试，先把分支改成可观察日志或显式状态

不要做：

- 不要重写依赖注入容器
- 不要引入真实 Redis、真实网络依赖

验收标准：

- 新增 `tests/core/test_services.py`
- `EventService` / `ConnectionService` / `CacheService` 的关键生命周期都被直接覆盖
- 不新增新的 `except Exception`

自测命令：

```bash
pytest tests/core/test_services.py -q
```

### N2 Security Compliance 失败契约第一批

目标：让 `encryption_manager` 和 `audit_logger` 的失败路径变成“可预期、可测试、可解释”的行为。

建议修改文件：

- [bt_api_py/security_compliance/core/encryption_manager.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/security_compliance/core/encryption_manager.py)
- [bt_api_py/security_compliance/core/audit_logger.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/security_compliance/core/audit_logger.py)
- [tests/test_security_compliance.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/test_security_compliance.py)

具体动作：

1. 把“文件损坏 / 解密失败 / 元数据异常 / 写日志失败”等路径映射到明确错误
2. 优先改成窄异常或统一包装到 `EncryptionError` / `AuditError`
3. 用 `pytest.raises(...)` 补至少 `4` 个负路径测试
4. 避免 `pass` 静默吞掉日志损坏场景

不要做：

- 不要引入真实 AWS KMS / Vault 依赖
- 不要重写整套安全框架

验收标准：

- 目标文件中的静默 `pass` 明显减少
- [tests/test_security_compliance.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/test_security_compliance.py) 至少新增 `4` 个负路径断言
- 缺依赖或损坏输入时，失败原因能被测试稳定观察到

自测命令：

```bash
pytest tests/test_security_compliance.py -q
```

### N3 Risk Policy Engine 失败上下文治理

目标：保留现有公开行为不变，但让策略引擎在失败时留下足够上下文，而不是简单返回 `False`。

建议修改文件：

- [bt_api_py/risk_management/core/policy_engine.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/risk_management/core/policy_engine.py)
- [bt_api_py/risk_management/core/limits_manager.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/risk_management/core/limits_manager.py)
- [tests/test_risk_management.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/test_risk_management.py)

具体动作：

1. 找出返回 `False` 的异常路径，保留错误上下文到日志或状态对象
2. 给无效规则、重复规则、动作处理失败补测试
3. 如果暂时不能改返回类型，至少让调用方能读到失败原因

不要做：

- 不要把 `PolicyEngine` 改成全新架构
- 不要同时改 `RiskManager` 全部调用方

验收标准：

- `PolicyEngine` 关键失败路径不再只有裸 `False`
- [tests/test_risk_management.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/test_risk_management.py) 新增负路径测试
- 修改不破坏现有公开方法签名

自测命令：

```bash
pytest tests/test_risk_management.py -q
```

### N4 Binance/IB 运行时日志与吞错清理

目标：清理第二批运行时热点，把 REST/WebSocket 路径中的 `print()` 和宽泛吞错继续往下压。

建议修改文件：

- [bt_api_py/feeds/live_binance/request_base.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/feeds/live_binance/request_base.py)
- [bt_api_py/feeds/live_binance/spot.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/feeds/live_binance/spot.py)
- [bt_api_py/feeds/live_ib_web_stream.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/feeds/live_ib_web_stream.py)

具体动作：

1. 把生产路径 `print()` 迁移到 logger
2. 把 `except Exception` 改成“记录上下文 + 明确异常/降级”
3. 保留 `exchange_name`、`symbol`、`conid` 等上下文字段

不要做：

- 不要重写 Binance 或 IB 的协议实现
- 不要把 demo 输出也一起删掉，优先只清理运行时路径

验收标准：

- 目标运行时文件中的生产路径 `print()` 清零
- 目标文件中的 `except Exception` 数量下降
- 相关测试不因本地逻辑回归失败

自测命令：

```bash
rg '\\bprint\\s*\\(' \
  bt_api_py/feeds/live_binance/request_base.py \
  bt_api_py/feeds/live_binance/spot.py \
  bt_api_py/feeds/live_ib_web_stream.py
pytest tests/feeds/test_live_binance_spot_request_data.py tests/feeds/test_live_ib_web_request_data.py -q
```

如果失败只来自网络或 IP 白名单，允许保留，但要在 PR 描述里写清楚。

### N5 Spot Request Base 模板化试点

目标：证明一组 spot request base 可以共享公共骨架，减少复制式维护成本。

建议修改文件：

- [bt_api_py/feeds/live_bitbank/request_base.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/feeds/live_bitbank/request_base.py)
- [bt_api_py/feeds/live_bithumb/request_base.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/feeds/live_bithumb/request_base.py)
- [bt_api_py/feeds/live_buda/request_base.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/feeds/live_buda/request_base.py)
- `bt_api_py/feeds/request_base_support.py`（新增）
- [tests/feeds/test_bitbank.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/feeds/test_bitbank.py)
- [tests/feeds/test_bithumb.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/feeds/test_bithumb.py)
- [tests/feeds/test_buda.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/feeds/test_buda.py)

具体动作：

1. 抽出公共 `request()` / `async_request()` / `async_callback()` / `connect()` / `disconnect()` 骨架
2. 让试点适配器只保留签名与私有鉴权差异
3. 保持现有对外类名和注册方式不变

不要做：

- 不要一次改完所有 `live_*/request_base.py`
- 不要引入代码生成器

验收标准：

- 至少 `2` 个试点适配器复用共享 helper/mixin
- 试点文件中的复制代码明显下降
- 相关测试保持通过

自测命令：

```bash
pytest tests/feeds/test_bitbank.py tests/feeds/test_bithumb.py tests/feeds/test_buda.py -q
```

### N6 Live Request 测试标记与脚本化清理

目标：把最常见的一批 live/request-data 测试明确归到 network/integration，并清理脚本式 `print()` / `return` 写法。

建议修改文件：

- [tests/feeds/test_live_binance_spot_request_data.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/feeds/test_live_binance_spot_request_data.py)
- [tests/feeds/test_live_binance_swap_request_data.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/feeds/test_live_binance_swap_request_data.py)
- [tests/feeds/test_live_okx_spot_request_data.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/feeds/test_live_okx_spot_request_data.py)
- [tests/feeds/test_okx_swap_req_funding.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/feeds/test_okx_swap_req_funding.py)
- [tests/feeds/test_live_ib_web_request_data.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/feeds/test_live_ib_web_request_data.py)
- [tests/test_bt_api.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/test_bt_api.py)

具体动作：

1. 给真正依赖 `read_account_config()` 或 live endpoint 的测试补 `@pytest.mark.network`
2. 必要时同时补 `@pytest.mark.integration`
3. 把 `print()` 改成断言、帮助函数或 `pytest.skip(...)`
4. 把超时后的 `return` 改成 `pytest.skip(...)` 或明确断言

不要做：

- 不要删除 live coverage
- 不要把纯解析测试也一股脑标成 network

验收标准：

- 目标文件中依赖真实账号/网络的测试都被显式标记
- 目标文件不再用 `return` 结束 pytest 测试
- `not network` 选择器不会再误跑这些 live 测试

自测命令：

```bash
rg "read_account_config|@pytest.mark.network|@pytest.mark.integration" \
  tests/feeds/test_live_binance_spot_request_data.py \
  tests/feeds/test_live_binance_swap_request_data.py \
  tests/feeds/test_live_okx_spot_request_data.py \
  tests/feeds/test_okx_swap_req_funding.py \
  tests/feeds/test_live_ib_web_request_data.py \
  tests/test_bt_api.py -n
pytest \
  tests/feeds/test_live_binance_spot_request_data.py \
  tests/feeds/test_live_binance_swap_request_data.py \
  tests/feeds/test_live_okx_spot_request_data.py \
  tests/feeds/test_okx_swap_req_funding.py \
  tests/feeds/test_live_ib_web_request_data.py \
  tests/test_bt_api.py -m 'not network' -q
```

## 8. 建议任务分配

如果有 2 个初级工程师，建议这样分：

### 工程师 A

- N1 Core Services 生命周期测试补齐
- N4 Binance/IB 运行时日志与吞错清理
- N6 Live Request 测试标记与脚本化清理

### 工程师 B

- N2 Security Compliance 失败契约第一批
- N5 Spot Request Base 模板化试点

### 由高级工程师带着做

- N3 Risk Policy Engine 失败上下文治理

## 9. 完成定义

本轮完成，至少意味着：

- `core/services` 拥有直接单元测试
- `security_compliance` 与 `risk_management` 不再只有正向路径测试
- 第二批运行时 feed 热点继续下降
- 一组 live/request-data 测试的 network 边界被明确下来
- spot request base 的模板化试点证明值得继续做，而不是停留在想法层
