# Iteration 51: CTP cancel deadline

- Repository: `bt_api_ctp`, branch `dev`.
- Commit: `32218975940bbb05e3f603e118ade86c3e8689b2` (parent `f6e0a06da5fb28150c0e1faf650e7913c8c5c355`).
- Scope: optional monotonic deadline for direct `submit_order_action`, checked immediately before the native request after SDK lock waits. Existing calls without a deadline remain supported. A confirmed pre-dispatch expiry clears the reservation and request count; an exception after native entry preserves ambiguous evidence.
- Acceptance: `tests/test_ctp_direct_sim_admission.py`, 18 passed with one existing pytest configuration warning; independent source review passed. Tests use a fake native endpoint and a real contended lock.
- Boundary: OS scheduling can intervene between the final check and native entry. This is not real-account or native CTP acceptance. Backtrader must pass the deadline and classify the pre-dispatch expiry before its integration can be accepted.
