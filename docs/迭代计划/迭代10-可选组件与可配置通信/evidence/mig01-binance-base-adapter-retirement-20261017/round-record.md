# MIG01 Binance/Base legacy-adapter retirement

Date: 2026-10-17  
Parent repository: `D:\bt_api_py`, branch `dev`; parent HEAD before this round: `002d8d45be73be25d6622f5028bab1120685c9fc`.

## Committed package changes

- `bt_api/bt_api_binance`, branch `dev`: commit `d23492761e7c78502119c541a6693c82ab51388a`. Four paths remove the legacy `BinanceGatewayAdapter` wrapper/export, replace its positive legacy test with a retired-module check, and update the API boundary documentation. `BinanceDirectClient` and the USD-M account-session provider/factory remain.
- `bt_api/bt_api_base`, branch `dev`: commit `09d3ea8b07f8d3f724f2124271a2ac53a0be863e`. Two paths remove the lazy Binance adapter mapping, mark that name retired before the `NoopGatewayAdapter` fallback, and test the failure contract.

The package-focused retired-export checks passed **3/3** in 2.56 seconds. The Base/Binance static review returned GO. Package indexes were empty after each child commit. The SDK parent commit records only these two gitlinks and this round record; unrelated working-tree changes were not staged.

This round retires the Binance legacy wrapper while preserving the direct client and account-provider APIs. It does not remove `bt_api_base.gateway`, prove an isolated install or wheel for this post-retirement source, or complete formal MIG01 acceptance. The prior R264 isolated-load smoke is not evidence for these new package bytes. The remaining IB Web and MT5 adapters still reference Base Gateway; their retirement and the final clean-build/isolated-install/normal-call acceptance remain outstanding.
