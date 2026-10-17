# Iteration10 CTP pre-start custody delivery

Accepted child: `bt_api/bt_api_ctp` existing `dev`,
`4d0f564d0dca33d535f50f4454212cd36da33bfb`
(parent `68a5e1147cb98ad341b0cae79bc39a24f6a74dd0`).
This SDK round changes only that gitlink and this record. Other work is preserved.

The exact CTP request feed now classifies a failed TraderClient constructor as
native-free before reporting connect failure. Its native API has not started and
no trader has been retained. Custom/uncertain feeds remain unknown, and close
preserves their custody. Gateway's unknown-owner guard is unchanged.

Root final-byte QA: **7 passed / 2.24 s / exit 0**, no warnings, empty stderr,
183 source/config pins and three repository HEADs unchanged. The test uses the
real feed/provider and a failing constructor leaf; it performs no native API
creation/start or account operation. Package imports do not establish native
module absence. Independent static review passed. Root output is
`D:/temp/ctp-pre-native-owner-root-r1-20261017/`; stdout SHA-256 is
`de5e4f54909858d47f2eb339f6d58099634c7c9ffba63805cfcc7be5c6ab9238`.
Full child scope and source pins are in
`bt_api/bt_api_ctp/docs/ITERATION10_PRESTART_NATIVE_CUSTODY_20261017.md`.
Child postcommit verification confirmed the exact three paths, matching blobs,
and empty index. Accepted QA was not repeated for delivery.

Evidence level: source-component regression only. Formal progress remains
**60/68** at this round. VENUE01/02 and the original retained session 6414 are
unchanged; this commit neither resolves nor retries that session. ARB06 continues
independently. DIM07/LOAD01 execution preparation is ready but memory remains
below the existing 10,603,200,512-byte gate; dual-machine cases remain deferred.

The commit containing this record is the parent delivery revision.
