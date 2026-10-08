# Iteration 41 monitor read-only service R3: accepted dev round

Date: 2026-10-08

## Scope and repository

- Child repository: `D:/bt_api_py/bt_api/bt_api_monitor`, existing `dev`.
- Child commit: `dac72780c33ac181fa90bb4acf7cd4845916543c`.
- Parent repository: `D:/bt_api_py`, existing `dev`; this round updates only
  the monitor gitlink and this record. The parent commit is the commit that
  first adds this record (`git log --diff-filter=A -- this-path`).
- No additional development worktree was created.

The accepted child files are `src/bt_api_monitor/__init__.py`,
`src/bt_api_monitor/durable.py`, `src/bt_api_monitor/readonly_events.py`,
`tests/test_readonly_events.py`, and four exact byte-preservation rules in
`.gitattributes`.

## Accepted behavior

The authenticated service reads an existing SQLite outbox without creating
schemas, appending events, acknowledging events, or updating checkpoints.
Requests bind scope, allowed event types, instance, generation, cursor, and
nonce. Returned payloads are immutable and credential fields are rejected.

R3 reads all allowed types in one ordered SQLite query. This fixes a cursor
race in which separate type queries could skip an event inserted between
queries. A deterministic interleaving regression verifies both events are
returned in sequence without loss.

## Acceptance evidence

Evidence level: **LOCAL_COMPONENT_ONLY**. This round does not establish
provider behavior, real-account certification, TH01, or the optional-component
deployment matrix.

- Author: 21 focused tests passed; broader package compatibility run passed
  135 tests. Its existing environment warnings were recorded separately.
- Independent reviewer: 21 focused tests passed, with source-origin,
  authentication rejection, payload immutability, and checkpoint-isolation
  checks. The spawned synthetic child closed normally with exit code 0.
- Root: 21 focused tests passed in an isolated process, exit code 0, no
  warnings (1.65 seconds). All four source/test pins matched before and after;
  imports came from the explicit monitor/base source roots and QA site root.
  The SDK parent was not imported. Tests included actual local child startup,
  authenticated reads, and normal shutdown using synthetic data.
- Git checkout filters preserve the four reviewed source/test byte streams.

Public evidence packets are in the Backtrader repository under
`docs/_internal/opts/requirements/迭代41-实盘执行风控监控与示例架构重构/evidence/`:

- `iteration41-bt-api-monitor-readonly-events-r3-local-component-2026-10-08/`
- `iteration41-bt-api-monitor-readonly-events-r3-independent-review-oct7f-2026-10-08/`
- `iteration41-bt-api-monitor-readonly-events-r3-root-local-gate-2026-10-08/`

## Resume point

Continue the next separately reviewed TH01 round: typed Root private-storage
custody, monitor ingestion from the actual TradeLogger output, and bounded
complete-account read-only evidence. Preserve unrelated existing changes and
commit the next round only after its agreed checks pass.
