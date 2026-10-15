# REG02 Root 裁定（2026-10-15）

结论：PASS。范围为正式计划第10节REG02的本机多服务部分失败合同；不要求此条同时证明真实交易所、跨机器或负载。

运行来源：SDK `44846f4fd33f34f9b48350013ab697600300e77b`，Base `b4aabd1e17b9c7252fa7b4d266932087d99b5517`，Gateway `59b164ab7f89c6428ae6546103aad6058dab4c16`，Monitor `536a4f04747182749fabdac5e28624f80465bc53`，Transport `614e2d3837e93bd9a8d30066b883e47a03020d51`。运行命令与工作目录见command.txt，parent/child实际模块来源见observation.json。

| 正式要求 | 直接证据 |
|---|---|
| 必需服务拒绝，回收本次临时登记 | 第二个真实服务返回SCOPE_DENIED；失败runtime为failed、零bindings，cleanup/unknown/unregister uncertainty均零；第一服务新增的唯一临时登记不再active，预先存在的登记仍active |
| 必需服务缺失，回收部分成功登记 | 未监听端点返回REQUEST_NOT_SENT；同样验证仅本次登记被回收，原登记保留 |
| 非必需遥测缺失明确降级 | degraded_count=1及REQUEST_NOT_SENT；只有store.account挂载，runtime.telemetry调用明确无路由 |
| 无direct回退、无删除已有事实 | 失败runtime后续调用SESSION_NOT_REGISTERED；没有direct adapter/凭据/provider入口，原Gateway事实持续可读，SQLite业务行前后完全相同 |
| 实际链路和生命周期 | 两个不同PID/端点的spawn ServiceHost、真实SDK/CURVE；两个child均closed ack及exit0，四runtime closed，零强杀/cleanup error |

r5 pytest单节点1 passed、exit0，JUnit无失败/错误/跳过。stdout有一条既有pytest未知配置warning。先前r1–r4是已保留的fixture/runner失败，不改写或计为通过。

原始文件位于 `D:/temp/iteration10-reg02-partial-failure-20261015-r5/`。此处副本经过Git正常文本处理，不把外部原始文件SHA当成Git blob SHA：

- observation.json：`63da1a7b464c41acd1c58fbf151f49d8928f3becd1b107833e66f6a1d939e8cc`
- command.txt：`c37c6501fc4d9ff6d8301b41a6183fbea599ae859a7f3a684e16f92b583126a5`
- stdout.log（副本stdout.txt）：`84a86d34057c46e1197ef6ce3d05b39b51f7c7ef54b3221842e05b99d1483b27`
- junit.xml：`0d94459f02184767a138aa963d9763ff2ce6639aef18376bf78ed272f98c21be`
- 测试源码：42100 bytes / `b619bcc6e9decd24ea86841a59ad7c99a081bf61897afc0622cda8bc0a1a8afb`

reviewer消息误报测试大小42003；run-summary及Root实测均为42100，SHA与review一致。原消息/记录保留。observation中的formal_pass_claimed=false也是原运行时字段；本文件是随后由Root依据正式条目作出的独立裁定。
