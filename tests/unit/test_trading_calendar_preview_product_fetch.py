"""Fixed-source integration with synthetic network substitute; no real HTTP."""

import contextlib
import copy
import hashlib
import json
import os
import subprocess
import sys
import time

import pytest

from bt_api_py import trading_calendar_candidate_inbox as inbox
from bt_api_py import trading_calendar_product_fetch_observation as product_fetch
from tests.unit.test_trading_calendar_preview_catalog import (
    catalog_flags,
    command,
    make_entry,
    run,
    setup_preview,
    show,
    write_cache,
)


def inject_runner(tmp_path):
    inject = tmp_path / "inject"
    inject.mkdir()
    (inject / "sitecustomize.py").write_text("""
import os, sys, json
if "--_worker" in sys.argv:
    from dataclasses import replace
    import bt_api_ctp.calendar_shfe_product_fetch as f
    original_collect = f.collect_product_page
    def fake(*, source_key, **kwargs):
        if os.environ.get("COLLECTOR_MARKER"):
            from pathlib import Path
            Path(os.environ["COLLECTOR_MARKER"]).write_text("called")
        mode = os.environ.get("PRODUCT_MODE", "success")
        if mode == "cleanup":
            import subprocess
            from pathlib import Path
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            Path(os.environ["CHILD_MARKER"]).write_text(str(child.pid) + " " + str(os.getpgid(child.pid)) + " " + str(os.getpgrp()))
            return f.ProductFetchResult("UNKNOWN", "CHILD_CLEANUP_UNCERTAIN", "NOT_OBSERVED", __import__("datetime").datetime.now(__import__("datetime").timezone.utc), None, f.SOURCES[source_key], "SUPERVISED_DEFAULT_TLS_POLICY", "NOT_ASSESSED")
        if mode == "error":
            return original_collect(source_key=source_key, runner=lambda *a: ("TLS_ERROR", None)).__class__(
                "UNKNOWN", "TLS_ERROR", "NOT_OBSERVED", __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
                None, f.SOURCES[source_key], "SUPERVISED_DEFAULT_TLS_POLICY", "NOT_ASSESSED")
        symbol = "BC" if mode == "mismatch" else "" if mode == "missing" else "CU"
        row = {"ContractSymbol": symbol, "ListingExchange": "上海期货交易所", "data_standard": "2023-01-01"}
        if mode == "sensitive": row["note"] = os.environ["SENSITIVE_PATH"].upper()
        body = ("<script>let pageList = " + json.dumps([row], ensure_ascii=False) + ";</script>").encode()
        page = f.parse_shfe_product_page(source_url=f.SOURCES[source_key], html=body)
        result = original_collect(source_key=source_key, runner=lambda *a: ("HTTP_OK", page))
        return replace(result, transport_assurance="SUPERVISED_DEFAULT_TLS_POLICY")
    f.collect_product_page = fake
""")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(inject) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def flags(*extra):
    return ["--retain-candidate", "--trusted-local-posix", "--product-source", "CU_FUTURE", *extra]


@pytest.mark.parametrize(
    "mode,status,reason,transport",
    [
        ("success", "CANDIDATE", "UNVERIFIED_PAGE", "HTTP_OK"),
        ("mismatch", "UNKNOWN", "IDENTITY_MISMATCH", "HTTP_OK"),
        ("missing", "UNKNOWN", "IDENTITY_INCOMPLETE", "HTTP_OK"),
        ("error", "UNKNOWN", "FETCH_FAILED", "TLS_ERROR"),
        ("sensitive", "UNKNOWN", "SENSITIVE_CONTENT", "HTTP_OK"),
    ],
)
def test_cli_retains_fixed_source_with_unverified_status(tmp_path, mode, status, reason, transport):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject_runner(tmp_path)
    env["PRODUCT_MODE"] = mode
    env["SENSITIVE_PATH"] = str(config)
    result = run(command(root, config, supplement, *flags()), env=env)
    assert result.returncode == 10, result.stderr
    report = json.loads(result.stdout)
    assert report["candidate_status"] == "COMMITTED"
    doc = show(root, report["candidate_id"])
    assert doc["schema"].endswith("/4") and doc["payload"]["catalog"] is None
    product = doc["payload"]["product_fetch"]
    assert (product["status"], product["reason"], product["transport_status"]) == (
        status,
        reason,
        transport,
    )
    assert product["source_authentication"] == product["retained_data_binding"] == "NOT_PROVEN"
    assert product["coverage"] == product["current_record"] == "UNKNOWN"
    assert doc["payload"]["content"]["trading_days"] and not (root / "CURRENT.json").exists()
    if mode == "mismatch":
        assert product["identity_status"] == "SOURCE_FIELDS_MISMATCH" and product["page"]["records"]
    if mode == "missing":
        assert (
            product["identity_status"] == "SOURCE_FIELDS_INCOMPLETE" and product["page"]["records"]
        )
    if mode == "sensitive":
        assert product["page"] is None and str(config).casefold() not in json.dumps(doc).casefold()


def test_v4_can_also_retain_unverified_local_catalog(tmp_path):
    root, cache, config, supplement = setup_preview(tmp_path)
    write_cache(cache, [make_entry("rb2701")])
    env = inject_runner(tmp_path)
    result = run(
        command(root, config, supplement, *catalog_flags(cache), "--product-source", "CU_FUTURE"),
        env=env,
    )
    assert result.returncode == 10, result.stderr
    doc = show(root, json.loads(result.stdout)["candidate_id"])
    assert doc["schema"].endswith("/4")
    assert doc["payload"]["catalog"]["status"] == "CANDIDATE"
    assert doc["payload"]["product_fetch"]["status"] == "CANDIDATE"


def test_sensitive_path_with_json_escaping_is_not_retained(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    special = config.with_name('config"quoted.yaml')
    special.write_text(config.read_text(encoding="utf-8"), encoding="utf-8")
    env = inject_runner(tmp_path)
    env["PRODUCT_MODE"] = "sensitive"
    env["SENSITIVE_PATH"] = str(special)
    result = run(command(root, special, supplement, *flags()), env=env)
    assert result.returncode == 10, result.stderr
    product = show(root, json.loads(result.stdout)["candidate_id"])["payload"]["product_fetch"]
    assert product["reason"] == "SENSITIVE_CONTENT" and product["page"] is None


@pytest.mark.parametrize(
    "extra",
    [
        ["--product-source", "CU_FUTURE"],
        ["--retain-candidate", "--product-source", "CU_FUTURE"],
        [
            "--retain-candidate",
            "--trusted-local-posix",
            "--product-source",
            "https://example.invalid/",
        ],
        [
            "--retain-candidate",
            "--trusted-local-posix",
            "--product-source",
            "CU_FUTURE",
            "--product-page-url",
            "https://example.invalid/",
            "--product-page-file",
            "/tmp/secret",
        ],
    ],
)
def test_rejects_incomplete_or_mutually_exclusive_options(tmp_path, extra):
    root, _, config, supplement = setup_preview(tmp_path)
    result = run(command(root, config, supplement, *extra))
    assert result.returncode == 2 and "/tmp/secret" not in result.stderr + result.stdout
    assert not (root / "inbox").exists()


def test_no_explicit_source_never_calls_product_collector(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject_runner(tmp_path)
    env["PRODUCT_MODE"] = "error"
    marker = tmp_path / "collector-called"
    env["COLLECTOR_MARKER"] = str(marker)
    result = run(
        command(root, config, supplement, "--retain-candidate", "--trusted-local-posix"), env=env
    )
    doc = show(root, json.loads(result.stdout)["candidate_id"])
    assert doc["schema"].endswith("/1") and "product_fetch" not in doc["payload"]
    assert not marker.exists()


def test_invalid_and_tampered_fetch_cannot_claim_success(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject_runner(tmp_path)
    result = run(command(root, config, supplement, *flags()), env=env)
    cid = json.loads(result.stdout)["candidate_id"]
    path = root / "inbox" / f"candidate-{cid}.json"
    original = json.loads(path.read_bytes())
    for field, changed in [
        ("source_authentication", "PROVEN"),
        ("identity_status", "SOURCE_FIELDS_MISMATCH"),
        ("transport_status", "TLS_ERROR"),
        ("status", "UNKNOWN"),
    ]:
        data = copy.deepcopy(original)
        data["payload"]["product_fetch"][field] = changed
        data["payload_sha256"] = hashlib.sha256(inbox._canonical(data["payload"])).hexdigest()
        path.write_bytes(inbox._canonical(data))
        with pytest.raises((ValueError, TypeError)):
            inbox.read_candidate(root=root, candidate_id=cid, filesystem="TRUSTED_LOCAL_POSIX")


def test_insufficient_budget_skips_network():
    data = product_fetch.fetch_observation("CU_FUTURE", total_timeout=0)
    assert (data["status"], data["reason"], data["transport_status"]) == (
        "UNKNOWN",
        "BUDGET_EXHAUSTED",
        "NOT_ATTEMPTED",
    )
    product_fetch.validate_fetch(data)


def test_provider_cleanup_uncertain_kills_group_and_preserves_unknown_candidate(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject_runner(tmp_path)
    marker = tmp_path / "cleanup-pid"
    env["PRODUCT_MODE"] = "cleanup"
    env["CHILD_MARKER"] = str(marker)
    result = run(command(root, config, supplement, *flags()), env=env)
    assert result.returncode == 25, (result.stdout, result.stderr)
    report = json.loads(result.stdout)
    assert report["candidate_status"] == "COMMIT_UNKNOWN"
    assert report["reason"] == "WORKER_CLEANUP_UNCERTAIN"
    doc = show(root, report["candidate_id"])
    product = doc["payload"]["product_fetch"]
    assert (product["status"], product["reason"], product["transport_status"]) == (
        "UNKNOWN",
        "CHILD_CLEANUP_UNCERTAIN",
        "CHILD_CLEANUP_UNCERTAIN",
    )
    pid, child_group, worker_group = map(int, marker.read_text().split())
    assert child_group == worker_group
    for _ in range(30):
        proc = subprocess.run(
            ["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
        )
        if proc.returncode != 0 or not proc.stdout.strip() or proc.stdout.strip().startswith("Z"):
            break
        time.sleep(0.1)
    else:
        pytest.fail("unreaped provider process remained running")
    assert not (root / "CURRENT.json").exists()


def test_direct_worker_session_leader_does_not_kill_same_group_sentinel(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject_runner(tmp_path)
    marker = tmp_path / "direct-pid"
    env["PRODUCT_MODE"] = "cleanup"
    env["CHILD_MARKER"] = str(marker)
    args = {
        "config_file": str(config),
        "calendar_root": str(root),
        "supplement_file": str(supplement),
        "budget_seconds": 3,
        "enable_shfe": False,
        "gateway_enabled": False,
        "direct_write_enabled": False,
        "max_retries": 0,
        "retry_interval_seconds": 0,
        "candidate_id": "a" * 32,
        "trusted_local_posix": True,
        "product_source": "CU_FUTURE",
        "product_deadline": time.monotonic() + 2,
    }
    worker = subprocess.Popen(
        [sys.executable, "-m", "bt_api_py.trading_calendar_preview", "--_worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    pid = None
    try:
        stdout, _ = worker.communicate(input=json.dumps(args).encode(), timeout=10)
        assert worker.returncode == 10
        report = json.loads(stdout.splitlines()[-1])
        assert report["candidate_status"] == "COMMIT_UNKNOWN"
        assert report["candidate_reason"] == "CHILD_CLEANUP_UNCERTAIN"
        pid, child_group, worker_group = map(int, marker.read_text().split())
        assert child_group == worker_group == worker.pid
        os.kill(pid, 0)  # sentinel is still alive: the worker did not signal its group
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=5)
        if pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, 9)


def test_total_timeout_kills_worker_and_nested_provider_process_group(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    inject = tmp_path / "inject"
    inject.mkdir()
    marker = tmp_path / "child-pid"
    (inject / "sitecustomize.py").write_text("""
import os, sys, time
if "--_worker" in sys.argv:
    import bt_api_ctp.calendar_shfe_product_fetch as f
    from pathlib import Path
    original = f.subprocess.Popen
    def slow(cmd, **kwargs):
        child = original([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        Path(os.environ["CHILD_MARKER"]).write_text(str(child.pid) + " " + str(os.getpgid(child.pid)) + " " + str(os.getpgrp()))
        return child
    f.subprocess.Popen = slow
    original_selector = f.selectors.DefaultSelector
    class StuckSelector(original_selector):
        def select(self, timeout=None):
            time.sleep(30)
            return []
    f.selectors.DefaultSelector = StuckSelector
""")
    env = os.environ.copy()
    env["CHILD_MARKER"] = str(marker)
    env["PYTHONPATH"] = str(inject) + os.pathsep + env.get("PYTHONPATH", "")
    cmd = command(root, config, supplement, *flags())
    cmd[cmd.index("60")] = "3"
    result = run(cmd, env=env)
    assert result.returncode == 25, (result.stdout, result.stderr)
    assert json.loads(result.stdout)["candidate_status"] == "COMMIT_UNKNOWN"
    pid, child_group, worker_group = map(int, marker.read_text().split())
    assert child_group == worker_group
    for _ in range(30):
        proc = subprocess.run(
            ["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
        )
        if proc.returncode != 0 or not proc.stdout.strip() or proc.stdout.strip().startswith("Z"):
            break
        time.sleep(0.1)
    else:
        pytest.fail("nested provider process remained running")
    assert not (root / "CURRENT.json").exists()
