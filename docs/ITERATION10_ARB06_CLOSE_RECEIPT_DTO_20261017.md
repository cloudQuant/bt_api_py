# Iteration 10 ARB06 close-receipt DTO slice

This narrow `dev` source-unit slice contains the Risk close-receipt DTO in
`bt_api_py/service_group_roll_exit.py`, six claim-clock/wire cases in
`tests/unit/test_service_group_roll_exit_claim_clock.py`, and this record. It
does not include a fixture patch, the source-hash distinction case, the Risk
receipt producer, or the group coordinator/source. Those remain outside this
slice.

The historical seven-case run used SDK `dev` parent
`45a44442d4e696d70678e84f68c29ad6df25a1fa`. The original seven-case test file
is preserved byte-for-byte at
`.codex-tmp/afi08-arb06-close-receipt-claim-clock-seven-node-original-20261017.py`
(6,587 bytes, SHA-256
`025908176a69d401a26c7110d2bffa1575a4e2cf46a0f69547a6cd38efc22a6d`). Its
historical command was `python -m pytest -q -p no:asyncio
tests/unit/test_service_group_roll_exit_claim_clock.py`; the console reported
7 passed, exit 0, in 1.57 seconds, with one warning for the unrecognized
`asyncio_default_fixture_loop_scope` option. No stdout, stderr, JUnit,
interpreter-path, or import-origin artifact was saved. The run imported
`_source_and_request` from the mixed worktree helper
`tests/unit/test_service_group_roll_exit.py` (observed post-run at 23,138 bytes,
SHA-256 `72e616d224581834c5d84fba022366916fdad6acb330b14880fafc87a975380a`).
Consequently this is historical local source-unit evidence, not fixed-source
evidence.

The six retained cases are the finite-float claim timestamp wire roundtrip,
two boolean timestamp rejections, and three non-finite timestamp rejections.
Their test functions and local receipt/observation/wire factory ASTs are
unchanged from the preserved seven-case file; only the last source-hash test
and its exclusive `dataclasses.replace`, `GroupRollExitSourceFactsV1`, and
`_source_and_request` imports were removed. The six cases did not use that
mixed helper. The seventh source-hash distinction case is deliberately not
part of this delivery.

The selected DTO source is 80,296 bytes, SHA-256
`4d388076d84699c86c964a3c3492d1eb418bf551ebf81468d04703f6dcab2c9e`. The
current narrowed test is 5,129 bytes, SHA-256
`9140f84847ae3271e48ae2768076bb0470ff8957ac614b5e41b84c1a089f3434`. No test
was rerun for this scope reduction. The six retained cases are a subset of the
historical seven-pass run, but neither that run nor this record binds the
imported production module bytes to an immutable fixed-source export. The delivery parent is SDK dev b958e89a6ad6f8ee194f76911248e5f865462cf6; the selected source pin is unchanged. No Host, provider, or real-account path was exercised.
