# bt_api_py

`bt_api_py` provides one `BtApi` façade over installed exchange plugins and an optional ZeroMQ forwarding boundary. The active documentation describes the current package contract; archived plans and historical venue claims are not release guarantees.

## Start here

- [Install and run doctor](getting-started/installation.md)
- [Use the typed API](guides/usage_guide.md)
- [Read the public API reference](reference/core-api.md)
- [Understand direct and ZMQ boundaries](explanation/architecture.md)
- [Read support-status evidence rules](operations/support-status-policy.md)

## Runtime guarantee boundaries

- Direct mode preserves the installed Feed's legacy return values.
- ZMQ mode returns typed snapshots or typed command acknowledgements for its implemented operations.
- `Consistency.LIVE` never silently returns an old market cache entry; `Consistency.CACHE_OK` marks an allowed cache result as stale.
- Missing plugins, unsupported operations, live-query failure, stale-cache absence, and unknown command results are separate domain errors.

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
