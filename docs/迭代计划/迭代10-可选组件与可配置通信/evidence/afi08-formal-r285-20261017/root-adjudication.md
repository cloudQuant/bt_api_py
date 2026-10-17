# R285：AFI08 正式验收裁定

AFI08 正式 PASS，进度由58/68增至59/68。按正式计划第10节，停策略进程、止增、撤单、减仓和账户管理均有独立授权及服务端结果证据；告警确认不解除风险冻结。独立条款审计没有发现剩余缺口。

本轮补齐真实策略子进程停止：已安装 AFI 正常 HTTP 入口先对精确 owner/strategy/run 执行 STOP_NEW，再由 manager IPC 正常停止本轮策略。越权请求404且无效果；合法请求返回 fenced、process stopped、exit code 0、subscription released。真实子进程退出，两个本机合成 Host 正常关闭。未撤销开放订单，结果明确 left_open_unverified；仓位 unobserved，不宣称全部平仓。

Root 固定来源单节点一次通过：1 passed/4.75秒/exit0，84个输入与9个仓库HEAD未变，stderr空。保留两条既有警告（Quandl弃用、Windows Proactor/zmq selector线程）。作者5项启动/清理fake测试通过（3.41秒/exit0，一条既有Quandl警告），仅补充失败路径覆盖，不代替真实进程结果。AFI dev `47d8a3270cd948efcd9e222c275969bbd40005c9` 已提交三项测试/fixture及轮次记录；父提交01a554ce，未改生产路径，索引为空，其他WIP保留。

其余条款复用已接受证据：FREEZE与告警ACK独立、UNKNOWN/过期；持久STOP_NEW；CANCEL部分完成/UNKNOWN/幂等和越权；REDUCE经实际Risk owner单次派发及重放；账户管理的owner/account授权、启动就绪、停止和最终关闭。完整原始证据路径见manifest的prior_clauses。已有测试未为本次归档重跑。

运行环境是新Python3.12.10隔离venv；28个预检模块来自site-packages，SDK wheel源为6ff06e1（运行时checkout为其文档后继d459209），AFI源01a554ce、BT源6b25ee94，六领域包版本与archive身份列于spec。测试fixture按原目录层级复制，R249 helper与两个BT输入使用已提交固定字节。Base归档CRLF与wheel LF差异经规范化核对。pip check仍报告五项有意省略的功能依赖，未宣称完整应用依赖安装；本轮所需导入与实际链路通过。

证据范围为本机合成服务及真实本机策略进程，不新增真实交易所/账户、跨机或负载验收。原始命令、stdout/stderr、JUnit、源码前后pins、环境来源、选定补丁与提交回执均在raw-evidence.zip；不打包凭证、pytest临时数据库、venv或大型wheel/archive。
