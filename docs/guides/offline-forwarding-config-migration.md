# Offline forwarding configuration migration

`bt_api_py.config_migration` converts a retired `ForwardingConfig` record into
a **draft** made from the current client and service schemas. It is an offline
configuration tool. It does not load configuration files, inspect credential
stores, contact providers, start services, authorize a client, transfer an
account session, or mark the result ready to start. The documented command sets
`BT_API_PY_LIGHT_IMPORT=1`, which also keeps the package initializer from
importing the optional `BtApi` facade while Python resolves the module.

Run the public synthetic success example from the SDK repository root. The
example intentionally omits `registration.strategy_ref` and the binding's
`account_ref` from the target client template; the explicit identity mapping
fills both. The request also requires a versioned operator recovery-plan
reference. The public parsers and deployment validator check the target graph.

```powershell
$env:BT_API_PY_LIGHT_IMPORT = "1"
python -m bt_api_py.config_migration --example
```

The example prints both request and result. The result contains
`strategy_ref: "strategy-s01"` and `account_ref: "account-a01"`. Its target
endpoint comes from the supplied target profiles; the old command and market
endpoints are never copied. The result remains
`DRAFT_REQUIRES_OPERATOR_REVIEW`, with `ready_to_start: false` and
`authorization_granted: false`.

To convert an operator-authored JSON request, keep the target client template,
target service configurations, and explicit identity/scope mappings in that
request, then run:

```powershell
$env:BT_API_PY_LIGHT_IMPORT = "1"
Get-Content -Raw .\migration-request.json |
  python -m bt_api_py.config_migration > .\migration-draft.json
```

The old account and strategy identifiers must match the mapping exactly. The
converter fills missing target account and strategy references and rejects
conflicts. Every target client binding and service must be present and pass
`parse_client_config`, `parse_service_config`, and
`validate_deployment`. Every scope selector must appear in the explicit
`target_scope_refs` list, which must exactly match the scope references used by
the selected target graph. These checks validate shape and references; they do
not grant the referenced permissions.

`recovery_plan_ref` must be a versioned reference (for example,
`account-a01-manual-reconcile-v1`). The converter does not load or execute that
plan, or transfer legacy recovery state; it records the reference for operator
review and keeps `ready_to_start` false. The operator must verify that the
referenced recovery procedure exists and is suitable before starting anything.

The tool refuses a non-empty legacy private endpoint because there is no safe
equivalent private-channel conversion. It also refuses non-default legacy
market timeout/cache settings because the target contract does not preserve
those semantics. Unknown legacy fields, including credential or recovery
fields, are rejected instead of discarded. Old endpoint strings are treated
only as legacy input and are not emitted. Security profile references,
service activation components, endpoint roles, target scopes, registration
policy and service requirements must be explicitly supplied in the target
configs. Credentials, authorization, live recovery state and writer ownership
are not migrated; resolve and review them separately before any deployment.

The rejection example is synthetic and performs no network or process action:

```powershell
$env:BT_API_PY_LIGHT_IMPORT = "1"
python -m bt_api_py.config_migration --example-reject-private
```

It exits with code 2 and reports only
`UNSUPPORTED_PRIVATE_CHANNEL at $.legacy_forwarding_config.private_endpoint`.
