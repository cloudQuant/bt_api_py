# CTP SimNow read-only operator profile

Repository/branch: `D:/bt_api_py`, `dev`. Delivery parent: `74a5094fcfcb2995f5a79f75b423e1b5103864b7`.

This round adds the trusted SDK account-process and MarketHub profiles for the fixed SimNow Set2 7x24 environment. The account profile permits only READ principals, uses the owned CTP account provider, and requires an exact typed native-close receipt with a positive integer connection generation. The market profile uses the existing quote-v2 provider. Credentials remain in the trusted environment and are not part of the profile document or public status.

Scope: `bt_api_py/ctp_account_process.py`, `tests/unit/test_ctp_simnow_operator_profile.py`, the Gateway gitlink, and this record. Gateway advances from `6580a894011d70d3ea747b9d299fbc73390d9796` to `17715155469b6de0e64add45f325ca3b0fad9a28`, including the separately accepted account-process adapter and native-generation projector repair. Existing Base `fb47f3d` and CTP `68a5e11` provide the other dependencies. Independent static dependency review found no dependency on uncommitted SDK, Execution, Risk, or Monitor changes.

Validation: the author reported seven focused tests passed in 1.91 seconds with the existing pytest configuration warning, using Anaconda Python 3.11 and `-p no:asyncio`. The result was reported in the task/tool exchange; a separate raw log was not saved. Independent static review accepted the read-only scopes, profile closure, and integer-generation check. Root verified the final source and test SHA-256 below and did not repeat the accepted tests. This is local component/source acceptance only; no actual login, provider request, native lifecycle, or formal VENUE01/02 PASS is established.

- Source: 20,929 bytes, `66f576a76545117647e733ee323f0f1e71a2c6c032a09c3779b22c6070e25b43`.
- Test: 15,929 bytes, `a5a6517ff92310c0b375b53968e077e777f67d972d687e2bcb2c12c5f532c2f4`.

The scoped commit hash and postcommit scope/index checks are recorded in `.codex-tmp/ctp-simnow-readonly-profile-root-postcommit-20261017.json`. Actual read-only account/market acceptance is a separate next round; trading is not enabled by this profile.
