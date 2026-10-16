# RISK02 翻转轮裁定（第186轮，2026-10-16）

- 对象：`docs/迭代计划/迭代10-可选组件与可配置通信/迭代10验收状态汇总.md` RISK02 行
- 条款（`正式迭代计划.md:760`）：TTL、UNKNOWN、部分成交、撤单及重启的敞口转换；汇率/乘数/数据过期；证据单元：规则及完整订单观察链；不凭模拟余额宣称账户通过
- 独立对抗审计（只读，未运行被测系统）：`D:/temp/it10risk02-audit-20261016-r1/audit.md`，sha256 `14ac2eba4f11d0494c48d921d7dc9ad1f61ce2b3fe31f4b82ead0eaa8990bca9`，结论 **GO-with-disclosures**
- 审计副本：`acceptance-audit-r1/audit.md`（同哈希 `14ac2eba…`）

## 裁定

**翻转 正式 PASS，累计 30/68。** 八个片段（TTL、UNKNOWN、部分成交、撤单、重启、汇率、乘数、数据过期）与三项证据单元要求（规则链、完整订单观察链、不凭模拟余额）均有已接受证据覆盖（r43/r68 包级链；r134/r146/r150 宿主链）；对抗性证伪未发现矛盾；残差为披露级。

## 强制披露（随 PASS 逐字发布）

> RISK02 独立对抗审计（r1，2026-10-16）判定为 GO-with-disclosures，翻转 正式 PASS 时须附带以下披露：
> 1. 全部证据基于合成 provider 叶节点与真实 ServiceHost/风险网关(LIVE_GROSS)/journal/SQLite 链，属包级与既有服务链验证，不构成真实账户验收；本条款证据不凭模拟余额宣称账户通过。
> 2. r150 已闭合 r146 遗留缺口（TTL 到期 PERMIT_NOT_ACTIVE→ADMISSION_DENIED；intent deadline 仅写入 canonical_payload 且不进入 provider 请求；UNKNOWN 跨重启保留敞口且不重派发），并含 journal `RESERVED→ADMISSION_DENIED` 转移修复 `b712a45e`。
> 3. 被依赖逻辑在现 HEAD 经核等价（DIM01 增补 `bb762465`；r166 同 run 等价 `ed09b3ca`；r167 resource=None 字节等价 `e2bedb3`；`adb6ec5` legacy REDUCE 原样保留），但三个宿主节点（r134/r146/r150）未在其后重跑；建议翻转轮于现 HEAD 重跑该三节点闭口后落账。
> 4. 失败历史均保留（r1–r4、r138 r2–r4、r146 v5–v7）；费率非零换算在宿主链未验（测试 fee=0）；transport/execution 子模块 gitlink 存在漂移，风险准入路径不导入对应模块，不受影响。

## 验收轮核对（第186轮）

- 锚点复核：提交 `b712a45e`（journal 转移修复，恰一行）、`2f9d6a99`（r146 宿主轮）、`b3b2c6d6`（r134 宿主轮）均存在；journal 转移 `"RESERVED": {"CLAIMED", "NO_DISPATCH", "UNKNOWN", "ADMISSION_DENIED"}` 现于 `bt_api_py/service_risk_order_journal.py:3204` 在线。
- 披露 3 的「现 HEAD 重跑 r134/r146/r150」建议：**本轮不执行**。依据用户长期规则「不要为交付重复已接受 QA」及失败历史不得原样重试的约束；后置提交对三条宿主链依赖路径的等价性已经审计逐项核验（`bb762465` 附加型、`ed09b3ca` 同 run 路径等价、`e2bedb3` resource=None 字节等价、`adb6ec5` legacy REDUCE 原样保留）。后续如出现对上述依赖路径的实质修改，须重开本条款。
- 不凭模拟余额宣称账户通过：全部裁决与证据级别明示 `…NOT_REAL_ACCOUNT`，本 PASS 不构成真实账户验收（见披露 1）。
- 失败历史保留：r1–r4（D:/temp/it10riskttl-root-20261016-r{1..4}）、r138 r2–r4、r146 v5–v7 均未被覆盖或删除。
- 审计方式：只读（文件读取、`git show/log/diff`、sha256sum）；未运行被测系统；未修改任何仓库文件。
