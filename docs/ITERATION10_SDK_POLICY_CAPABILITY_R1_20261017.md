# Iteration 10 SDK policy-read capability, R1 — 2026-10-17

**Scope.** Restore the two read operations required by the AFI policy HTTP maker/checker grants on the SDK `risk/policy_catalog` port. The `service_components.py` capability declaration and `service_runtime.py` port allowlist both add `policy.inspect` and `policy.confirmation.read`; they remain read-only and are not added to the write-operation set. The focused test validates the component declaration, Runtime allowlist, and maker/checker `ResourceGrant` contracts.

**Source basis.** SDK `dev` HEAD `e17a77020755f527c52456b532a86bbc9decf4ea`. The selected patch changes only those two production files, adds `tests/unit/test_service_risk_policy_capability_contract.py`, and adds this record. No other live changes are part of the patch.

| File | HEAD blob | QA candidate bytes | LF postimage |
|---|---:|---:|---:|
| `bt_api_py/service_components.py` | 140,609 B; `2437e4f0784ce1185eb78b57cc8d7aa013a3116ab6ac98e01a7eef2298e9a4c5` | 143,729 B; `dcc588ce51f8c7af79a52fa0ae120b63c9cf53066a99384b20fe0208f019bc0a` (CRLF) | 140,711 B; `42258e0dce33af4d66fa7a2bf0c4acd44b273972de18bf9d4058686542c98a97` |
| `bt_api_py/service_runtime.py` | 80,321 B; `e9aa275bbd32e6d025aeee4ed7a74b6c321424c49da24ff1ee70b90768c37ed1` | 82,290 B; `6bf4977ca132e746d595b30b5a255990b9d2de3dabe2f0d9e8a3411c4b3687de` (CRLF) | 80,383 B; `1166acdab7948924ec89614fb30c4ab3d75abe3d21d9058319db9d5aab0436af` |

For each production file, normalizing candidate CRLF to LF exactly matches the corresponding HEAD Git blob plus only the two intended operation lines. The delivery patch is generated from those canonical LF blobs; raw candidate hashes are not represented as commit postimages.

**Focused check.** Fixed-source candidate test SHA-256 `262d6beed80b026e1ef7f89ebade8dfca2d6aaedf0172108582d783e80af98ed`; `C:\anaconda3\python.exe -m pytest -q --noconftest -p no:cacheprovider tests/unit/test_service_risk_policy_capability_contract.py`, with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and `PYTHONPATH` limited to the fixed SDK/Base/Risk candidate, completed with exit 0: `1 passed in 1.66s`. Imports resolved from the fixed candidate tree. Output exists only in the Codex tool response; no raw stdout/JUnit file was saved. This was a no-Host capability contract check, not AFI05/06 or R9 end-to-end acceptance.
