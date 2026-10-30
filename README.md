# bt_api_py

[![Python 3.11-3.13](https://img.shields.io/badge/python-3.11--3.13-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://github.com/cloudQuant/bt_api_py/actions/workflows/tests.yml/badge.svg)](https://github.com/cloudQuant/bt_api_py/actions/workflows/tests.yml)

`bt_api_py` is a Python façade for exchange plugins and direct Feed calls. Managed live execution uses Backtrader's explicit `ServiceExecutionSession` and the service-v2 `ServiceHost`; this SDK does not provide a separate forwarding runtime. The package is an integration framework—not a declaration that every registered plugin or venue is ready for live trading.

## SDK family and responsibility boundaries

The package names describe separate boundaries, not a promise that every route
has been composed or deployed. The detailed Chinese system architecture is in
[TRADING_SYSTEM_ARCHITECTURE.md](https://github.com/cloudQuant/backtrader/blob/dev/docs/TRADING_SYSTEM_ARCHITECTURE.md).

The shared [trading-mode standard](https://github.com/cloudQuant/backtrader/blob/dev/docs/source/advanced/architecture/trading-modes_zh.md) defines `replay`, read-only `shadow`, `paper` using an external simulated account, and `live` using a production account. Iteration 52 implements and validates `paper` and `live`; the other modes are inventoried for later integration. A mode name does not authorize account writes, and provider capabilities and environment-specific differences still require validation.

The long-term integration goal is to reuse the same compatible Backtrader strategy source for backtesting and these four modes, selecting the appropriate Feed, Broker, Store and configuration instead of rewriting strategy code. Adapter capabilities and differences must be explicit; this goal does not claim all combinations are implemented or accepted today.

| Area | Responsibility | Boundary |
| --- | --- | --- |
| Backtrader | Backtesting and strategy engine. Its `ServiceExecutionSession` is the Backtrader-side entry for managed execution through the service-v2 `ServiceHost`. | Owns strategy and framework lifecycle; it is not an exchange plugin package. |
| `bt_api_py` | Public `BtApi` façade, direct Feed access, and assembly/discovery of exchange plugins. | Direct calls use the configured local provider path. The SDK has no separate forwarding runtime. |
| [`bt_api_base`](https://github.com/cloudQuant/bt_api_base) | Shared plugin contracts, data containers, feed protocols, and common integration utilities. | Shared primitives do not certify an exchange implementation or account. |
| [`bt_api_runtime`](https://github.com/cloudQuant/bt_api_runtime) | A proposed optional preflight/runtime boundary and a separate pure-data run-configuration contract. | The contract parser is an independently built candidate; it does not resolve references or authorize execution. Preflight and session orchestration remain unimplemented here, and existing `backtrader_runtime` behavior remains in Backtrader. |
| [`bt_api_gateway`](https://github.com/cloudQuant/bt_api_gateway) | Provider-neutral account-scoped command and read-side routing, with durable local journal contracts. | Requires server-composed authentication, account-owner execution, and write admission. It has no provider client, credential loader, or socket server. |
| [`bt_api_risk`](https://github.com/cloudQuant/bt_api_risk) | Reusable risk, policy/admission, and portfolio analytics components. | A package being installed does not mean a particular order route invokes or is authorized by a risk gate. |
| [`bt_api_execution`](https://github.com/cloudQuant/bt_api_execution) | Provider-neutral managed execution records, dispatch claims, and reconciliation contracts. | Uses an injected provider port and admission gate; it is not an exchange adapter or a complete deployment composition. |
| [`bt_api_monitor`](https://github.com/cloudQuant/bt_api_monitor) | Metrics, health observations, telemetry, and optional event intake components. | Monitoring roles must be explicitly composed and enabled; observations alone do not establish account readiness. |
| [`bt_api_transport_zmq`](https://github.com/cloudQuant/bt_api_transport_zmq) | Optional versioned wire protocol and authenticated ZeroMQ transport. | Transports commands and events; it does not own provider execution, order state, or risk policy. |
| Venue provider packages (`bt_api_binance`, `bt_api_okx`, `bt_api_ctp`, and others) | Exchange-specific REST, WebSocket, native API, and Feed integrations. | Registration or source presence is not evidence that the package is installed, configured, or accepted for live use. |

### Direct calls and managed service calls

- **Direct SDK path:** application code calls `BtApi` or a Feed in its process;
  the configured provider plugin performs the exchange interaction. The
  optional `execution_config` wraps this direct API with the SDK's own local
  execution journal. It is separate from Backtrader's managed execution path.
- **Managed service path:** a Backtrader strategy uses its explicit
  `ServiceExecutionSession` and service-v2 interfaces. A server-side `ServiceHost`
  composes authenticated scopes and the configured service handlers; transport,
  gateway, risk, execution, and provider adapters remain separate boundaries.
  Installing this SDK or configuring a direct `BtApi` object does not enable
  that service route.

Repository or submodule presence, source tests, examples, and successful package
installation are not live-account acceptance. They do not by themselves prove
authentication, current and complete account state, authorized order dispatch,
cancellation and recovery, or operational monitoring for a specific venue and
account. Use the support-status evidence and deployment-specific acceptance
records for those claims.

Release-blocking CI targets Python 3.11–3.13. Python 3.14 is canary-only.

## Release 0.15.4

This quality-focused patch makes the lint-debt gate compare checkout paths
consistently on Windows and POSIX, retains the iteration-07 execution-session
typing and rollback coverage, and pins the bundled adapter source revisions to
commits derived from their current `master` branches. It does not expand the
set of venues represented as ready for live trading; use `doctor` and the support-status
policy to inspect the evidence available in your environment.

## Install and diagnose

```bash
python -m pip install bt_api_py
python -m bt_api_py.doctor --bundle core-reference --format json
```

The doctor command verifies installed package metadata and reports plugins as installed, disabled, or unavailable. It does not authenticate to an exchange or place orders.

## Direct typed order example

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

`account_id="paper"` above is an illustrative account identifier, not a trading-mode selector or write authorization. The selected provider, endpoint, credentials, and account determine the actual environment.

`OrderRequest` is the cross-transport contract. The historical positional form remains only as a compatibility layer and must include a side-qualified type such as `"buy-limit"`; a bare `"limit"` or `"market"` cannot safely infer side.

## Normalized results and durable execution

The existing `BtApi` methods accept `normalized=True` for standard account,
instrument, position and order results. `poll_event(exchange_name)` returns
standard market, order and trade dictionaries; quantities retain the venue's
native units. Position intent uses `position_side`, `offset`, `position_mode`
and `quantity_unit` on `OrderRequest`, including CTP dated closes and native
order references. Unsupported venue capabilities fail explicitly.

Enable a durable execution session with the optional constructor argument:

```python
api = BtApi(
    exchange_kwargs=exchange_config,  # Supply your configured accounts.
    debug=False,
    execution_config={
        "order_journal": "local-orders.jsonl",
        "account_currency": "USDT",
        "order_poll_interval": 0.2,
    },
)
```

`BtApi` remains the direct SDK facade. Its optional execution session owns the
exclusive journal lock, intent persistence, client ID uniqueness, uncertain
execution reconciliation and cumulative fill/fee state. Call
`new_client_order_id(exchange_name)` before binding a local order reference,
submit a typed request with `normalized=True`, then keep polling `poll_event`
even when no market bar arrives. A timeout never triggers a replacement order.
An unresolved historical intent blocks new placements until the original order
is reconciled. Reuse the same journal across restarts and call `close()` when done.

With a session enabled, legacy/raw writes, asynchronous writes and bulk
cancellation are explicitly rejected where they cannot honor its journal
contract. Omitting `execution_config` preserves the historical API behavior.
`get_execution_summary()` reports the session's unresolved orders and fees;
`get_all_balances(normalized=True)` preserves individual account currencies,
and `get_portfolio_balance()` rejects mixed or nonzero unknown currencies.
This is direct provider execution support; it does not contain Backtrader
orders, feeds or strategy accounting. Managed live execution that requires
Host-owned admission and dispatch uses Backtrader's explicit
`ServiceExecutionSession` and service-v2 `ServiceHost` path.

## Direct provider reads

The direct backend preserves each provider Feed's native result shape. Forwarding reads through `BtApi` have been retired. For managed service market or account reads, use the configured service-v2 read services; this package's direct Feed API does not silently route to a remote service.

To configure the direct provider path, pass its registered exchange settings when constructing `BtApi`. The service transport and its read scopes are configured separately at the Host boundary.

## Historical tick collection (CTP)

The `bt_api_ctp` plugin ships a whole-market tick collector that persists every
depth snapshot to Parquet for backtesting: one file per instrument under
`<data_root>/<trading_day>/<exchange>/<instrument_id>.parquet`, plus a per-day
`report.json` with row counts and in-session gaps. It is driven by an external
scheduler (cron/systemd/launchd units included) and exits by itself at the close:

```bash
# 推荐：启动脚本（自动加载 .env、定位解释器、前台跑到收盘）
sh ctp_data/start_collector.sh

# 等价的手工方式（必须在仓库根目录执行：data_root 是相对仓库根的相对路径）
python -m bt_api_ctp.collector --config ctp_data/collector.yaml --until-close --wait-open
```

`ctp_data/` is the collection runtime directory; see
[`ctp_data/README.md`](ctp_data/README.md) for setup, scheduling and stopping.
Reading the data and the deployment checklist:
[`bt_api/bt_api_ctp/README.md`](bt_api/bt_api_ctp/README.md) and
[`bt_api/bt_api_ctp/deploy/collector/README.md`](bt_api/bt_api_ctp/deploy/collector/README.md).

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
- [Release notes 0.15.4](docs/releases/0.15.4.md)

Use a clean checkout and retain JSON/JUnit/log artifacts when validating exchange plugins. A registry entry, source directory, or historical test number is not release evidence.
