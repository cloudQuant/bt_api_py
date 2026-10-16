# 第159轮根裁定：AFI07 剩余子条款收口与正式通过

- 裁定：GO；AFI07 由部分转为正式通过（正式累计 25/68 → 26/68）。依据 正式迭代计划.md:799 两栏逐片段映射：
  - 手工单、策略单及已批准目标经同一 owner：r9 节点3（真实 HTTP 手工 + HTTP 已批准目标 + 直连 SDK 策略单由同一 spawned owner 子进程服务，同一 journal 全部 ACKED，account_ref 共享）。
  - 两个 AFI worker 并发/重启重放同操作只派发一次、同键异内容拒绝：r129（手工单两个真实 HTTP 进程/磁盘 DB 并发同键只派发一次、异内容 409、第三进程重启重放）＋ r151 第二节点（双独立 app 进程同键、重启代）＋ r9 冲突断言。
  - 审批摘要/版本/过期/撤销校验：r149/r151（stale 409 `APPROVAL_POLICY_STALE`、撤销 409 `APPROVAL_REVOKED`、过期 409 `APPROVAL_EXPIRED`、伪造 422、他主体 403）。
  - 已接受组撤销有处置结果：r9 节点5（REJECTED_BEFORE_DISPATCH + budget RELEASED + budget_transition 载荷 + 持久复开与原始 SQLite 行核对；不安全释放拒绝且状态不变）。
  - 证据栏：真实 HTTP/SDK/订单 journal 与合成 provider 计数（r9 恰 3 次授权派发）；持久操作键→intent_id 映射、归属与最终事实；普通操作不修改组腿（组腿保留 id 拒绝 INVALID_REQUEST、快照 canonical-JSON 字节一致）。
- 固定来源：r9 输出 `D:/temp/afi_iter10_approved_target_http_root_20261016_r9`（exit 0、5 passed、junit 5/0/0/0、2147 pins 前后不变、stderr 空、stdout sha256 `27501f42…`）；独立复核者 r10fresh 复跑（仅 output 改名；5 passed in 100.19s、stdout `bb0e849c…`）；独立评审 GO（`.codex-tmp/afi07-remainder-review-status.md`：diff 纯追加 1627/0、工件完整性、2147/2147 pin 重哈希、runner 冻结等同）。
- AFI 提交：`794eab0c`（父 `34f59117`；仅测试文件，blob `6612a7bb…` 与 QA 读取字节一致）；README/scripts 6 项修改与 3 项新增未纳入、原样保留。父仓本轮为证据归档：无 SDK 源码改动、无子仓 gitlink 更新。
- 披露（不阻塞）：(1) 组撤销处置节点在 SqliteOrderGroupStore/coordinator 真实存储机具上直接验证（无 HTTP→SDK→host 路由），条款要求的“有处置结果”已由持久处置满足；(2) 双 worker 并发已在手工单与已批准目标两类真实复现，策略单类共用同一 owner/journal/操作键机具（节点3 同 owner 恰一次计数），未单独复现双 worker 策略单并发；(3) 两腿同账户组 reservation 按 account_ref 去重呈单相位（NOT_STARTED，测试内注明）。
- 证据等级 `SYNTHETIC_HTTP_SDK_EXECUTION_HOST_APPROVED_TARGET_WITH_SYNTHETIC_RISK_AND_PROVIDER`：真实 AFI lifespan/HTTP、派生 Execution ServiceHost 子进程、SQLite journal；合成 Risk 与 provider 叶；非真实账户/venue。
- 迁移说明：r9 工件自工作区暂存位置 `evidence/afi-approved-target-cross-process-20261016/qa/r9/` 并入本目录（字节不变）；该目录 r8 与第151轮记录原样保留。
- 剩余与 AFI07 无关：其他 AFI 用例（AFI01–AFI06、AFI08–AFI13）状态不变；AFI 侧本候选文件所属功能的其余工作线不变。
