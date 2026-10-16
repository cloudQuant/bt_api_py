# Iteration10 market maintenance optimization

This accepted source round reduces repeated work for the validated single
market-hub ServiceHost. Each request still performs the initial maintenance
sweep; an unhandled request or timeout still performs the second sweep.
Mixed-component Hosts and Hosts with upstream dependencies retain the existing
behavior. Registry request dispatch and unregister continue to check expiry.

The predicate requires the exact component types and the already-loaded
canonical market-hub module identity. A same-name duck-typed object is rejected.
The selected change uses no fields from the separate Paper or roll-exit work.

The existing eight-client real CURVE sample reported registry projections
falling from 89 calls / 124.93 ms to 48 calls / 60.94 ms. These are small-sample
measurements, not a throughput guarantee or proof of the previous startup
timeout's cause. Four focused nodes covered the local wire sample, expiry,
unregister, continuing independent streams and topic compatibility. The author
reported all four passing, with existing pytest-configuration and Windows ZMQ
warnings. After the predicate type/identity correction, its affected node alone
passed (1/1, one existing configuration warning); the four accepted nodes were
not repeated.

Independent review by `venue_acceptance_readiness` returned `GO_STATIC` for
the two-path selected patch, 21,885 bytes, SHA-256
`6ce752d50174919d73947981ad9eb006383488858a9013f8bf9cedf69ed9f033`.
It applies only the required imports, `serve_once` and predicate changes in
`bt_api_py/service_host.py`, plus the targeted
`tests/unit/test_service_market_topic_v2_integration.py` changes. The test's
reviewed checkout is 38,186 bytes, SHA-256
`03c17eb0a49a688ebf66967bee44067095bedc346bd0c031876bf7b65da59514`.
The reviewer and Root did not rerun accepted QA. Other shared-file changes
remain outside this commit.

LOAD01 remains incomplete. Its 100 strategy processes, ten account services,
shared hub and Monitor, full 30-minute load, slow consumers and reconnects are
not established by this round. The resource preflight remains in force. The
official formal total remains 47/68.
