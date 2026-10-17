# MIG01 Backtrader Store retirement installed-wheel check

Date: 2026-10-17
Evidence state: prepared for Root review; no formal MIG01 adjudication recorded here.

This check builds Backtrader from the exact committed source archive at `6b25ee94ffec14cfad4b511eb6370eba38521c10` (parent `b9b226be6a82d91bdcb94bf29dfc1ccdad0611c5`) and installs that wheel into a fresh Python 3.12.10 virtual environment with system site packages disabled. The offline install used the accepted M6 SDK wheel from source commit `35e3879a7c3c43e38fce0bfdacd0668c1f69d6a2` and Base wheel from `fb47f3dd733613494aa1d86cfd94b2d6fe70314d`. The wheel, tar archive, and venv are external artifacts; their absolute paths, sizes, and hashes are bound in `manifest.json` and are not copied into the evidence ZIP.

The built Backtrader wheel has 532 package members and all 532 match the selected Git archive. The retired-gateway source scan found no hits; `btapistore.py` and `service_execution_session.py` are present. Installed Backtrader, SDK, and Base origins resolve inside the isolated venv. The gateway, execution, risk, monitor, and transport-ZMQ distributions and the `zmq` module are absent.

Three isolated local checks exited 0 with empty stderr: a DirectBackend call using a synthetic feed with ZMQ/network blocked; an in-memory three-bar Cerebro run; and the Store retirement smoke, which rejected retired constructor/environment routes before API construction, preserved direct Store behavior, and exercised a fake read-only service API with zero writes. The installed `bt-gateway-operator --help` command also exited 0 with empty stderr and did not start a service.

The wheel build emitted setuptools deprecation, missing `setuptools_scm` metadata, and Git file-listing warnings. The build exit was 0, and package bytes matched the pinned archive. A prior wrapper attempt in output r1 duplicated `git` in the archive command and exited 1 before producing an archive or wheel. Its exact script, command receipt, and stderr are retained in the ZIP as setup-failure history; r2 is the successful corrected build.

This is local installed-package and synthetic-call evidence. No pytest suite, Host, real ServiceHost RPC, provider, venue, credential, account, or network call ran. The read-only service path uses an injected fake API. The SDK wheel is the specifically pinned M6 artifact, not current SDK dev; this record makes no claim that it represents a later SDK revision. Root adjudication remains pending.

Evidence files:

- `raw-evidence.zip`: 341,037 bytes, SHA-256 `9b94dee3ad912076cc6440b1c09a7c780c9716935f994e00ebcc882df5dfc300`.
- `manifest.json`: 14,690 bytes, SHA-256 `542efc66a86fbc2716d97d220b1901cb982a7dc6de9bb5805a35bb4c2fbdcf42`.

The ZIP contains 31 exact inputs: executed build/install/smoke scripts, command receipts, raw stdout/stderr, wheel inspection, pip install report, installed-package audit, `pyvenv.cfg`, and the small r1 setup-failure record. It excludes the 474 MB source tar, built wheels, and venv.