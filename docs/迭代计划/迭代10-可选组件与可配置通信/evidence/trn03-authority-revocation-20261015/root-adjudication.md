# TRN03 授权与受限行情撤权验收裁定

正式结果：PASS，组合既有已接受的真实 CURVE/ACL 用例与本轮新增撤权节点。没有重新执行已通过节点；不由本条推导 DIST01 双机或 VENUE 真实账户通过。

| 正式条款 | 对应证据 |
|---|---|
| 未授权 client、伪造身份/作用域/动作 | 第02轮 SDK 14 passed 中 `tests/unit/test_service_runtime.py::test_configured_proxy_real_curve_tcp_across_spawned_process`，来源 e7fdefa6a：合法 CURVE 先成功读取，同证书伪造 actor DENIED、错账户 SCOPE_DENIED、未授予动作 CAPABILITY_UNAVAILABLE，真实子进程正常 exit0 |
| 远程模式认证、ROUTER identity 不作为授权 | 第02轮 transport 30 passed；`test_command_transport.py::test_remote_curve_zap_identity_and_scope_acl_gate_handler_on_local_tcp`（43751c7）使用 allow_remote=True、wildcard TCP/CURVE/ZAP；未知 key、错账户/策略/动作不进入 handler。`test_service_v2.py::test_curve_authenticated_principal_overrides_forged_routing_and_header_claims`（3834c511）实际接收伪造 ROUTER/header 声明，同时单独取 trusted ZAP principal；拒绝行为由前一行 Registry RPC 证据支持 |
| 跨账户/策略读取 | 第03轮账户集成 66765c3（进度第313–317行附近，Root1passed）及账户视图 fbd12d3，真实 spawn/CURVE，错账户拒绝、伪造 strategy_ref 为 SCOPE_DENIED；host集成7263a33补充错账户与未授予动作 |
| 行情越权订阅 | GW01第38轮，真实十策略/共享上游：market.subscribe 越权明确 SCOPE_DENIED，provider/ref计数不变 |
| 行情撤权 | 本轮精确冻结 R3，A+B 初始接收，旧 host 正常关闭后同端点启动 B-only audience；保留的旧 A SUB 重连触发 libzmq HANDSHAKE_FAILED_AUTH，B 收到不同 epoch 的唯一 marker，A 未收到；A 新登记 REQUEST_NOT_SENT、无 binding |
| 控制请求重放 | MON02第60轮1122c0c真实 ServiceHost/CURVE控制节点：同 command_id 的延迟 FREEZE 在 RESUME 后只返回原终态，freeze/resume效果不重复。业务 command_id 与 transport request ID 按正式合同分别处理 |

本轮 R3 的精确测试26372字节/SHA-256 9d55ce69036a3451980f3dfbe34266b133e0c58c164877f80b580d381e50498c，归档 executed-test.py 与 canonical 文件相同。Python3.11.5，1 passed/5.11秒/exit0，JUnit零失败/错误/跳过。一条已知 asyncio_default_fixture_loop_scope 配置 warning 保留；stderr包含Python -v导入诊断。两次正常 host 关闭断言均 exitcode=0/reaped=true，provider启停各1/1，无强制或清理失败。固定SDK c6+第98轮普通两文件修复，Base b4aabd、Gateway59b164、Transport29ab；11项选定源码前后pins相同。显式PYTHONPATH与源码pin绑定来源，未单独记录运行时__file__断言；post-run PathFinder探针失败不作来源证据。

旧节点使用已提交进度中的聚合成功记录及精确源码提交，未声称存在未保存的逐节点Root result/hash。本轮原JUnit/result/log/launch/preflight完整归档。result.json中的JUnit容器读取修正是从原XML重读，无测试重跑。R1握手等待器提前返回与R2错误期待RPC层拒绝的失败保留，不抹除也不计PASS。

范围：所有网络连接均为本机loopback；远程模式配置不是物理异机链路。本轮撤权证明的是受控停止旧host并更换发布端点audience后的重连拒绝，不是无中断热撤权。正式计划不承诺无中断热更新。provider为合成行情，不使用真实账户。独立 afi_lifespan_review 对R3源码及全条款证据链只读复核GO；Root读取原产物、校验pin和JUnit，没有重跑QA。
