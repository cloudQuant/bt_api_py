# DIM07 Gateway quota binding r1

- Scope: expose the Gateway account binding's existing quota owner through one stable, explicit `dispatch_quota_admission` callback; add its focused binding test and update the Gateway gitlink. No implicit propagation is added.
- Gateway child: `bt_api_gateway/dev` commit `3e303e103781b942e4425e55d856bb09cf583d76` (includes the quota adapter and account-scoped manual bucket compatibility).
- SDK parent: `bt_api_py/dev`, base `98f0b15680077a641036e63c25b1210a2dc39e7c`; the resulting delivery commit is recorded in Git history.
- Candidate pins: `bt_api_py/gateway_operator.py` 34,569 B / `9A089B31C7A2EDB4272FFC4D7A5394177E58599E2A8784A4638EDF50DEA460A9`; `tests/integration/test_gateway_quota_admission.py` 21,203 B / `B84CE6322287104193A91EEA85F12EB691EDD52BE145BC3588718798B35F5706`.
- Focused synthetic check: `test_account_binding_exposes_one_explicit_dispatch_callback`, 1 passed. Independent static review: GO_STATIC for the exact source/test pins.
- Evidence boundary: this is callback-binding evidence only; it does not establish Risk/Execution injection, deployment Host behavior, provider/account behavior, or formal DIM07 acceptance.
