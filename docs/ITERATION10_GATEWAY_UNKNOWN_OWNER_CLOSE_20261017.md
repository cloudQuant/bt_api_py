# Gateway unknown native-owner close recovery — 2026-10-17

Repository: `D:/bt_api_py`, branch `dev`, parent `33e38839e84305d3749e8cba0725a3cf099c53e2`.

This round updates only the Gateway gitlink from `17715155469b6de0e64add45f325ca3b0fad9a28` to accepted child commit `29b09555ae6429c2b40d6bfb69dc923a8a71993d`, plus this record. The child commit contains its account-process implementation, focused tests, and existing round record.

After a connect failure leaves native ownership unknown, the child can call the same retained provider's normal close. A successful native close requires literal true, a positive native generation, and a complete validated stop receipt; unknown ownership is never treated as native-free. The parent exposes only finite protocol and close diagnostics.

Acceptance is source/synthetic only: the initial focused run had seven passes and one fixture assertion failure. After the fixture-only correction, only that failed selector was rerun: one pass in 0.44 seconds, exit zero. Independent static review accepted the final source/test pins. No accepted suite was repeated for parent delivery.

Root verified the child commit's parent, exact three-file scope, final source/test/record hashes, and empty child index before this gitlink update. This does not establish SimNow account acceptance, actual retained-provider closure, or a new formal Iteration10 pass. Retained session 6414 was not accessed, retried, closed, or terminated. All unrelated SDK changes remain outside this round.

The parent commit is identified by the Git commit containing this record; the additive local postcommit receipt records its hash.
