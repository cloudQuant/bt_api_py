# MIG01-R1 independent review — 2026-10-16 (read-only)

## Verdict: GO

Scoped to the R1 increment only (retired SDK entry points fail closed). NOT a MIG01
acceptance claim: both legacy dirs remain, wheel 0.15.4 still ships 19 legacy entries,
residual tracker still 168/36.

## Verified (evidence)
- Artifact integrity, r2 (final): all 7 spec.json:77-83 pins match current worktree bytes
  (re-hashed 10:35); stdout sha256 93af382b matches result.json:15; junit 83/0/0/0;
  sources_unchanged/heads_unchanged true (result.json:5,14); source-before==source-after;
  scan JSON hashes match (result.json:39-43); runner.py:52-58,237 pins/guards; r1 consistent
  (only artifact id/output/test_bt_api_quality pin/times differ).
- Retired entry points raise before side effects: bt_api_py/__init__.py:225-230 (no legacy
  import), bt_api_py/bt_api.py:3646-3658 reached at :988 before plugins/feeds (:990-996);
  probe: threads 1→1, no bt_api_py.forwarding/gateway/zmq in sys.modules; direct mode OK.
- Error class stable/exported with target+detail: _contracts/errors.py:135-153,
  _contracts/__init__.py:16,79, bt_api_py/__init__.py:85,383; identical class object both paths.
- Star-import raises LegacyForwardingRetiredError (target=ForwardingClient); names enumerable
  in __all__ (bt_api_py/__init__.py:331-340). Named import fails loudly.
- In-surface tests only: test_bt_api_quality.py:189-197, test_direct_zmq_bt_api_contract.py:37-53,
  test_normalized_public_api.py:782-796; no conftest/skip hacks; full test_normalized_public_api.py
  still 86 passed.
- Breakage matrix: all 4 modules positively rely on retired path (dispatch _zmq_api:94-105;
  env_guard:674-735; session:3671-3679×3; gateway_runtime fixture:22-48). No missed module
  (all BtApi-ZMQ construction sites enumerated). Counts: env_guard 3 ✓, session 3 ✓,
  gateway 14 ✓; dispatch observed 14 failed+2 skipped (pinned anaconda env) / 15 failed
  (working asyncio) vs claimed 17 → overstated; test_direct_legacy_async...:279 does NOT rely
  on the legacy path (passes with asyncio plugin). Total observed 34 (pinned env; 20 failed
  +14 errors) vs claimed 37.
- Residual inventory reproducible: 59+6+12+9+33+49=168 per-pattern refs, 36 files; wheel 19/145,
  egg-info 68+22, CI hit lines all re-verified. Unique-line union is 155 (168 is per-pattern sum).
- No security hole: change is strictly fail-closed (no check removed); no auth surface touched.
- Minimality: only the 7 R1 files differ for R1; no incidental rewrites.
- Other repo state: DIM01 diffs unchanged/no MIG01 content; risk submodule dirty = risk WIP.

## Findings / conditions (non-blocking)
1. Status.md line-counts to correct: 37→34-35 matrix nodes (dispatch 17→14-15; drop the
   direct-async attribution); errors.py "21"→19 lines.
2. HEAD moved during review: 99bec079→4e46ff01→5ff3da11 (unrelated DIM01 commits; neither
   touched R1 files, pins still byte-identical). QA head anchor is therefore stale — re-run
   the fixed-source QA (or re-pin) on the commit-time tip so the receipt names the real parent.
3. r1→r2 "lint-only rework" claim not directly verifiable from kept artifacts (r1 file content
   not retained); r2 pins are authoritative and match.
4. Scan trees exclude gitignored build/ (stale copy still has _FORWARDING_EXPORTS); harmless.
