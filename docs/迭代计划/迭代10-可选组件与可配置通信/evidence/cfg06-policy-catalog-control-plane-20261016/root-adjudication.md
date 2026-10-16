# CFG06 第154轮裁定：policy_catalog 控制面与 analytics/ml extras 边界（2026-10-16）

## 范围与提交
- 父仓 5 文件：`bt_api_py/service_components.py`（内置 `("risk","policy_catalog")` 角色：端口 `service.risk_policy`、五操作、写操作三分、资源键 `catalog`、存储/服务类型校验）、`bt_api_py/service_host.py`（授权形状校验合并 operations/write_operations 错配判定，保留三端口例外）、`bt_api_py/service_registry.py`（纯格式）、`tests/unit/test_service_policy_catalog_control_plane.py`（5 项）、`tests/unit/test_service_components_group_runtime_bundle.py`（1 项）。
- risk 子仓 3 文件：`service_component.py`（`PolicyCatalogStore`/`PolicyCatalogService`/`ConfirmedPolicyStore`）、`__init__.py`（缺 extra 显式 ImportError）、`pyproject.toml`（numpy→[analytics]、scikit-learn→[ml]）。

## 固定来源验收
- spec `.codex-tmp/iteration10-cfg06-root-spec-r1.json`（sha256 `177941af1abed9e042fcf45929307fceabfc260f80dda979b8c11aa10a3dec8f`，170 pins，cwd D:/bt_api_py）。
- 运行 `D:/temp/it10cfg06-root-20261016-r1`：runner/pytest exit 0、6 passed/1 warning（5.05s）、junit 6/0/0/0、sources_unchanged、heads_unchanged（parent e2f51910/risk 756ffbd4/base b4aabd1e/exec dc8bd7a3/zmq a397fdb4）、stdout sha256 `ec6a3e5023e76d23432e7ee76faaf11d8db609f41da83657bdca9d645aa0399c`、stderr 0 字节。

## 独立静态复核
- SDK 5 文件：GO（`.codex-tmp/cfg06-review-status.md`，授权判定重构前后偏差与测试真实性逐项核对）。
- risk 3 文件：GO（`.codex-tmp/cfg06-risk-review-status.md`，不可变版本寻址、owner scope、pin 语义、基础路径无 numpy/sklearn 依赖核对）。

## 已关闭 / 剩余
- 关闭：application/service 出站 client 真实注册（同一 service 出站绑定 + application manager 经线缆）；policy_catalog 控制面（类型/版本拒绝、目录失联不废除已确认政策）；账户 admission 无 numpy/sklearn 可用、缺 extra 明确无能力、无任意模块加载（options fail-closed 与 internal-only 角色拒绝记录）。
- 剩余：`group_policy` 角色 internal-only（`NO_SUPPORTED_PUBLIC_PORTS`，测试如实记录），`portfolio_analytics` 无真实注册（完整 analytics worker 未实现）。CFG06 仍为部分/本机进程，不新增正式 PASS，累计 25/68。
- 证据等级：FIXED_SOURCE_ROOT_REAL_SERVICEHOST_ZMQ_CURVE_POLICY_CATALOG_CONTROL_PLANE_AND_GROUP_RUNTIME_BUNDLE_UNITS_ONLY（进程内真实 ZMQ+CURVE host 与子进程边界；无跨进程服务链、无真实账户）。
- 组合限制：QA 运行时的邻居 DIM01 未提交文件（`core/admission.py`、`core/exposure.py`、SDK `service_risk_order_entry.py`）不在本轮提交内；提交组合的独立复跑另行安排。
