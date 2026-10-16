# Iteration 10 Risk policy binding source integration R1

Parent repository: `bt_api_py`, branch `dev`, starting commit `7ac9b3b340b204ffcd382f9b8c1d3ff6584bbd5b`.

This parent round advances only the Risk submodule pointer to the accepted child source round and records its boundary. Risk child commit: `489ef3d9a319356e72ccab4082540a63d3692d2f`, parent `5434184d35a10418b2fc53e5ec7f805ee5edc884`. The child round adds the immutable `DurableRiskPolicyBinding` readback and `DurableRiskGate.bind_policy(scope)` to the public Risk API, with SQLite restart/reservation-preservation regression coverage.

The parent scope is exactly:

- `bt_api/bt_api_risk` (gitlink only)
- `docs/ITERATION10_RISK_POLICY_BINDING_R1_20261017.md`

The child’s two focused SQLite tests passed against the fixed Risk/Base source export. The corrected AFI fixed-source R3 candidate cold-imported the binding/resolver production modules from that candidate and collected its one AFI HTTP/Host selector; that collection did not execute the test body or start a Host. No tests were rerun for this parent gitlink update. No provider or real account was used. The round does not claim AFI05/AFI06 formal acceptance or `consumer_effective=true`.
