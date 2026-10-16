# AFI01 owner scope through ServiceHost — focused candidate R2

R2 changes only the new AFI01 integration test’s cleanup assertion so an absent or malformed ServiceHost close reply cannot be swallowed. The original R1 author pass remains archived separately; R1 is not the final candidate.

The final candidate passed one focused node in 3.76 seconds. It persists two active users and two portfolio composition snapshots in SQLite, authenticates the requests through the real JWT/owner dependencies, runs distinct SDK strategy/read clients and owner workers against a real local SQLite-backed ServiceHost, and uses only a synthetic provider. Each user reads its own ACKED order and portfolio. Forged identity, delegate, tenant/workspace and owner selectors do not switch authority; cross-account/order-scope/portfolio requests are rejected; denied requests add no read-worker operation or ServiceHost gate/provider/query count. The HTTP layer uses `httpx.ASGITransport`, not a TCP listener.

The exact test, command, readback pins, source-root list and limitations are in `source-pins.json`; captured focused pytest output is in `author-focused-run.log`. Candidate test pin: 20,440 bytes, SHA-256 `695c628cee836e611182764c074053d1f2ca393983ac7782cc37cbc60c293ebe`.

The test uses the server-owned `ServiceReadAccessDirectory` map keyed by persisted `User.id`; it does not claim a persisted organization tenant or a durable user-to-account assignment table. AFI07 approver/delegation/replay requirements rely on the already accepted formal AFI07 evidence. This is author-only evidence pending Root source-pin review; it does not assert a Root run or formal AFI01 PASS.
