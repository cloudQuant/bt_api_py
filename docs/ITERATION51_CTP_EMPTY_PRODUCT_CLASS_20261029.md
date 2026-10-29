# Iteration 51: CTP empty product class

- Repository: `bt_api/bt_api_ctp`, branch `dev`, commit `6c7d9b7` (parent `427d71a`).
- Scope: interpret NUL readback as an empty intent only for `CThostFtdcQryProductField.ProductClass`; other fields and nonempty values retain strict checking.
- Acceptance: 3 focused cases and all 35 `test_ctp_g5_source_unification.py` cases passed using a real local SWIG field and fake request leaves. Independent source review passed. No provider or account operation was performed.
- Broader two-file run: 99 passed and one unrelated managed-cancel fixture failed at an `OrderActionRef` SWIG setter before the query path. This is not a full-suite pass.
- Explicitly empty depth ProductClass and margin/option HedgeFlag char filters remain outside this round and may still fail strict readback. No real provider acceptance is claimed.
