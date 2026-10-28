# Iteration 51: direct CTP session recheck

- Repository: `bt_api_ctp`, branch `dev`.
- Commit: `427d71a02ce78c79d3399623302fa81d623485a9` (parent `32218975940bbb05e3f603e118ade86c3e8689b2`).
- Scope: revalidate the direct write capability, account, session, connection generation and request identity under the SDK state lock before native insert or cancel dispatch. Preserve attempt-owned evidence and counters when concurrent calls reuse a request key.
- Acceptance: six focused modules, 217 passed and four deselected, with one existing pytest configuration warning; independent source review passed. One deselected parameter uses a string for an integer-only native field and fails in fixture construction before the production method. Tests use synthetic native endpoints.
- Boundary: the lock is released before the native call; OS scheduling can intervene after the final check. This is not real-account or native CTP acceptance.
