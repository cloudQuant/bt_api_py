# 项目概览

`bt_api_py` 是一个 Python API 门面：它负责加载已安装的交易所插件、维护 legacy Feed 兼容层，并提供可选的 Forwarding/ZMQ 运行时边界。

项目有三个相互独立的事实层：

1. 包是否可安装、wheel 中是否包含 doctor 所需资源；
2. 当前 transport contract 是否通过本地测试；
3. 某个插件是否在干净环境中安装、导入并完成 profile 验证。

只有第三层的同 SHA、新鲜证据才能提高某个插件的对外 support tier；它不能由注册数、历史测试数量或 source checkout 推导。

<!-- BEGIN GENERATED:EXCHANGE_SUPPORT_OVERVIEW -->
## Support status

The entries below are evidence tiers, not a count of production-ready exchanges.

| Scope | Tier | Evidence boundary | Current limitation |
| --- | --- | --- | --- |
| core-reference bundle | `experimental` | Bundle metadata for BINANCE___SPOT, OKX___SPOT and CTP___FUTURE; not a live-trading or installed-plugin certification. | Current isolated submodule diagnostic has no initialized plugin worktrees, so package install/import/test certification is pending. |
| other registered plugins | `unverified` | Registry or submodule presence only. | Do not infer REST, WebSocket, paper-trading, or production readiness from registration alone. |

Blocking CI supports Python `3.11`, `3.12`, `3.13`; Python `3.14` is canary-only.

See `docs/operations/support-status-policy.md` for the evidence and expiry rules.
<!-- END GENERATED:EXCHANGE_SUPPORT_OVERVIEW -->
