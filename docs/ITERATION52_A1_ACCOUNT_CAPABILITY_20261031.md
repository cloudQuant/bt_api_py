# 迭代 52 A1：无 worker 账户能力对齐

日期：2026-10-31。仓库：`D:/bt_api_py` 既有 `dev`；基线 `509cc5ebbe010c598e3b4d6ddd62fd7eae48e793`。本记录与 SDK 源码和测试在同一提交，可用 Git 文件历史定位该轮提交。

不含 provider-session worker 的内置账户只读组件原本不提供 `account.instrument_metadata`，但 Host 按完整计划要求该项，因而 fail-closed 地拒绝启动。本轮让 Host 在真实构造的指定 `GatewayReadService`、精确资源形态和唯一账户端口证明无 worker 且实际能力只少该项时，允许这一项精确省略。worker 键存在但无效时仍拒绝，不回落为无 worker；其他操作、scope、instance、写操作及版本差异仍不接受。grant 投影只收窄命中端口上的元数据操作，不扩大授权。

独立静态审阅通过。指定的 Host source/fake 测试 10 passed、34 deselected，包含无 worker 和真实 worker 路径、额外能力差异及无效 worker 反例；未执行真实账户。首次测试收集因全局 pytest-asyncio 插件与 pytest 不兼容失败，未运行候选用例；禁用该插件后通过。Ruff E4/E7/E9/F 检查仅报告既有、未修改的 `service_host.py` E731；改动区域无新增相关诊断。`git diff --check` 通过。

该轮只证明本地源码及合成组件行为，不证明双 Host 纵切、真实 provider、真实账户或正式 case 接受；双 Host 测试需在该提交后另行运行并接受。
