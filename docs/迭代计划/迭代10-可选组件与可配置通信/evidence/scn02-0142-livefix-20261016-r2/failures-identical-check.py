# -*- coding: utf-8 -*-
"""Successor #2: verify programmatically that the pytest failure/error lists are
identical between the FIXED tree run (unit-tests-out.txt) and the PRISTINE-HEAD
subset run (head-pristine-failing-subset.txt).

Pure text comparison; reads the two files next to this script; never writes to
the repo. Writes only the result to stdout (caller redirects to a file)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

PAIRS = [
    ("unit-tests-out.txt", "head-pristine-failing-subset.txt"),
]

# pytest short-summary lines start with FAILED / ERROR at column 0.
LINE_RE = re.compile(r"^(?P<kind>FAILED|ERROR)\s+(?P<nodeid>\S+)\s*$")

# final counts line, e.g. "38 failed, 854 passed, 13 skipped, 2 errors in 40.18s"
COUNT_RE = re.compile(
    r"^(?P<failed>\d+) failed(?:, (?P<passed>\d+) passed)?"
    r"(?:, (?P<skipped>\d+) skipped)?(?:, (?P<errors>\d+) errors?)? in "
)


def extract(path: Path):
    failed: list[str] = []
    errors: list[str] = []
    counts = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.rstrip()
        m = LINE_RE.match(line)
        if m:
            (failed if m.group("kind") == "FAILED" else errors).append(m.group("nodeid"))
            continue
        mc = COUNT_RE.match(line)
        if mc:
            counts = (
                int(mc.group("failed")),
                int(mc.group("passed") or 0),
                int(mc.group("skipped") or 0),
                int(mc.group("errors") or 0),
            )
    return failed, errors, counts


def main() -> int:
    ok = True
    for fixed_name, head_name in PAIRS:
        f_fail, f_err, f_counts = extract(HERE / fixed_name)
        h_fail, h_err, h_counts = extract(HERE / head_name)
        print(f"== fixed tree: {fixed_name}")
        print(f"   FAILED lines: {len(f_fail)}  ERROR lines: {len(f_err)}  counts: {f_counts}")
        print(f"== pristine HEAD subset: {head_name}")
        print(f"   FAILED lines: {len(h_fail)}  ERROR lines: {len(h_err)}  counts: {h_counts}")

        same_failed_set = set(f_fail) == set(h_fail)
        same_error_set = set(f_err) == set(h_err)
        same_failed_order = f_fail == h_fail
        same_error_order = f_err == h_err

        print()
        print(f"FAILED set identical: {same_failed_set}")
        print(f"ERROR set identical:  {same_error_set}")
        print(f"FAILED order identical: {same_failed_order}")
        print(f"ERROR order identical:  {same_error_order}")

        unique_fixed_failed = sorted(set(f_fail) - set(h_fail))
        unique_head_failed = sorted(set(h_fail) - set(f_fail))
        unique_fixed_err = sorted(set(f_err) - set(h_err))
        unique_head_err = sorted(set(h_err) - set(f_err))
        print(f"FAILED only in fixed tree: {len(unique_fixed_failed)}")
        for nid in unique_fixed_failed:
            print(f"   + {nid}")
        print(f"FAILED only in pristine subset: {len(unique_head_failed)}")
        for nid in unique_head_failed:
            print(f"   + {nid}")
        print(f"ERROR only in fixed tree: {len(unique_fixed_err)}")
        for nid in unique_fixed_err:
            print(f"   + {nid}")
        print(f"ERROR only in pristine subset: {len(unique_head_err)}")
        for nid in unique_head_err:
            print(f"   + {nid}")

        # duplicate detection (paranoia: short summary should list each once)
        dup_f = len(f_fail) != len(set(f_fail))
        dup_h = len(h_fail) != len(set(h_fail))
        print(f"duplicate FAILED lines in fixed/head: {dup_f}/{dup_h}")

        fixed_total = f_counts[0] + f_counts[3]
        head_total = h_counts[0] + h_counts[3]
        totals_match = fixed_total == head_total == len(f_fail) + len(f_err) == len(h_fail) + len(h_err)
        print(f"failed+error totals (fixed/head/listed): {fixed_total}/{head_total}/"
              f"{len(f_fail) + len(f_err)}/{len(h_fail) + len(h_err)} -> match: {totals_match}")

        verdict = (
            same_failed_set
            and same_error_set
            and same_failed_order
            and same_error_order
            and not dup_f
            and not dup_h
            and totals_match
        )
        print()
        print(f"VERDICT ({fixed_name} vs {head_name}): "
              f"{'IDENTICAL failure/error lists' if verdict else 'LISTS DIFFER'}")
        ok = ok and verdict
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
