# Iteration10 transport extra metadata correction

Repository/branch: `bt_api_py`, existing `dev`; delivery parent `03b1fd110a21124f88879f76ffd4537f79813f9c`.

The `transport-zmq` extra referred to `bt_api_base[transport-zmq]`, although the retired Base wheel no longer declares that extra. This round removes that one obsolete dependency entry. It retains the normal `bt_api_base>=0.15.8` dependency and the dedicated `bt_api_transport_zmq>=0.1.0` extra dependency. Scope is `pyproject.toml` and this record.

Validation used a clean Git export of SDK `feb91480a04bb948662d91147eee37383fc17d36` plus the one-line metadata patch. The later parent commit changes only the Execution gitlink and its record; the packaging inputs are unchanged. Built wheel: `D:/temp/mig01-transport-extra-r1-feb914-clean/dist/bt_api_py-0.15.5-py3-none-any.whl`, 1,421,508 bytes, SHA-256 `4329520d00c6c244bbd358acd8c1dbc43c4574e396bc9a943b15d24489660691`. Its METADATA retains the dedicated transport dependency and contains no obsolete Base extra requirement.

An offline CPython 3.12.10 pip dry-run against the fixed local wheelhouse resolved 43 packages including SDK, Base, transport, pyzmq, and PyNaCl without installation. Report: `D:/temp/mig01-transport-extra-r1-feb914-clean/pip-dry-run-report-py312.json`, SHA-256 `a1e67a9946c942c40187244f1404a327ca0038823bd0d11ea8626ff7ad2bbef6`. The earlier CPython 3.11 resolver attempt could not use the wheelhouse's CPython 3.12 NumPy wheel and is not a pass.

The exact patch, build/resolver records and input/output pins remain under `.codex-tmp/mig01-transport-zmq-extra-r1/`. No accepted test was repeated for delivery. Evidence is `WHEEL_METADATA_AND_OFFLINE_RESOLUTION_ONLY`; this does not prove normal AFI research, BacktestService, installed service execution, real-account behavior, or formal MIG01 completion. Those acceptance paths remain separate work.

The scoped delivery hash is recorded by Git and `.codex-tmp/mig01-transport-extra-parent-postcommit-20261017.json`. Other SDK and package checkout changes remain outside this round.
