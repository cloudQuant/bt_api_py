# Iteration 10 SCN02/013 CTP query-scope fix

Parent repository: `bt_api_py`, branch `dev`. Parent baseline: `abbabad63a0d2a22730d1755c8c2657040223e01`.

This round advances only the CTP package gitlink to child commit `e9ad8b63cbe531be0410e9e38d15ab020f9e117b` and adds this record. The CTP child commit is based on `da4f7badc36ca4a8b7b1020048fb0b3598021f32` and contains the reader fix, two focused regressions, and its package note.

`CtpInstrumentMetadataReader.read()` now validates query start time against the authenticated Trader scope captured before the query. It still checks the post-query scope with `_same_scope` and uses that verified current scope in the result. The regression suite covers a delayed post-query scope capture and a changed Trader generation.

The child focused run used `C:/anaconda3/python.exe -m pytest -p no:asyncio -q tests/test_instrument_metadata.py`: **5 passed**, with one existing warning for the unknown `asyncio_default_fixture_loop_scope` pytest option. Ruff and `git diff --check` passed. Independent static review returned GO.

Root reports the affected SCN02/013 service-entry integration node passed **1 test in 24.68 seconds**. Its preserved output is under `D:/temp/scn02-013-service-entry-e9ad8b63-r1-20261017/`; Backtrader's run record is `D:/source_code/backtrader/docs/iteration10-scn02-013-service-observation-r1-20261017.md`. The E and Risk package links were `4374237705478039d60760e5f68df0e6ed076907` and `5434184d35a10418b2fc53e5ec7f805ee5edc884` before and after this parent change.

Evidence is limited to focused package tests and the reported local synthetic service-entry integration. It does not establish real-account or external-provider acceptance.
