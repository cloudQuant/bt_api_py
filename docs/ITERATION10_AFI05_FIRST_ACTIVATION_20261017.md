# AFI05 account policy first activation

Repository: D:/bt_api_py, branch dev. Delivery parent: 597f4164f525afbd9f84f558efa4f3047d94bc0d.

The accepted change handles only POLICY_NOT_CONFIRMED during trusted account owner startup. It resolves the exact published account policy, binds and reads it back from the durable Risk gate, then persists confirmation. Other resolution failures and conflicting ledgers remain failures. The selected component and test blobs are the independently reviewed candidate based on 7173211; both base blobs and the Risk b6e4a7bc dependency are unchanged at the delivery parent.

The fixed source SQLite test exercised first activation and offline restart: JUnit recorded one test, zero failures/errors/skips; stdout recorded one passed in 1.74s, stderr was empty. All 288 source pins were postverified. The OS pytest exit code is UNKNOWN because the original wrapper failed after pytest while saving that field. No successful process exit is inferred and this accepted test was not repeated. Independent static review: GO_STATIC.

Evidence level: SOURCE_SQLITE_FIRST_ACTIVATION_AND_OFFLINE_RESTART_ONLY. No Host, provider, real account or formal AFI05 case acceptance is added. Fixed QA: .codex-tmp/afi05-first-activation-sdk-qa-r1-20261017. Selected patch SHA-256: 0cb2f5e84e52aa561af1681492a8fd8d8308bc6709ed93cedb6894844ee5f32f.

Only the reviewed index hunks and this record are committed. The existing component worktree has a different unaccepted inline implementation; its bytes are preserved and are not claimed as the tested source. The missing accepted test is appended without replacing existing test edits. Subsequent acceptance must use the committed source or separately validate that worktree composition.
