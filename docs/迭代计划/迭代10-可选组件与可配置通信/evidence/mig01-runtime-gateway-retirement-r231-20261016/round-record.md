# 第231轮：MIG01 runtime_plugins 旧 gateway writer 入口退役

日期：2026-10-16  
仓库：`D:\bt_api_py` / `dev`  
证据目录：`evidence/mig01-runtime-gateway-retirement-r231-20261016/`  
正式MIG01：仍未PASS；本轮只验证旧 `runtime_plugins.gateway_dispatch` 入口 fail-closed，不代表两套旧目录或发行依赖闭合。

## 精确源码范围与交付状态

候选提交范围恰为三个SDK父仓文件：

- `bt_api_py/runtime_plugins/__init__.py` — 5053字节，SHA-256 `536b5b6f48b6ca3f53feb4aa4a773c0e71798bad530854a33f7c5aaa779e8e8f`。
- `bt_api_py/runtime_plugins/gateway_dispatch.py` — 2736字节，SHA-256 `c582824b34e076f3b63595438854f9c27add2c9b0c8e9cd058a3087cfea7e606`。
- `tests/test_runtime_gateway_dispatch_binding.py` — 5447字节，SHA-256 `c90f35549d73b21b1d7705bbe5a9d5618567b07c81faa10f7d3e68ec21a7bf2d`。

验收前SDK HEAD为`33c381007abcfc437044c67f4c752a788944846d`，Base HEAD为`cc6036c89c534a17d93e6b793f930c576b4aa960`；Root结果确认两者执行前后相同。本记录不包含Base或provider源码，不改Root进度文件，不提交也不改索引。Root可只按manifest中的三个路径进行review和精确提交。

## Root执行结果

Root于`2026-10-16T13:00:32.887209+00:00`执行R2 focused QA：5项、5 passed、0 failed、0 errors、0 skipped，pytest exit 0；stdout摘要为`5 passed in 2.10s`。18个执行来源pin前后未变，SDK/Base两个HEAD前后未变。Root执行spec SHA-256为`90f30d7489d4072644caf95240dd834539552b12cf50ae911763f17403e997c0`（6198字节）；focused runner SHA-256为`8b7f470be77270adeb9d72a83776b827c4653004b39835eeca0f43bf63cf24ed`（3204字节），R2输出副本SHA-256为`8b7f470be77270adeb9d72a83776b827c4653004b39835eeca0f43bf63cf24ed`。Root结果文件SHA-256为`a7b26d1449e9bcd96e03c39ea0af1e5440679dc80966b9cc679dca16ee70e3ea`（774字节）。

本节点覆盖包级旧factory/runtime/client构造的早期拒绝、direct submodule fail-closed导出、`runtime_plugins`普通导入的副作用边界，以及源树package-discovery检查。导入副作用检查在fresh subprocess中明确开启`BT_API_PY_LIGHT_IMPORT=1`，核实SDK/Base/module来源路径，并设15秒timeout；因此这是light-import源码边界证据。五个测试不启动ServiceHost，不调用`service.order_entry`，不创建ZMQ socket，不调用provider或账户。

第五节点检查的是`setup.py`中的`find_packages`源树结果。**没有构建或安装wheel；不据此声称安装包移除旧模块。**当前SDK安装发现规则仍包含该fail-closed `gateway_dispatch.py`。独立静态review对本轮冻结字节给出`GO_STATIC`；没有另行运行QA。

R2输出的11个原始文件按字节归档在`raw-evidence.zip`，SHA-256 `9026496d5b24b007591b32ec0ac1f197b985d4ca6891d993dd5fd5e79d99b56f`（8826字节）；manifest列出全部成员pin。本轮只归档R2的原始输出，不混入其他轮次的文件。

## MIG01正式剩余项与最短发行验收路径

正式计划§11.1要求在版本边界删除`bt_api_py/forwarding`、base内gateway、旧导出/启动入口与旧wire，不保留导入壳，并交付无网络副作用的配置转换器；§11.2要求两套旧运行实现、活动入口和依赖删除且新正常入口/安装回归通过；§11.3和MIG01/MIG04要求明确版本/extras/consumer矩阵、隔离安装与跨仓正常调用。R231只把`runtime_plugins.gateway_dispatch`的旧启动/写入组合改为fail-closed边界，**尚未完成上述删除或发行闭包**。

当前明确阻塞发行闭包的生产依赖包括：SDK `bt_api_py/forwarding`（14个模块）仍在`setup.py`包发现范围，SDK `_gateway_wire.py`与旧`gateway` façade仍在；Base `bt_api_base.gateway`（16个源码文件）仍作为被打包目录，`gateway/runtime.py`中的`_ForwardingBridge`会反向导入SDK旧`forwarding.schema/transport`；Binance、CTP、IB Web、MT5、OKX五个provider adapter仍以该Base gateway接口/模型为依赖。`runtime_plugins.__all__`和延迟属性仍保留旧Gateway名称以指向本轮fail-closed模块。活动测试中仍有`tests/test_forwarding_*`、`tests/forwarding/`、`tests/bt_api_contract/test_gateway_runtime_*`及旧wire合同节点依赖旧树；需要逐项将仍有意义的市场/服务语义迁到唯一普通写链`ServiceExecutionSession → service-v2 Host → service.order_entry`，历史证据fixture保持原字节但排除运行依赖。

建议把下一次发行闭包作为一个受影响消费者就绪后的完整P7R候选，而非重复加入口小轮：先完成五个provider adapter与新中立provider/服务端口的切换，并核实AFI P9B/P9C/P9D所涉及的旧Gateway读写/启停消费者已迁移或明确退役；随后一次性移除两旧目录、`runtime_plugins`旧符号、SDK旧Gateway façade/`_gateway_wire`、活动旧测试依赖和旧wheel资源。仅保留不可运行的迁移错误或测试壳也不能作为最终闭包。

候选发布最短验收应先构建Base、SDK、transport、Gateway和选定provider的非editable wheels并扫描成员/依赖metadata；再建两个干净venv：普通direct/backtest环境不装四个领域包与pyzmq，运行普通SDK/Backtrader入口；选定ZMQ环境仅装矩阵指定的Base/SDK/transport/Gateway/provider版本，运行已有ServiceExecutionSession→service-v2 Host→service.order_entry链及必要市场读回归，验证没有旧forwarding导入。另以离线测试证明旧配置转换无网络副作用、不可等价输入明确报错，并用`pip check`和发布矩阵记录consumer版本。正式计划中“父仓/Base当前强制pyzmq”的描述应在发行闭包前按实际metadata复核：当前SDK/Base主`dependencies`未列pyzmq，Base仅提供`transport-zmq` optional extra，transport包自身依赖pyzmq。

本轮没有跑wheel、隔离安装、ServiceHost或真实provider/账户调用；没有新增正式MIG01 PASS。历史fixtures不在本轮scope内，也没有删改。

归档SHA-256：`9026496d5b24b007591b32ec0ac1f197b985d4ca6891d993dd5fd5e79d99b56f`（8826字节，11项）。manifest记录全部raw成员、三个精确源码pin和Root运行来源；本记录不引用自身hash，避免递归。
