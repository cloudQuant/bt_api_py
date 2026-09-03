# Support-status policy

## Tiers

| Tier | Meaning | Permitted claim |
| --- | --- | --- |
| `unverified` | Registration, source presence, or historical note only | No capability or operational claim |
| `experimental` | A bounded current artifact proves a narrow scope | State the exact scope and limitations |
| `fully_supported` | Current, complete evidence for the declared scope | Release-quality claim for that scope only |
| `certified` | Maintainer-approved evidence meeting the certified policy | Certified claim for that scope only |

`fully_supported` and `certified` entries require all of: `receipt_path`, `head_sha`, `profile`, `validated_at`, `expires_at`, an existing receipt, and an unexpired timestamp. The documentation contract checker rejects a missing field or expired evidence.

## Evidence boundaries

- A successful package import does not prove a live exchange connection.
- A direct Feed test does not prove the ZMQ transport protocol.
- A source checkout does not prove the wheel users install.
- A historical CI run or a count of registered plugins does not prove current support.
- A scheduled diagnostic run with failures remains useful evidence when every package and stderr log is retained; it does not raise a tier.

Support metadata lives in `docs/data/exchange_support_matrix.json`. Update the evidence first, run `python scripts/generate_exchange_support_docs.py`, then run `python scripts/ci/check_docs_contract.py` before changing public wording.
