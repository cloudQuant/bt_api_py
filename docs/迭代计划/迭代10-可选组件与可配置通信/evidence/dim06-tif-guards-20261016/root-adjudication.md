# DIM06 venue TIF guards — Root acceptance record (R209, 2026-10-16)

## Accepted increment

Root accepts the narrow local adapter-contract increment for endpoint-scoped Binance LIMIT time-in-force guards and the OKX market-plus-post-only rejection. This is not a formal DIM06 PASS.

The exact production source blobs delivered on the two package dev branches are:

- Binance bt_api/bt_api_binance/src/bt_api_binance/feeds/rest_trade.py, SHA-256 19d0c782ad440a3d04853fa269595c3b73050a0f1922b3c4a899652f00cf8f86.
- OKX bt_api/bt_api_okx/src/bt_api_okx/feeds/live_okx/mixins/trade_mixin.py, SHA-256 9241d5d9dac48ab75eee9fb03234259aed8bf9a7a18c72506f4e6997bc6859c9.

The accepted child commits are Binance/dev 7d2270ceac3fa6ff2c41e5b8776bdd47ae01526b (parent b438a277c92a22851a16be0be1a8c8ee9f852b79) and OKX/dev fb24657f4b7aa4834935c3dd0c819c1c51f9358f (parent 8c0019db377141664513908b8ff4071643369fd5). Each child postcommit receipt reports the exact two-path scope, matching committed/checkout hashes, and empty index: .codex-tmp/iteration10-r209-bt_api_binance-postcommit.json SHA-256 213eeeaad0286a8a8fc685e79d2fe58b9089ec5c2bc841118c39a3e92ad96c76; .codex-tmp/iteration10-r209-bt_api_okx-postcommit.json SHA-256 3908ca019efe3d8672900794faabbed14dcba29f71eb595781d37a603a37755.

## Root fixed-source run

The new SDK contract test is tests/bt_api_contract/test_dim06_venue_tif_incompatibility_guards.py, 8,571 bytes, SHA-256 71a7c6dda9e13b48a1d1c3f2f1bb40b64207d3b8e725eade328e6ead41aa8489. Root ran it once from D:\temp\iteration10-dim06-tif-guards-root-20261016-r1: 21 passed in 1.36s, pytest exit 0, zero warnings, stderr empty. The 221 canonical source pins matched before and after. SDK parent d400f0f98724554c0aed3f41ebfaffcff4d9e774, Base b4aabd1e17b9c7252fa7b4d266932087d99b5517, Binance pre-delivery HEAD b438a277c92a22851a16be0be1a8c8ee9f852b79, and OKX pre-delivery HEAD 8c0019db377141664513908b8ff4071643369fd5 were unchanged during that run. Child commits subsequently record those exact source blobs.

The tests exercise canonical SDK adapters, venue mappers and order builders through synthetic terminal request spies. Binance coverage includes supported endpoint-specific TIF values, local rejection cases, unchanged market behavior, and native SWAP GTD with its required date. OKX rejects market plus post-only while retaining the valid limit/post-only and market/non-post-only neighbors. No real provider, account, credentials, or network request was used.

The 11 original run files are preserved byte-for-byte in raw-evidence.zip, SHA-256 c3b5ef3c2aec8c1fa59a2b5f68015a372c64bd0c9ab3aba803bc072eb3ad2615, 35,744 bytes. The ZIP contains command.json, exit.txt, junit.xml, result.json, runner.py, running.json, source-before.json, source-after.json, spec.json, stderr.log, and stdout.log. Each extracted entry was hash-checked against the captured source file. The frozen spec and result keep the preparation-time evidence_level string ending in UNEXECUTED; that metadata was not rewritten. The actual run outcome is established by pytest_exit, JUnit, exit.txt and captured stdout/stderr.

## Formal boundary

Formal plan §10 DIM06 (正式迭代计划.md:778) includes CTP trading-day, settlement, reconnect and OrderRef recovery; venue position and margin modes; isolation of supported account-management actions across strategies; UNKNOWN recovery blocking; and explicit rejection of unsupported management. The R163/R191 crosswalks leave margin-pool attribution and three session-path nodes for disposition. This increment closes only the local TIF guard evidence gap. DIM06 remains partial; the official total stays 37/68, with no formal PASS added.

The SDK parent gitlink/test/evidence/progress delivery is scoped for the subsequent parent commit. This record does not claim that parent commit has already occurred. No separate independent-review result is included.