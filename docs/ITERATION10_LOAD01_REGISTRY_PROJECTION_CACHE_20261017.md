# LOAD01 Registry projection parsing cache

Scope: cache only immutable WireIdentity/WireScope values parsed from persisted registration JSON. The key includes the registration ID and both exact JSON strings; an LRU bounded by max_sessions evicts old entries. Every call still reads the current SQLite rows and checks the authoritative epoch, expiration, policy/ref/version and Risk receipt. Changed or malformed JSON takes the original strict parser path. Eligible registration sets, leases and authorization decisions are not cached.

The selected development patch is based on SDK dev 4ce27fba10a3422683bc4cfd9e364443f422d07a and changes only the OrderedDict import, cache initialization and registration policy parser reuse, plus one regression test. Other service_registry.py working changes are excluded. Independent review accepted this exact patch (3ca279de19c377e16c06d7925f8caae879134032204ee2e865d21505a208aac5) and final test (42d4c12f76e1436c96f462488809fa8ce4c45f420a47a66011ea907b132e5d6b).

## Verification and limits

The author's focused fixed-source unit run reported 1 passed in 0.98 seconds, with one existing pytest configuration warning. It covers exact-content hits, current Risk receipt validation, changed and malformed content, expiry and max_sessions=1 eviction. This result is retained as an author console/session summary; no raw stdout or JUnit was saved. Root did not repeat it for delivery. The unit loaded the pinned R10 source plus the cache overlay, not the mixed working Registry file.

A separate instrumented comparison used the same script and 100 synthetic registrations for 2,000 owner projection calls per variant. Baseline projection total was 20.7104793 seconds; candidate total was 2.0620531 seconds. Identity and scope parsing each fell from 200,000 calls to 100. The variants ran sequentially, and tail latency is sensitive to local scheduling; these numbers are a bounded diagnostic, not a service SLO or a whole-application speedup.

- Fixed R10 Registry: 100,410 bytes, SHA-256 2003091ad28031db7deb2ca85f336e66252207fd43c28c6bdf8300edfc23a20e.
- Fixed R10 plus selected cache: 101,703 bytes, SHA-256 421adf49153e90142a02ced2ee0f886f88d2fa68427537fbe905b3abc299e61a.
- Script `.codex-tmp/load01_registry_projection_profile_r1.py`: SHA-256 084223536f10c1dc083816db2fd70ad166ce30cd27f9ef3f8eec0fab1878f598.
- Baseline `.codex-tmp/load01-registry-projection-profile-r2-baseline.json`: SHA-256 b8f77cf1178259a6d8127dc3b6d880d750280223f4e4a198f75dee38d1204ac2.
- Candidate `.codex-tmp/load01-registry-projection-profile-r2-candidate.json`: SHA-256 8c7353fe84172cf6b9830950e1872204c4cc1b00084a826e5635f8482ceabc57.

The comparison did not start a Host, transport, child process or provider. It supports repeated registration decoding as a measured bottleneck; it does not establish the exact cause of strategy-033's earlier 5-second subscribe timeout. LOAD01 still requires the unchanged 100-strategy/30-minute acceptance and normal cleanup. This component round adds no formal PASS. The original R10-r2b failed result remains unchanged, and the next actual pilot is gated by the original available-memory threshold.
