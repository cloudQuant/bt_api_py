# AFI05/06 owner-confirm SDK allowlist round

日期：2026-10-17
仓库：D:\bt_api_py，分支 dev
基线：56ea40182be2d4eb8b21ed54ce830efa16e491af

本轮只把 Risk 已实现的 policy.budget.owner_confirm_member  operation 纳入 SDK 固定能力目录：service_runtime.py 的 service.risk_policy 客户端白名单一处，以及 service_components.py 的 risk/policy_catalog 公共 capability 与 write_operations_by_port 两处；同时提交精确 Host 路由测试。service_components.py 沿用当前基线中的 DIM04 close recovery projection，不覆盖工作区混合改动。

复用的 Root Host R2 结果位于 D:\temp\iteration10-afi-owner-confirm-host-focused-root-20261017-r2，pytest exit 0，1 passed，另有一个 Windows Proactor warning。结果 SHA-256 为 0E5D920F32102EC669437BF676D58B54A9B5B405F80225E975B6B7FCFDA6D756；spec SHA-256 为 CB7B0B384A11B11016F3E59790FDA841A9CAD83A97B36FF8D4F4FA22FDAAE140。证据级别为 LOCAL_SYNTHETIC_SERVICEHOST_OWNER_CONFIRM_DEPLOYABILITY_ONLY。该测试确认精确操作已注册并到达 Risk catalog handler；测试只注入 catalog store，因此预期结果是 CONFIRMATION_STORE_UNAVAILABLE。它不证明确认写入成功、AccountGate 已消费新版本或 consumer_effective=true。

R2 QA 运行于旧的 bt_api_py HEAD 2140c0d87f2e06bfd6a1094f56de3dc5fde52ed4，service_components.py 使用当时更广的工作区源文件。该历史源与当前干净基线加本轮三条 allowlist 的组合并非逐字节相同；本轮没有重跑 QA，包内 manifest 保留了这个来源差异和完整文件 pin。独立静态审查对精确三 hunk 返回 GO_STATIC。AFI05/06 正式验收及账户级消费者生效仍未完成。