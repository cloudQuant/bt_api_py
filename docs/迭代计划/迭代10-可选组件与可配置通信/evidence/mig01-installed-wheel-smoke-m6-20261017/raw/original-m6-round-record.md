# MIG01 m6 isolated install and local entry checks

Evidence: `FRESH_NO_SYSTEM_SITE_WHEEL_INSTALL_AND_LOCAL_NORMAL_ENTRY_SMOKES`. All artifacts are under `D:\t\m6`.

## Fixed source and wheels

- SDK `35e3879a7c3c43e38fce0bfdacd0668c1f69d6a2`; Base `fb47f3dd733613494aa1d86cfd94b2d6fe70314d`; Backtrader `b9b226be6a82d91bdcb94bf29dfc1ccdad0611c5`.
- Python `3.12.10`; fresh venv `D:\t\m6\venv`; `include-system-site-packages = false`; install used only the local wheelhouse with `--no-index`.
- `backtrader-1.4.0-py3-none-any.whl`: 1867229 bytes, SHA-256 `fdd8aea68d42924f3567df6025b8182b2be356dc3337984845947822a70f76f2`.
- `bt_api_base-0.15.8-py3-none-any.whl`: 151881 bytes, SHA-256 `4ecf13ca2258fa4f6a18e3d337b411dddbc222d33599ae1ced7a65d217297b6b`.
- `bt_api_py-0.15.5-py3-none-any.whl`: 1455969 bytes, SHA-256 `734b16e8442a75b80fbb90a93ef91d93ba25981c933024ff9c6adb44af2f190c`.

- SDK source archive SHA-256: `0e63a6ca890c046a3c7ef39f0a16a551e627f3574a75ce06de9c585033dc787d`; Base source archive SHA-256: `aeafce1a887ca45c7bc1552311865e1a01146ef95eab97e9e841da5903f6dab1`; selected Backtrader archive SHA-256: `7cde5d5d2482454040add31b2db75900175b87ac704847a3b1938d4c6cd5d3f3`.

## Results

- Converter positive: exit 0; generated identity refs `{'account_ref': 'account-a01', 'strategy_ref': 'strategy-s01'}`, status `DRAFT_REQUIRES_OPERATOR_REVIEW`, `ready_to_start=false`, `authorization_granted=false`.
- Converter negative: existing capture verified, not rerun; exit 2, empty stdout, exact `UNSUPPORTED_PRIVATE_CHANNEL at $.legacy_forwarding_config.private_endpoint` rejection.
- Installed operator CLI `bt-gateway-operator --help`: exit 0.
- Installed `BtApi` DirectBackend smoke: exit 0; one local synthetic feed read; no ZMQ import/distribution or network attempt.
- Installed Backtrader offline Cerebro: exit 0; strategy processed 3 in-memory bars; no domain package/ZMQ import or network attempt.
- Import-origin probe: `bt_api_py`, `bt_api_base`, and `backtrader` all loaded from this venv’s `site-packages`; domain distributions and `pyzmq` absent.

## Scope and retained diagnostic

This did not run pytest, ServiceHost, credentials, real accounts, or exchange traffic. AFI’s accepted R5 ServiceV2 route remains separate and was not rerun. The first DirectBackend smoke failed only because its temporary harness assumed an injected feed would be closed by `BtApi.close()`; that unsupported assertion was removed, and the narrowed DirectBackend call passed. The original wrapper’s expected-exit-2 handling failure is retained; negative CLI was only checked from captured output.


Machine-readable result: `m6-final-result.json` SHA-256 `c7aaddcd463511217653855774e805073e2ca66cb9961b80df547daab3079c5f`.