# Execution 业务日志投递到 Monitor

`ServiceExecutionMonitorRelay` 把正常 Execution 提交、撤单产生的持久日志，投递给独立
Monitor 的 `events.append`。它使用现有 SDK runtime 和 ZMQ 通信配置；没有配置 Monitor
时，不创建 reader、消费游标库或投递任务，Execution 仍可独立运行。

## 部署与配置

将 relay 放在 Execution 日志所在机器的独立进程，使用只读 journal reader。Monitor 可在
另一台机器；本轮本机测试不代表已完成双机验收。SQLite 日志不通过网络文件系统共享。
一个 relay 固定一个 `ExecutionScope`，同账户多个策略各自有游标；多账户可以部署多个
relay。进程调度、自动重启和多 scope 调度由部署者负责，本入口不启动后台线程。

复用已经解析的 `ServiceConfig`，在投递进程使用的配置中声明以下绑定，其他根字段与
通信 profile 按现有服务配置填写。该片段不能单独作为完整配置加载。

```yaml
clients:
  monitor:
    config_schema_version: 1
    settings:
      bindings:
        - mount: upstream.telemetry
          service_id: account-monitor
          instance_id: durable-ingress
          scope_ref: account-a-monitor
          transport_ref: monitor-connect
      options:
        request_timeout_ms: 2000
        heartbeat_interval_seconds: 5
```

`monitor-connect` 继续使用 `backend: zmq`、`rpc_connect` 和 CURVE security reference。
身份、密钥、scope resolver、peer resolver、日志路径及消费游标路径由可信部署代码提供，
不把密钥放入该配置片段。Monitor 的 ACL 必须授予这个 service 身份 `events.append`，
并把 WireScope 映射到允许 `execution.lifecycle`、`execution.cancellation` 的
`MonitorIngressTarget`。

使用 `execution_monitor_relay_configured(config, scope_ref=...)` 判断这个精确绑定是否存在。
不再添加另一份 `enabled` 开关。这个检查不建立连接或导入可选领域包。

执行投递的 runtime 由独立进程持有，只使用其出站通信能力；无需启动该进程的 ServiceHost
或领域组件。**不要把投递循环放进订单服务的 `serve_once`、`maintain`、readiness 或订单
回调，也不要把 Monitor 作为订单宿主启动成功的必需依赖。** Monitor 故障影响观测与积压，
订单提交仍由 Execution 自身的事务、风控和持久化条件决定。

## 装配与生命周期

在同一 owner thread 上执行以下顺序；`runtime` 是以可信 service 身份构造、尚未启动的
`ServiceClientRuntime`，请求操作限定为 `upstream.telemetry: {events.append}`。

```python
from contextlib import ExitStack

from bt_api_py.service_execution_monitor import (
    ServiceExecutionMonitorRelay,
    execution_monitor_relay_configured,
)

if execution_monitor_relay_configured(config, scope_ref=monitor_scope_ref):
    # 只在选择了这项能力之后导入和创建可选资源。
    from bt_api_execution.journal_reader import ExecutionJournalReader

    with ExitStack() as resources:
        resources.callback(runtime.close)
        runtime.start()
        reader = ExecutionJournalReader(journal_path, scope=execution_scope)
        resources.callback(reader.close)
        relay = ServiceExecutionMonitorRelay(
            runtime,
            reader,
            scope_ref=monitor_scope_ref,
            checkpoint_path=checkpoint_path,
        )
        resources.callback(relay.close)
        while not stop_event.wait(0.25):
            runtime.maintain()
            result = relay.poll(limit=32)
```

这个示例遇到错误会退出投递循环并关闭资源；部署 supervisor 可按有上限的退避重新启动。
不要在订单线程等待重启。若由同进程恢复，先完成 runtime 的 `recover()`，待注册健康后
调用 `relay.rebind()`，再继续轮询；它只接受相同稳定目标，不会重放订单提交或撤单请求。
切换目标需显式创建新的 relay，其独立消费游标不会继承旧目标的确认位置。

`relay.close()` 只关闭自身状态，不关闭借用的 runtime 或 reader。部署者需要检查
`runtime.state`、`cleanup_failures` 和 `unregister_uncertainties`；注销不确定不等于远端
注册已释放。接收端和 registry 应保留各自的持久库，不能用删除数据库处理重连问题。

## 持久性与隔离语义

- Execution 的订单状态和业务 outbox 原本就在同一 SQLite 事务中写入。本入口只读这些
  已提交事实，不构造第二份本地事件队列，也不获取 writer lease 或迁移 Execution schema。
- 订单和撤单是两个独立序列。`consumer_id` 标识目的地加本地源通道（journal incarnation
  与 scope），在同一源代次的进程/runtime/session 重启间保持稳定；新代次是新通道。该 ID
  不发给 Monitor，不会创建远端 consumer。消费游标还显式绑定代次、`ExecutionScope.key`、
  流名及投影版本；scope 过滤产生的序号间隙合法。
- `SequencedOutboxEvent.sequence` 是 Execution 源日志序号。Monitor 回执中的 sequence 是
  远端库序号；它用于确认远端事件，不替代或推进源消费游标。源游标只记录已确认的源行。
- 发送前关闭源数据库的读事务。Monitor 回执校验通过后，才在独立 SQLite 中以短事务推进
  游标。发送失败、回执不确定或游标提交失败，都允许重放同一确定性事件 ID；Monitor 的
  持久去重负责避免重复记录。这是至少一次投递，不承诺跨数据库原子事务。
- 每次恢复消费前核对已确认事件的 ID 和投影内容。源文件替换、同代次日志回退/截断、
  内容冲突或目标变动会停止投递，不默默清零游标或跳过损坏行。
- reader 的账户必须与注册 WireScope 的账户一致。策略级 WireScope 还必须匹配策略；
  账户级目标可接收该账户的各个策略。Provider、环境及交易日的部署映射仍由可信代码提供。
  本入口拒绝多账户集合、portfolio、group、leg、manual book 等额外约束；当前 reader 不具备
  这些维度的过滤能力，不能把账户日志冒充某个组合或订单组的完整事实。
- 每轮 RPC 次数受 `limit` 限制，两种流轮转；这不是整轮墙钟时间或长期磁盘用量上限。

## 事件内容与当前边界

只投递日志代次、流名、序号、事件 ID、业务事件类型、状态、精确纳秒时间及订单/撤单引用，
以及可信的 provider、环境、账户、策略和交易日归属。账户级 Monitor 因此能区分不同策略中
同名的订单；这些归属仍需结合注册权限使用，不能由 payload 扩大读取授权。
reader 的 SQL 不读取 `payload_json`。这些是生命周期元数据，没有成交金额、手续费、
余额、持仓或 provider 原始回复；不能把它们用于推断完整账户经济事实或组合 PnL。

本轮没有增加源日志清理、压缩或磁盘配额策略。Monitor 停机时积压保留在 Execution journal，
部署者须监测磁盘余量；未完成安全保留策略前，不得删除消费游标所依赖的源行。关键业务日志
自身持久化失败与 Monitor 离线是不同故障，不能用“监控可降级”绕过前者。

这条链不替代 Gateway 的账户事实生产、Risk 决策事件、普通策略遥测、组合风控规则或 AFI
页面接入。独立可执行部署入口、长期积压治理和跨机器验收仍须按迭代计划继续完成。
