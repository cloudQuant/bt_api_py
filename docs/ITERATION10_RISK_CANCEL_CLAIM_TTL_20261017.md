# Iteration 10: CANCEL claim expiry under SQLite lock wait

The SDK now pins Risk dev `888745c48aceb9ff4c60d2db720684ef075ef1d3` (parent `d936a6ccc8e283d8d71eab68790574e30e007fa3`). The child round rechecks the current clock and CANCEL permit expiry after acquiring the SQLite writer transaction and immediately before dispatch-claim commit. A permit that expired while waiting cannot become a valid claim.

The accepted focused SQLite/fake-clock regression passed one test, with one existing pytest configuration warning. Independent static review returned GO. The regression holds a real writer transaction, advances the injected clock beyond the permit TTL, and observes `PERMIT_NOT_ACTIVE`, no persisted dispatch claim, and no freeze. No accepted QA was repeated for this gitlink delivery.

Scope: the Risk gitlink and this record only. The child commit contains three selected admission hunks, its regression, and its round record. Other Risk and SDK work in progress is preserved. Evidence is local synthetic SQLite/clock; this does not establish real-provider acceptance or complete AFI08.
