# Iteration 10 MIG01: Base Gateway runtime retirement

Prepared for the `bt_api_py` `dev` parent commit after the queued R280 SDK
round. The selected parent scope is exactly three package gitlinks, one
compatibility test, and this record.

The Base package removes `bt_api_base.gateway` runtime/adapter modules and
their runtime tests, along with the `transport-zmq` optional extra. Its retired
boundary test and package metadata checks remain. The dependent SDK, OKX, and
CTP tests now check that retired Base runtime modules cannot be resolved;
OKX's managed ticker/history provider positives and CTP's provider-module
absence check remain.

Selected package commits:

- Base: `fb47f3dd733613494aa1d86cfd94b2d6fe70314d`, including the separately
  accepted ExchangeID/account-protocol parent `5fed13194f7c060671895a9722943d38dc31ebe0`.
- CTP: `68a5e1147cb98ad341b0cae79bc39a24f6a74dd0`, including the accepted
  account-provider child `c944ef334ae35a7bdbbcd3f93efb7b9262df0237` and the
  existing market-provider source cleanup. This does not claim venue
  acceptance.
- OKX: `6a326d239559089f5cb2e290f1fa1cf1914b2b96`.

Focused retirement checks passed: SDK 3, OKX 2, and CTP 2. The SDK and CTP
runs each reported the existing unknown `asyncio_default_fixture_loop_scope`
pytest configuration warning. The Base source focus previously passed 44
tests with one such warning; it was not repeated. A source-copy wheel smoke
reported 101 members, no Base gateway members, and no `transport-zmq` extra;
its `--target --no-deps --no-index` import check resolved the normal plugin
loader/registry APIs and not the retired runtime. These are local source and
package checks, not Host, account, or formal MIG01 acceptance.

The commit preserves normal direct/plugin and ServiceHost APIs. Remaining
provider capabilities and full clean-install/service-entry acceptance remain
separate MIG01 work.
