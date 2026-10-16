# 第169轮根裁定：ARB06 组残差/未对冲 durable Monitor 中继（提交 `dcad4003`）

- 裁定：GO（增量）。ARB01–ARB06 组仍部分，不新增正式 PASS（28/68）。依据：三 Host 真实线上新节点两轮 1 passed、监控回归 25 passed、独立复核 GO-with-disclosures。
- 范围（2 文件，+2251，提交 `dcad4003`）：
  - 新增 `bt_api_py/service_group_monitor.py`（1524 行，sha `41ee954c…`）：只读 SQLite reader（`mode=ro` URI + `PRAGMA query_only` 断言；全部 SQL 参数化），读不可变 `order_group_events` 的 GROUP_RECOVERY_REQUIRED 关联 request_json；独立 checkpoint DB（`relay_checkpoints` 按 namespace CAS 游标、锚点重验、同文件/hardlink/symlink 拒绝）；仅显式 opt-in `poll()`；产出 durable Monitor 事件 `group.residual`（`sdk.group_monitor_projection.v1` payload），幂等身份 `service-event:<sha256>`。
  - 新增 `tests/integration/test_arb06_group_monitor_relay_service.py`（727 行，sha `f939f3e6…`）：三 Host 真实线节点（ARB03 组集群 + Monitor host + relay）断言 durable 投影、二次 poll 幂等与源只读。
- 验收证据（`D:/temp/it10arb06-20261016-r1`；归档 qa 14 件）：
  - run1-baseline-monitor 25 passed / exit 0（15.78s，改前 canary）；
  - run2a collection error 如实保留（`TypeError: unsupported operand type(s) for |: 'tuple' and 'set'` @ service_group_monitor.py:85，exit 2）；
  - run2-arb06-node 1 passed / exit 0（12.43s）；run3 1 passed / exit 0（12.15s，fresh basetemp）；
  - run4-regression-monitor 25 passed / exit 0（15.69s）；
  - 268 件聚合 sha `022aeb66…`；mtime 链证明 run2/3/4 均在文件最后编辑之后执行。
- 独立复核 GO-with-disclosures（`.codex-tmp/arb06-review-status.md`；归档 review/independent-review.md）：哈希/原始日志逐项确认；披露：(a) 未过滤 journal 查询 + 逐事件 scope 校验在多 portfolio journal 上可能因 SCOPE_MISMATCH 永久停摆，"one relay per scope" 缓解说法不准确；(b) 新失败路径缺单元测试。均非本轮阻塞项，登记为后续工作。
- 条款映射：ARB06（组残差/未对冲 durable Monitor 事件 + 三 Host→Monitor 节点）覆盖残差事件投影、幂等重放与只读源；组残差跨域处置与停摆缓解仍为缺口。
- 提交：`dcad4003` = 上述 2 文件 + 本证据目录（qa 5 目录 + 独立复核 + 源码 2 件与 live patch）+ 进度/汇总文档。
- 证据等级：FIXED_SOURCE_SPAWNED_THREE_HOST_CURVE_MONITOR_RELAY_SYNTHETIC_PROVIDER_SCENARIO_ONLY。
