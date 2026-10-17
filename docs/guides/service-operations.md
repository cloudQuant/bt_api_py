# Optional services: configuration and lifecycle

This guide describes the service APIs and configuration shape currently committed in this checkout. The YAML examples are inputs to the public offline parser; they do not carry runtime authority and do not start a service. The strings ending in `-ref`, security profiles, resource paths, policy versions, and registration grants must be supplied and resolved by trusted deployment code.

All YAML paths below are relative to the repository root.

## Roles and examples

| Role | Example | Capability shown |
| --- | --- | --- |
| Gateway market | `examples/services/market-gateway-service.yaml` | `gateway` / `market_hub` on `service.market_source` |
| Gateway account, Execution, and Risk | `examples/services/account-service.yaml` | `gateway` / `account_session_worker`, `execution` / `account_owner`, and `risk` / `account_admission` in one account-scoped ServiceHost |
| Monitor durable ingress | `examples/services/monitor-ingress-service.yaml` | `monitor` / `durable_ingress` on `service.telemetry_ingress` |
| Monitor reader | `examples/services/monitor-reader-service.yaml` and `examples/services/monitor-reader-client.yaml` | roleless `monitor` query on `service.monitor_query`, paired with an application client |
| Risk portfolio analysis | `examples/services/portfolio-analysis-service.yaml` | `risk` / `portfolio_analytics` |

The account service intentionally demonstrates the normal composed account path. The parser allows a service configuration to declare components and endpoint bindings; deployment must still provide a trusted `TrustedServiceAuthority`, registration/requirements records, component resources, and transport keys. A reference in YAML is only a lookup key. It does not grant access or load arbitrary Python code.

Monitor examples follow the committed Gateway-to-Monitor configuration test. Durable ingress and query/read services can be separate hosts with distinct service identities and listener profiles. The ingress process needs deployment-owned durable storage and authorized producer principals; the reader needs the corresponding read targets and grants. The ingress service identity and endpoint match the existing optional telemetry binding in `examples/services/strategy-readonly.yaml`.

## Zero-configuration clients and strategy/account combinations

Ordinary SDK/backtest use needs no optional service configuration. The existing `examples/services/unconfigured-client.yaml` is `{}`. An empty client configuration means no optional service bindings; it is not a request to discover or launch services. The existing `examples/services/strategy-readonly.yaml` shows a single strategy with a market binding, one account-read binding, and an optional Monitor telemetry binding.

`examples/services/strategy-two-accounts.yaml` shows one strategy process reading two separately identified accounts. Each account points to its own service and scope reference. `examples/services/account-service-b02.yaml` supplies the matching second account configuration shape. `examples/services/strategy-account-a01-s002.yaml` shows another strategy identity reading account A. Each strategy runs with its own `strategy_ref`, client session, and authorized scope; create a separate client configuration per strategy. The deployment's registration policy must grant exactly the intended strategy/account combinations. A client-side `account_ref` is a routing request, not authorization.

The pair `examples/services/account-service-two-host-shape.yaml` and `examples/services/strategy-account-a01-two-host-shape.yaml` illustrates matching service/client endpoints. It uses `192.0.2.10`, a documentation-only address, and is not directly startable. For a real deployment, bind the service to an address assigned to its host and set the client's `rpc_connect` to that same reachable service address. Do not use a wildcard bind address as the client endpoint. Configure firewall rules, CURVE keys, peer identities, and registration grants through the deployment's trusted resolver. The examples do not establish that a dual-machine deployment has been tested.

For co-located services, the committed samples use loopback endpoints such as `rpc_bind: tcp://127.0.0.1:7601` and the matching client `rpc_connect: tcp://127.0.0.1:7601`. Market data also needs a `market_bind` listener and matching `market_connect` endpoint, as shown by the market Gateway and strategy client examples. Keep ports distinct for simultaneously running services.

## Start, serve, and close a service

`ServiceHost` is a Python API, not a general service command-line launcher. From the repository root, these commands validate the committed account-service and strategy-client examples offline:

```console
python -m bt_api_py.service_config_io --role service examples/services/account-service.yaml
python -m bt_api_py.service_config_io --role client examples/services/strategy-readonly.yaml
```

The parser reports `activated: false`; it does not resolve references, create a listener, or start components.

The service owner constructs the already-validated configuration, trusted authority, registry path, and resource provider, then calls the lifecycle methods on one owner thread:

```python
from pathlib import Path
from threading import Event

from bt_api_py.service_components import ServiceResourceProvider
from bt_api_py.service_config import ServiceConfig
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority


def serve_service(
    config: ServiceConfig,
    authority: TrustedServiceAuthority,
    registry_path: Path,
    resource_provider: ServiceResourceProvider,
    stop_event: Event,
) -> None:
    host = ServiceHost(config, authority, registry_path, resource_provider)
    try:
        host.start()
        while not stop_event.is_set():
            host.serve_once(timeout_ms=250)
    finally:
        host.close()
```

`ServiceHost.start()`, every `serve_once()`, and `close()` must run on the same thread. Likewise, construct `ServiceClientRuntime` and call its `start()`, `maintain()`, and `close()` methods on that same owner thread. The loops check the stop signal between bounded polls. A deployment must define how it reports a start or close failure and retains the owner process/thread while shutdown is uncertain; this sample deliberately does not add a restart supervisor. Do not start a second account owner or retry an uncertain write by creating a new command identity. Preserve the original identity and use the supported query/recovery path before an operator authorizes another owner.

`TrustedServiceAuthority` and `ServiceResourceProvider` are deliberately function arguments. The config parser does not create them. Deployment code must resolve the service identity, peer principals, scopes, policy/requirements documents, CURVE key material, provider/account sessions, durable storage, and component resources from approved sources. Never place key bytes or private paths in these YAML files.

## Register a client and retain its summary

`ServiceClientRuntime.start()` returns the public `RegistrationSummary`. Keep and inspect that object; do not synthesize a receipt from YAML. `summary.bindings` is a tuple of `RegisteredBinding` values. Useful public fields include `registration_id`, `service_id`, `instance_id`, `scope_ref`, `service_epoch`, `operations`, `required`, and `active`. The summary also reports `degraded_count` and `degradation_codes` for optional registrations. Record only fields approved for operational logs, and never log keys or secret resolver output.

```python
from threading import Event

from bt_api_py.service_runtime import RegistrationSummary, ServiceClientRuntime


# `runtime` is constructed by trusted application code
# from a parsed ClientConfig and deployment-provided identity/resolvers/transport.
def run_client(runtime: ServiceClientRuntime, stop_event: Event) -> None:
    try:
        summary: RegistrationSummary = runtime.start()
        for binding in summary.bindings:
            print(
                binding.mount,
                binding.registration_id,
                binding.service_id,
                binding.instance_id,
                binding.service_epoch,
                binding.active,
            )
        while not stop_event.is_set():
            runtime.maintain()
            stop_event.wait(timeout=0.5)
    finally:
        runtime.close()
```

The caller supplies the already-constructed `ServiceClientRuntime` and its shutdown event; the excerpt does not invent a runtime factory or standalone launcher. The identity, resolvers, and transport factory are trusted application dependencies, not YAML-loaded imports. The application must call `runtime.close()` on its owner thread during normal shutdown. Inspect the summary after `start()` and apply the application's required-binding policy before using the client. The server's durable registry and admission checks remain authoritative on every request.

## Startup failure, UNKNOWN results, and recovery

If `start()` fails or close cannot be confirmed, keep the existing owner lifecycle state and references available for diagnosis. Do not automatically spawn a replacement owner while the original process/thread or cleanup result is uncertain. The SDK does not provide a generic command to force-recover an unknown owner.

For an operation that returns an unknown outcome, retain its original client/order identity and query or recover that same operation through its authorized service path. Do not issue a blind second write with a fresh identity, infer success from a client timeout, or release a risk hold because the response was lost. Recovery must use the persisted owner/execution evidence and the service's current authorization checks. If the existing API cannot prove a terminal result, leave the outcome unresolved and require the deployment's operator procedure.

For planned shutdown, stop new strategy work, close application clients on their owner threads, then close each service host on its owner thread. Confirm each close result before removing process/control references or allowing a new account owner to start. Keep the registration summary and service epoch with the operational record so a restart can be distinguished from the prior service generation.

## Migrate old forwarding settings

Use the [offline forwarding configuration migration guide](offline-forwarding-config-migration.md) to convert old settings into a draft that requires operator review. That tool does not migrate secrets, authorize a service, resolve policy references, or start a host. Keep the source configuration until the operator has reviewed the generated service and client files.
