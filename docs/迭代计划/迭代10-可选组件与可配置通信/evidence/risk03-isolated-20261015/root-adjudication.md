# RISK03 Root 裁定（2026-10-15）

结论：PASS，覆盖正式计划第10节RISK03；本机真实服务和传输，provider为合成，不是交易所账户验收。

| 正式要求 | 证据 |
|---|---|
| 未安装execution/gateway仍可工作 | 新Python3.11.5 venv、include-system-site-packages=false；普通wheel/resolver安装SDK0821e41f、Base b4aabd1、Risk72d955e、Transport614e2d3；安装清单、import spec及pip check确认Execution/Gateway/Monitor均未安装 |
| 接原有派发且最终Risk准入 | 普通SDK代理→CURVE→ServiceHost risk.account_owner→真实Risk gate→BtApi DirectBackend；合成provider承接最后的下单/读取 |
| 策略省略risk客户端仍不能绕过 | 只挂载broker.orders/broker.order_query；两个允许订单之后超额第三笔ADMISSION_DENIED，provider写入列表不增加 |
| 真实服务订单观察 | submit/重复submit/query及策略重启后同ID查询；ACKED_TRACKED、provider ID、单次dispatch、order/fill读取和Risk占用一致 |
| 拒绝零provider写入、无direct回退 | 超额/元数据版本错误/foreign account拒绝后provider写入列表与此前精确相同；未挂载任何fallback，关闭无强杀 |

Root命令在formal-command.json；pytest stdout和JUnit记录单节点1 passed、4.49秒、exit0、零warnings。测试与helper复制自已提交SDK，只有空tests/unit包标记作为artifact适配。生产代码通过wheel安装，不使用editable；测试导入根不含SDK包。

实际进程原始模块观察合并于process-origins.json，Root逐项核对于root-origin-verification.json。四个加载SDK的进程使用venv解释器/prefix，全部已记录具体文件来自venv site-packages，未加载Execution/Gateway/Monitor。`bt_api_risk.containers`没有__file__，其namespace路径没有被本次hook记录；另一个Anaconda记录未加载任何SDK模块。此证据不宣称所有辅助解释器均隔离，也不宣称namespace对象身份被额外验证。

Git blob为LF，导出/安装文件为CRLF；Root验证目标测试、helper、Risk entry/journal仅此换行差异。原始Git/test/export各自SHA保留，不将它们混称为原始字节相同。原环境报告READY_FOR_ROOT_FORMAL_NODE和pytest_executed=false是创建时记录，后续实际执行由本裁定及原始pytest输出证明。

完整原始构建/安装/运行目录：`D:/temp/iteration10-risk03-isolated-20261015-r1`。本目录是公开验收摘要和原始运行观察副本；不含凭据、私有账户或wheel二进制。第84轮已有恢复用例不因本次交付重复运行。
