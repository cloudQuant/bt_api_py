# CFG02 Root 验收裁定（2026-10-15）

结论：正式 CFG02 PASS。本轮新增一个正式用例，总数由18/68变为19/68。
本目录保存一次已完成运行的原始命令、stdout、JUnit、exit、服务stderr、两侧pip check
及后续只读来源核对；未重跑已通过节点。完整安装/构建产物保留于
`D:/temp/iteration10-cfg02-real-20261015-r1`，本目录不复制venv或大型安装报告。

## 合同与直接证据

| CFG02要求 | 实际证据 |
|---|---|
| 显式组件清单启动，不需要enabled | 正常parse_service_config和ServiceHost启动，摘要精确一个gateway/market_hub、一个service.market_source端口及五个实际操作；无其它组件 |
| 未列出的包未安装不影响服务 | 两个独立Python3.11.5 venv，system site packages关闭，正常resolver非editable wheel安装且pip check通过；客户端Execution/Gateway/Risk/Monitor四包的distribution、find_spec、已加载模块均不存在；服务器仅Gateway存在，其余三包同样不存在 |
| 策略注册后实际代理访问 | 普通ServiceClientRuntime经CURVE登记store.market，普通ServiceMarketClient订阅，实际收到provider发布的精确key/data和sequence=4；没有替换runtime或transport |
| 正常结束 | provider starts/stops=1/1、active registrations归零、runtime cleanup/unregister uncertainty均零、host closed、子进程exit0、控制和输出reader停止、forced_cleanup=[] |

唯一节点为`test_installed_gateway_component_serves_a_registered_strategy_without_other_domains`。
pytest记录1 passed/1.86秒，exit0；JUnit为tests=1、failures/errors/skipped=0。
测试文件SHA-256 `017797364f74ebd61e3210052864f5d994d7b5f35e3f85834a1b7bc2b0b01455`，
独立trn02_acceptance对该精确pin静态GO；Root读取了原日志、JUnit、stderr和正常关闭断言。

## 安装来源

SDK实际构建源为`28854c73a235645adcc0f6a6997053d6592504f1`，不是c6c832f；
来源由build log、Git archive PAX comment及归档成员与该提交Git blob逐项比较确定。
Base为`b4aabd1e17b9c7252fa7b4d266932087d99b5517`，Gateway为
`59b164ab7f89c6428ae6546103aad6058dab4c16`，Transport为
`29ab21e714292a363538491e4ff8461d336d1dfc`。
source-install-verification.json记录四个归档和wheel pins，以及八个运行时观测模块的
Git blob→archive→build source→wheel→installed链。后四层原始字节一致；相对Git blob
的比较明确记录CRLF/LF差异，不声称全部raw一致。Root另外复核四archive/wheel和八个
已安装文件hash；test-only副本与仓库test原始字节相同。

## 范围限制

这是本机真实进程、正常已安装代理/服务及合成行情provider证据。CFG02合同已满足，
但只覆盖store.market，CFG04的所有代理矩阵仍待完成；不代表真实账户、双机或负载。
服务stderr保留一条Windows ZMQ Proactor/selector线程warning，没有掩盖为零warning。
正常路径关闭已断言；异常路径中部分close错误被抑制，故不据此声明全部失败清理已验证。

原始pytest.stdout.txt SHA-256 `1f2587b332c8472ca946aed66faa686cb667aedb88a6f21b0d0e9c2d851ac435`；
JUnit `fabf2cae7dd1b937b041fd4bd39dce0f9d256fd914f66f1ad61756f5b2aaf97c`；
source-install-verification.json `dfb88e090c8e4530515498c27729d39760f6e335f82f4544cb00f53b2333f320`。
目录.gitattributes保留归档字节。第95轮父仓精确交付测试、本证据目录及两份进度文档，
实际提交SHA由Git历史及后续轮次记录确定。
