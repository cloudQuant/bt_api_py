# 第178轮根裁定：MIG03 增量A — 首写后拒绝回退的最小生产闸门（2026-10-16）

范围：3 文件。`bt_api_py/service_migration_gate.py` 新增（16721 B，sha `70700cd648da85d9dcca4ff0e55022cd44f2f5e12090a0734a852b5f3f0cc39d`）；
`bt_api_py/service_host.py` +24/-0（66489 B，sha `07e95f92b5782dd18412b0397ab1c50e3ecf8482c720392314d93f4cbf9d0be1`）；
`tests/unit/test_service_migration_gate.py` 新增 12 节点（17406 B，sha `afaca97a071d5f7069203d3a2f4aaa907c2d71607bd440d76b2cf3b58eb263cb`）。
无其他文件改动；纯新增 + 单文件最小接线。

## 机制（对照正式迭代计划.md:784 “首次新写之后无已验收反向迁移则拒绝回旧版”）

- 闸门态为受保护状态文件旁的 JSON 标记 `<state_path>.migration-cutover.json`（原子写 O_CREAT|O_EXCL tmp + fsync + os.replace；损坏/外来标记 fail-closed `CUTOVER_MARKER_UNREADABLE`，绝不当作“没迁移”）。
- API：`arm_migration_cutover`（同 epoch 对幂等、既存不同对 `CUTOVER_MARKER_CONFLICT`）、`record_migration_first_write`（COMMITTED→SEALED 单向，记录首个业务写事实；无标记时无害 False 可接线）、`enforce_migration_start`（无标记 no-op；COMMITTED no-op；SEALED 仅 destination_epoch 或显式已验收 reverse epoch 可通过，其余一律 `REVERSE_MIGRATION_REFUSED`）、`rollback_migration_cutover`（seal 前移除标记=完全还原；seal 后拒绝除非已记录 accepted reverse）、`accept_reverse_migration`（默认不存在）。
- 宿主接线（service_host.py 唯一消费点）：L225 关键字参数 `migration_deployment_epoch`、L243-245 校验 `INVALID_DEPLOYMENT_EPOCH`、**L346 `start()` 首动作 `_enforce_migration_gate()` 先于一切资源/registry 步骤**（registry 打开本身会写新 service_epoch、释放 write claims，sealed 状态必须先拒）、L782-792 `MigrationGateError.code` → `ServiceHostError(code)` 机读保真。既有调用方不传该参数 → 纯 passthrough（回归证明）。

## 验收

- 作者证据 `author/`（run-01..20，全部保留原退出码）：编译冒烟 exit 0；新套件多字节版本各轮 12 passed（run-04/05/14/15/17/18）、collect-only 12（run-06/20）；回归 host 32（run-07/16）、registry 21（run-08）、spawned host integration 1（run-09）、REG03 spawn drill 1（run-10）、execution integration 1（run-11）、ruff 全过（run-13/19）；失败轮原样保留（run-02 pytest-timeout 未装 exit 4；run-03 pytest-asyncio/import-mode 冲突 exit 2；run-12 ruff 预修复 exit 1）。
- 独立复核 GO（`review/`）：三文件 hash 与作者 sha256.txt/作者 wiring diff 逐字节一致；作者声明 12 节点与 32 节点均独立复现；探针 A：78 项检查（11 种损坏 × 7 操作）全 fail-closed；探针 B：seal 单向、sealed 后拒绝且标记字节不变；探针 C-v2：验收跨新解释器持久、作用域化、rollback 后完全解除（v1 为探针自身缺陷，原样保留）；探针 D：同对并发 arm 40/40 干净；探针 E：宿主级 6/6（sealed+旧 epoch → `ServiceHostError(REVERSE_MIGRATION_REFUSED)` 零副作用、无标记 passthrough、destination/accepted 可通过、损坏 → `CUTOVER_MARKER_UNREADABLE`）。
- 条款引用核对：复核确认正式迭代计划.md:865 引文逐字存在。

## 披露（复核 F1–F5，均不构成 NO-GO）

- F1（中等）：arm/冲突判定为 check-then-write 非原子——异对并发 arm 30/40 双成功（last-writer-wins）、同对竞争返回值与最终文件 18/40 不一致；最终文件始终是合法单文档、无撕裂。**enforcement 仅按 epoch，闸门决策不受影响**；限多写者/多操作员竞态，单操作员演练不受影响。
- F2：不带闸门代码的旧二进制无法被协作代码拒绝（作者已披露；旧 epoch 由带闸门代码的部署声明）。
- F3：当前没有生产调用者 arm/seal/accept（仅测试调用），唯一消费点 `enforce_migration_start`；闸门在 incr-B 前为 inert，符合增量A范围。
- F4（轻）：`schema_version != 1` 检查使 JSON `true`/`1.0` 通过（True==1），仅此字段，其余严格校验；无实际影响。
- F5（设计注记）：accept 后 `deployment_epoch=None` 的 post-seal rollback 可移除标记；rollback 未接宿主，无生产路径。

## 条款映射

MIG03（正式迭代计划.md:784）：子片段“首次新写之后无已验收反向迁移则拒绝回旧版”在闸门组件 + 宿主 start 强制层落地（含拒绝先于一切副作用、码保真、跨解释器持久）。
仍缺：受控迁移演练（drain→fence→按 ID 导入守恒→新 epoch→重登记→回退拒绝，incr-B）、调用计数、双 owner/UNKNOWN 删除边界、真实 provider 切换。**MIG03 仍部分，不新增正式 PASS（28/68）。**

证据等级 `FIXED_SOURCE_CUTOVER_GATE_UNITS_AND_HOST_WIRING_WITH_ADVERSARIAL_PROBES_ONLY`。
归档排除：双方 basetemp-* 工作目录、探针 probe data/ 状态目录（SQLite/标记样本，可再生产；结论均在探针日志中）。

## 归档内容

- `author/`（100 件）：00-INDEX.txt、run-01..20 每轮 cmd/exit/stdout/stderr、各轮 precheck 进程快照、service_host.wiring.diff.txt、sha256.txt。
- `review/`（61 件）：REVIEW-SUMMARY.txt、diff-vs-author.txt、git-status-before/after/final、hashes-local.txt、review-service_host.diff.txt、run-r01..r07 每轮记录、probes/ 五个探针脚本+日志。
- `author-status.md`、`review-status.md`、`mig03-recon.md`、本裁定文、99-evidence-sha256.txt。
