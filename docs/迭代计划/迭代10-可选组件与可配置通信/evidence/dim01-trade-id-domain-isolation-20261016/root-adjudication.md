# 第160轮根裁定：DIM01 trade_id 域隔离正式通过

- 裁定：GO；DIM01 由部分转为正式通过（正式累计 26/68 → 27/68）。依据 正式迭代计划.md:773 两栏逐片段映射：
  - 同策略多品种：strategy-a 在同一 provider 账户上跨 spot（BTC-USD/ETH-USD）与 perp（BTC-USD-PERP）多品种，逐产品绑定独立 wire account_ref 而共享 provider 账户身份。
  - 同账户多策略：strategy-a 与 strategy-b 同账户 spot，独立 scope/route/分配，提供商行不串。
  - 同 venue 双账户：fixture venue 下 account-a/account-b 独立身份空间。
  - 同名不同产品/环境合约：BTC-USD spot（v1）与 BTC-USD perp（v7）同名合约 bare trade/order id 不互认；别名与轮换 profile 不开口新身份空间；域内 scope mode 冲突 fail-closed。
  - 订单/成交号碰撞不串账：域内 trade-id/order-id 复用呈 UNKNOWN + latch（occupancy 冻结存续）恰 1 次 dispatch、不二次入账；域间 bare id 复用合法。
  - 证据栏：完整 SDK/owner 存储及路由（真实 ZMQ+CURVE ServiceHost/ServiceClientRuntime 注册 + owner SQLite 日志）；逐维正反例；去重结果（providers 恰 1 行、trade_ids 恰 `('trade-1',)`）。
- 固定来源：r2a `D:/temp/it10dim01-p3-20261016-r2a`（exit 0、1 passed、junit 1/0/0/0、7 pins 前后不变、stdout `2903f1ec…`）；r2b `D:/temp/it10dim01-p3-20261016-r2b`（exit 0、20 passed、junit 20/0/0/0、stdout `5c5046d1…`）；两运行 heads 前后不变（父 46697739、分支 dev）。
- 独立复核：GO（`.codex-tmp/dim01-p2-review-status.md`，80 行）：静态审查无生产弱化、digest 修复与生产调用点语义等同、测试真实断言；独立复现 6 restart 节点失败并判定为 registry run_id 释放/接管缺口（`service_registry.py` 1940-1948：`row.run_id` 不同即拒；2050-2057：注销仅清 active_registration_id；`service_runtime.py` 332 每次启动新 run_id），属恢复/注册生命周期归属域、非本用例 ID 隔离文本，排除有据；后续开发项 #32。
- 提交：risk `089b5efd71ebd0398c73e9aab370d674d88b41ba`（父 `131175a4`；仅 `core/admission.py` + `core/exposure.py`，`service_component.py` 其它工作线 WIP 原样保留）；父仓本提交 = `service_risk_order_entry.py`、`service_risk_order_journal.py`、`test_service_order_entry_components.py`、`test_service_risk_only_order_integration.py`、新增 `test_dim01_identity_isolation_service.py` + risk gitlink + 本证据目录与进度/汇总文档。
- 披露：`tests/unit/test_service_risk_order_journal_decimal_match.py`（未被 r2a/r2b pin）保留未提交；6 个 restart 节点为既有已提交节点、当前树失败状态如实保留并归档 fresh-basetemp 复现证据（disposition/）。
- 落盘说明：r2a/r2b 于父 `46697739`（第157轮）时点运行；本提交前先行落地第158（ARB01，test-only）与第159（AFI07，docs）两轮，被提交的 5 文件字节与 QA pins 逐项一致（r11 校验时重断言），risk 子仓提交仅含 QA pins 的两文件。
- 证据等级：AUTHOR_WORKTREE_QA_AND_STATIC_SOURCE_REVIEW_ONLY（本机真实 ZMQ+CURVE ServiceHost 线内；合成 provider，非真实账户/venue）。
