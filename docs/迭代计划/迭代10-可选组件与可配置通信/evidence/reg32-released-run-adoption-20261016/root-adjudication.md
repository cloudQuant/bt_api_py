# 第166轮根裁定：registry 释放运行接管（工作线 #32，DIM01 restart 披露收口）

- 裁定：GO（单元/场景证据）。DIM01 保持正式 PASS，DIM01–DIM09 组仍部分，正式 28/68 不变。依据：目标 20 节点在 r1 两轮与 r2（提交树）一轮全绿 + 独立静态复核 GO。
- 范围（2 文件，+109/-35，提交 `ed09b3ca`）：`bt_api_py/service_registry.py`（`_claim_strategy_write`：任一活跃 claim → 纯 `DUPLICATE_ACTIVE_RUN`（result `{}`）；全部 claim 已释放且恰一个其他 durable run → 元组拒绝携带 `result.strategy_write_run_id`；BEGIN IMMEDIATE 串行化竞争 adopter，败者得纯拒绝、无双活运行窗口；同 run 释放重绑路径与 HEAD 等价；旧进程经 `_expire_sessions` 失效后写路径以 NOT_REGISTERED/claim 复核双重拒绝，fail-closed）、`bt_api_py/service_runtime.py`（`ServiceRuntimeError.result` 默认 `{}`；`_rpc` 失败分支附加 result；策略注册重试恰一次并经 `_released_run_adoption_run_id` 校验代码/角色/run id；采纳 scope 贯穿后续 ops 与 startup 清理；非确定性失败仍记 unknown registration）。文件 sha：service_registry.py `7c708a434397…`、service_runtime.py `3d7fde5919b6…`。
- 验收证据：
  - R1 `D:/temp/it10reg32-20261016-r1`：目标文件 `tests/unit/test_service_risk_only_order_integration.py` run1/run2 各 20 passed；关联套件 run-reg3 31p、run-rh 81p、run-reg03 1p、run-arb 6p、run-dim01 1p、run-oe 6p；run-grp 59p/1f 与 run-hostint 1f/1p 为外国红（D3，登记为第168轮修复对象）。
  - R2 `D:/temp/it10reg32-20261016-r2`（提交树复跑，qa/ 归档）：run1 20 passed / exit 0（junit tests=20 failures=0，11:33:51）；run2（config_scoped 套件）20p/1f、run3（host_integration 四 sqlite 节点）1f —— 均为外国红，如实保留。
- 独立复核 GO（`.codex-tmp/reg32-review-status.md` sha `68b240ef…`；归档 review/independent-review.md）：哈希/语义/竞争窗口/全仓 DUPLICATE_ACTIVE_RUN 断言全过；D2 `sdk-fix.diff` 陈旧（终稿字节经 mtime/哈希链证明为实际被测量）；D4 wire 可见变化仅"释放单运行向量"附 run id，仓内 exact-dict 消费者已核对安全。
- 条款映射：DIM01 第160轮披露"registry run_id 缺口后续工作线 #32"关闭于注册/接管语义层（durable-only 释放检测 + 恰一次采纳重试 + 失效 session 不可写）；"6 个 restart 节点为既有节点"披露不变。
- 提交：`ed09b3ca` = 上述 2 文件 + 本证据目录（qa R2 三轮 + 独立复核 + 源码两文件与 live patch）+ 进度/汇总文档。
- 证据等级：FIXED_SOURCE_REGISTRY_RELEASED_RUN_ADOPTION_UNITS_AND_CURRENT_TREE_REGRESSION_ONLY。
