# R254: AFI10 quote-consumer lifecycle and legacy-channel exclusions

Date: 2026-10-16. AFI repository: `D:\source_code\ai-for-investor` / `dev`. Parent SDK repository: `D:\bt_api_py` / `dev`.

This round is based on AFI HEAD `80d9b8ad5eb48df9e2f01d944d57cee428950a12` and fixes only the four exact quote-consumer paths listed in the manifest. Normal owner shutdown now permits the same service singleton to reload config and start a new owner. An uncertain close retains the prior owner and loaded/error state and fails closed. Both `get_data_sources` and `get_quotes` exclude exact service-v2-bound symbols from raw-ZMQ receiver/subscription routing; unconfigured symbols retain the existing legacy behavior. Workspace symbols are included in route discovery before the final quote plan is built.

The author ran only four R254 regression nodes: three unit tests and one synthetic local ServiceHost/SDK client/AFI HTTP integration. Result: 4/4 passed, 0 failed, 0 errors, 0 skipped, pytest exit 0, 3.86 seconds, one Windows ZMQ Proactor compatibility warning. Raw stdout, stderr, JUnit, exact command/environment, lint, whitespace, and scoped diff-check outputs are archived. No R248 suite rerun occurred.

Evidence level is `AUTHOR_LOCAL_SYNTHETIC_HOST_UI_ONLY`. This is not Root QA or formal AFI10 acceptance. It does not prove production OKX WSS-to-Hub delivery, real Host/provider/account installation, or full retirement of the legacy receiver/runtime and manager `GatewayRuntime` consumers. MIG01 owns the SDK owner-thread drain and OKX provider leaf; this AFI round does not edit SDK paths or publish across threads. No progress document was changed. Author did not stage or commit.

The parent SDK checkout HEAD at packet creation is `489354905d914d6a1f2c4fc5353553e3b82ae814` and its cached index was empty: true. Other owners' parent worktree changes are excluded from this packet. AFI cached index was empty at freeze: true.
