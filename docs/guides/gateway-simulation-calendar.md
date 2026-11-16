# Gateway 固定交易日日历读取（模拟切片）

日期：2026-11-16。本入口仅支持 `SIMULATION_ONLY`；所有响应保持 `source_authentication=NOT_PROVEN`、`trade_authorization=NONE`，不能用于证明 CTP 交易日、账户权限或生产日切成功。

## 服务端与客户端装配

显式 Gateway `trading_owner` 组件可声明只读 `service.calendar_read` 端口，操作为 `calendar.query.v1` 与 `calendar.windows.v1`。旧客户端默认仅申请单日操作；范围操作须显式选择。资源工厂必须提供精确 `GatewayCalendarService`，并声明每个 principal、execution domain、account、strategy、run、route、venue、kind、product、contract 的允许组合。Registry 的服务权限与这份 scope 列表分别检查，写入授权不随日历端口变化。

服务只接受受信本地装配提供的 `SimulationCalendarBinding`；其 Store 必须经过 `ReceiptCheckedCalendarStore` 检查已发布内容及 receipt 链。服务启动后由 `ServiceHost` 单次绑定真实 Registry；查询参数不能携带日切事件、来源代际、激活指令或本地路径。日切验证来自服务端私有 owner/gate，客户端不能用请求的 trading_day 触发换版。同日发布 V2 后仍读取 V1；通过模拟 gate 日切才加载 V2，载入失败保留旧版本并标记 STALE。源代际变化必须显式提供新 binding；代际历史有界且不复用。

策略 Gateway 配置显式增加 `store.calendar` binding，与 broker 使用相同 service、instance、transport 和 scope，并增加下列设置。示例为合成配置片段，不能直接替代部署授权：

```yaml
calendar_settings:
  mode: SIMULATION_ONLY
  range_read: true  # 可选，默认 false
  scopes:
    - route: synthetic-route
      venue: SHFE
      kind: future
      product: rb
      contract: mock_rb
```

配置文件解析、会话和 `GatewayBtApi.trading_calendar_query` 已接线。受保护 CTP 运营 CLI 尚未从部署文件自动构造可信日历 owner 或生产日切来源；007 真实配置也未自动启用此切片。程序化资源工厂与合成 ServiceHost 测试不等于真实部署已完成。

## 查询与结果

请求必须给出精确 route、venue、kind、product、contract 及 ISO trading_day。允许范围为固定 owner 交易日至之后 366 个日历日，同时必须落在已声明覆盖内。查询从一次固定视图取得窗口、场所日状态和 publication/content/version 身份；窗口为半开区间。单响应最多 128 个窗口、32 KiB，超限拒绝，不截断为完整结果。

策略端在 RPC 前后以及结果交付前复核当前注册、service epoch、认证 peer、scope/run 和租约。一次 query 的所有等待共用显式 timeout 预算；超时保留已有句柄，再次读取相同请求时重验，不重发日历查询。查询完成的代际观察不表示后续 socket 发送瞬间仍具备跨线程原子性。

SDK 返回独立的不可变 `GatewayCalendarQueryResult`，不冒充 direct 模式的未绑定 CURRENT 查询。BT Gateway Store/Feed 每次显式读取通过 SDK 验证服务端版本，不沿用跨请求本地窗口缓存，不打开本地 CURRENT。空的 COMPLETE 窗口集合不证明休市；STALE 或 UNKNOWN 不产生 active=True。

## UTC 范围读取

显式 `range_read: true` 后，SDK `GatewayBtApi.trading_calendar_windows` 和 BT `Feed.trading_windows` 通过一次 `calendar.windows.v1` RPC 查询半开 UTC 区间。服务端必须针对精确 scope 配置 `CalendarRangePolicy(policy_id, route, venue, kind, product, contract, max_advance_days, max_spill_days)`；两个天数必填，分别限于 0–31 和 0–7，不提供隐式默认策略。策略只是 `DEPLOYMENT_DECLARED_UNVERIFIED` 声明，不证明真实交易日或覆盖外没有夜盘。

服务从同一固定视图读取全部已编译窗口的归属界限，再枚举可能的 owner 交易日。窗口数据与声明冲突、覆盖缺失、候选日未知、历史 owner 窗口相交或读取期间切代时返回 UNKNOWN；不以自然日、星期或七天假期上限猜归属。CTP 的编译界限只观察已存在数据，完整范围仍须逐个候选日检查显式场所日与合约数据。

结果 `GatewayCalendarWindowsResult` 保留 publication/binding、policy、已发现的 gaps 和每个窗口原始 owner 交易日、ID、时间边界。同一模板 ID 可以跨交易日重复；窗口不裁剪。最多查询 366 天区间和 405 个候选 owner，响应仍受 128 个窗口与 32 KiB 限制。UNKNOWN 不返回部分窗口，gaps 只列已发现缺口，不保证列出全部缺口。空 COMPLETE 不证明休市；STALE 不成为当前有效来源。

BT 使用一次总超时五秒的 SDK 调用，范围能力关闭时返回 UNKNOWN，不退回逐自然日查询。direct 模式原有范围路径未由本轮改变。

## 未完成边界

- 单日、指定交易日 session 和声明范围内的 UTC 查询已接入模拟切片；真实运营 CLI 的日历资源与可信日切来源尚未部署。
- 固定视图、显式 scope 与本地 receipt 链证明模拟读取一致性，不证明外部来源真实性、覆盖之外不存在夜盘窗口或交易所实时状态。
- 本轮覆盖真实 Registry/ServiceHost/CURVE 与合成发布/日切；BT 两 Feed 集成使用 Store 状态桩。完整 Store 生命周期、实际 007 策略、真实 Host 重启旧 epoch、安装包及原生 Windows 尚未由此切片验收。
