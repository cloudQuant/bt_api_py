# MIG01 R3 wire 交付轮 — 根裁定（第189轮，2026-10-16）

- 结论：**GO-with-disclosures**。接受为开发增量并提交（父仓 SDK 5 文件 + 本证据归档 + MIG03 翻转审计归档 + 两处文档注记）。MIG01 仍**部分**；正式 PASS 计数 30/68 不变（本轮无翻转）。本轮解除 CFG06 正式 r2 所依赖的 MIG01-R3 阻塞。
- 任务背景：mig01-completion-recon.md §B.5（加性 wire 轮，不删除）：为直连后端补一个自包含的 wire 客户端路径，使 T3/T4 合约测试（此前因 legacy-forwarding 退役而红）以恰 17 节点转绿。

## 交付文件（工作区 sha256，前→后）

| 文件 | before | after |
|---|---|---|
| `bt_api_py/_gateway_wire.py`（新增） | —（ABSENT） | `85c8ce4514308bf4fbc0cb3d636f98510243df77b9151b4e9ca5da7536b3616c` |
| `bt_api_py/_direct_backend.py` | `003a016bd883a56acf420988a1c8acddf5021d7a06909e1dcc752a81bc681073` | `8e28ff14ad1644c8bd272aa7740b54906cc7af2fac2cbb3284d3735944d4e26d` |
| `bt_api_py/bt_api.py` | `abceffc22168442aa6325b43391f14c0f5c0540202ddedeeaad6041fdd5e33f0` | `91363d1ced5b032a61152a8c9af7c7a26e73a9ab2f7990dd7a69d7ba202caab4` |
| `tests/bt_api_contract/test_gateway_runtime_unknown_execution.py`（T4 re-home） | `27f30fd7…`（见披露 1） | `50da4a0d5a9abe774af76653e67fb5ded15e3fbdf2cc7ddcea0acb12fef06d44` |
| `tests/bt_api_contract/test_execution_session.py`（T3 re-home） | `6496100809f750ee88cb0c9d9055ae0c8a0878688711c8eb3879644d5deb99af` | `e83f6b2dc973830531dcfa899f279598adddbdfad71d8e65d2f91e210b7a081b` |
| `bt_api_py/_normalization.py` | `90bde313…` | `90bde313…`（未动） |

## 交付内容
- `_gateway_wire.py`：客户端 DTO 投影 over `bt_api_gateway.command.v1` 信封（严格 `to_dict`/`from_dict`，字段集镜像退役 schema.py 形状）；`GatewayWireConfig`（新配置类型）、`GatewayWireClient`（`_send_command_sync`/`command_status`/`disconnect`）、`build_intent`（MT5 lots / CTP contracts / 其余 venue `CapabilityNotSupportedError`）、结果归一（`outcome_from_ack`/`outcome_from_dispatch_status`）恰输出 6 个 SDK 契约键 {status, execution_unknown, terminal_confirmed, definite_reject, order_id, client_order_id}；**零** `bt_api_gateway` / `bt_api_py.forwarding` import（复核证实）。
- `_direct_backend.py`：可选 `client=` 构造钩子；`client=None`⇒原路径语义不变；wire 分支仅在有 client 时生效（intent→发送→ack 归一；异常时探 `command_status` 映射路由状态并吞探错；任何异常文本不投影）；新增 `poll_event`/`close` 传输分支。
- `bt_api.py`：`gateway_wire_config` 构造 kwarg（dict 可强制转换）；`_build_backend` 仅从新配置类型装配客户端；退役抛错条件 = `ZMQ and gateway_wire_config is None`，故 ZMQ 单独与 ZMQ+ForwardingConfig 抛 `LegacyForwardingRetiredError` 与旧行为字节等同。
- T3/T4 re-home：各仅 2 hunk（import 行 + `forwarding_config=`→`gateway_wire_config=`），断言/harness 正文逐字节保留；往返链路为生产路径端到端（旧 `GatewayRuntime` 作参考服务端，SDK 侧为新 `_gateway_wire` 客户端）。

## 运行事实
- 生产方证据 `D:\temp\it10mig01-r3-wire-20261016-r1`：T4 `14passed`、T3 节点 `3passed`、combined `17passed/exit0`（恰达 17 节点目标）；回归 A 76p+1S、B 64p+1S、C 184p+2S 全 exit0（skip 为禁用插件 autoload 的既有工件）；pins before==after。首轮 T4 暴露 `_unknown_outcome` 缺 `execution_unknown=True` 的真实缺陷并修复（报告 §3）。
- 独立对抗复核 `D:\temp\it10mig01-r3-review-20261016-r1`（全文见 `review/mig01-r3-review.md`）：hash 全对、零 gateway import、6 键归一（含运行期抽查）、hunk 逐条核验；**独立复现** combined `17passed/exit0` + regA `76p+1S/exit0`。

## 披露（随提交）
1. T4 pre-edit pin `27f30fd7…` 系 recon 文档旧值，**不可从 HEAD 任何形态复现**（复核以 819 EOL 混排/12 种部分内容变体证伪；CRLF(HEAD)=`1240f28f…`）。重构建证明现文件 == CRLF(HEAD) + 恰两 hunk == post pin，行为全绿 → 非阻断，但显式记录 pin 卫生瑕疵。
2. 回归 B/C 无独立重跑（复核仅独立重跑 combined + regA；B/C 由生产方 junit 存证）。
3. 真实 `bt_api_gateway` 路由器（durable router）未接通 = R4/R5 范围；`outcome_from_dispatch_status` 非 None 行分支与 `gateway_wire_config` dict-coercion 路径无测试。
4. 本轮为首轮 wire 增量，MIG01 仍部分：旧转发目录/wheel 残留（166h/35f）与真实路由器接线未验。

## 版本管理
- 提交范围：上述 5 文件 + 本证据目录 + `evidence/mig03-flip-audit-20261016/audit.md` + 文档注记（ledger MIG01/MIG03 行 + 进度文档第189轮）。
- 时间线：14:0x 生产方报告 → 14:03 独立复核 GO-with-disclosures → root 提交。
- 后续：CFG06 正式 r2（解锁）；MIG01 R4/R5 路由器接线与残差收口。
