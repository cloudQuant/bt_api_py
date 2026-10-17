# LOAD01 resource-helper source binding

Repository: bt_api_py existing dev, parent a70a26666c38129046cd20a726f31016ba7c5e13. Scope: two added runner entries, one focused regression, this record and its exact QA archive. Existing runner WIP is preserved and excluded.

The resource diagnostics helper was present in the committed source archive but absent from the runtime manifest and loaded-module checker. The runner now lists its file in the manifest and validates its imported path and digest before load execution. Existing archive, source, workload, resource and cleanup checks remain unchanged. R13 artifacts are untouched; R14 must export a fresh committed parent archive containing the helper as a normal member.

Root ran the focused regression once with the two exact runner functions extracted through AST and fake module/archive leaves. It verifies helper inclusion and rejects both a missing pin and a wrong digest. The accepted result is one passed test, exit zero; source pins and HEAD were unchanged. This is source-binding unit evidence, not a whole-runner import, native lifecycle, pilot or LOAD01 pass. Independent static review covered the two-line change and WIP preservation. Formal status remains 62/68.

QA archive: 4619 bytes, SHA-256 5da043726ecb690c7435f0a94e7a81c8bba4a5d138809a68ffd171ddd93fcf3e. Raw command, output, JUnit and before/after pins are retained inside. The new regression uses ordinary Git text normalization; its staged bytes are checked as the exact LF projection of the tested source. Runner staged bytes are HEAD plus the two accepted lines. All other tracked WIP and the index baseline were checked before staging.
