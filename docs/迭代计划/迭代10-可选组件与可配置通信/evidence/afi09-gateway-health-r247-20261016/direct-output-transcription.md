# R247 direct-output transcription and evidence limits

**Evidence level:** `SOURCE_FAKE_LOCAL_SYNTHETIC_SERVICEHOST_DIRECT_FUNCTION_ONLY`. This is not pytest evidence and does not close formal AFI09.

The retained task context preserves the final process exit code and two output lines verbatim, but it does not contain a standalone raw stdout capture. The warning messages were retained only as diagnostic descriptions, not exact text. This file therefore distinguishes verbatim lines from summarized diagnostics rather than presenting a reconstructed block as an exact transcript.

Verbatim retained output lines:

```text
All checks passed!
AFI09 focused contract + synthetic HTTP integration: PASS
```

The process exited with code `0`. Between those lines, the process emitted the known Pydantic protected-namespace warning for `model_id` and the known Windows PyZMQ Proactor-loop `add_reader` warning (selector-support thread registered). Their exact original wording is unavailable; these descriptions are not quotations.

The invoked functions were called directly from Python, not through pytest:

- `test_afi09_gateway_health_keeps_missing_owner_status_unobserved()`
- `test_afi09_gateway_health_http_reads_real_owner_facts_without_connecting_again(Path(tmpdir))`

The final combined invocation is retained as tool session `57953`; earlier successful synthetic HTTP function call `97701` is historical. The final call ran both functions and static checks in one process. The test bodies used a local SDK Gateway `ServiceHost` process with synthetic account/provider leaves and FastAPI `ASGITransport`. This was not a live HTTP socket, real account, external provider, or external network call.

The focused result established that the health request returned HTTP 200 for the configured authorized account, exposed explicit owner ready/generation facts, showed a fresh account observation with the same generation, left market-feed health unobserved, and returned no app/server Curve secrets. A different user was denied with HTTP 403. The health read did not reconnect or reread the provider. The synthetic worker's counters were `(connect, read, close) = (1, 1, 0)` before shutdown and `(1, 1, 1)` after normal close. The separate contract call confirmed missing owner status stays unobserved even when the account observation is fresh.

These are direct-function/local-synthetic observations only. The earlier pytest collection attempt did not reach these functions because repository conftest dependency collection failed (`purgedcv` was unavailable; a separate attempt encountered a `.slowapi.env` decoding issue). No pytest pass or test count is claimed. Earlier direct-function fixture attempts with an out-of-range worker interval and separate asyncio loops failed and were corrected before the retained successful invocation; they remain failures, not passes. No real credentials or provider/account access was used, no UI path was exercised, and the synthetic app mounted the router directly.

The candidate patch is preserved in `candidate.patch`. The Root postcommit receipt is `root-postcommit-receipt.json`; it records AFI dev `8943fc0fe15c0c34ed33d7a20e42637e5831e2a3` with parent `80d9b8ad5eb48df9e2f01d944d57cee428950a12`, the exact eight committed paths, `index_empty: true`, and selected checkout hashes unchanged. The receipt explicitly does **not** claim whole-worktree stability.
