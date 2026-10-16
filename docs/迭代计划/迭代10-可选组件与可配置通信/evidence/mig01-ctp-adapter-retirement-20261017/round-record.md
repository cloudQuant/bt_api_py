# MIG01 CTP legacy-adapter retirement (2026-10-17)

Parent SDK baseline: `D:\bt_api_py` dev `47975b5526c34500b71fdcb49372716df4e9a79e`. This round updates only the Base and CTP package gitlinks plus this record; it does not claim MIG01 completion.

The CTP child committed nine paths on dev as `4a255893da21afd6bc6c05b5abed2df4b75a439a` (parent `1235ac1ab34a1d9520b36ce96aee0b53f18bfaec`): removed `bt_api_ctp.gateway` and its 1,070-line `CtpGatewayAdapter`, plus four adapter-only tests; retained native query/direct-feed behavior and CTP-owned position-dimension projection; added native ticker/account/position projection and retired-entry regressions. Base committed one path on dev as `447671631ac214363d5ae99638cd1f81c7f81322` (parent `6f314acda4cec224843d63ca5bc07d8cea5bdf46`): removed the CTP lazy adapter mapping and made that symbol explicitly retired.

Focused source evidence: author run passed 22 tests with one existing pytest configuration warning. After a test-only portability correction for a missing namespace-package parent, the two retirement tests passed with the same warning; the 22-test set was not rerun. Independent static review returned GO on the final source pins. No Host, account, credentials, or live provider were used.

A clean temporary source copy built `bt_api_ctp-2.0.4+iteration41.i9-cp311-cp311-win_amd64.whl` (5,459,917 bytes, SHA-256 `2abd16d6d46cac3560e10d873366e0d3fe257a432815491132610b334a74bf4e`). Its 93 archive entries contain no `bt_api_ctp.gateway/` members. The first in-checkout build was contaminated by stale `build/lib` files and is excluded. This was a wheel build/member inspection, not an isolated installation test.

The CTP ServiceHost account-provider capability remains unimplemented and is not claimed here. MIG01 remains incomplete; broader old-directory/runtime, provider, installation, and normal-call closures remain separate work.
