# 决策：12k 行平行子系统处置（F-06、E-01）

**日期**：2026-08-17
**状态**：已采纳（选项 A + 最小集）

## 背景

`risk_management/` + `monitoring/` + `security_compliance/` 约 1.2 万行代码未接入主链路；其中 `security_compliance.core.audit_logger` 的审计日志不记录真实下单（合规假象），而 `forwarding/router.py` 自带一套 `RiskRuleSet` 风控，与 `risk_management` 构成两套互不通的风控。

## 决策

**选项 A（接入）+ 最小集：本轮接入 `AuditLogger`（资金审计刚需），其余子系统标注"参考实现/未接入生产路径"。**

理由：

1. 审计日志是资金合规的硬需求，且改动小、风险低（在下单/撤单路径发一条审计事件）。
2. `risk_management` 的 ML 风控与 `RiskRuleSet` 的收敛涉及产品决策（两套风控的职责边界），另立 backlog。
3. `monitoring` collector 的接入依赖部署形态，逐仓立项。

## 落地内容

- `forwarding/router.py` 的 `place_order` / `cancel_order` 成功与失败路径各发一条 `AuditLogger` 审计事件。
- 其余子系统在 README / 模块 docstring 标注"参考实现/未接入生产路径"，移除 production-ready 宣称。

## 后续 backlog

- RiskRuleSet 与 risk_management 两套风控的收敛（职责边界决策）。
- monitoring collector 接入主链路。
