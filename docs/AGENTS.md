# Documentation maintenance notes

The active documentation must describe the package currently in this checkout, not a historical plugin count or a removed source-tree layout.

- Public runtime wording follows `BtApi -> OperationBackend -> DirectBackend | ZmqBtApiBackend`.
- Plugin and support wording follows `docs/data/exchange_support_matrix.json` and the support-status policy.
- Python 3.11–3.13 are blocking CI targets; Python 3.14 is canary-only.
- New order examples use `OrderRequest`; legacy positional order calls are compatibility examples only.
- A wheel test, a local source import, a submodule diagnostic, and an external release gate are distinct evidence types.

Before changing active documentation, run:

```bash
python scripts/generate_exchange_support_docs.py
python scripts/ci/check_docs_contract.py
mkdocs build --strict
```

Plans, generated receipts, and historical architecture material may stay in the repository, but should not be used as current support claims without fresh evidence.
