# CFG01 Root 验收裁定（2026-10-15）

结论：CFG01 正式 PASS，范围是正式计划第10节的普通配置、普通SDK/Backtrader行为及
新增可选组件资源边界。原探针依旧为 ASSERTION_FAILED / exit 1；本裁定依据其完整运行
事实及独立元数据核对，不改写失败结果，也不声明该探针进程成功。

## 对应要求与原始证据

| 要求 | 原始证据 |
|---|---|
| 无新块与空clients保持普通配置行为 | observation.phase_observations.configs中的两个实际字节输入，经load_client_config与真实CLI main(argv)解析：valid、client_count/component_count=0、activated=false、CLI exit0 |
| 正常SDK与回测 | ordinary_sdk为默认BtApi/DirectBackend、正常close；synthetic_backtest为正常Cerebro.run处理一根合成bar，无替换SDK/Backtrader业务实现 |
| 隔离安装 | 正常pip解析安装SDK/Base/Backtrader及依赖；pip check无依赖错误。固定venv不包含四领域、ZMQ、spdlog发行包，运行时distribution与loaded模块记录也为空 |
| 不启动新服务、不加载四包 | service_host/runtime/components均未加载；watched_import_attempts空；ServiceHost构造计数零；四领域包缺席且未加载 |
| 新增可选资源为零 | config/default_sdk_direct/synthetic_backtest三个阶段的线程、进程、socket及被观察Win32创建事件为空；不将此推广为全局OS句柄零增量 |

独立审查逐项核对脚本与原始JSON，认可业务条件有支持。Root重新阅读实际执行脚本、
原始结果、manifest、更正报告，核对README列示的八个原始payload大小/hash、实际wheel
hash/METADATA版本、venv配置及pip check。Root初次只读METADATA字符串检查因CRLF/LF
比较失败；使用标准email parser确认Version=0.15.5，没有执行候选代码或重跑业务probe。

## 原始失败及更正

原probe唯一失败断言从manifest顶层读取build_tool_version；正确位置是
build.build_tool_version，值为1.6.1。wheel、安装metadata及manifest wheel版本均为
0.15.5，direct_url及wheel hash一致。独立只读reconciliation更正该解释。

manifest另外把无新块配置写为68字节；实际是65字节，与固定提交Git blob及所记SHA一致。
这项描述错误保留为reconciliation唯一false项，不改manifest。原exit1来自原工具调用，
后续reconciliation内写入的数字不是独立启动回执。

## 来源与证据局限

固定SDK提交cdb1b81e39b661afb145ff3d9e94695c97429d7e，wheel及source archive的SHA见
README和build-manifest。原命令使用该新venv的Scripts/python.exe及绝对probe路径，cwd
为其空临时run-cwd，清除PYTHONPATH并设置PYTHONNOUSERSITE=1；原probe没有-I/-S，后续
纯元数据核对才使用-I/-S。probe没有sys.path注入。原调用代码在任务工具记录中，未留
独立落盘argv receipt；不把后来说明冒充原始receipt。

原JSON没有sys.executable/sys.prefix/BtApi.__file__字段。隔离来源依据实际启动命令、
venv配置、非仓库路径、运行时wheel/direct_url与依赖路径综合判断，不声称直接观测了
未记录字段。该证据足以支持本条目的正常隔离安装，不扩张为任意导入来源的审计保证。

导入阶段真实观察到urllib3 IPv6探测socket一次、pandas/NumPy环境探测Popen两次；
整体进程句柄诊断增量438，direct与Cerebro阶段分别增加12和2，Python活动线程增量0。
这些观察完整保留，不将普通依赖导入归为新可选服务，也不声称“全局资源零增长”。

本次仅关闭CFG01。CFG02–04、MIG04、实际远程代理、负载、跨机及provider验收继续独立
推进。新服务源码的后续修改仍须做受影响检查；本条PASS不替代整个发行组合冻结验收。
