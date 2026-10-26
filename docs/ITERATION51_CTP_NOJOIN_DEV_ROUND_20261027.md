# Iteration 51 CTP shutdown source round

The existing dev checkout of bt_api_ctp committed the explicit nonblocking no-Join lifecycle as `574cd2dd495a3c675d7cd23fcbf23a797a08c04d` (parent `48b476ea90665791c7d2f86935720ce238931520`). This parent round updates only that gitlink and this record.

`MdClient.start` and `TraderClient.start` accept `join_observer=False` only with `block=False`; default Join behavior is preserved. API/SPI ownership, startup/request/callback drain, one Release and exact deferred-owner completion are required for a complete stop receipt. Failed or uncertain cleanup remains incomplete.

Acceptance: independent static GO, 72 default regression passes plus one optional missing-runtime-module skip, and 37 distinct focused fake-native cases across separate runs. The final test change removed one unused local and its two affected cases passed again; it was not a single 37-case invocation. All pytest runs recorded one existing unknown-config warning. Exact source/test pins and original diagnostic/QA outputs are in the submodule's [round record](../bt_api/bt_api_ctp/docs/evidence/iteration51-nojoin-lifecycle-20261027/round-record.json).

The existing native SDK primitive was separately tested on new closed-loopback APIs and released normally; that does not accept this new client implementation, example integration or any account case. Installed candidate testing and example wiring remain next. Specialized OneShotMdDiagnosticClient does not expose the new option. No retained host was operated and no push was performed.
