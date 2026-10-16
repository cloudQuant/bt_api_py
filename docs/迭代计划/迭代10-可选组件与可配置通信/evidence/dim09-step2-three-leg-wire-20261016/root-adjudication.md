# DIM09 Step-2 根裁定（root adjudication，2026-10-16）

裁定对象：DIM09 Step-2「SDK 三腿 v3 wire 闸门 + host 级测试」交付与独立复核。
**裁定：GO 并提交；DIM09 保持部分（28/68 不变）** —— v3 生产报价构建器拓宽（execution
仓 Step-1 follow-up）未落地，端到端生产报价路径仍 fail-closed（见披露 1），条款证据链
待该 follow-up 完成后进入收口轮。

## 交付物与哈希（live bytes，复核起止一致）

- `bt_api_py/service_execution_group.py` sha256
  `d6b37ccfce7f2044e935fa22f1706d553122e450e1c1a3e1b849a54573d4addb`
- `bt_api_py/service_group_reservations.py` sha256
  `70926f4bea2e345cb29830e9721bd9d46a31ac574e629f1ab8d9995a9f0253b6`
- `bt_api_py/service_execution_group_client.py` sha256
  `6fc51a31e61ce8f0238f65b343133ae58bd6b4e6483947ac67150be4c7e3e2c5`
- `tests/integration/_dim09_three_leg_fixtures.py`（新增）sha256
  `f387b9508ff97bec1ce8c811bc51a3237b59ceb3c7b3debe731dff3654443dd1`
- `tests/integration/test_dim09_three_leg_group_service.py`（新增，5 节点）sha256
  `0827e05e02ef0cd3cb2525fe6b249815ed56e21b684c47d8e964bf8ab6fba278`

## 门锚（service_execution_group.py 行号）

- S1 v3 三腿白名单：`_parse_request` :797，v3 恰 3 腿 :819，per-leg key 切换 :822-823，
  ratio 分支 :839（`_parse_ratio` :1925，精确正 decimal 字符串）。
- S2 声明版本：`_submit_declared_version` :1906（legacy 4 键→1；5 键 `version` 必须
  `type(...) is int` 且 ==3，否则 `GROUP_VERSION_INVALID`：1917-1921）。
- S4 重放：`_assert_exact_submit_replay` :613（leg key 集区分 641-642；版本一致性 658）。
- S5 既有绑定：`_existing_submit_binding` :592（legacy 4 键或 versioned 5 键：602）。
- S6（CFG06b 组准入闸门）:546-555 继承未改（复核确认与 HEAD 字节一致）。
- S7：`service_group_reservations.py` `_parse_members` :652，成员 1..3 :661-662。
- S9：`service_execution_group_client.py` `AcceptedGroupBinding.__post_init__` :80 成员向量
  :105-106；`_normalize_members` :1068 数量门 :1099-1100。

## 新测试（5 节点，条款映射见下）

- test_dim09_three_legs_reach_paired_with_source_backed_facts（L62）：C/P/F 三腿成交、
  PAIRED、逐腿 claim+resolution 证明链、预算 HELD、残差 (0,0,0)。
- test_dim09_second_leg_rejected_stops_third_leg_over_wire（L158）：第二腿被 provider 拒付，
  第三腿未派发（0 provider 调用）、RECOVERY_REQUIRED/chain_interrupted_zero_fill、hold 保留。
- test_dim09_coordinator_restart_reconciles_third_leg_unknown（L204）：第三腿回执丢失→UNKNOWN；
  owner 重启（同 writer_id）+ 协调器重启；原 ID 查询；不重发；hold 保留；replay→PAIRED。
- test_dim09_group_policy_denial_blocks_three_leg_submit（L295）：CFG06b 门在接收前拒绝、
  零派发、GROUP_POLICY_DENIED。
- test_dim09_three_leg_wire_whitelist_is_fail_closed（L333）：S1/S2 负例、S7 4/0 成员
  INVALID_MEMBERS + 1/3 正例、S9 direct-binding 0/4 负例 + 1/3 正例、legacy 2 腿对照。

## 验收记录

- 作者轮（author-r1/，源自 D:/temp/it10dim09-step2-20261016-r1）：run-1「56 passed in
  47.50s」exit 0；run-2「56 passed in 49.92s」exit 0；collect-only「56 tests collected」exit 0；
  145 pins 三变体一致且与 live 字节相符；sources/heads 前后不变。序列化：12:54-12:56 检测到
  短命 anaconda3 进程爆发，记录后 bounded wait 直至 PS-CLEAR 才启动 run-1（无 kill）。
- 独立复核（review-r1/，源自 D:/temp/it10dim09-step2-review-20261016-r1，GO-with-disclosures）：
  起止 13:03:57/13:12:02 哈希一致；新文件单独 5 passed 10.18s exit 0；作者 7 模块套件
  56 passed 47.09s exit 0；S1/S2/S4/S5/S7/S9 逐项 wire 探针证实（版本 True/3.0/"3"/2 →
  GROUP_VERSION_INVALID；额外 body/leg 键 → INVALID_REQUEST/GROUP_LEG_INVALID；比例不合法
  失败关闭；replay 比例变化 → GROUP_BINDING_CONFLICT；Decimal 等值改写法接受）；S6 区域与
  HEAD 字节一致；无证伪项；重跑全部在 13:30 窗口前结束。

## 条款映射（正式迭代计划.md:781）

| 条款片段 | 覆盖证据 |
|---|---|
| 三腿 C/P/F 比例和乘数 | Step-1 v3 合同（execution `6e89d30`）+ 本 Step-2 S1/S2 比率解析与白名单 + 节点1 |
| 各阶段零/部分成交 | Step-1 协调器层分段链（v3 residual_vector 仅 v3）+ 节点2 第三腿零成交链停 |
| 第二/第三腿拒绝或 UNKNOWN | 节点2（拒绝）/ 节点3（UNKNOWN）over wire |
| 策略退出及协调重启 | 节点3 owner+协调器重启、不重发、原 ID 查询、replay→PAIRED |
| 残差向量与预算守恒 | 节点1 残差 (0,0,0) + 预算 HELD；节点2/3 hold 保留 |
| 实际期权成交 | 按条款「另计」，不在本轮范围 |

## 披露（不阻塞本轮提交）

1. **生产报价构建器未拓宽（DIM09 PASS 前置）**：execution 仓
   `bt_api/bt_api_execution/src/bt_api_execution/group_budget.py:48` 仍拒绝 version != 1 →
   真实 v3 三腿请求在生产报价路径 fail-closed 为 GROUP_BUDGET_QUOTE_INVALID；测试经
   `_replicated_v3_quote`（文档化镜像）构造 v3 报价。复核直接探针证实。**v3 报价构建器拓宽
   = execution 仓 Step-1 follow-up**，落地并复核后 DIM09 方可进入收口轮。
2. **CFG06b r1 receipt 已被取代**：3 个被 pin 文件 live 与 CFG06b r1 时点不同
   （975c2c6b/37a20e82/675a54b7 vs d6b37ccf/6fc51a31/70926f4b；reservations pin 在 HEAD
   即已漂移 via `9d1dc457`/`69cffe84`）——**CFG06 正式轮必须重建 spec**。
3. 复核期间一次 6s 探针与外来 python 突发重叠（bounded 120s wait 耗尽后记录，无 kill）；
   必需套件从未重叠。
4. `_replicated_v3_quote` 与生产实现的偏差：`sum()` vs `_exact_decimal_sum`、import 位置
   —— 对测试取值 immaterial。
5. `_normalize_members` >3 成员负例仅代码级核对（ctor 负例已测）。
6. 作者 QA 前开发期运行日志为自报（不可独立复算），QA 证据自身齐备。

## 证据裁剪（照实）

归档仅排除运行 scratch：`pytest-tmp/`、`basetemp/`、`*.sqlite*`、`*.lock`、`__pycache__`
（完整树保留于 D:/temp/it10dim09-step2-20261016-r1 与 it10dim09-step2-review-20261016-r1）。
