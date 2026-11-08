# bt_api_py

The current direct-trading refactor keeps `bt_api_base`, `bt_api_ctp`, `bt_api_okx`, and `bt_api_binance` under `bt_api/`. The other submodule checkouts have been moved intact to `bt_api/deperated/` for later review. This source move does not uninstall installed distributions or disable their entry points. Existing documentation below describes the broader SDK family and its historical or optional functionality; the new layout, direct order paths, package installation, and behavior have not been accepted.

CTP front selection uses `bt_api_ctp.ctp_env_selector`; the old SDK facade and its YAML configuration are removed. The provider selects its own defaults, which differ from the old facade, and automatic set1 selection requires verified trading-day evidence. This source change has not been behavior- or account-accepted.

Position-mode acknowledgement codes are provider declarations: the Base default is unspecified. Binance, CTP and non-swap OKX preserve the previous `200`; OKX swap preserves `0`, and ten historical policies retain `200`. These inherited values do not prove venue capability. Third-party Base subclasses no longer receive a default; dynamic policies, packaging and behavior remain unvalidated.

Binance/OKX bound read-only runtime registration and exact owner/config checks live in their providers; Base carries the SDK-injected API type, and SDK owns composition and finite error mapping. The two SDK-specific registration modules and `compose_binance_read_only_runtime` / `compose_okx_read_only_runtime` are removed. `compose_read_only_runtime` creates a new owner and differs from reusing an existing one. Entry points, thread/cleanup and behavior remain unaccepted. CTP managed-runtime route, environment and instrument-field checks live in `bt_api_ctp.managed_runtime_route_validation`; SDK retains grant expiry, session and lifecycle. Importing the CTP-specific SDK module requires that provider. CTP preflight settlement settings are provider-owned; the SDK selects the route and retains the call timing and error type. The CTP provider is loaded on demand and this path is unaccepted.

Execution-ledger identity format is declared per provider via `bt_api_base.execution_ledger_identity_policy`, not hard-coded venue names. The 13 built-in providers retain their prior format choices; this does not prove identity. Missing or undiscoverable declarations fail, including historical/custom providers, and successful selections are process-cached. Migration, packaging and behavior remain unaccepted. A separate provider `account_risk_source_policy` declares whether native account binding is still required for neutral risk reads. CTP remains unavailable; the other 12 retain the old branch, not proof of source. Undeclared providers fail and discovery/behavior are unaccepted.

Gateway SDK composition now accepts the neutral `GatewayMarketBinding` and a trusted provider factory; `GatewayOkxMarketBinding` and the `okx_market` parameter are removed. An OKX caller supplies `bt_api_okx.market_provider.OkxTickerQueueMarketProvider` as its factory. The generic binding permits nonempty per-principal subsets; OKX key validation occurs at subscription and provider settings at construction. This breaking source change and lifecycle behavior are unaccepted.

CTP managed Risk order payload construction and cancel-target matching live in `bt_api_ctp.risk_dispatch_binding`; Base declares the optional provider contract, while SDK retains evidence selection; Risk retains claims and journal fencing. Target orders with a schema now require a matching provider declaration: undeclared schemas fail, a breaking change. Journal compatibility and behavior remain unvalidated. The CTP provider also owns native request field comparison, dispatch/zero-fill projections and zero-fill evidence field matching; SDK retains session, exact type/source checks and journal lifecycle. The provider is trusted for field matching. Risk-order position-dimension source projection is selected by the provider policy, without an SDK venue-name branch. CTP declares the projection at `bt_api_ctp.risk_source_position_projection`; the other 12 built-ins retain the previous generic path. Missing declarations or required projectors fail closed, so third-party policies need updates. SDK retains source/session checks and error mapping; provider discovery and behavior are unvalidated.

CTP recovery row instrument/side/offset fields, active-order classification, order/intent matching and trade-key projection live in `bt_api_ctp.recovery_row_projection`; the SDK supplies its original value selector, quantity checks and instrument resolver, retaining proof, journal, session and authorization. The provider participates in field decisions; import timing and behavior remain unvalidated.

SDK `account_risk_projection.position_audit_close_error` classifies a blocked order using caller-supplied local position facts; the Broker retains its audit state and framework objects. This decision does not establish account provenance or grant write authority, and behavior is unvalidated. Neutral top-of-book checks and midpoint projection live in SDK `market_stream_contracts.orderbook_midpoint`; Feed retains event dispatch and bar-line updates. Tick trading-day and action-day fields are explicitly selected by each provider: CTP includes `TradingDay` and `ActionDay` alongside neutral names; the other 12 select neutral names only. Base defaults are `None`, and the former uppercase candidates for the other ten have been removed. Dynamic input coverage remains unvalidated. Base order-book sequence/audit defaults omit Binance-native fields; legacy providers keep their old values. Base balance, position, order, trade, status and position-mode defaults no longer include selected Binance/OKX/CTP native aliases; the explicit historical policy and its four direct consumers retain the prior mappings. Base permission, position-mode, event-identity and instrument-symbol defaults also contain neutral fields only; the three explicit provider policies retain their declarations and ten legacy providers retain historical fields. Third-party Base subclasses change behavior; coverage and behavior remain unaccepted.

All 13 providers explicitly declare `trade_id_fields` using neutral names and native fields confirmed in their container source; Base defaults to `None` and the SDK reads the selected fields. Bybit, dYdX and Kraken declare no additional native trade-ID fields. Unconfirmed generic `id`/`tradeId` aliases are no longer assumed across venues; narrowing and additions are breaking changes. The `group_fill` provenance alias contract is separate: IDs including Gateio, HTX and MEXC still require verification and normalization does not prove them. Dynamic inputs and behavior remain unvalidated.

Order-status and trade-fee field candidates are selected by provider policy. All 13 providers explicitly declare order-status candidates: Binance, OKX and CTP retain their existing declarations, while the other ten each declare `status`, `order_status` and `state`; the Base legacy policy no longer declares these candidates. Trade-fee candidates remain policy-selected. A policy without declarations fails explicitly. This field ownership move does not establish native status coverage; third-party policies and behavior remain unvalidated. Placement position-mode fencing is also provider-declared: OKX/Binance retain the existing guard, other built-in routes retain the previous unguarded behavior, and undeclared routes reject placement. This breaking change does not prove venue capability.

Neutral position-row matching against configured route and instrument identities lives in `bt_api_py.broker_event_envelope`; Backtrader retains feed/order binding collection and local positions. This source change is unaccepted.

The neutral unverified execution-event identifier projection lives in `bt_api_py.execution_event_projection`; Backtrader retains local order binding, UNKNOWN updates and drop recording. The Store completed-bar delivery queue remains framework-owned. Cross-package behavior is unaccepted.

CTP managed order and cancel identity value types live in `bt_api_ctp.managed_request_identity`. SDK request models resolve them lazily when those fields are used, with lazy legacy SDK exports. Module identity, serialization, annotation resolution, source binding, optional packaging and behavior remain unaccepted; these values grant no execution authority.

Binance, OKX and CTP declare their own explicit order-identity aliases and side projections; the remaining providers explicitly retain the historical set. Known identity aliases outside the selected policy raise an execution-unknown error. Dynamic payload coverage, packaging and behavior remain unvalidated.

Order provenance selects neutral fields and native aliases declared by each of the 13 providers, using scoped v5/v6. Risk and group proofs use only v6, bind route-specific alias digests, and group source events persist and recheck that digest. The v1–v4 contracts have been removed. Bybit, dYdX and Kraken currently declare no native fill aliases; their field coverage requires review. This is uncommitted source work; dynamic providers, old-record handling, packaging and behavior remain unvalidated.

SimNow native order, cancel and query projections, and the native query collection workflow, now reside in the CTP provider; the SDK retains the before/after session-source fence. The SDK retains session composition and injects its unified cancel request type; class origins, dependencies and behavior remain unvalidated.

The working-tree CTP gate approval, credential binding comparison, selected-front scope and entry/recovery scope checks are SDK-owned; the provider invokes them under its native lock. Retired runtime provenance and remaining reverse dependencies are unresolved; no behavior or trust acceptance is claimed.

The working-tree `managed_execution_mailbox` supplies the bounded synchronous owner-thread handoff using runtime-owned state; it neither creates a thread nor grants execution authority. Concurrency and behavior remain unvalidated.

[![Python 3.11-3.13](https://img.shields.io/badge/python-3.11--3.13-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://github.com/cloudQuant/bt_api_py/actions/workflows/tests.yml/badge.svg)](https://github.com/cloudQuant/bt_api_py/actions/workflows/tests.yml)

`bt_api_py` is a Python façade for exchange plugins and direct Feed calls. Managed live execution uses Backtrader's explicit `ServiceExecutionSession` and the service-v2 `ServiceHost`; this SDK does not provide a separate forwarding runtime. The package is an integration framework—not a declaration that every registered plugin or venue is ready for live trading.

Risk analytics, limits, policy and ML live in the optional `bt_api_risk` package. The historical SDK top-level risk entry lazily exposes that package; its former risk submodule paths are removed. The SDK `risk` extra selects risk analytics and ML extras. The working-tree merge uses the SDK split implementation in place of the Risk package analytical copies; behavioral and distribution acceptance remain outstanding.

The former `bt_api_py.backtrader.BtApiBroker` mock-only placeholder and the unrelated legacy `bt_api.adapters` mock broker registry have been removed. The framework integration belongs to Backtrader and venue discovery uses the SDK provider/plugin contracts; external users of the retired adapter API must migrate. This is an unaccepted breaking source change.

The working-tree risk-only direct journal, SQLite/OS-lock storage, record types, event chain and writer fencing reside in `bt_api_risk`. `bt_api_py.service_risk_order_journal` composes provider evidence selection and Gateway receipt verification for the SDK journal subclass; its existing entry and record names remain. Optional Execution cancellation-source binding, verification and projections are composed by `service_execution_risk_journal_bridge` with lazy Execution imports; the Risk journal itself no longer imports Execution. A standalone Risk journal rejects those paths without the bridge. Cross-package types, source binding, installation, concurrency and behavior remain unvalidated. CTP native order/action field construction and prepared-request, target and receipt binding belong to the CTP provider; owner thread, session and authorization remain in the SDK. The provider participates in field-consistency checks; MRO, source binding and behavior remain unvalidated. These source changes are not behavior- or distribution-accepted.

The working-tree neutral normalized order observation and source projection types reside in `bt_api_base.order_observation_contracts`; the former SDK normalization import remains an alias to those same types. This is not source attestation, behavior acceptance or distribution compatibility evidence.

CTP native order, cancel, insert-error and trade callback parsing/mapping resides in `bt_api_ctp.execution_callback_mapping`; Execution retains its event/correlation types and lazy mapping compatibility entrypoints. Managed integration requires both packages and remains source-only, without behavior or installation acceptance.

The working-tree SDK computes target coverage against neutral account-execution instrument snapshots. Backtrader retains strategy, broker and feed ownership; coverage does not establish source authenticity. This source move is unaccepted.

The CTP callback session, native-source bridge and callback-ledger adapter are provider-owned; Execution retains its store, leases and event types and exposes lazy compatibility imports. Their cross-package source, import and behavior remain unaccepted.

CTP managed request/binding and native sender/result contracts reside in the CTP provider; the durable single-worker claim, UNKNOWN and completion workflow remains in Execution. Source and distribution are not behavior-accepted.

CTP trade-stream selection and native Trader/feed state comparison are provider-owned; SDK subscription state and approval authority stay in the SDK. The source split is not runtime or account acceptance.

The working-tree CTP path-budget policy, limits, journal value/row projections and budget transition rules reside in optional Risk; SDK session reservation seals, source bindings, writer fences and durable state stay in the SDK. The budget path loads Risk lazily and fails when unavailable. This source split and its exception/import compatibility are unvalidated.

Risk dispatch evidence uses a Base declaration selected by the exact Risk account provider; CTP declares its existing field and schema names. The Risk journal retains the claim, event chain and writer fence; SDK composition selects the provider profile. Other providers are not established by this source refactor, and entry-point installation and behavior remain unvalidated.

## SDK family and responsibility boundaries

The package names describe separate boundaries, not a promise that every route
has been composed or deployed. The detailed Chinese system architecture is in
[TRADING_SYSTEM_ARCHITECTURE.md](https://github.com/cloudQuant/backtrader/blob/dev/docs/TRADING_SYSTEM_ARCHITECTURE.md).

The shared [trading-mode standard](https://github.com/cloudQuant/backtrader/blob/dev/docs/source/advanced/architecture/trading-modes_zh.md) defines `replay`, read-only `shadow`, `paper` using an external simulated account, and `live` using a production account. Iteration 53 first restores and refactors the direct-trading boundary without requiring optional components; the remaining Iteration 52 `paper` and `live` work follows. `replay`, `shadow`, and old local matching remain deferred. A mode name does not authorize account writes, and provider capabilities and environment-specific differences still require validation.

The long-term integration goal is to reuse the same compatible Backtrader strategy source for backtesting and these four modes, selecting the appropriate Feed, Broker, Store and configuration instead of rewriting strategy code. Adapter capabilities and differences must be explicit; this goal does not claim all combinations are implemented or accepted today.

| Area | Responsibility | Boundary |
| --- | --- | --- |
| Backtrader | Backtesting and strategy engine. Its `ServiceExecutionSession` is the Backtrader-side entry for managed execution through the service-v2 `ServiceHost`. | Owns strategy and framework lifecycle; it is not an exchange plugin package. |
| `bt_api_py` | Public `BtApi` façade, direct Feed access, and assembly/discovery of exchange plugins. | Direct calls use the configured local provider path. The SDK has no separate forwarding runtime. |
| [`bt_api_base`](https://github.com/cloudQuant/bt_api_base) | Shared plugin contracts, data containers, feed protocols, and common integration utilities. | Shared primitives do not certify an exchange implementation or account. |
| [`bt_api_runtime`](https://github.com/cloudQuant/bt_api_runtime) | Optional run configuration and runtime assembly. | Its contract parses configuration and matches symbolic references. A source/fake candidate assembles read-only paper/live bar observation with existing Backtrader components; it does not verify actual provider/account identity, authorize orders, or establish complete runtime or account acceptance. Iteration 53 plans to migrate responsibilities, switch active callers, and remove the old `backtrader_runtime` after verification; the optional runtime remains unnecessary for the direct route. |
| [`bt_api_gateway`](https://github.com/cloudQuant/bt_api_gateway) | Provider-neutral account-scoped command and read-side routing, with durable local journal contracts. | Requires server-composed authentication, account-owner execution, and write admission. It has no provider client, credential loader, or socket server. |
| [`bt_api_risk`](https://github.com/cloudQuant/bt_api_risk) | Reusable risk, policy/admission, and portfolio analytics components. | A package being installed does not mean a particular order route invokes or is authorized by a risk gate. |
| [`bt_api_execution`](https://github.com/cloudQuant/bt_api_execution) | Optional execution management: durable dispatch and reconciliation; target scope also includes aggregation of registered strategy signals/intents, order slicing, algorithmic execution, and fill/fee allocation. | Multi-strategy aggregation, slicing, and algorithm scheduling are target responsibilities, not fully implemented or accepted capabilities. Strategies generate signals; Risk owns admission; provider ports supply venue facts. The target direct route does not require this package. |
| [`bt_api_monitor`](https://github.com/cloudQuant/bt_api_monitor) | Metrics, health observations, telemetry, and optional event intake components. | Monitoring roles must be explicitly composed and enabled; observations alone do not establish account readiness. |
| [`bt_api_transport_zmq`](https://github.com/cloudQuant/bt_api_transport_zmq) | Optional versioned wire protocol and authenticated ZeroMQ transport. | Transports commands and events; it does not own provider execution, order state, or risk policy. |
| Venue provider packages (`bt_api_binance`, `bt_api_okx`, `bt_api_ctp`, and others) | Exchange-specific REST, WebSocket, native API, and Feed integrations. | Registration or source presence is not evidence that the package is installed, configured, or accepted for live use. |

Direct account-configuration method aliases are declared per provider route; the SDK discovers them and uses the standard method when none is declared. OKX/Binance source declarations and distribution compatibility remain unvalidated.

Neutral event-time field selection, UTC conversion and connection/subscription scope classification now reside in `bt_api_py.market_tick_contracts`; Feed retains its own scope and bar state, and the classification does not attest provenance; these conversions do not attest source clocks. This source migration has not been behavior-validated.

Order-side projector entry points receive the full row; provider-specific nested-field extraction is owned by each provider. Binance's nested `o` selection is no longer in the unified SDK. This is an uncommitted breaking entry-point signature change and has not been behavior- or installation-validated.

The working-tree `service_recovery` facade checks neutral paper-fill field sets, source-event identities, bindings and economics, and encodes recovery context scalars and exact decimals for the host recovery attestor; the host still owns feed/bar and commission-context bindings. This source migration is not recovery or account acceptance.

The working-tree `service_execution_assembly` owns shared runtime-state, nonnegative-counter and bounded error-code projections, plus aggregation of owned runtime facts; the framework session retains its own engine lifecycle, cleanup error classification and report. This cross-package source change has not been behavior-validated.

The working-tree `monitor_threshold_composition` wraps Monitor threshold workers as SDK Host resources. Monitor owns the worker and rules, while SDK owns the exact Host-resource composition. The former Monitor package-level composition exports moved to the SDK; lifecycle and installation remain unvalidated.

SDK and Monitor ELK formatters refer to the same working-tree `bt_api_base.logging_context` identifiers; their configuration paths share `bt_api_base.logging_setup`. Aligned distributions and runtime behavior remain unvalidated.

The working-tree monitoring extra selects `bt_api_monitor`; metrics, the default registry, collector, system metrics, exchange health, Grafana, decorators, Prometheus and ELK are Monitor-owned. The corresponding former `bt_api_py.monitoring` submodules are removed, while the old package entry uses these Monitor objects. The unified versions retain the former SDK async-duration, Prometheus and ELK behavior; direct exporter calls default to loopback, while monitoring configuration may explicitly select another host. Monitoring configuration now lives in `bt_api_monitor.config`, using the former SDK configuration validation and cleanup implementation; the former SDK configuration submodule was removed. This is a breaking source change without installation or behavior acceptance.

CTP native query-method selection and public session-field projection are provider-owned; the SDK retains route and transport guards. This source migration has not been behavior-validated.

CTP recovery-row account/day field selection and the existing active-order status projection are provider-owned; the SDK retains query and session coordination. Status semantics and distribution compatibility have not been validated.

CTP query terminal-packet and native account-row validation are provider-owned in the working tree; the SDK retains its pre/post session guards. The cross-package source is not runtime acceptance.

SDK-internal CTP ingress queue views, sealing and private-event fences live in `bt_api_py._ctp_ingress`; `BtApi` retains per-instance binding and authorization timing. This source refactor has not been behavior-validated.

CTP native active-front and authenticated market-identity checks are being moved to `bt_api_ctp.credential_binding_fronts`; `BtApi` retains its feed, stream, ingress and authorization state. This source migration has not been validated.

The isolated SimNow probe is provider-owned in `bt_api_ctp.simnow_probe`; `bt_api_py.ctp_probe` delegates lazily. This uncommitted migration has not been validated for installation or runtime.

The working-tree SDK projects fields from already verified neutral submit acknowledgements; the marker in a mapping does not establish provenance. This source migration is not behavior acceptance.

Correlated-command contract types are loaded through the SDK command module; framework order bindings and dispatch lifecycle remain with the consuming adapter. The source migration has not been behavior-validated.

The working-tree `market_stream_contracts` also validates neutral tick/order-book channel selections and configured depth; callers retain subscription timing and lifecycle. This source migration has not been behavior-validated.

The working-tree `route_identity_projection` checks supplied route sets and selects exact live route names; the host retains instance and lifecycle checks. Route names do not grant account authority. This source migration has not been behavior-validated.

The working-tree `execution_identity_fence` retains host-generation identity-vector fence history while the host supplies its current API and generation. It does not create provider identity evidence; this source migration has not been behavior-validated.

The working-tree `order_preflight` owns rule/economics decision coordination and identity bindings; hosts supply their order projection and lifecycle context. This source migration has not been behavior-validated.

Direct execution-option extraction, command-queue sizing and funding refresh-option normalization are owned by the SDK assembly, queue and funding modules; callers retain their own session lifecycle. These source changes are not behavior or installation acceptance.

The working-tree `market_delivery_projection` projects neutral event fields and continuity for consumers; the host retains delivery state, and this projection does not attest the source clock. This source migration has not been behavior-validated.

The working-tree `service_recovery_quote` owns neutral quote-revision validation, quote-to-fill field comparison and per-consumer cursor state; the host binds observations to its own bars and retains source-trust responsibility. This source migration has not been behavior-validated.

The working-tree `service_market_bindings` owns neutral service bar and order-book binding types, consumed by Backtrader and optional `bt_api_runtime`; cross-package imports and distribution remain unvalidated.

The working-tree SDK account-balance reader also checks configured/live route alignment and batch shape before a framework projects balances. Route names do not establish account identity; the migration is not behavior or installation acceptance.

The same SDK module checks service binding collections, account coverage, and distinct client instances. The consuming framework retains its feed/position mapping; these checks do not establish account authority. The migration is source-only and unvalidated.

The working-tree `ServiceBarHistoryHandoff` owns per-consumer history/live bootstrap, ordering and deduplication state; callers supply their bar projection and identity. This source migration has not been behavior-validated.

The working-tree `ServiceBarStreamSession` retains the epoch and sequence of one service bar stream; its caller projects validated bars and owns client closure. This change is not behavior or installation acceptance.

The working-tree `bt_api_py.factor_order_correlation` matches neutral local and provider order observations by client ID while the caller constructs its own result; it does not establish provider authority and has not been validated.

Result collection fields are selected by provider policy in the working tree; Binance and MEXC declare the native `symbols` container, while nine other providers retain the legacy candidate pending review. CTP/OKX field narrowing and distribution compatibility have not been validated.

Neutral reconciliation projection checks include caller-bounded position flatness, and neutral risk projections can be marked incomplete without changing their facts. These working-tree changes are unvalidated.

Exact reconciliation-route selection and caller-bounded timeout validation are SDK source work in progress; the caller remains responsible for active API lifecycle binding.

The working-tree `bt_api_py.command_coordinator` coordinates a command lane and its UNKNOWN safety state while callers supply receipt projections and lifecycle callbacks. Its behavior remains unvalidated.

The working-tree `bt_api_py.market_stream_session` retains exact subscription handles per consumer and delegates provider operations while callers supply route/lifecycle callbacks; it has not been validated for runtime or installation.

The working-tree `bt_api_py.account_risk_state` owns a per-instance neutral risk snapshot cache and refresh reservation; callers retain lifecycle and identity callbacks. This does not enable optional business risk policy or establish account facts, and remains unvalidated.

The working-tree execution configuration policy is selected by exact provider route; its declarations describe fields and do not attest account or credential identity. These changes require aligned base and provider distributions and are not runtime acceptance.

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

## 免责声明与使用风险（审阅草案）

[免责声明与使用风险草案](DISCLAIMER.md)说明软件、数据、回测和实盘交易的主要风险；该文件尚未批准生效，其中拟议的责任限制不是现行条款。
现行软件许可及第三方许可保持；许可调整状态和待审事项见[许可审阅说明](LICENSE-REVIEW.md)。

## Contributing and verification

- [Installation](docs/getting-started/installation.md)
- [Runtime architecture](docs/explanation/architecture.md)
- [BtApi reference](docs/reference/bt_api.md)
- [Support-status policy](docs/operations/support-status-policy.md)
- [Submodule validation profiles](docs/ci/submodule-validation-profiles.md)
- [Release notes 0.15.4](docs/releases/0.15.4.md)

Use a clean checkout and retain JSON/JUnit/log artifacts when validating exchange plugins. A registry entry, source directory, or historical test number is not release evidence.

## Iteration 63 development contracts

The 0.16.0.dev63 SDK/Base development versions introduce a required provider
`direct_write_error_policy: DirectWriteErrorPolicy` for PLACE and CANCEL. It
contains separate immutable sets of error codes that mean the native outcome
is unknown. A missing or invalid declaration refuses writes before preparation;
read-only provider access is unchanged. CTP, OKX and Binance in the matching
development version matrix declare their policies. Old provider names and
contracts are not silently adapted. This is a partial iteration 63 delivery,
not acceptance of a shared Gateway or any account capability.

`bt_api_py.service_runtime_thread.ThreadedServiceClientRuntime` owns the actual
`ServiceClientRuntime` and its registrations on one dedicated thread. Its trusted
factory constructs that runtime there; queued calls and lease maintenance use
the same registration. Before-start timeouts cancel the queued call, while
after-start timeouts remain unknown and must not be retried blindly. Startup
and close report unresolved cleanup; inherited instances reject use after fork.
An injected monotonic clock must share the runtime's clock domain. A transport
that blocks despite its timeout can still stop renewal; the wrapper does not
make such a call cancellable or automatically reconnect/replay it.

Local source tests and a synthetic ServiceHost/CURVE loopback have been checked.
The new Gateway trading client has not yet been connected. No SimNow/production
write acceptance or cross-platform certification is implied.

### Iteration 63 Gateway dispatch and storage RPC slice

The optional `gateway` extra installs `bt_api_gateway>=0.2.0.dev63` and
`bt_api_transport_zmq>=0.1.0`: ZeroMQ is the default Gateway transport.
The independent `transport-zmq` extra remains available. Future middleware
implementations belong in separate transport packages behind the same neutral
service contracts; no additional backend is introduced by this iteration. `compose_gateway_direct_dispatcher`
binds the trusted local provider instance to the Gateway guard and durable
DISPATCHING commit port. Only complete scalar `OrderRequest.to_dict()` payloads
are accepted; account and all order fields are checked again before commit.

The explicit `trading_owner` service role currently publishes only
`direct.reserve`, `direct.release`, and `direct.command_status`. It is a journal
RPC slice, not a complete trading owner. Strategy identity comes from the
authenticated registry context; service proxies are not yet accepted. After
STOP_NEW, pass `gateway_direct_service.recovery_requested_operations()` to
`ServiceClientRuntime(requested_operations=...)` for the same run's limited
status/release registration. Release keeps its write claim; reserve remains
blocked. The future remote client must use this recovery path explicitly.

The first reservation's TTL is not extended by a duplicate request, even if a
different TTL is supplied. Replies expose the original generation, relative
remaining TTL and `expiry_pending`; expiry observation is not durable EXPIRED
or evidence of NOT_SENT. Native dispatch is not exposed by this RPC slice.
The intended deployment is an independent Gateway process reached through
strategy configuration, with no embedded owner or local fallback.
