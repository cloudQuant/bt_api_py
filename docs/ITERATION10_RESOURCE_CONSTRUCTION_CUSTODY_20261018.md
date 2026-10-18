# Iteration10 resource construction custody — 2026-10-18

Repository/branch: bt_api_py / dev. Parent: `53c83afab1ed656dbe8c7f2ed058a33ab7d51a32`.

When a trusted resource provider fails after creating a partial runtime, its exception can now carry the exact owned ComponentResources. The component builder hands that handle to the Host cleanup tracker before reporting the finite startup failure. Direct callers attempt normal close once; if close or tracking fails, the typed exception keeps the resource reachable for normal retry. Existing unrelated service_components changes are excluded from this round.

Root fixed-source QA: 5 passed / 1.69 seconds, exit zero, no warnings or stderr, no listed external-operation denials. All 430 source pins and five repository HEADs remained unchanged; the selected component module origin matched. Tests cover exact handoff without premature close, direct normal cleanup, failed-close retry, failed handoff, and rejection of borrowed no-op cleanup. Independent static review: GO_STATIC. New-test Ruff passed.

Evidence level: LOCAL_COMPONENT_RESOURCE_CONSTRUCTION_CUSTODY_ONLY. These tests exercise the real component builder and resource wrapper with synthetic failure leaves; they do not start a ServiceHost, CTP native session, or account. Formal acceptance remains 62/68. The runtime composition, real venue acceptance, load test, and deferred two-machine cases remain outstanding.

[Raw QA and exact source archive](evidence/iteration10-resource-construction-custody-20261018/root-qa.zip).

Archive SHA-256: `91786411d115c5e85b7fbfe380519dabe75894dbc619e4e0be223c9be0f88f2d`.

The commit containing this record identifies delivery; the Root postcommit receipt is retained under `.codex-tmp/ctp-resource-custody-root-20261018/sdk-postcommit.json`.
