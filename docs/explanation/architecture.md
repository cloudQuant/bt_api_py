# Runtime architecture

```text
Backtest or direct SDK use
    application
        |
      BtApi
        |
   DirectBackend
        |
 ExchangeRegistry feeds

Managed live execution
    Cerebro
        |
ServiceExecutionSession
        |
 service-v2 client
        |
    ServiceHost
        |
 service.order_entry
        |
 execution owner

Service reads
    market subscriptions -> GatewayMarketHub
    account snapshots    -> account session
```

## Direct and backtest use

`BtApi` with the direct backend remains the ordinary SDK entry point for
backtests and direct provider use. Plugin feeds keep their venue-native market
and account data projections. This path does not create a separate forwarding
runtime.

## Managed live execution

Live orders run through Backtrader's explicit `ServiceExecutionSession` and the
service-v2 `ServiceHost`. The session owns the Cerebro lifecycle; the Host
validates and admits each intent, reserves risk, journals dispatch state, and
routes accepted work through `service.order_entry` to its execution owner.
Configuration or transport failure is reported to the caller; it does not
silently fall back to direct feed execution.

Market and account reads use the Host's read services and their provider
interfaces. A provider data projection alone does not make that provider a
configured Host factory. Deployments must configure the intended provider and
transport explicitly.

## Transport and failure behavior

The service transport is selected by deployment configuration. ZeroMQ is an
optional transport implementation; its use does not change the execution
ownership or order-entry contract. Authentication and account scope are
checked at the service boundary.

The SDK's `bt_api_py.forwarding`, `bt_api_py.gateway`, and
`bt_api_py.runtime_plugins.gateway_dispatch` packages are removed. The Base
distribution still carries `bt_api_base.gateway` for downstream import
compatibility, and its old `GatewayRuntime` constructor fails closed. AFI still
has imports that must be migrated before that Base package can be removed.
Historical design and QA records that mention the retired runtimes remain
historical evidence and do not describe the supported SDK execution path.
