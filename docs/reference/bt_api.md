# BtApi

`BtApi` is the public façade for direct provider and backtest operations. It
uses `DirectBackend` by default and returns the native result shape from the
selected Feed.

```python
from bt_api_py import BtApi

api = BtApi(
    exchange_kwargs={"SIM___SPOT": {"market_data_only": True}},
    debug=False,
)
try:
    tick = api.get_tick("SIM___SPOT", "BTC-USDT")
finally:
    api.close()
```

The old `transport_mode="zmq"`, `forwarding_config`, and `gateway_wire_config`
entry points are retired. They raise `LegacyForwardingRetiredError` before
provider initialization. Managed live writes use Backtrader's explicit
`ServiceExecutionSession` with a service-v2 `ServiceHost` and its
`service.order_entry` path.

::: bt_api_py.bt_api.BtApi
    options:
      show_root_heading: true
      show_source: false
      members_order: source
      heading_level: 2
      show_if_no_docstring: false
      filters:
        - "!^_"
        - "!^__"
