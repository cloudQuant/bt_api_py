# bt_api_py

[![Python 3.11-3.13](https://img.shields.io/badge/python-3.11--3.13-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://github.com/cloudQuant/bt_api_py/actions/workflows/tests.yml/badge.svg)](https://github.com/cloudQuant/bt_api_py/actions/workflows/tests.yml)

`bt_api_py` is a Python façade for exchange plugins, direct Feed calls, and an optional ZeroMQ forwarding boundary. It is a library and integration framework—not a declaration that every registered plugin or venue is ready for live trading.

Release-blocking CI targets Python 3.11–3.13. Python 3.14 is canary-only.

## Install and diagnose

```bash
python -m pip install bt_api_py
python -m bt_api_py.doctor --bundle core-reference --format json
```

The doctor command verifies installed package metadata and reports plugins as installed, disabled, or unavailable. It does not authenticate to an exchange or place orders.

## Typed order example

```python
from decimal import Decimal

from bt_api_py import BtApi, OrderRequest, OrderType, Side

api = BtApi(exchange_kwargs={"BINANCE___SPOT": {"testnet": True}})
ack_or_venue_result = api.make_order(
    "BINANCE___SPOT",
    OrderRequest(
        symbol="BTCUSDT",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("0.001"),
        price=Decimal("50000"),
        account_id="paper",
        client_order_id="example-order-1",
    ),
)
```

`OrderRequest` is the cross-transport contract. The historical positional form remains only as a compatibility layer and must include a side-qualified type such as `"buy-limit"`; a bare `"limit"` or `"market"` cannot safely infer side.

## Direct and forwarding reads

Direct mode preserves the native Feed result shape. In ZMQ mode, typed reads use `Consistency`:

```python
from bt_api_py import Consistency

# With a configured forwarding service and an active market subscription:
snapshot = api.get_tick("SIM___SPOT", "BTC-USDT", consistency=Consistency.CACHE_OK)
```

`LIVE` waits for a post-call event within the configured timeout. `CACHE_OK` only returns a bounded, explicitly stale snapshot; cache misses and timeouts use different domain errors. ZMQ public trades are not part of the current forwarding protocol and fail explicitly instead of falling back to a local Feed.

## Support status

<!-- BEGIN GENERATED:EXCHANGE_SUPPORT_OVERVIEW -->
## Support status

The entries below are evidence tiers, not a count of production-ready exchanges.

| Scope | Tier | Evidence boundary | Current limitation |
| --- | --- | --- | --- |
| core-reference bundle | `experimental` | Bundle metadata for BINANCE___SPOT, OKX___SPOT and CTP___FUTURE; not a live-trading or installed-plugin certification. | Current isolated submodule diagnostic has no initialized plugin worktrees, so package install/import/test certification is pending. |
| other registered plugins | `unverified` | Registry or submodule presence only. | Do not infer REST, WebSocket, paper-trading, or production readiness from registration alone. |

Blocking CI supports Python `3.11`, `3.12`, `3.13`; Python `3.14` is canary-only.

See `docs/operations/support-status-policy.md` for the evidence and expiry rules.
<!-- END GENERATED:EXCHANGE_SUPPORT_OVERVIEW -->

## Contributing and verification

- [Installation](docs/getting-started/installation.md)
- [Runtime architecture](docs/explanation/architecture.md)
- [BtApi reference](docs/reference/bt_api.md)
- [Support-status policy](docs/operations/support-status-policy.md)
- [Submodule validation profiles](docs/ci/submodule-validation-profiles.md)

Use a clean checkout and retain JSON/JUnit/log artifacts when validating exchange plugins. A registry entry, source directory, or historical test number is not release evidence.
