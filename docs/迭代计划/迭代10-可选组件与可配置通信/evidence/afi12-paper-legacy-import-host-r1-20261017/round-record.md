# AFI12 Host-owned legacy Paper import client slice

Date: 2026-10-17
Repository: `D:\bt_api_py`, branch `dev`
Parent before this round: `1ee1c1ea9aefa961f0c555d0487f03bac2511521`

This round delivers only the selected AFI12 Host-owned legacy Paper import wire/client slice. It includes the six approved SDK overlays, the import client and focused Host integration test, the Execution package dependency pinned to `5b83e7e584ff70f851e4b655ace781ab6b68fbf7`, and this record. The selected source payload is bound by `.codex-tmp/afi12-paper-legacy-host-selected-payloads-root-r3/manifest.json` (5,644 bytes, SHA-256 `6a4f09861ce157df46033ceee627dcd3b2abfd091ad321af0e533ca042f26bac`). The six SDK source patches were checked against the current `dev` bases before staging; each selected base blob matched.

The focused Host integration selector is `tests/unit/test_service_paper_legacy_import_host_integration.py` (16,615 bytes, SHA-256 `cc17a3c52c5cdf5fa47c8661252e5668b383587df592dd37692cb979a890d793`). Prior accepted review and focused evidence are reused; this delivery did not rerun the selector. Execution adapter source is pinned by the selected package commit above; its file is 12,878 bytes, SHA-256 `e1f081868a4447eb262ef23011778271a8bfdd6f5946f2cab0c4b0262ce8105e`.

Evidence remains a local Host/client integration slice. The related AFI HTTP integration uses a fixed `get_current_user` test override, so it does not establish JWT authentication; persisted owner-scope lookup and Host-side trusted binding are exercised separately. This round does not close nonempty-import residual rollback/idempotency, nor does it claim formal AFI12 completion or real-user migration acceptance.
