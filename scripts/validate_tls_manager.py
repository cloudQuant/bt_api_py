#!/usr/bin/env python3
"""Validate tls_manager tests with ruff and pytest."""

import subprocess
import sys

def run_cmd(name: str, cmd: list[str], timeout: int = 180) -> int:
    """Run a command with timeout and return exit code."""
    print(f"=== {name} ===", flush=True)
    try:
        r = subprocess.run(cmd, cwd='.', text=True, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        print(f"{name} timed out", flush=True)
        return 1
    
    if r.stdout:
        print(r.stdout)
    if r.stderr:
        print("--- STDERR ---")
        print(r.stderr)
    print(f"--- EXIT {r.returncode} ---", flush=True)
    return r.returncode

def main() -> int:
    overall = 0
    
    # 1. ruff check
    rc = run_cmd("ruff-security-compliance-tests", [sys.executable, "-m", "ruff", "check", "tests/test_security_compliance.py"])
    if rc != 0:
        overall = 1
    
    # 2. pytest tls manager tests
    rc = run_cmd("pytest-tls-manager-tests", [sys.executable, "-m", "pytest", "tests/test_security_compliance.py::TestTLSManager", "-v"])
    if rc != 0:
        overall = 1
    
    # 3. coverage for tls manager module
    rc = run_cmd("coverage-tls-manager-module", [
        sys.executable, "-m", "pytest", 
        "tests/test_security_compliance.py::TestTLSManager",
        "--cov=bt_api_py.security_compliance.network.tls_manager",
        "--cov-report=term",
        "--cov-fail-under=0"
    ])
    if rc != 0:
        overall = 1
    
    return overall

if __name__ == "__main__":
    raise SystemExit(main())
