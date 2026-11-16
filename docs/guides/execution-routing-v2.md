# Execution 路由配置 v2 与旧请求保留

日期：2026-11-16。证据范围：本地源码与合成测试。此配置不建立账户授权、独立写者栅栏、当前程序 build 测量或交易所品种规则；完整 Gateway 执行链仍未验收。

## 新请求

`execution_routing.schema_version` 使用 `2`。在既有配置字段之外，顶层明确提供 `algorithm_build_id`；每条规则明确提供 `schedule_binding.quantity_step`、`price_step` 和 `retry_limit`。build 与步长必须由部署方对照实际 Execution build 和品种规则配置，不能从默认值或请求端自报内容推断。

以下仅展示新增字段的合成示意，不是可直接用于账户的完整配置：

```yaml
schema_version: 2
algorithm_build_id: synthetic-test-build-1
# 其余既有 account_ref、strategy_ref、instruments、授权选择等字段仍须配置。
rules:
  - id: synthetic-rule-1
    # 其余 match、scheme、绝对 deadline、time_in_force、trading_day 仍须配置。
    schedule_binding:
      quantity_step: "1"
      price_step: "1"
      retry_limit: 0
    algorithm:
      class: PASS
      version: 1
      params: {}
```

v2 中省略 `algorithm` 等同于显式 PASS / 1 / `{}`，仅在订单已匹配 Execution 规则后适用。SLICE、TWAP 仍需明确参数和整数版本；服务器独立核验允许的算法、版本、参数、build、授权和账户状态。非限价母单不回退 direct。

SDK 从已验证的订单限价和原始绝对期限构造冻结计划。`spec.plan_digest` 使用整个计划的规范 ASCII JSON 摘要；`request_sha256` 使用包含 `request_id` 的完整四字段请求的规范 wire 字节摘要，两者含义不同。请求恰好为 `request_id`、`order_type`、`spec`、`algorithm_plan`，不通过自报 `strategy`/`delegation` 字段建立权限。部署配置中保留的选择信息不等同于服务端独立委托授权。

## 旧配置与重启

schema v1 仍可解析、读取历史记录；匹配到需要创建新母单的请求会返回 `EXECUTION_FROZEN_PLAN_REQUIRED`，不发送 RPC，不回退 direct。未匹配规则的既有 direct 选路语义不变。

既有五字段 PREPARED 请求保留原 ID、原 JSON 字节与原摘要规则，不自动改写为四字段请求、不重发。新四字段请求的 PREPARED 也必须先完成本地事务再单次发送。当前配置改变不会使已存请求改用新规则重建。

只有精确 `ExecutionParentClient` 从当前认证绑定返回、且与原请求 ID、完整摘要和母单 ID 一致的本地收单观察，才投影为 `EXECUTION_LOCAL_RECEIPT`。它不代表 Gateway 接受、柜台接受、成交或可撤单。普通注入端口、裸字典、自报观察对象和未知响应保持 UNKNOWN。查询中的 NOT_FOUND / NOT_SENT 不证明先前请求没有发送，不触发自动重发；撤单仍需后续正式母单协议。

## 本轮验证与边界

主线程复跑 SDK 路由、客户端、匹配及日志六文件：199 passed；Execution 冻结计划、算法配置、政策 Intake、母单提交及恢复五文件：142 passed。合成 Atomic Intake 测试证明四字段计划及摘要可以通过本地合同检查；它使用测试写者前提，不能移除正式服务的独立写者、Risk 或 Gateway 门禁。未做安装包或真实账户写入验证。
