# CFG04 五类代理隔离安装证据（第101轮）

接受级别：PARTIAL_CFG04_INSTALLED_FIVE_PROXY_INTEGRATION。正式用例仍为21/68。
这是固定发行组合的本机真实进程/传输证据，不是当前HEAD新版服务或真实账户验收。

R5单节点使用Python3.11.5、pytest9.1.1，1 passed/2.60秒/exit0，无pytest warning。
服务端原始stderr有一条PyZMQ Proactor add_reader RuntimeWarning，未删除或忽略。
client与server为独立venv，均无system-site-packages，均非editable安装，pip check通过。
客户端未安装四个领域包；服务端在另一进程装配真实ServiceHost/CURVE/SQLite。

实际执行store.account、broker.orders、broker.order_query、risk.read_model、runtime.telemetry：
账户读取、订单提交/查询/撤单、Risk snapshot、遥测append均经正常SDK代理。
provider、dispatch和admission为本地合成实现。正常runtime/host/reader关闭、子进程exit0；
任何强制回收都会使节点失败。客户端components/embedded/服务端槽拒绝亦有断言。

broker.order_groups仅验证配置解析；execution.account_books.query未调用；store.market证据
来自已接受第95轮CFG02。本节点不是“所有代理”完整矩阵，故CFG04保持部分。

源码组合为SDK28854c73、Baseb4aabd1e、Gateway59b164ab、Transport29ab21e、Execution84d97c9、
Riska099e367、Monitor536a4f04。完整提交及wheel/安装来源见source-install-verification.json。
Root重新只读核对七wheel、两原安装报告和19个Git blob→wheel→installed源码；
十九项只存在明确CRLF/LF差异。该核对没有执行应用或重跑测试。SDK/Execution安装提交早于
本轮父70938108，不能把这次通过移用于当前新版Host/components/config或组组件。

精确执行测试83431字节/d2901493…c577bf归档test-executed.py；canonical当前与其原始字节一致。
原CFG02测试/服务函数AST与HEAD一致，独立iteration10_parallel_assessment只读复核窄GO。
成功时pytest捕获了测试打印的JSON报告，磁盘日志未保存该报告；证据是精确断言、原始
通过日志/JUnit、退出码与stderr，不声称独立持久化业务报告存在。命令原样归档；
server interpreter通过BT_API_ITERATION10_CFG04_SERVER_PYTHON提供，实际启动与源位置
受测试断言；原命令文件并未记录完整环境快照。

早期相对路径、配置、guard目录、tuple/list及stale-copy失败保留在原artifact目录，
没有改写为成功或重复执行已通过R5。此轮不改生产文件、不更新子包、不新建worktree。
提交父70938108460d15d79f8bb194687cce073990dae5；实际提交SHA由本文件Git历史查询。
