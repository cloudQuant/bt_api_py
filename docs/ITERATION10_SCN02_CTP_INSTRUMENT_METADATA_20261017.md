# Iteration10 SCN02 CTP instrument metadata read

This source round adds a typed, read-only instrument metadata query through the
existing ServiceHost account-read capability. It is not a formal SCN02 PASS,
live provider/account acceptance, or proof of qualified/fresh quote timing.

The CTP child `dev` commit is `68baeed14daea1f1dc6cc6c1ddfbbd82d329fe10`
and contains only `instrument_metadata.py` plus its focused tests. The Gateway
child `dev` commit is `69e4a2b51052826b150dddb5eca1b3205779826f`; its three
paths add the trusted provider adapter, owner-worker read, and capability-gated
service operation. The parent SDK slice adds the strict client DTO/parser,
runtime operation, only the two account-read capability hunks in
`service_components.py`, and the loopback integration test. The parent checkout
was `b3356f304a3fc482d1f61b85d3b079622d460102` before this selected commit.

The provider binds the query to the exact native CTP request feed, authenticated
Trader generation, and current opaque MD quote-clock scope. The wire DTO keeps
Trader/account/MD generations and query/quote clock identities separate.
Native instrument-query fields provide `PriceTick`, `VolumeMultiple`, and
product class, but no lower/upper price limits; the response marks those
unavailable rather than synthesizing them. Product class is not used to infer
account margin-pool or coverage.

The focused Python 3.11 run used
`PYTHONPATH=D:/bt_api_py;D:/bt_api_py/bt_api/bt_api_base/src;D:/bt_api_py/bt_api/bt_api_gateway/src;D:/bt_api_py/bt_api/bt_api_ctp/src;D:/bt_api_py/bt_api/bt_api_transport/src`
and selected `tests/test_instrument_metadata.py` plus
`tests/integration/test_service_account_instrument_metadata.py`: 4 passed,
exit 0, with the existing pytest config warning and the ZeroMQ Proactor
`add_reader` fallback warning. This is fake-terminal-leaf source evidence; no
live account or provider was used. The independent static review returned GO
for this narrow source chain.

One integration boundary remains open: the separate CTP market quote provider
currently constructs a fresh MD stream, while this metadata response is bound
to the account owner's exact stream. A composed consumer must share one
trusted native stream/source or remain fail-closed; this round does not claim
that metadata and separately-created quote streams match.
