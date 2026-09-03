# BtApi

`BtApi` is the single public façade. It builds a `DirectBackend` by default; passing `transport_mode=TransportMode.ZMQ` and a `ForwardingConfig` activates the forwarding backend.

```python
from bt_api_py import BtApi, ForwardingConfig, TransportMode

api = BtApi(
    debug=False,
    transport_mode=TransportMode.ZMQ,
    forwarding_config=ForwardingConfig(
        command_endpoint="tcp://gateway.example:5555",
        market_endpoint="tcp://gateway.example:5556",
        private_endpoint="tcp://gateway.example:5557",
        account_id="paper",
        strategy_id="strategy-a",
    ),
)
```

The endpoints above are illustrative; configuring a client does not establish a production authorization boundary. Inspect `api.get_capabilities(exchange_name)` before relying on an operation.

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
