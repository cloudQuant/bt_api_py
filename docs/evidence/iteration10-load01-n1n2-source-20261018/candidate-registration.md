# LOAD01 非均匀场景登记草稿（待 P0 冻结）

状态：候选场景定义，供 Root 裁定后登记。本文不修改正式迭代计划或新增性能阈值；代码接线仍属候选状态，不表示登记获批、已运行或通过。所有比例、分布和时点是可复现的场景输入，不是性能阈值。

## 固定基准 U0

正式计划 §9 P0（第 705 行）要求登记固定机器基线、负载阈值和测量方法；§10 LOAD01（第 807–813 行）固定初始工程负载。U0 保持原值：100 个独立 SDK 策略进程、10 个合成账户 owner、1 个共享 market hub、1 个 Monitor、20 个公共行情主题、总输入 200 条/秒、每条约 256 字节、持续 1,800 秒；每账户 2 个合成订单生命周期/秒，即全局 20 个/秒。所有 100 个策略订阅 20 个主题时，注册关系为 2,000 个策略—主题对，均匀输入对应约 20,000 条策略行情投递/秒。报告必须分别列上游输入率和策略投递率。

固定来源 runner 是 `tests/unit/test_load01_hundred_strategy_30min.py::test_load01_hundred_strategy_sustained`。当前 market key 是同一 synthetic/loopback/paper/spot/trades 域下 20 个不同 symbol；策略按 `index // 10` 固定到一个账户；每个策略订阅全部 20 个主题；market producer 按主题轮询发消息。U0 不因下面的附加场景而重写。

下列已存在的强制安全边界必须逐次保留：启动前可用内存至少 10,603,200,512 字节（112 个子进程 × 72 MiB 预测加 2 GiB）；运行期间至少保留 2 GiB；系统 CPU 连续 12 个采样达到 95% 即停止；策略行情队列不得超过 256 个事件或 512 KiB，遥测队列不得静默丢弃；当前 market HWM 为 128，回放上限为 2,048 个事件/1 MiB；RPC 超时 5 秒、租约 30 秒；保留正常关闭、无未知注销、无未恢复序号缺口及无跨账户泄漏检查。若任何硬保护触发，记录受保护的失败并停止该次运行，不降低门槛或缩短后改称 LOAD01 通过。

当前 runner 报告字段明确为 `performance_threshold_registration: NOT_REGISTERED_IN_P0`。吞吐、端到端 p50/p95/p99、超时、缺口/补传、CPU、内存、队列/磁盘、连接数和关闭后资源的逐场景验收阈值仍待固定机器基线后登记。下文的事件数、分布和 DIM07 已有观测值不能替代这些阈值。

## 候选非均匀配置

所有 profile 沿用 U0 的 100/10/1/1 拓扑、20 主题和 200 条/秒总行情输入。约 256 字节是 U0 的消息大小参考目标；N1/N2 的 trade 消息保留既有填充，bars 消息直接承载 synthetic provider 给出的 OHLCV、周期和更新时间事实，不为凑到 256 字节填充。Runner 按 topic 报告稳定 JSON 序列化后的实测每消息字节数；N1/N2 可与 U0 参考值不同，不据此推定性能通过。性能级运行建议仍采用完整 1,800 秒；若 P0 要求不同持续时间，必须在运行前逐场景登记。120 秒 pilot、局部 Host 测试或独立组件波不构成该 profile 的性能验收。每个 profile 单独保存配置、实际订阅数、输入/投递数、逻辑订单组比例、topic 实测消息字节和完整指标。

| ID | 固定输入与分布 | 当前 LOAD01 接线情况 |
|---|---|---|
| N1 部分重叠、同名跨市场、多周期 | 保持 20 个主题。K00/K01 使用相同 `native_symbol=BTC-USDT`，但分别设 `venue=SIM-A/SIM-B`、不同 `instrument_id`；K02/K03/K04 使用同一 `instrument_id=SYNTH-X`、同一 venue、`channel=bars`，`timeframe=1m/5m/15m`；K05–K19 为不同 synthetic 合约。S000–S099 全部订阅 K00–K04（5 个公共键）；再分为 S000–033、S034–066、S067–099 三组，分别订阅 K05–K09、K10–K14、K15–K19。每个策略订阅 10 个键，共 1,000 个绑定。每主题 10 条/秒时，预期输入 200/秒、投递 10,000/秒。每个 topic 的 JSON 消息字节数按实际 payload 单独测量和报告，256 字节只作为 U0 参考目标。 | 候选接线在 `tests/unit/_load01_profiles.py`、`tests/unit/test_load01_profiles.py` 和 LOAD01 runner 中实现：同名跨 venue / 多周期 key、部分重叠订阅及 direct synthetic bar facts；bars 不作聚合或修正，且按 topic 报告实测字节。仅静态接线状态，未运行 QA/Host/LOAD01，也未获 P0 登记或性能阈值；正式验收资格保持关闭。 |
| N2 热点合约与热点账户 | 继承 N1 的订阅矩阵。每个 1 秒桶恰好 200 条：K00/K01 各 70 条；K02–K07 各 4 条；K08–K19 各 3 条。策略账户映射为 A00/A01 各 40 个策略，A02–A05 各 3 个，A06–A09 各 2 个（合计 100）。全局订单生命周期仍为 20/秒，但分布为 A00/A01 各 8/秒、A02–A09 各 0.5/秒，即 80% 订单落在 20% 账户上。按 N1 绑定数计算，预期行情投递为 16,802/秒。每个 topic 的 JSON 消息字节数按实际 payload 单独测量和报告，256 字节只作为 U0 参考目标。 | 候选接线在 `tests/unit/_load01_profiles.py`、`tests/unit/test_load01_profiles.py` 和 LOAD01 runner 中实现：复用 N1 key / 部分订阅矩阵，并接入热点 topic 调度、热点账户映射和逐账户订单排程。仅静态接线状态，未运行 QA/Host/LOAD01，也未获 P0 登记或性能阈值；正式验收资格保持关闭。 |
| N3 一策略多账户 | S000–S009 各获准访问 A00 和 A01。其余策略分 10 组各 9 个：A00→S010–018、A01→S019–027、A02→S028–036、A03→S037–045、A04→S046–054、A05→S055–063、A06→S064–072、A07→S073–081、A08→S082–090、A09→S091–099。共 110 个策略—账户授权绑定；A00/A01 各 19 个策略，其余账户各 9 个。每个 10 秒桶中，每个 S000–S009 各向 A00、A01 发 1 个生命周期；A00/A01 的本账户策略组各补 10 个，A02–A09 各由本账户组发 20 个。这样每账户仍为 2/秒，全局仍为 20/秒。行情保持 U0 全订阅。 | 现有 `_strategy_config` 每个策略仅创建一个 account binding，进程账户通过 `index // 10` 选定；没有同一客户端多账户的 workload 配置或计数归属。需接通已授权的多账户 mount 和逐账户统计。 |
| N4 同账户多合约 | 维持 10 个策略/账户。令 A[a] 的四个市场键为 K[(2a+j) mod 20]（j=0..3），相邻账户共享两个键。每账户指定 S[10a] 订阅并交易全部 4 个键；该账户另外 9 个策略各订阅一个键，按本地序号 mod 4 分配。共 130 个策略—主题绑定。每个账户每 10 秒发 20 个逻辑生命周期，按四键轮转，每键 5 个；全局仍为 20/秒。 | market DTO 能表达这些键，但 LOAD01 订单构造和账户映射没有按 instrument 路由。需要把订单查询/提交/成交事实绑定到实际 instrument ID，并验证同一 owner 的多合约归属。 |
| N5 指定同账户三腿组 | 1 个固定 portfolio 和协调服务；10 个账户 owner。每个 10 秒、每账户 20 个逻辑订单入口中，19 个是单腿、1 个是同账户三腿组（5% group-intent 比例）；全局每 10 秒 10 个组、190 个单腿意图。每组三腿分别为 put/future/call，元数据、方向、比例、乘数、步长和执行政策必须直接复用当前已接受 DIM09 fixture 的精确输入；按组意图和逐腿派发分别统计。若每组产生 3 次腿派发，则不可把 20 个逻辑入口/秒写成 20 个订单派发/秒。 | LOAD01 runner 没有 group/portfolio/leg 路由。DIM09 的本机 synthetic-provider/service 证据是独立的组业务覆盖，不是 100 策略负载。只接入已验证模板，不增加任意多腿或未登记执行能力。 |
| N6 组合压力：热点、慢消费者、重连、查询限流 | 在 N1/N2 分布上运行；沿用主 runner 已编码的一个慢行情消费者：S000 在 t=30–45 秒暂停轮询 15 秒。Full 模式重连继续使用主 runner 当前调度：窗口 k=1..5 在 t=300k 秒启动，每账户一个本地策略槽 `(k−1) mod 10` 重连，账户间按 1 秒错开。另在 t=60 秒加入一轮共享 CTP query quota 波：100 个策略进程各发 2 个 `ctp_query` 准入请求（共 200），复用 DIM07 固定来源波的 quota-domain、队列和 30 秒 deadline 参数；报告实际授权、排队、拒绝和调用耗时。 | 慢轮询和重连已在 sustained runner 源码中，但 R13 pilot 在 t≈20 秒因可用内存低于 2 GiB 退出，未到 t=30 慢轮询或 t=300 首次 full-mode 重连。主 runner 只请求 `broker.order_query` grant，全文没有调用 `orders.query`；也没有 `ctp_query` 波。DIM07 的 100 进程/200 请求热点波、四进程并发重连和 R224 slow-sink 是独立证据，需最小接线后才可组成 N6。 |

N6 的组合结果应分别计数：市场输入、每键投递、market replay/缺口、重连恢复、慢消费者队列/回放、quota 调用等待，以及订单/组业务事实。不得只用单一平均值掩盖热点账户或热点键。查询波的容量与 deadline 参数从固定 DIM07 来源复制，不得在本草稿重算或放宽。

## 已有可复用路径与本轮实际缺口

- 可运行但尚未完整通过的入口：上述 100 策略 runner 有 `pilot`（120 秒）和 `full`（1,800 秒）两种模式及固定来源/资源检查。R13 唯一实际 pilot 曾达到 112 个子进程 ready，但在持续负载约 20 秒时可用内存降至 1,888,423,936 字节并触发原 2 GiB 保护；没有完成 120 秒，更没有完成 1,800 秒或正常关闭。因此不登记 U0 或任何非均匀 profile 的 LOAD01 PASS。
- 已有窄覆盖但不替代 LOAD01：10 策略 × 20 主题 startup wave（只覆盖 1 个十策略波，报告未保存可恢复的 batch latency）；R224 单 owner/10 relay scope slow sink（28 ms append、2 个五秒周期、40 个 order RPC）；DIM07 100 进程/200 个 CTP 查询准入请求热点波；DIM07 四进程并发重连；DIM09 同账户三腿协调与 synthetic provider。每项都保留其原证据范围，不拼成全负载验收。
- 可复用模型：`MarketInstrumentKey` 包含 provider/venue/environment/product/instrument/native symbol；`MarketSubscriptionKey` 另含 source/entitlement/channel/depth/timeframe。它们能区分场景身份，但不等于当前 runner 或 provider 已经执行这些分布。
- 最小缺少的 LOAD01 接线：部分订阅集合与按主题配额的发布计划；重复 native symbol 的跨 venue key；非空 timeframe/bar 事件来源；策略多账户和订单 instrument 选择；三腿 group coordinator 入口与逐腿度量；在主负载中的 CTP query quota 调用及其与慢消费者/重连的共存统计。现有单项业务/配额测试可作 fixture/API 来源，不需要建立第二套通用 runner。

## P0 冻结与通过报告

在第一次性能级运行前，P0 必须针对固定机器和固定来源版本，为 U0 及每个被选 profile 登记：硬件/OS/Python/依赖版本、持续时间、时钟精度；市场端到端和 RPC 的 p50/p95/p99 上限；请求超时、序号缺口与补传完成界；CPU、物理内存、客户端/服务队列与磁盘上限；连接数和正常关闭后资源残留条件。跨机只在记录时钟同步误差后报单向延迟，否则报 RTT。

这些数值当前没有由计划、R13 失败试跑、10 策略 wave、DIM07 quota 波或注册解析微基准建立。尤其不把 DIM07 的准入 p95、R224 的 28 ms sink 注入值、注册解析脚本耗时或启动 pilot 的短样本改写为 LOAD01 SLA。没有固定基线和逐 profile 阈值时，只能报告有界观察与安全门结果，不能声称性能验收完成，也不能据此推导 015 的 HFT 能力。
