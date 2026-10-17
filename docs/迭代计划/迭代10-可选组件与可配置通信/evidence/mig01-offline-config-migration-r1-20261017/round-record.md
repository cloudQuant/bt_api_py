# MIG01 offline legacy-config converter R1

Date: 2026-10-17  
SDK: `D:\bt_api_py`, branch `dev`, fixed source HEAD `1cb885c1504fc0166c33c9a912c75c458f6d31cf`.  
Base dependency: `D:\bt_api_py\bt_api\bt_api_base`, branch `dev`, fixed HEAD `fb47f3dd733613494aa1d86cfd94b2d6fe70314d`.  
Evidence level: `FIXED_SOURCE_OFFLINE_CONFIG_MIGRATION_UNIT_ONLY_NO_HOST_NO_NETWORK`. This does not complete formal MIG01 acceptance.

## Scope

The converter accepts decoded legacy forwarding configuration and an explicit target template. It fills omitted target `account_ref` and `strategy_ref` only from the supplied identity mapping, and rejects any prefilled target value that conflicts. Target scopes, service graph, and a versioned recovery-plan reference must be supplied and validated. Private legacy endpoints and unsupported nondefault legacy semantics fail closed. It does not copy credentials/endpoints, infer authorization or recovery state, start a runtime, or make a draft ready to start.

The public command is `python -m bt_api_py.config_migration`; the guide sets `BT_API_PY_LIGHT_IMPORT=1` on every PowerShell invocation. The selected tests cover positive conversion, rejection behavior, CLI/stdin examples, and a fresh light-mode import guarded against network and process/thread startup.

## Fixed-source QA

Root ran the prepared spec `.codex-tmp/mig01-offline-config-migration-focused-r1.json` (56,314 bytes, SHA-256 `40aafb99dbb9ea00690c3b77eefa35935bfb3ed4acf01e7de9090379bb3ab186`) with `.codex-tmp/iteration10-focused-qa.py` (3,204 bytes, SHA-256 `8b7f470be77270adeb9d72a83776b827c4653004b39835eeca0f43bf63cf24ed`). Output is `D:\temp\iteration10-mig01-config-migration-root-20261017-r1`; command started `2026-10-17T06:23:58.983790+00:00` and ended `2026-10-17T06:24:01.977013+00:00` (2.993223 seconds wall time).

- Pytest: **18 passed**, 0 failed/errors/skipped; JUnit time 2.113 seconds; stdout summary 2.45 seconds.
- Process exit: 0; stderr is empty (0 bytes).
- 271 source SHA-256 pins were unchanged. SDK and Base HEADs were unchanged at the values above.
- stdout: 196 bytes, SHA-256 `703e1f1fafb553a13376cb83d931e7dbdd2e42c331b0f144ba5535aedca78f60`. JUnit: 3257 bytes, SHA-256 `083cd79b6dea57811efcb2925f94877f627ec8c50a2b853d187c4a9a02da02ab`. Result: 787 bytes, SHA-256 `b3a881f3ade6ddf3831a98933a04f873b4b90a496ace4f479e9a071e51d66f11`.
- No Host, account, network, or wheel build/install was performed.

Independent `mig03_partial_delivery_review` static review returned `GO_STATIC`; reviewer did not run tests. The guide's final per-command light-import line is included in the frozen guide pin below.

The earlier author run reported 18 passes and one existing warning but had no raw log and used the live SDK tree while `service_config.py` and `service_deployment.py` had unrelated worktree edits. It is retained as author-reported context only; Root's fixed-source QA above is the accepted evidence for this round.

## Package check and limits

The existing `setup.py` package discovery includes `bt_api_py` and `bt_api_py.*`, so the new module is expected to enter a normal wheel. The actual wheel member check and fresh `system_site_packages=False` CLI check remain pending for the next round; no wheel/install result is claimed here. The prepared commands and dependency caveat are in the pinned QA spec's `package_check_plan`. The prior MIG01 wheelhouse contains only four domain wheels and old SDK/Base revisions, so it is not a suitable complete dependency closure for this candidate.

## Candidate paths

| Path | Bytes | SHA-256 |
|---|---:|---|
| `bt_api_py/config_migration.py` | 21474 | `ee0071b531fe20da3cb29d5b66b7fb321a6ecf811d88d1bf9d2ce6ea809c664c` |
| `tests/unit/test_mig01_offline_config_migration.py` | 10472 | `9493b372aeacb5ef401dd74eac819f96220031ef8b4688cd3a2882241f266b67` |
| `docs/guides/offline-forwarding-config-migration.md` | 3751 | `014f9f1fcc8e9f273d733bf93c1a1cafd7452326a7c3efa0a3e188c9fe30ae59` |
| `docs/迭代计划/迭代10-可选组件与可配置通信/evidence/mig01-offline-config-migration-r1-20261017/round-record.md` | pending self-pin | recorded in the accompanying out-of-band manifest |

No files were staged or committed. The source manifest lists exactly these three new files plus this round record; it does not include unrelated SDK worktree changes.
