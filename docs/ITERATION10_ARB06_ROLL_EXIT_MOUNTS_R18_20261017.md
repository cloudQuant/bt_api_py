# Iteration 10 ARB06 roll-exit mount correction — R18

This small SDK round gives the registered group roll-exit RPCs their dedicated upstream mounts. The coordinator must route `groups.roll_exit` and `groups.query_roll_exit` to the registered Risk roll-exit operations, rather than borrowing the ordinary order-entry and order-query mounts.

The selected SDK change is limited to `bt_api_py/service_execution_group.py`:

- `_ROLL_EXIT_ORDERS_MOUNT`: `_ORDERS_MOUNT` → `upstream.group_roll_exit_orders`
- `_ROLL_EXIT_QUERY_MOUNT`: `_ORDER_QUERY_MOUNT` → `upstream.group_roll_exit_order_query`

The source-selection base is SDK `dev` HEAD `33bdcb17c57d8e16c82a53e691ae09d2df820d9e`; the base Git blob for the source file is `fed2d2d5ac916cc0efafb786a91a075cb87adda7`. The selected source postimage is 207,144 bytes, SHA-256 `5b8bc8eed89506e3b7768a92bf38fe79e6cf6808f36bacd240a807ad362e534c`, Git blob `6335c64d217e7e9636832d8ca629ec459cae361e`. The working checkout contains unrelated changes in this mixed file; they are outside this round. The selected patch contains only the two assignments above.

## R18 evidence

Root's R18 run passed the single local synthetic Cerebro roll-exit integration node: 1 passed, exit 0, 19.60 seconds, with three known warnings and empty stderr. The fixed spec was `D:/temp/arb06-group-roll-exit-cerebro-abe0-4f3-fixed-r18-query-diagnostic-20261017/host-spec-r18.json` (183,356 bytes, SHA-256 `6e9dd1724df7571ac097b21221b647fd1d35a62461e02e9dfeb45232537323a0`) and pinned 900 source inputs. It bound SDK HEAD `33bdcb17c57d8e16c82a53e691ae09d2df820d9e` and Backtrader HEAD `6b25ee94ffec14cfad4b511eb6370eba38521c10`.

The test exercised the coordinator-to-registered-Risk roll-exit route, one synthetic terminal CLOSE fill and its receipt, and the public same-action query/recovery path. It observed one Risk provider call, zero remaining source-strategy closable exposure, and the peer strategy's `0.75` allocation preserved. The host cleanup receipt reports all hosts closed and joined, zero cleanup errors, and `cleanup_verified=true`. The R18 SDK source overlay also contained test-only query diagnostics; those diagnostics are not part of this selected production change.

The run does not establish a closed aggregate order group: the durable group snapshot remained `PAIRED` at revision 18 while the roll-exit action and source exposure were resolved. It is local synthetic evidence only. Formal ARB06 remains **HOLD**. The earlier R17 `FIRST_LEG_QUERY_UNKNOWN` was intermittent and its cause remains unconfirmed; R18 does not claim to have diagnosed or eliminated that condition.

This round has two paths: this record and the exact two-assignment source patch. The Backtrader integration-test file and its canonical dependency closure are being reviewed separately.
