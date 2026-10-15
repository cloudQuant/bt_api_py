# REG03 本机进程验收裁定

正式结果：PASS。对应正式计划 REG03 的重复注册幂等、双活写会话拒绝、重启重新登记，以及注销/租约过期不清理订单和风险占用。不是真实交易所或账户验收。

Root 仅执行一次归档的 `executed-test.py`，原名 test_reg03_decimal_r3.py，SHA-256 392f509ae2b059413d29ffeacb6429bf90ffdd0d2f6aaf8b1c09e91213bf1096。正常 PowerShell 调用运算符传递 pytest 参数，显式可导入的测试根目录解决 Windows spawn 导入；命令/环境见 invocation.json。Python 3.11.5，1 passed in 5.24s，exit 0，JUnit 一项且无失败/错误/跳过，stderr 为空，没有 warning 报告。

真实 spawned ServiceHost、CURVE RPC、SQLite Registry/Execution/Risk 均运行；只使用合成超时 provider 末端与可注入 Registry 时钟。相同会话/申请返回相同登记；竞争写会话拒绝 DUPLICATE_ACTIVE_RUN。首次派发产生 UNKNOWN、100000 USD 风险占用、dispatch claim 和冻结。随后注销、租约过期、分别 recover、正常关闭旧 host、原数据库启动新 host、STALE_EPOCH 拒绝和显式 recover，均查询同一 intent，业务投影保持相等，派发总数 1、撤单 0。最终在 host 外重新打开数据库核对原记录。

两 host 均正常 close ACK、join 完成、退出码 0，Pipe/process close 完成。forced_cleanup、failures、runtime_cleanup_failures 均为空。observation 的 child_pipe_eof=false 表示未观察 EOF，不作 EOF 成功声明。

固定 SDK 基线 c6c832f4e12bf999dd554ba518d9421ecf6b1eb8，加两项生产修复：普通订单角色准确传播 write_operations，使 Registry 真实执行双活拒绝；未提供可选 account-books reader 时允许仅缺该查询能力，所有其他操作和写能力仍精确匹配。固定依赖由 source-verification.json 记录；本次不加载并行开发的组角色接线。生产原始与 LF 规范化 pins 见 manifest.json。未把本次历史固定版本证据扩张为所有后续依赖版本的整套回归。

此前失败保留在 D:/temp/reg03-durable-business-c6-writeops-20261015-r1、r2、r3：r1 已到 UNKNOWN/Risk 但金额字符串断言不当；r2 的测试目录布局导致 spawn 导入失败；r3 首次 Start-Process 预检参数错误及 Root 一次错误函数名预检均在用例执行前失败。没有重试未改变的业务失败节点；本次 Decimal 比较保留精确其余字段和后续完整投影相等约束。

独立 backtrader_entry_review 只读复核正式条款、精确执行副本、固定源码、JUnit/原日志/持久事实及退出证据，返回 GO。交付的 canonical tests/unit 文件另做 lint/format 清理及仅在环境变量缺失时使用仓库根路径；显式空值仍拒绝，固定环境分支业务语义不变。canonical SHA-256 c20eb948dd7000c2350f3a64f0ef5735d1ece8896463576494b7ffab888b05f0 获独立静态 GO 和 Ruff 通过，不声称该新字节重新执行过。原执行副本逐字保留，不重复已通过 QA。
