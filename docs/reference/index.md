# API 参考

bt_api_py 完整 Python API 参考文档，由源代码自动生成。

<div class="grid cards" markdown>

- :material-api: **[BtApi](bt_api.md)**

    核心入口类，统一管理多交易所连接、行情、交易和账户操作。

- :material-shield-lock: **[认证配置](auth_config.md)**

    各交易所的连接认证配置类：`CtpAuthConfig`、`IbWebAuthConfig` 等。

- :material-alert-circle: **[异常体系](exceptions.md)**

    统一异常层次结构，精确捕获不同类型的错误。

- :material-view-grid: **[注册表](registry.md)**

    `ExchangeRegistry` — 即插即用交易所扩展机制。

- :material-broadcast: **[事件总线](event_bus.md)**

    `EventBus` — 发布/订阅事件驱动架构。

</div>
