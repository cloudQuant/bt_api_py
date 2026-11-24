"""Executable acceptance: start five 007 services on Windows and report readiness.

Run from a shell (NOT via pytest collection)::

    PYTHONPATH="<source stub>" python -m tests.integration.win11_five_service_acceptance

Synthetic everything; no real credentials/accounts/venue.  See the companion
``_win11_service_harness.py`` for the reusable API and
``docs/.../Win11安全原语等价适配-2026-11-18.md`` for evidence and boundaries.
"""
from __future__ import annotations

import json
import sys

from tests.integration._win11_service_harness import (
    WINDOWS,
    FiveServiceHarness,
    bootstrap_sys_path,
)


def main() -> int:
    if not WINDOWS:
        print(json.dumps({"status": "SKIP", "reason": "Windows-only harness"}))
        return 0
    bootstrap_sys_path()

    with FiveServiceHarness() as harness:
        print(f"harness root: {harness.root}", flush=True)

        # Self-contained CLI services.
        print("\n[1/4] Monitor...", flush=True)
        mon = harness.start_monitor()
        print(f"  {mon}", flush=True)

        print("\n[2/4] Runtime...", flush=True)
        run = harness.start_runtime()
        print(f"  {run}", flush=True)

        print("\n[3/4] Execution...", flush=True)
        exe = harness.start_execution()
        print(f"  {exe}", flush=True)

        print("\n[4/4] Gateway(dry-run) + Risk(real CLI)...", flush=True)
        gw = harness.start_gateway_dryrun()
        print(f"  gateway dry-run: {gw}", flush=True)
        try:
            harness.start_core(strategies=())
            chain = harness.report().get("risk_chain", {})
            print(f"  gateway+risk chain: {chain}", flush=True)
        except Exception as exc:  # pragma: no cover - diagnostic only
            print(f"  gateway+risk chain FAILED: {type(exc).__name__}: {exc}", flush=True)

        print("\n=== LIVENESS (while all processes are up) ===", flush=True)
        live = harness.liveness()
        print(json.dumps(live, ensure_ascii=False), flush=True)

    # After the context exits, child pipes have been drained; report readiness.
    print("\n=== READINESS (after output drained) ===", flush=True)
    full = harness.report()
    for name, info in full.items():
        if isinstance(info, dict) and "ready_seen" in info:
            print(f"  {name:14s} ready_seen={info['ready_seen']}", flush=True)

    return 0 if all(live.get(k) for k in ("gateway", "risk", "monitor", "runtime", "execution")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
