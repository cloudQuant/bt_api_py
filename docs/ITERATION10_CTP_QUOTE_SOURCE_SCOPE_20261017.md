# Iteration10 CTP quote source scope

Accepted scope: producer session and receive-clock identity only. This round
does not add a formal case PASS; the official total remains 47/68.

The existing CTP `dev` checkout committed
`d83dfde1b54aca90e21c42e434cc47bb3e2e930a`, parent
`85917b31965039c5e0d973d3bce66070c0dc6280`. Its exact three paths are
`src/bt_api_ctp/feeds/live_ctp_feed.py`, `src/bt_api_ctp/feeds/__init__.py`, and
`tests/test_iter22_contracts.py`. This parent round changes only the CTP gitlink
and this record, from SDK parent `fdd20f1e90b107b976bd2d66d71094878c2ffb1a`.

An opaque scope is issued from the authenticated native MD stream and binds
the stream, native client, callback authority and session generations. Managed
quotes use that session's source domain and captured receive clock. Disconnect,
error, generation change or client replacement invalidates the scope. User
configuration cannot supply source, quality or freshness authority. Exchange
clock mapping, error bounds and Backtrader same-clock qualification remain
unproven; quality stays unknown and freshness remains false.

The focused author run on the committed source reported `4 passed, 104
deselected, 1 warning in 1.13s`; the warning is the existing unknown pytest
configuration option. It used `C:/anaconda3/python.exe` and disabled only the
incompatible asyncio plugin for these synchronous tests. Raw output is retained
at `D:/temp/scn02_ctp_clock_source_focused_pytest_r6.txt` (566 bytes,
SHA-256 `2ecb4ec33272dab545388775b40b4f2e577267d1fd76009c946b8b8478398403`).
Independent source review by `resume_scn_contracts` returned
`GO_SOURCE_REVIEW_ONLY`, including the disconnect invalidation path. Root
verified the child commit's exact scope and the original output bytes; accepted
QA was not repeated. No live provider login or account operation occurred.

The separate CTP metadata reader and Backtrader consumer changes remain their
own rounds. Existing unrelated changes are preserved.
