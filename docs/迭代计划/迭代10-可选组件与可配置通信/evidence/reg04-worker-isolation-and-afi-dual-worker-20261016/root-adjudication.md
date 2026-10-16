# REG04 增量1+2 根裁定（root adjudication，2026-10-16）

裁定对象：REG04 增量1（bt_api_py 宿主级 worker 隔离测试）与增量2（AFI 真实链双 worker 测试）的交付
与两轮独立复核。**裁定：两增量 GO 并提交；REG04 保持部分（28/68 不变）。** 依据 recon
（recon.md §3 INCR-3，正文 `REG04 cannot be declared PASS with the mapping clause untested unless so
recorded`）：旧 ID 映射半项既未实现也未获得经记录的 scope 决定，PASS 前置未满足。

## 交付物与哈希

- `tests/unit/test_service_reg04_worker_isolation.py`（新增，4 节点，纯新增无生产改动）：
  sha256 `7c749a6810295b8f7431d6e01bf39663e3477b248c32455f588e338685910861`。
- AFI 仓 `src/backend/tests/test_service_read_reg04_workers.py`（新增，2 节点）：
  sha256 `e8c35811387873affe184462f4f7568eaacde58f277bf2bca578b78e8f87277a`；
  AFI 提交 `e9c7ca1d`（dev，父 `794eab0c`，1 file, +1297）。
- 父仓提交见第180轮进度条目（与本归档同提交）。

## 验收（作者轮，全部 exit 0；失败尝试原样保留）

- 增量1（author-incr1/）：collect-only 4 collected；run2/run3 4 passed（4.74s/4.74s）；
  并发生产改动后 run4 复核 4 passed（4.92s）；run1 修前失败（1 failed/3 passed）保留。
  证据含 7 个生产模块 run-4 时点哈希（production-modules.sha256）。
- 增量2（author-incr2/）：r9 同源 runner（`8b7f470b…`）+ 6 根冻结 PYTHONPATH + 8 env unset；
  2148 pins（2147 r9 + 新文件）重算一致；collect-only 2 collected；run1 2 passed（60.86s）、
  run2 2 passed（57.86s）；sources/heads 前后不变（AFI `794eab0c`）。
  失败尝试（syntaxerror / scopeassert / approvedcount）原样保留。

## 独立复核 GO ×2（review-incr1-status.md / review-incr2-status.md）

- 增量1：两轮独立复跑 4 passed；7 生产模块哈希全对；真实机具逐项核验——spawned ServiceHost
  CURVE、真实 `_claim_strategy_write` 拒绝第二策略 run、零 handler 调用、caller_bindings
  身份检查拒绝、freeze 计数器 0/恰一次；无 skip/xfail/删断言。
- 增量2：文件 sha 与 6 冻结 tar 全对；独立复跑 2 passed（57.56s）；2148/2148 pins 一致；
  真实 uvicorn/生产 lifespan 双进程、真实 claim、恰一次派发 durable 断言通过；
  复核独立重算了 pin 集合。

## 条款映射（正式迭代计划.md:747）

| 条款片段 | 覆盖证据 |
|---|---|
| AFI 多 worker 不占策略单活名额 | 增量2 N1/N2（真实 HTTP→SDK→Execution 双 app 进程 + 策略 run 同 owner）；增量1 节点1（宿主级） |
| 应用/service actor 与策略/人工 book 归属分离 | 增量1 节点1 scope 正反例（account-only/未知 book 拒绝）；增量2 归属 durable 断言 + 跨会话/用户单人工 book scope |
| Monitor 转交不扩权 | 增量1 节点4（错 scope/route/actor 全拒绝、合法冻结恰一次、内部 op 不可越权） |
| 服务间越权预留拒绝 | 增量1 节点2/3（无 TrustedGroupReservationPolicy → 拒绝且零 handler 调用） |
| 策略/人工 book/旧 ID 映射不产生双份额度 | 策略/人工 book 已覆盖（披露见下）；**旧 ID 映射未实现未测 → PASS 前置未满足** |

## 披露（不阻塞本轮提交）

1. 增量1 worker 在策略 claim 存续期间仅注册/续期，不提交订单（隔离性质为注册面；M1）。
2. 增量2 账户簿 E/R 残差投影未挂载于该 owner fixture：无双重额度断言取最强可得机具
   （gate.reserved 每单一次 + 组预算单 RESERVED + 单人工 book scope + journal/provider 恰一次），
   复核判 adequate。
3. 增量1 复核细项：节点2「adapter 未构造」为代码序推断；节点4 使用同一 grant 的 intruder
   依赖 caller_bindings 身份检查；节点1 早前红跑说明的是注册面差异（已修复于新文件）。
4. 增量2 复核细项：closed[3]/[6] 为合成 provider 叶上的跨进程回执、额外记录缺席为传递论证；
   未使用的 monkeypatch 形参 2 处（1003/1293，外观）。
5. 证据裁剪（照实）：仅排除运行 basetemp scratch（`pytest-tmp/`，含 reg04-shared-authority.sqlite3 ×10）
   与运行期 SQLite 数据/锁文件（32 件，2097162 B）；完整树保留于 D:/temp/it10reg04-20261016-r1、
   it10reg04-review-20261016-r1、it10reg04-incr2-20261016-r1、it10reg04-incr2-review-20261016-r1b。
