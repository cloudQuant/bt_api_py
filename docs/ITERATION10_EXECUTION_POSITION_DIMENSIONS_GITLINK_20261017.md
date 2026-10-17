# Iteration10 Execution position dimensions dependency

Repository and branch: `bt_api_py`, existing `dev`; parent SDK commit `feb91480a04bb948662d91147eee37383fc17d36`.

This round updates only the `bt_api/bt_api_execution` gitlink and this record. The child advances from `d5563ecb4f0bd057322bec3a72febdc90e1a2d93` to accepted `dev` commit `3462d6d02a12e6d98d1ba47aee6f290d234c7dc7`.

The child adds validated `OrderIntent.position_side` and `position_bucket` fields and preserves legacy default/dated-close payload fingerprints. Committed Paper Risk admission and the pending group roll-exit integration access these fields; the previous committed contract lacked them and raised `AttributeError` before dispatch. Existing live, uncommitted contract changes had hidden this missing dependency in mixed-source tests.

Root's focused child verification passed seven contract cases with exit zero, 131 unchanged source pins, and empty stderr. Evidence: `D:/temp/dim05-e-position-dimensions-r1-output/result.json`, SHA-256 `5c15c6e4643503952bf1cca5769c3e9ad31428549a5607fc82a6e1ffc0cf8164`. Child postcommit record: `.codex-tmp/dim05-e-dimensions-root-postcommit-20261017.json`. The committed `contracts.py` blob is SHA-256 `7ec500ddc04b5fce4551ffaecfc2bfa7fc0b9e85b98106986d1595035e9680d3`; Root verified its bytes equal the accepted candidate after CRLF-to-LF normalization.

Acceptance level: `SOURCE_UNIT_CONTRACT_ONLY`. No tests were repeated for this gitlink delivery, and no Host, provider, real account, or formal Iteration10 case pass is claimed. Next acceptance uses this committed dependency in the real Paper admission and group roll-exit paths. Unrelated SDK and child checkout modifications remain outside this round.

The parent delivery hash is recorded by Git and the additive Root postcommit receipt `.codex-tmp/execution-position-dimensions-parent-postcommit-20261017.json`.
