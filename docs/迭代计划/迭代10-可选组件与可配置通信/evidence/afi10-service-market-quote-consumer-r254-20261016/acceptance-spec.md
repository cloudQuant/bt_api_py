# R254 acceptance spec: quote-consumer lifecycle and legacy exclusions

This small AFI-only candidate was prepared from current AFI dev HEAD `80d9b8ad5eb48df9e2f01d944d57cee428950a12` and contains exactly the four source/test paths listed in `candidate-partial-index.json`; it excludes all other checkout WIP. The tracked `quote_service.py` parent blob remains `ede3011d4acc59e4d082c1733f2d45ec5faf36c3`, the same source blob recorded by R248; the new adapter and tests are untracked selected paths.

Acceptance requirements:

1. After normal `QuoteService.shutdown()` closes every service-market owner, the same singleton re-reads the unchanged deployment config and creates a fresh owner for the same JWT subject and exact market key. The local service-v2 Host/client/HTTP integration returns a normal quote before and after restart, then closes with no active registration.
2. `get_data_sources()` sends only unconfigured workspace symbols to the legacy receiver/subscription path. A mixed workspace excludes the configured OKX ticker key but retains its unconfigured symbol; when all workspace symbols are configured, no legacy receiver or subscription starts.
3. `get_quotes()` includes running-workspace symbols in service-v2 route discovery even when omitted from the explicit `symbols` argument. A configured workspace-only symbol appears as a service-market row with workspace metadata, while an unconfigured explicit manual symbol retains its legacy subscription behavior.
4. An uncertain owner close retains the exact deployment reference and loaded/error state. The closed/uncertain route returns an error and does not fall back to raw ZMQ, avoiding a second owner or duplicate provider path.

Four focused author-local tests passed: three unit regressions and one real local ServiceHost + SDK client + AFI HTTP integration regression. The integration uses a synthetic provider publishing on the GatewayMarketHub owner thread. Pytest emitted one Windows ZeroMQ Proactor compatibility warning; exit was zero. Ruff lint and scoped `git diff --check` passed. A whole-file Ruff format advisory reports inherited formatting differences in the R248 candidate; its raw output is preserved separately and was not used as an acceptance gate.

Evidence level: `AUTHOR_LOCAL_SYNTHETIC_HOST_UI_ONLY`. No Root QA or formal AFI10 PASS is claimed. This does not prove production OKX WSS queue-to-Hub delivery, a production Host install, a real provider/account, or complete retirement of the legacy quote/runtime path and `GatewayRuntime` manager consumers. Unconfigured and non-OKX legacy behavior intentionally remains while replacement slices are built. No progress document was edited; author did not stage or commit.
