# Iteration10 market bootstrap batch source round

Scope: bounded `market.bootstrap_many` (at most 20 subscriptions), SDK `ServiceMarketClient.start_many`, the three SDK operation allowlists, and LOAD01 startup measurement. Gateway implementation is committed at `6580a894011d70d3ea747b9d299fbc73390d9796`. The SDK selection is recorded in `.codex-tmp/load01-market-bootstrap-many-sdk-selected-r1.json`; its patch SHA-256 is `02fb0580bb5a2cc6708d6026c29fa6d7a828457b4d6b462b897d720ee51ff090`.

Every key is authorized and admitted before provider effects. Local subscribers are ready before bootstrap. Legacy fallback occurs only when the authenticated binding lacks the optional batch operation, before dispatch. Uncertain dispatch/reply outcomes retain subscriptions for explicit cleanup. A required source descriptor cannot disappear through an epoch mismatch.

Focused synthetic checks: Gateway batch selection 5 passed; SDK batch selection 3 passed; SDK bootstrap/descriptor selection 7 passed before its distinguishing fixture correction; the corrected required-descriptor regression then passed once. The last regression aligns snapshot and head epochs while retaining the earlier subscription/descriptor epoch, so it distinguishes the repaired guard. Existing pytest configuration warnings are retained. Independent static review accepted the final implementation, regression and selected dependency closure. Accepted checks were not repeated for delivery.

This is source/component acceptance only. No Host, 100-process or 30-minute LOAD01 acceptance is claimed. Workload limits and memory gate remain unchanged. Other SDK and child-package work in progress is excluded. Root's postcommit receipt records the resulting SDK dev commit.
