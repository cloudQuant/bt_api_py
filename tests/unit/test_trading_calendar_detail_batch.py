"""Explicit /5 batch observations with synthetic network substitutes only."""

import copy
import hashlib
import json
import os
import subprocess
import sys
import time

import pytest

from bt_api_py import trading_calendar_candidate_inbox as inbox
from bt_api_py import trading_calendar_detail_batch as batch
from tests.unit.test_trading_calendar_preview_catalog import command, run, setup_preview, show
from tests.unit.test_trading_calendar_preview_navigation import flags as nav_flags
from tests.unit.test_trading_calendar_preview_navigation import page

FUTURE = "/products/futures/metal/cu_f/"
OPTION = "/products/option/metal/cu_o/"
EXTRA = "/products/futures/metal/zz_f/"
EXTRA2 = "/products/futures/metal/yy_f/"


def fixture_candidate(tmp_path, *, nav_page=None, discover=True):
    root, _, config, supplement = setup_preview(tmp_path)
    source = tmp_path / "local-page.html"
    source.write_bytes(page() if nav_page is None else nav_page)
    created = run(command(root, config, supplement, *nav_flags(source, discover=discover)))
    assert created.returncode == 10, created.stderr
    cid = json.loads(created.stdout)["candidate_id"]
    return root, cid, show(root, cid)


def batch_command(root, cid, paths, *, budget=15, per_request=3):
    cmd = [
        sys.executable,
        "-m",
        "bt_api_py.trading_calendar_detail_batch",
        "--calendar-root",
        str(root),
        "--candidate-id",
        cid,
        "--trusted-local-posix",
        "--budget-seconds",
        str(budget),
        "--per-request-seconds",
        str(per_request),
    ]
    for path in paths:
        cmd.extend(["--path", path])
    return cmd


def inject(tmp_path, extra=""):
    folder = tmp_path / "inject"
    folder.mkdir()
    (folder / "sitecustomize.py").write_text(
        """
import os, sys, json, socket
from pathlib import Path
if "--_worker" in sys.argv:
    from dataclasses import replace
    from bt_api_py import trading_calendar_candidate_inbox as inbox
    import bt_api_ctp.calendar_shfe_detail_fetch as f
    original_read = inbox.read_candidate
    original_collect = f.collect_detail_page
    def read_once(**kwargs):
        marker = Path(os.environ["READ_MARKER"])
        marker.write_text((marker.read_text() if marker.exists() else "") + "1")
        return original_read(**kwargs)
    inbox.read_candidate = read_once
    def fake(*, path, **kwargs):
        marker = Path(os.environ["CALL_MARKER"])
        marker.write_text((marker.read_text() if marker.exists() else "") + path + "\\n")
        mode = os.environ.get("DETAIL_MODE", "success")
        if mode == "raises":
            raise RuntimeError("synthetic collector failure")
        if mode in {"cleanup", "hang"}:
            import subprocess, time
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            Path(os.environ["CHILD_MARKER"]).write_text(str(child.pid) + " " + str(os.getpgid(child.pid)) + " " + str(os.getpgrp()))
            if mode == "hang": time.sleep(30)
            return f.DetailFetchResult("UNKNOWN", "CHILD_CLEANUP_UNCERTAIN", "NOT_OBSERVED", __import__("datetime").datetime.now(__import__("datetime").timezone.utc), None, path, f._path_kind(path), "SUPERVISED_DEFAULT_TLS_POLICY", "NOT_ASSESSED")
        if mode == "failure":
            result = original_collect(path=path, runner=lambda *a: ("TLS_ERROR", None))
            return replace(result, transport_assurance="SUPERVISED_DEFAULT_TLS_POLICY")
        opt = f._path_kind(path) == "option"
        symbol = "看涨期权：CU-合约月份-C-行权价格；看跌期权：CU-合约月份-P-行权价格" if opt else "CU"
        hours = os.environ["CALENDAR_ROOT"] if mode == "unsafe" else ("时" * 1800 if mode == "large" else "上午9:00-11:30及交易所其他时间")
        date = "2023-99-99" if mode == "invalid_date" else "2023-03-29"
        rows = [{"ContractSymbol": symbol, "ListingExchange": "上海期货交易所", "data_standard": date, "TradingHours": hours}]
        if mode == "conflict": rows.append({**rows[0], "ListingExchange": "上海国际能源交易中心"})
        script = "pageList_o" if opt else "pageList"
        body = ("<script>let " + script + " = " + json.dumps(rows, ensure_ascii=False) + ";</script>").encode()
        parsed = f.parse_shfe_product_page(source_url="https://www.shfe.com.cn" + path, html=body)
        result = original_collect(path=path, runner=lambda *a: ("HTTP_OK", parsed))
        if mode == "invalid_result":
            result = replace(result, kind="option" if not opt else "future")
        return replace(result, transport_assurance="SUPERVISED_DEFAULT_TLS_POLICY")
    f.collect_detail_page = fake
class NoNetwork(socket.socket):
    def connect(self, *a, **k): raise AssertionError("network forbidden")
socket.socket = NoNetwork
"""
        + extra
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(folder) + os.pathsep + env.get("PYTHONPATH", "")
    env["READ_MARKER"] = str(tmp_path / "reads")
    env["CALL_MARKER"] = str(tmp_path / "calls")
    env["CHILD_MARKER"] = str(tmp_path / "child")
    return env


def test_real_cli_reads_v5_once_sequentially_and_reports_unverified_details(tmp_path):
    root, cid, doc = fixture_candidate(tmp_path)
    env = inject(tmp_path)
    env["CALENDAR_ROOT"] = str(root)
    result = run(batch_command(root, cid, [FUTURE, OPTION]), env=env)
    assert result.returncode == 0, (result.stdout, result.stderr)
    result_data = json.loads(result.stdout)
    assert result_data["status"] == "OBSERVED_UNVERIFIED"
    assert (
        result_data["candidate_id"] == cid
        and result_data["candidate_payload_sha256"] == doc["payload_sha256"]
    )
    assert (
        result_data["candidate_authentication"]
        == result_data["retained_data_binding"]
        == "NOT_PROVEN"
    )
    assert (
        result_data["source_authentication"]
        == result_data["freshness"]
        == result_data["coverage"]
        == "NOT_PROVEN"
    )
    assert [item["path"] for item in result_data["items"]] == [FUTURE, OPTION]
    assert [item["status"] for item in result_data["items"]] == ["CANDIDATE", "CANDIDATE"]
    assert all(
        item["records"][0]["trading_hours"] == "上午9:00-11:30及交易所其他时间"
        for item in result_data["items"]
    )
    assert (tmp_path / "reads").read_text() == "1"
    assert (tmp_path / "calls").read_text().splitlines() == [FUTURE, OPTION]
    assert str(root) not in result.stdout + result.stderr and "<script>" not in result.stdout
    assert not (root / "CURRENT.json").exists()


@pytest.mark.parametrize("case", ["schema", "unknown", "conflict", "nonmember", "bad_hash"])
def test_invalid_candidate_or_selection_never_starts_network(tmp_path, case):
    if case == "unknown":
        content = page(bad_nav="<section>unexpected</section>")
    elif case == "conflict":
        content = page(
            duplicate='<li class="isActive"><a href="/products/futures/metal/cu_f/">另一名称</a></li>'
        )
    else:
        content = page()
    root, cid, _ = fixture_candidate(tmp_path, nav_page=content, discover=(case != "schema"))
    if case == "bad_hash":
        path = root / "inbox" / f"candidate-{cid}.json"
        data = json.loads(path.read_bytes())
        data["payload_sha256"] = "0" * 64
        path.write_bytes(inbox._canonical(data))
    env = inject(tmp_path)
    env["CALENDAR_ROOT"] = str(root)
    paths = [FUTURE, EXTRA] if case == "nonmember" else [FUTURE]
    result = run(batch_command(root, cid, paths), env=env)
    assert result.returncode == 2
    output = json.loads(result.stdout)
    assert output["status"] == "ERROR"
    assert all(item["status"] == "NOT_ATTEMPTED_REJECTED" for item in output["items"])
    assert not (tmp_path / "calls").exists()
    assert str(root) not in result.stdout + result.stderr


def test_duplicate_invalid_path_and_untrusted_filesystem_fail_before_worker(tmp_path):
    root, cid, _ = fixture_candidate(tmp_path)
    env = inject(tmp_path)
    env["CALENDAR_ROOT"] = str(root)
    commands = [
        batch_command(root, cid, [FUTURE, FUTURE]),
        batch_command(root, cid, ["//evil.invalid/x"]),
        [x for x in batch_command(root, cid, [FUTURE]) if x != "--trusted-local-posix"],
    ]
    for cmd in commands:
        result = run(cmd, env=env)
        assert result.returncode == 2 and json.loads(result.stdout)["reason"] == "INVALID_ARGUMENTS"
    assert not (tmp_path / "calls").exists() and not (tmp_path / "reads").exists()


def test_expired_budget_marks_each_path_not_attempted(tmp_path, monkeypatch):
    root, cid, _ = fixture_candidate(tmp_path)
    import bt_api_ctp.calendar_shfe_detail_fetch as fetch

    monkeypatch.setattr(fetch, "collect_detail_page", lambda **kw: pytest.fail("unexpected call"))
    state = batch._work(str(root), cid, [FUTURE, OPTION], time.monotonic() - 1, 1, lambda _: None)
    assert state["status"] == "PARTIAL" and state["reason"] == "BUDGET_EXHAUSTED"
    assert [item["status"] for item in state["items"]] == ["NOT_ATTEMPTED_BUDGET"] * 2


@pytest.mark.parametrize(
    "mode,status,reason",
    [
        ("failure", "UNKNOWN", "FETCH_FAILED"),
        ("unsafe", "NEEDS_REVIEW", "DETAIL_FIELDS_INCOMPLETE"),
        ("invalid_date", "NEEDS_REVIEW", "DETAIL_FIELDS_INCOMPLETE"),
        ("conflict", "NEEDS_REVIEW", "DETAIL_REVIEW"),
    ],
)
def test_per_item_failure_unsafe_field_and_conflict_are_explicit(tmp_path, mode, status, reason):
    root, cid, _ = fixture_candidate(tmp_path)
    env = inject(tmp_path)
    env["CALENDAR_ROOT"] = str(root)
    env["DETAIL_MODE"] = mode
    result = run(batch_command(root, cid, [FUTURE]), env=env)
    assert result.returncode == 10, (result.stdout, result.stderr)
    output = json.loads(result.stdout)
    item = output["items"][0]
    assert (item["status"], item["reason"]) == (status, reason)
    assert str(root) not in result.stdout + result.stderr
    if mode == "unsafe":
        assert item["records"][0]["trading_hours"] is None
    if mode == "invalid_date":
        assert item["records"][0]["raw_date"] is None
    if mode == "conflict":
        assert item["identity_status"] == "CONFLICTING_FIELDS" and len(item["records"]) == 2


def test_rehashed_candidate_does_not_become_authenticated(tmp_path):
    root, cid, _ = fixture_candidate(tmp_path)
    path = root / "inbox" / f"candidate-{cid}.json"
    doc = json.loads(path.read_bytes())
    doc["payload"]["product_navigation"]["navigation"]["links"][0]["path"] = EXTRA
    doc["payload_sha256"] = hashlib.sha256(inbox._canonical(doc["payload"])).hexdigest()
    path.write_bytes(inbox._canonical(doc))
    env = inject(tmp_path)
    env["CALENDAR_ROOT"] = str(root)
    result = run(batch_command(root, cid, [EXTRA]), env=env)
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["candidate_payload_sha256"] == doc["payload_sha256"]
    assert output["candidate_authentication"] == output["retained_data_binding"] == "NOT_PROVEN"


def test_provider_cleanup_uncertain_stops_remaining_and_supervisor_cleans_group(tmp_path):
    root, cid, _ = fixture_candidate(tmp_path)
    env = inject(tmp_path)
    env["CALENDAR_ROOT"] = str(root)
    env["DETAIL_MODE"] = "cleanup"
    result = run(batch_command(root, cid, [FUTURE, OPTION]), env=env)
    assert result.returncode == 23, (result.stdout, result.stderr)
    output = json.loads(result.stdout)
    assert output["status"] == "CLEANUP_UNCERTAIN" and output["process_cleanup"] == "NOT_PROVEN"
    assert output["items"][0]["reason"] == "CHILD_CLEANUP_UNCERTAIN"
    assert output["items"][1]["status"] == "NOT_ATTEMPTED_CLEANUP"
    pid, group, worker_group = map(int, (tmp_path / "child").read_text().split())
    assert group == worker_group
    for _ in range(30):
        check = subprocess.run(
            ["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
        )
        if (
            check.returncode != 0
            or not check.stdout.strip()
            or check.stdout.strip().startswith("Z")
        ):
            break
        time.sleep(0.1)
    else:
        pytest.fail("test child remained running")


@pytest.mark.parametrize("mode", ["raises", "invalid_result"])
def test_collector_exception_or_bad_result_stops_batch_with_cleanup_unknown(tmp_path, mode):
    root, cid, _ = fixture_candidate(tmp_path)
    env = inject(tmp_path)
    env["CALENDAR_ROOT"] = str(root)
    env["DETAIL_MODE"] = mode
    result = run(batch_command(root, cid, [FUTURE, OPTION]), env=env)
    assert result.returncode == 23, (result.stdout, result.stderr)
    output = json.loads(result.stdout)
    assert (output["status"], output["reason"], output["process_cleanup"]) == (
        "CLEANUP_UNCERTAIN",
        "COLLECTOR_CLEANUP_UNCERTAIN",
        "NOT_PROVEN",
    )
    assert (output["items"][0]["status"], output["items"][0]["reason"]) == (
        "UNKNOWN",
        "COLLECTOR_PROTOCOL_ERROR",
    )
    assert output["items"][1]["status"] == "NOT_ATTEMPTED_CLEANUP"
    assert (tmp_path / "calls").read_text().splitlines() == [FUTURE]


def test_total_budget_reports_inflight_unknown_and_unattempted_paths(tmp_path):
    root, cid, _ = fixture_candidate(tmp_path)
    env = inject(tmp_path)
    env["CALENDAR_ROOT"] = str(root)
    env["DETAIL_MODE"] = "hang"
    result = run(batch_command(root, cid, [FUTURE, OPTION], budget=3, per_request=2), env=env)
    assert result.returncode == 23, (result.stdout, result.stderr)
    output = json.loads(result.stdout)
    assert output["status"] == "CLEANUP_UNCERTAIN" and output["process_cleanup"] == "NOT_PROVEN"
    assert (output["items"][0]["status"], output["items"][0]["reason"]) == (
        "UNKNOWN",
        "INTERRUPTED",
    )
    assert output["items"][1]["status"] == "NOT_ATTEMPTED_BUDGET"
    pid = int((tmp_path / "child").read_text().split()[0])
    for _ in range(30):
        check = subprocess.run(
            ["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
        )
        if (
            check.returncode != 0
            or not check.stdout.strip()
            or check.stdout.strip().startswith("Z")
        ):
            break
        time.sleep(0.1)
    else:
        pytest.fail("timed-out test child remained running")


def test_worker_result_semantics_reject_forged_success_and_partial(tmp_path):
    root, cid, _ = fixture_candidate(tmp_path)
    env = inject(tmp_path)
    env["CALENDAR_ROOT"] = str(root)
    result = run(batch_command(root, cid, [FUTURE]), env=env)
    assert result.returncode == 0, result.stderr
    original = json.loads(result.stdout)
    mutations = [
        lambda data: data["items"][0].update(parse_status="NEEDS_REVIEW"),
        lambda data: data["items"][0].update(identity_status="INCOMPLETE_FIELDS"),
        lambda data: data["items"][0]["records"][0].update(symbol=None),
        lambda data: data["items"][0]["records"][0].update(venue=None),
        lambda data: data["items"][0]["records"][0].update(trading_hours=None),
        lambda data: data["items"][0]["records"][0].update(raw_date="2023-99-99"),
        lambda data: data["items"][0]["records"][0].update(unknown_reasons=["SYMBOL_UNKNOWN"]),
        lambda data: data.update(status="PARTIAL", reason="NONE"),
        lambda data: data["items"][0].update(status="UNKNOWN", reason="FETCH_FAILED", records=None),
    ]
    for mutate in mutations:
        altered = copy.deepcopy(original)
        mutate(altered)
        with pytest.raises((ValueError, TypeError)):
            batch._validate_state(altered, candidate_id=cid, paths=[FUTURE], root=str(root))
    encoded = batch._json(original)
    duplicate = b'{"candidate_id":"' + cid.encode() + b'",' + encoded[1:]
    assert batch._read_last(duplicate, cid, [FUTURE], str(root)) is None


def test_worker_result_rejects_forged_failure_and_unattempted_states(tmp_path):
    root, cid, _ = fixture_candidate(tmp_path)
    state = batch._state(cid, [FUTURE])
    state["status"], state["reason"] = "PARTIAL", "BUDGET_EXHAUSTED"
    state["candidate_payload_sha256"] = "a" * 64
    state["items"][0].update(status="NOT_ATTEMPTED_BUDGET", reason="BUDGET_EXHAUSTED")
    batch._validate_state(state, candidate_id=cid, paths=[FUTURE], root=str(root))
    variants = []
    data = copy.deepcopy(state)
    data["items"][0]["transport_status"] = "HTTP_OK"
    variants.append(data)
    data = copy.deepcopy(state)
    data["status"] = "OBSERVED_UNVERIFIED"
    variants.append(data)
    data = copy.deepcopy(state)
    data["items"][0].update(status="UNKNOWN", reason="FETCH_FAILED", transport_status="HTTP_OK")
    variants.append(data)
    for altered in variants:
        with pytest.raises((ValueError, TypeError)):
            batch._validate_state(altered, candidate_id=cid, paths=[FUTURE], root=str(root))


def test_forged_collector_error_cannot_continue_batch(tmp_path):
    root, cid, _ = fixture_candidate(tmp_path)
    state = batch._state(cid, [FUTURE, OPTION])
    state["candidate_payload_sha256"] = "a" * 64
    state["status"], state["reason"] = "PARTIAL", "NONE"
    state["items"][0].update(
        status="UNKNOWN", reason="COLLECTOR_PROTOCOL_ERROR", transport_status="UNKNOWN"
    )
    state["items"][1].update(status="UNKNOWN", reason="FETCH_FAILED", transport_status="TLS_ERROR")
    with pytest.raises(ValueError):
        batch._validate_state(state, candidate_id=cid, paths=[FUTURE, OPTION], root=str(root))


def test_per_page_output_limit_is_explicit_without_truncating_records(tmp_path):
    from dataclasses import replace

    import bt_api_ctp.calendar_shfe_detail_fetch as fetch

    root = str(tmp_path.resolve() / "calendar")
    row = {
        "ContractSymbol": "CU",
        "ListingExchange": "上海期货交易所",
        "data_standard": "2023-03-29",
        "TradingHours": "时" * 2048,
    }
    body = (
        "<script>let pageList = " + json.dumps([row] * 3, ensure_ascii=False) + ";</script>"
    ).encode()
    parsed = fetch.parse_shfe_product_page(source_url="https://www.shfe.com.cn" + FUTURE, html=body)
    result = fetch.collect_detail_page(path=FUTURE, runner=lambda *a: ("HTTP_OK", parsed))
    item = batch._provider_item(
        FUTURE, replace(result, transport_assurance="SUPERVISED_DEFAULT_TLS_POLICY"), root
    )
    assert (item["status"], item["reason"], item["records"]) == (
        "UNKNOWN",
        "DETAIL_TOO_LARGE",
        None,
    )


def test_total_result_budget_preserves_earlier_and_marks_unattempted(tmp_path, monkeypatch):
    from dataclasses import replace

    import bt_api_ctp.calendar_shfe_detail_fetch as fetch

    extra_items = "".join(
        f'<li class="isActive"><a href="{path}">扩展{i}</a></li>'
        for i, path in enumerate([EXTRA, EXTRA2])
    )
    root, cid, _ = fixture_candidate(tmp_path, nav_page=page(duplicate=extra_items))
    original = fetch.collect_detail_page
    calls = []

    def fake(*, path, **kwargs):
        calls.append(path)
        hours = "上午9:00-11:30" if len(calls) == 1 else "时" * 1800
        row = {
            "ContractSymbol": "CU",
            "ListingExchange": "上海期货交易所",
            "data_standard": "2023-03-29",
            "TradingHours": hours,
        }
        body = (
            "<script>let pageList = " + json.dumps([row], ensure_ascii=False) + ";</script>"
        ).encode()
        parsed = fetch.parse_shfe_product_page(
            source_url="https://www.shfe.com.cn" + path, html=body
        )
        result = original(path=path, runner=lambda *a: ("HTTP_OK", parsed))
        return replace(result, transport_assurance="SUPERVISED_DEFAULT_TLS_POLICY")

    monkeypatch.setattr(fetch, "collect_detail_page", fake)
    monkeypatch.setattr(batch, "_MAX_OUTPUT", 5000)
    state = batch._work(
        str(root), cid, [FUTURE, EXTRA, EXTRA2, OPTION], time.monotonic() + 20, 3, lambda _: None
    )
    batch._validate_state(
        state, candidate_id=cid, paths=[FUTURE, EXTRA, EXTRA2, OPTION], root=str(root)
    )
    assert state["status"] == "PARTIAL" and state["reason"] == "RESULT_LIMIT"
    assert [i["status"] for i in state["items"]] == [
        "CANDIDATE",
        "UNKNOWN",
        "NOT_ATTEMPTED_RESULT_LIMIT",
        "NOT_ATTEMPTED_RESULT_LIMIT",
    ]
    assert state["items"][1]["records"] is None and calls == [FUTURE, EXTRA]


def test_direct_worker_session_leader_does_not_kill_own_group_sentinel(tmp_path):
    import contextlib

    root, cid, _ = fixture_candidate(tmp_path)
    env = inject(tmp_path)
    env["CALENDAR_ROOT"] = str(root)
    env["DETAIL_MODE"] = "cleanup"
    args = {
        "root": str(root),
        "candidate_id": cid,
        "paths": [FUTURE],
        "budget": 3,
        "per_request": 2,
        "deadline": time.monotonic() + 2,
        "filesystem": "TRUSTED_LOCAL_POSIX",
    }
    worker = subprocess.Popen(
        [sys.executable, "-m", "bt_api_py.trading_calendar_detail_batch", "--_worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    pid = None
    try:
        stdout, _ = worker.communicate(input=json.dumps(args).encode(), timeout=10)
        assert worker.returncode == 0
        state = json.loads(stdout.splitlines()[-1])
        assert state["status"] == "CLEANUP_UNCERTAIN" and state["process_cleanup"] == "NOT_PROVEN"
        pid, child_group, worker_group = map(int, (tmp_path / "child").read_text().split())
        assert child_group == worker_group == worker.pid
        os.kill(pid, 0)
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=5)
        if pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, 9)


def test_parent_stops_flooding_worker_at_streamed_protocol_limit(tmp_path, monkeypatch):
    original = subprocess.Popen
    children = []
    monkeypatch.setattr(batch, "_supervisor_supported", lambda: True)
    monkeypatch.setattr(batch, "_MAX_PROTOCOL", 8192)

    def noisy(_cmd, **kwargs):
        child = original(
            [
                sys.executable,
                "-c",
                "import sys,time; sys.stdout.buffer.write(b'x'*1000000); sys.stdout.flush(); time.sleep(30)",
            ],
            **kwargs,
        )
        children.append(child)
        return child

    monkeypatch.setattr(batch.subprocess, "Popen", noisy)
    result = batch._supervise(
        {
            "root": str(tmp_path.resolve() / "calendar"),
            "candidate_id": "a" * 32,
            "paths": [FUTURE],
            "budget": 3,
            "per_request": 2,
            "filesystem": "TRUSTED_LOCAL_POSIX",
        }
    )
    assert result["status"] == "CLEANUP_UNCERTAIN" and result["process_cleanup"] == "NOT_PROVEN"
    assert (
        result["items"][0]["status"] == "UNKNOWN"
        and result["items"][0]["reason"] == "ATTEMPT_UNKNOWN"
    )
    assert len(children) == 1 and children[0].poll() is not None


def _wait_test_process_gone(pid):
    for _ in range(30):
        check = subprocess.run(
            ["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
        )
        if (
            check.returncode != 0
            or not check.stdout.strip()
            or check.stdout.strip().startswith("Z")
        ):
            return
        time.sleep(0.1)
    pytest.fail("owned test descendant remained running")


@pytest.mark.parametrize("exit_code", [0, 17])
def test_eof_cleans_owned_hidden_descendant_before_reaping(tmp_path, monkeypatch, exit_code):
    import contextlib

    root = str(tmp_path.resolve() / "calendar")
    cid = "a" * 32
    state = batch._state(cid, [FUTURE])
    state.update(status="OBSERVED_UNVERIFIED", candidate_payload_sha256="b" * 64)
    state["items"][0].update(
        status="CANDIDATE",
        reason="NONE",
        transport_status="HTTP_OK",
        parse_status="CANDIDATE",
        identity_status="CONSISTENT_FIELDS_UNVERIFIED",
        page_sha256="c" * 64,
        records=[
            {
                "kind": "future",
                "symbol": "CU",
                "venue": "SHFE",
                "raw_date": "2023-03-29",
                "trading_hours": "上午9:00-11:30",
                "unknown_reasons": [],
                "effective_date": "UNKNOWN",
            }
        ],
    )
    batch._validate_state(state, candidate_id=cid, paths=[FUTURE], root=root)
    marker = tmp_path / "eof-child"
    payload = batch._json(state) + b"\n" if exit_code == 0 else b""
    sentinel = (
        f"import os,time\nopen({str(marker)!r},'w').write(str(os.getpid()))\ntime.sleep(30)\n"
    )
    code = (
        "import os,signal,subprocess,sys,time\n"
        "ack=[]\n"
        "signal.signal(signal.SIGTERM, lambda *args: ack.append(True))\n"
        f"p=subprocess.Popen([sys.executable,'-c',{sentinel!r}],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        f"while not os.path.exists({str(marker)!r}): time.sleep(.01)\n"
        f"sys.stdout.buffer.write({payload!r}); sys.stdout.flush()\n"
        + ("while not ack: time.sleep(.01)\n" if exit_code == 0 else "")
        + f"sys.exit({exit_code})\n"
    )
    original = subprocess.Popen
    spawned = []
    unrelated = original(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    def synthetic_worker(cmd, **kwargs):
        if cmd[1:4] != ["-m", "bt_api_py.trading_calendar_detail_batch", "--_worker"]:
            return original(cmd, **kwargs)
        child = original([sys.executable, "-c", code], **kwargs)
        spawned.append(child)
        return child

    monkeypatch.setattr(batch.subprocess, "Popen", synthetic_worker)
    monkeypatch.setattr(batch, "_supervisor_supported", lambda: True)
    pid = None
    try:
        result = batch._supervise(
            {
                "root": root,
                "candidate_id": cid,
                "paths": [FUTURE],
                "budget": 5,
                "per_request": 2,
                "filesystem": "TRUSTED_LOCAL_POSIX",
            }
        )
        pid = int(marker.read_text())
        assert result["status"] == (
            "OBSERVED_UNVERIFIED" if exit_code == 0 else "CLEANUP_UNCERTAIN"
        )
        assert result["process_cleanup"] == "NOT_PROVEN"
        _wait_test_process_gone(pid)
        assert unrelated.poll() is None and spawned[0].poll() is not None
    finally:
        if pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, 9)
        if unrelated.poll() is None:
            unrelated.kill()
            unrelated.wait(timeout=5)


def test_ignored_sigchld_rejects_before_spawning(tmp_path, monkeypatch):
    import signal

    def forbidden(*args, **kwargs):
        pytest.fail("must not spawn")

    monkeypatch.setattr(batch.signal, "getsignal", lambda sig: signal.SIG_IGN)
    monkeypatch.setattr(batch.subprocess, "Popen", forbidden)
    state = batch._supervise(
        {
            "root": str(tmp_path.resolve() / "calendar"),
            "candidate_id": "a" * 32,
            "paths": [FUTURE],
            "budget": 3,
            "per_request": 2,
            "filesystem": "TRUSTED_LOCAL_POSIX",
        }
    )
    assert state["status"] == "ERROR" and state["reason"] == "SUPERVISOR_UNSUPPORTED"
    assert state["items"][0]["status"] == "NOT_ATTEMPTED_REJECTED"


@pytest.mark.parametrize(
    "mode,expected",
    [("default", "True"), ("ignored", "False"), ("handler", "False"), ("thread", "False")],
)
def test_supervisor_environment_check_in_isolated_process(mode, expected):
    script = """
import signal, sys, threading
from bt_api_py import trading_calendar_detail_batch as batch
mode = sys.argv[1]
if mode == "ignored": signal.signal(signal.SIGCHLD, signal.SIG_IGN)
if mode == "handler": signal.signal(signal.SIGCHLD, lambda *args: None)
stop = threading.Event()
thread = None
if mode == "thread":
    thread = threading.Thread(target=stop.wait)
    thread.start()
print(batch._supervisor_supported())
stop.set()
if thread is not None: thread.join()
"""
    result = subprocess.run(
        [sys.executable, "-c", script, mode],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0 and result.stdout.strip() == expected
