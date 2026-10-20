# LOAD01 10/2 consumer and registration source round

Date: 2026-10-20  
Repository: `D:/bt_api_py`, existing `dev` checkout  
Delivery: pending unified Root review and commit; this record does not claim a commit.

The user authorized reducing the current LOAD01 target from 100 strategies / 10 accounts to 10 strategies / 2 synthetic accounts while retaining the 1,800-second run, U0/N1–N6 profiles, and engineering-stability thresholds. The candidate contract is `LOAD01-10S-2A-R1`, case `LOAD01_LOCAL_SYNTHETIC_10_STRATEGY_2_ACCOUNT`. Reports must carry that identity and exact `load.strategy_count=10` / `load.account_count=2`; old 100/10 reports remain historical evidence and cannot pass this case.

The registration preserves throughput tolerance ±5%, 5-second ordinary RPC timeout and p99 bound, 10-second recovery limits, and the 2 GiB free-memory reserve. The existing gate remains `72 MiB × actual child processes + 2 GiB`: 3,204,448,256 bytes for non-N5 profiles and 3,279,945,728 bytes for N5. N2 retains its 8/2 hotspot split and 3.2/0.8 orders per second; the separate LOAD01 N6 quota domain is `synthetic:load01-n6-hot-wave-v1`. These are proposed engineering contract values, not a full-load result.

Changed scope for this round:

- `scripts/load01_matrix_consumer.py`
- `tests/unit/test_load01_matrix_consumer.py`
- `tests/unit/test_load01_external_source_binding.py` (migrated from the frozen 100/10 registration to the 10/2 contract)
- `docs/evidence/iteration10-load01-engineering-registration-20261020/threshold-registration-r1.json` (136,098 bytes, SHA-256 `ec29c150a0de396e374776258d17a0e33386d3eb5a5b642214f76091b12e4536`)
- `docs/迭代计划/迭代10-可选组件与可配置通信/正式迭代计划.md`
- `docs/迭代计划/迭代10-可选组件与可配置通信/开发与验收进度.md`
- `docs/迭代计划/迭代10-可选组件与可配置通信/迭代10验收状态汇总.md`
- `docs/evidence/iteration10-load01-engineering-registration-20261020/consumer-source-round-record.md`

Initial source round checks: Python compilation passed; the focused consumer tests passed 26/26 before reviewer follow-up; documentation generation exited 0, the documentation contract check passed, and `mkdocs build --strict` exited 0 with existing dependency and navigation notices. The CRLF-aware scoped diff check reported no whitespace errors.

Final reviewer follow-up: `C:/Anaconda3/python.exe -m unittest tests.unit.test_load01_matrix_consumer tests.unit.test_load01_external_source_binding` passed 32/32. Regressions cover empty/truncated/missing matrix selectors, exact N6 20-second window and 30-second deadline in registration and emitted report facts, the exact 0.05 goodput tolerance, and altered registration mappings or raw bytes against the externally trusted digest. Both single-profile and matrix evaluation bind the decoded mapping to the exact raw bytes, then enforce the full registration contract before scoring. The evaluator API now requires `registration_raw: bytes` for both `evaluate_profile` and `evaluate_matrix`; the same bounded raw snapshot must be supplied with the decoded mapping and external Root binding. Report timing facts accept the runner’s finite numeric 20.0/30.0 projection while the registered timing fields remain exact integers 20/30.

No 30-minute LOAD01 run or venue operation was performed. Formal Iteration 10 remains 62/68, with no new PASS. The frozen 2026-10-18 R2 registration and R14 document retain their recorded hashes; the pre-existing modification to `iteration10-load01-n6-quota-source-20261018/round-record.json` was left untouched.

Final consumer source pins: `scripts/load01_matrix_consumer.py` 146,255 bytes, SHA-256 `9a52fff321664717e4a38c52b9c8001d8ed9411801e3840135c45237d1c4e7ed`; `tests/unit/test_load01_matrix_consumer.py` 71,704 bytes, SHA-256 `daf8d562de81f49c1ec9b80e2bc5e3bc90ab5c12a5c3fe426db440d406b68b49`; `tests/unit/test_load01_external_source_binding.py` 8,814 bytes, SHA-256 `d9c1ed320fc198caebd6d4af2c6c63efb53ddf923c47170b6ce76ccaae7e47fe`. The registration remains 136,098 bytes, SHA-256 `ec29c150a0de396e374776258d17a0e33386d3eb5a5b642214f76091b12e4536`. This source round performs no 30-minute run or venue operation; formal Iteration 10 remains 62/68 with no new PASS.
