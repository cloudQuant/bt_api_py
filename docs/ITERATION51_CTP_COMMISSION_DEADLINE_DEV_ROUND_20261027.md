# Iteration 51: CTP commission-query deadline

The existing `bt_api_ctp` `dev` checkout accepts an optional absolute monotonic
deadline for `query_instrument_commission_rate_result`. The query forwards it
to the existing executor, which applies its deadline contract to query-lock
and rate waits, checks before submission, and bounds callback waiting. Legacy
positional calls remain supported. A synchronous native request already
entered cannot be interrupted; this change does not claim otherwise.

The scoped SDK commit is `be716e7664fa5db8230ed2e502e06997853d2910`, parent
`8b3ce1f1f27aa29725a1891db07aa79db0126be8`, in repository
`D:/bt_api_py/bt_api/bt_api_ctp`, branch `dev`. It changes only
`src/bt_api_ctp/ctp/client.py` and `tests/test_instrument_queries.py`.

Root's targeted source/fake acceptance passed five tests with one existing
pytest configuration warning. It covered contended lock/no late submission,
normal completion, callback deadline, expired deadline/no submission, and
the legacy positional interface. Public source hashes matched before and
after that run. Raw local output is in
`D:/temp/i51-sdk-commission-deadline-root-qa-20261027-r1/`; this is not a
real-account or provider acceptance. Independent source review found the
lock test lacks a scheduling handshake, so it does not independently prove
the worker entered lock acquisition before its short deadline in every run.

The parent `bt_api_py` `dev` update binds only this SDK gitlink and this
record. Backtrader's optional commission sidecar and its complete acceptance
remain a separate round.
