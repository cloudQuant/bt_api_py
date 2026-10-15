# MIG02 第145轮正式裁定

**结论：正式 PASS，迭代10累计25/68。** 本裁定组合使用此前已接受的CFG05证据与本轮一个新的真实传输节点；没有重跑CFG05或其他已接受节点。

本轮新增节点以有效v2请求/回复确认CURVE v2服务端已就绪，随后发送由旧`forwarding.OrderCommand`序列化器生成的扁平JSON。旧格式被服务端消费但没有业务回复，业务handler计数没有增长。Root固定运行记录为1 passed、exit 0、0.81秒，stderr为空；保留一条PyZMQ/Tornado Windows Proactor `add_reader` RuntimeWarning。源码前后五项pin及Base/Transport包HEAD未变化。

沿用的CFG05证据包括第48轮离线配置/副作用检查、第127轮旧CLI/环境拒绝、第133轮BT_STORE_PROVIDER拒绝（两项direct/unset正例来自先前分轮证据）以及第135轮真实本地v4 CLI→runner→Cerebro/default broker正常入口。R48由作者报告11项离线检查通过及一条已知warning；未保留可回溯的Root原始stdout/JUnit与独立退出记录，本轮不重跑，也不把它写作Root运行。R127仅保存作者控制台结果6 passed/1 warning，缺原始日志/JUnit和独立退出记录；保留该限制，不将其描述成Root重跑。R133不是一次5/5执行。

**证据边界：** 新wire节点没有调用旧forwarding客户端，因而不单独证明客户端超时后的fallback选择；没有验证ServiceHost或跨机部署，也不推定MIG01、MIG04、账户或provider验收。该节点没有同次import-origin捕获，来源依据为明确的PYTHONPATH、固定的五项源码pin及旧schema按固定路径加载。MIG02 PASS是基于正式条款与上述组合证据的裁定，而不是把该单节点扩大解释为全部降级/部署场景。
