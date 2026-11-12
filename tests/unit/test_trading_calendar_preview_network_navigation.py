"""Explicit /6 navigation fetch and detail batching with synthetic providers only."""

import copy
import hashlib
import json
import os
import subprocess
import time

import pytest

from bt_api_py import trading_calendar_candidate_inbox as inbox
from bt_api_py import trading_calendar_navigation_fetch_observation as observation
from tests.unit.test_trading_calendar_detail_batch import FUTURE, OPTION, batch_command
from tests.unit.test_trading_calendar_preview_catalog import (
    SCOPE,
    catalog_flags,
    command,
    make_entry,
    run,
    setup_preview,
    show,
    write_cache,
)


def inject(tmp_path):
    folder = tmp_path / "inject-network-nav"
    folder.mkdir()
    (folder / "sitecustomize.py").write_text("""
import os, sys, json, socket
from pathlib import Path
if "--_worker" in sys.argv:
    from dataclasses import replace
    import bt_api_ctp.calendar_shfe_navigation_fetch as nav
    import bt_api_ctp.calendar_shfe_detail_fetch as detail
    if os.environ.get("NAV_MAX"):
        import bt_api_py.trading_calendar_product_navigation_observation as navigation_observation
        navigation_observation._MAX_NAV_BYTES = int(os.environ["NAV_MAX"])
    if os.environ.get("NAV_LIMIT_INBOX"):
        import bt_api_py.trading_calendar_candidate_inbox as candidate_inbox
        candidate_inbox._LIMIT_CATALOG = int(os.environ["NAV_LIMIT_INBOX"])
    from tests.unit.test_trading_calendar_preview_navigation import page
    original_nav, original_detail = nav.collect_navigation, detail.collect_detail_page
    def get_nav(**kwargs):
        marker = Path(os.environ["NAV_MARKER"])
        marker.write_text((marker.read_text() if marker.exists() else "") + "1")
        mode = os.environ.get("NAV_MODE", "success")
        if mode in {"cleanup", "raises", "hang", "systemexit", "keyboard"}:
            import subprocess, time, datetime
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            Path(os.environ["CHILD_MARKER"]).write_text(str(child.pid) + " " + str(os.getpgid(child.pid)) + " " + str(os.getpgrp()))
            if mode == "raises": raise ImportError("after child start")
            if mode == "systemexit": raise SystemExit(41)
            if mode == "keyboard": raise KeyboardInterrupt("after child start")
            if mode == "hang": time.sleep(30)
            return nav.NavigationFetchResult("UNKNOWN", "CHILD_CLEANUP_UNCERTAIN", "NOT_OBSERVED", datetime.datetime.now(datetime.timezone.utc), None, "SUPERVISED_DEFAULT_TLS_POLICY")
        if mode in {"error", "unsupported"}:
            transport = "TLS_ERROR" if mode == "error" else "SUPERVISOR_UNSUPPORTED"
            return replace(original_nav(runner=lambda *a: (transport, None)), transport_assurance="SUPERVISED_DEFAULT_TLS_POLICY")
        label = os.environ.get("SENSITIVE_PATH", "") if mode == "sensitive" else "铜"
        extra = '<li class="isActive"><a href="/products/futures/metal/cu_f/">另一名称</a></li>' if mode == "conflict" else ""
        if mode == "large":
            extra = "".join(f'<li class="isActive"><a href="/products/futures/metal/x{i}_f/">{"长" * 90}{i}</a></li>' for i in range(80))
        bad = "<section>unknown</section>" if mode == "structure" else ""
        raw = b"\\xff" if mode == "invalid_utf8" else page(future_label=label, duplicate=extra, bad_nav=bad)
        parsed = nav.parse_shfe_product_navigation(source_url=nav.SOURCE_URL, html=raw)
        return replace(original_nav(runner=lambda *a: ("HTTP_OK", parsed)), transport_assurance="SUPERVISED_DEFAULT_TLS_POLICY")
    def get_detail(*, path, **kwargs):
        marker = Path(os.environ["DETAIL_MARKER"])
        marker.write_text((marker.read_text() if marker.exists() else "") + path + "\\n")
        option = detail._path_kind(path) == "option"
        symbol = "看涨期权：CU-合约月份-C-行权价格；看跌期权：CU-合约月份-P-行权价格" if option else "CU"
        row = {"ContractSymbol": symbol, "ListingExchange": "上海期货交易所", "data_standard": "2023-03-29", "TradingHours": "上午9:00-11:30"}
        script = "pageList_o" if option else "pageList"
        body = ("<script>let " + script + " = " + json.dumps([row], ensure_ascii=False) + ";</script>").encode()
        parsed = detail.parse_shfe_product_page(source_url="https://www.shfe.com.cn" + path, html=body)
        return replace(original_detail(path=path, runner=lambda *a: ("HTTP_OK", parsed)), transport_assurance="SUPERVISED_DEFAULT_TLS_POLICY")
    nav.collect_navigation = get_nav
    detail.collect_detail_page = get_detail
class NoNetwork(socket.socket):
    def connect(self, *a, **kw): raise AssertionError("no real network")
socket.socket = NoNetwork
""")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(folder) + os.pathsep + env.get("PYTHONPATH", "")
    env["NAV_MARKER"] = str(tmp_path / "nav-called")
    env["DETAIL_MARKER"] = str(tmp_path / "detail-called")
    env["CHILD_MARKER"] = str(tmp_path / "child")
    return env


def flags():
    return ["--retain-candidate", "--trusted-local-posix", "--fetch-product-navigation"]


def create(root, config, supplement, env):
    result = run(command(root, config, supplement, *flags()), env=env)
    return result, json.loads(result.stdout)


def test_preview_to_v6_to_detail_without_real_network(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    result, report = create(root, config, supplement, env)
    assert result.returncode == 10, (result.stdout, result.stderr)
    doc = show(root, report["candidate_id"])
    assert doc["schema"] == "bt_api.calendar-preview-candidate/6"
    assert "product_page" not in doc["payload"] and "product_navigation" not in doc["payload"]
    observed = doc["payload"]["navigation_fetch"]
    assert (observed["status"], observed["transport_status"], observed["parse_status"]) == (
        "CANDIDATE",
        "HTTP_OK",
        "CANDIDATE",
    )
    assert (
        observed["source_authentication"]
        == observed["retained_data_binding"]
        == observed["coverage"]
        == "NOT_PROVEN"
    )
    assert observed["publication"] == "NOT_AUTHORIZED" and "same_buffer_observation" not in observed
    assert (tmp_path / "nav-called").read_text() == "1"
    details = run(batch_command(root, report["candidate_id"], [FUTURE, OPTION]), env=env)
    assert details.returncode == 0, (details.stdout, details.stderr)
    batch = json.loads(details.stdout)
    assert batch["status"] == "OBSERVED_UNVERIFIED"
    assert [row["path"] for row in batch["items"]] == [FUTURE, OPTION]
    assert batch["source_authentication"] == batch["coverage"] == "NOT_PROVEN"
    assert (tmp_path / "detail-called").read_text().splitlines() == [FUTURE, OPTION]
    assert all(
        str(p) not in result.stdout + result.stderr + details.stdout + details.stderr
        for p in (root, config, supplement)
    )
    assert not (root / "CURRENT.json").exists()


def test_no_explicit_navigation_never_calls_collector(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    result = run(
        command(root, config, supplement, "--retain-candidate", "--trusted-local-posix"), env=env
    )
    assert result.returncode == 10
    assert show(root, json.loads(result.stdout)["candidate_id"])["schema"].endswith("/1")
    assert not (tmp_path / "nav-called").exists()


@pytest.mark.parametrize(
    "extra",
    [
        ["--fetch-product-navigation"],
        ["--fetch-product-navigation", "--retain-candidate"],
        [
            "--fetch-product-navigation",
            "--retain-candidate",
            "--trusted-local-posix",
            "--product-source",
            "CU_FUTURE",
        ],
        [
            "--fetch-product-navigation",
            "--retain-candidate",
            "--trusted-local-posix",
            "--product-page-url",
            "https://example.invalid",
            "--product-page-file",
            "/private/path",
        ],
    ],
)
def test_invalid_combinations_start_no_collector(tmp_path, extra):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    result = run(command(root, config, supplement, *extra), env=env)
    assert result.returncode == 2 and not (tmp_path / "nav-called").exists()
    assert "/private/path" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "mode,status,reason,transport",
    [
        ("error", "UNKNOWN", "FETCH_FAILED", "TLS_ERROR"),
        ("unsupported", "UNKNOWN", "SUPERVISOR_UNSUPPORTED", "SUPERVISOR_UNSUPPORTED"),
        ("structure", "UNKNOWN", "PARSE_FAILED", "HTTP_OK"),
        ("invalid_utf8", "UNKNOWN", "PARSE_FAILED", "HTTP_OK"),
        ("conflict", "NEEDS_REVIEW", "UNVERIFIED_NAVIGATION", "HTTP_OK"),
        ("sensitive", "UNKNOWN", "SENSITIVE_CONTENT", "HTTP_OK"),
    ],
)
def test_failure_and_conflict_are_retained_but_not_used(tmp_path, mode, status, reason, transport):
    root, cache, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    env["NAV_MODE"] = mode
    if mode == "sensitive":
        write_cache(cache, [make_entry("rb2701")])
        env["SENSITIVE_PATH"] = SCOPE
        result = run(command(root, config, supplement, *flags(), *catalog_flags(cache)), env=env)
        report = json.loads(result.stdout)
    else:
        result, report = create(root, config, supplement, env)
    assert result.returncode == 10, result.stderr
    data = show(root, report["candidate_id"])["payload"]["navigation_fetch"]
    assert (data["status"], data["reason"], data["transport_status"]) == (status, reason, transport)
    assert str(config).casefold() not in json.dumps(data).casefold()
    if mode == "sensitive":
        assert SCOPE not in json.dumps(data)
    details = run(batch_command(root, report["candidate_id"], [FUTURE]), env=env)
    assert details.returncode == 2
    assert json.loads(details.stdout)["reason"] == (
        "NAVIGATION_CONFLICT" if mode == "conflict" else "NAVIGATION_UNKNOWN"
    )
    assert not (tmp_path / "detail-called").exists()


def test_nonmember_and_forgery_never_start_detail_fetch(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    result, report = create(root, config, supplement, env)
    assert result.returncode == 10
    cid = report["candidate_id"]
    absent = run(batch_command(root, cid, ["/products/futures/metal/zz_f/"]), env=env)
    assert json.loads(absent.stdout)["reason"] == "SELECTION_NOT_IN_NAVIGATION"
    path = root / "inbox" / f"candidate-{cid}.json"
    original = json.loads(path.read_bytes())
    for change in ("source", "reason", "path", "kind", "mixed"):
        data = copy.deepcopy(original)
        value = data["payload"]["navigation_fetch"]
        if change == "source":
            value["source_authentication"] = "PROVEN"
        elif change == "reason":
            value["reason"] = "FETCH_FAILED"
        elif change == "path":
            value["navigation"]["links"][0]["path"] = "https://evil.invalid/"
        elif change == "kind":
            value["navigation"]["links"][0]["kind"] = "option"
        else:
            data["schema"] = "bt_api.calendar-preview-candidate/5"
        data["payload_sha256"] = hashlib.sha256(inbox._canonical(data["payload"])).hexdigest()
        path.write_bytes(inbox._canonical(data))
        with pytest.raises(ValueError):
            inbox.read_candidate(root=root, candidate_id=cid, filesystem=inbox.TRUSTED_LOCAL_POSIX)
        rejected = run(batch_command(root, cid, [FUTURE]), env=env)
        assert (
            rejected.returncode == 2
            and json.loads(rejected.stdout)["reason"] == "CANDIDATE_UNAVAILABLE"
        )
    assert not (tmp_path / "detail-called").exists()


@pytest.mark.parametrize("mode", ["cleanup", "raises"])
def test_cleanup_unknown_uses_existing_parent_handshake(tmp_path, mode):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    env["NAV_MODE"] = mode
    result, report = create(root, config, supplement, env)
    assert result.returncode == 25, (result.stdout, result.stderr)
    assert (
        report["reason"] == "WORKER_CLEANUP_UNCERTAIN"
        and report["candidate_status"] == "COMMIT_UNKNOWN"
    )
    doc = show(root, report["candidate_id"])
    value = doc["payload"]["navigation_fetch"]
    assert value["status"] == "UNKNOWN" and value["transport_status"] == "CHILD_CLEANUP_UNCERTAIN"
    pid, child_group, worker_group = map(int, (tmp_path / "child").read_text().split())
    assert child_group == worker_group
    for _ in range(30):
        proc = subprocess.run(
            ["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
        )
        if proc.returncode != 0 or not proc.stdout.strip() or proc.stdout.strip().startswith("Z"):
            break
        time.sleep(0.1)
    else:
        pytest.fail("test-owned descendant remained live")
    assert not (root / "CURRENT.json").exists()


def test_fetch_budget_and_validate_finite_statuses():
    data = observation.fetch_navigation(total_timeout=0)
    assert (data["status"], data["reason"], data["transport_status"]) == (
        "UNKNOWN",
        "BUDGET_EXHAUSTED",
        "NOT_ATTEMPTED",
    )
    observation.validate_fetch(data)
    forged = copy.deepcopy(data)
    forged.update(status="CANDIDATE", reason="UNVERIFIED_NAVIGATION")
    with pytest.raises(ValueError):
        observation.validate_fetch(forged)


def test_v6_large_format_reader_and_v1_limit_are_separate(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    created, report = create(root, config, supplement, env)
    assert created.returncode == 10
    cid = report["candidate_id"]
    old = run(
        command(root, config, supplement, "--retain-candidate", "--trusted-local-posix"), env=env
    )
    old_id = json.loads(old.stdout)["candidate_id"]
    assert (root / "inbox" / f"candidate-{cid}.json").stat().st_size > 128
    monkeypatch.setattr(inbox, "_LIMIT", 128)
    assert inbox.read_candidate(root=root, candidate_id=cid, filesystem="TRUSTED_LOCAL_POSIX")[
        "schema"
    ].endswith("/6")
    with pytest.raises(ValueError):
        inbox.read_candidate(root=root, candidate_id=old_id, filesystem="TRUSTED_LOCAL_POSIX")


@pytest.mark.parametrize("limit", ["navigation", "envelope"])
def test_resource_limits_preserve_unknown_without_truncation(tmp_path, limit):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    if limit == "navigation":
        env["NAV_MAX"] = "32"
    else:
        env["NAV_MODE"] = "large"
        env["NAV_LIMIT_INBOX"] = "4096"
    result, report = create(root, config, supplement, env)
    assert result.returncode == 10, (result.stdout, result.stderr)
    payload = show(root, report["candidate_id"])["payload"]
    value = payload["navigation_fetch"]
    assert value["status"] == "UNKNOWN" and value["reason"] == "RESULT_RESOURCE_LIMIT"
    assert value["navigation"] is None and payload["content"]["trading_days"]
    assert not (root / "CURRENT.json").exists()


def test_candidate_tamper_cannot_echo_known_calendar_root(tmp_path):
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory(dir=os.path.realpath("/tmp"), prefix="nav6-") as folder:
        test_path = Path(folder)
        root, _, config, supplement = setup_preview(test_path)
        env = inject(test_path)
        created, report = create(root, config, supplement, env)
        assert created.returncode == 10 and len(str(root)) < 128
        path = root / "inbox" / f"candidate-{report['candidate_id']}.json"
        doc = json.loads(path.read_bytes())
        doc["payload"]["navigation_fetch"]["navigation"]["links"][0]["label"] = str(root).upper()
        doc["payload_sha256"] = hashlib.sha256(inbox._canonical(doc["payload"])).hexdigest()
        path.write_bytes(inbox._canonical(doc))
        with pytest.raises(ValueError):
            inbox.read_candidate(
                root=root, candidate_id=report["candidate_id"], filesystem="TRUSTED_LOCAL_POSIX"
            )


def test_navigation_timeout_stops_owned_worker_group(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    env["NAV_MODE"] = "hang"
    cmd = command(root, config, supplement, *flags())
    cmd[cmd.index("60")] = "3"
    result = run(cmd, env=env)
    assert result.returncode == 25, (result.stdout, result.stderr)
    report = json.loads(result.stdout)
    assert (
        report["reason"] == "WORKER_CLEANUP_UNCERTAIN"
        and report["candidate_status"] == "COMMIT_UNKNOWN"
    )
    pid, child_group, worker_group = map(int, (tmp_path / "child").read_text().split())
    assert child_group == worker_group
    for _ in range(30):
        proc = subprocess.run(
            ["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
        )
        if proc.returncode != 0 or not proc.stdout.strip() or proc.stdout.strip().startswith("Z"):
            break
        time.sleep(0.1)
    else:
        pytest.fail("test-owned descendant remained live")
    assert not (root / "CURRENT.json").exists()


@pytest.mark.parametrize("mode", ["systemexit", "keyboard"])
def test_navigation_baseexception_preserves_unknown_and_cleans_owned_group(tmp_path, mode):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    env["NAV_MODE"] = mode
    result, report = create(root, config, supplement, env)
    assert result.returncode == 25, (result.stdout, result.stderr)
    assert report["reason"] == "WORKER_CLEANUP_UNCERTAIN"
    assert report["candidate_status"] == "COMMIT_UNKNOWN"
    pid, child_group, worker_group = map(int, (tmp_path / "child").read_text().split())
    assert child_group == worker_group
    for _ in range(30):
        proc = subprocess.run(
            ["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
        )
        if proc.returncode != 0 or not proc.stdout.strip() or proc.stdout.strip().startswith("Z"):
            break
        time.sleep(0.1)
    else:
        pytest.fail("test-owned descendant remained live")
    assert not (root / "CURRENT.json").exists()
