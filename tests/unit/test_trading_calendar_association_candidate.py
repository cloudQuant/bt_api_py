"""Synthetic offline integration of the actual retained inputs and parsers."""

import json
import os
import select
import signal
import socket
import subprocess
import sys
import time
from copy import deepcopy
from dataclasses import replace

import pytest
from bt_api_ctp.calendar_local_catalog import read_local_catalog
from bt_api_ctp.calendar_shfe_detail_fetch import collect_detail_page
from bt_api_ctp.calendar_shfe_navigation_fetch import collect_navigation
from bt_api_ctp.calendar_shfe_page import parse_shfe_page
from bt_api_ctp.calendar_shfe_product_navigation import parse_shfe_product_navigation
from bt_api_ctp.calendar_shfe_product_page import parse_shfe_product_page

from bt_api_py import trading_calendar_association_candidate as association
from bt_api_py import trading_calendar_candidate_inbox as inbox
from bt_api_py import trading_calendar_detail_batch as detail
from bt_api_py import trading_calendar_detail_collection as collection
from bt_api_py import trading_calendar_navigation_fetch_observation as nav_observation
from tests.unit.test_trading_calendar_candidate_inbox import (
    HOURS,
    HOURS_URL,
    objects,
    source_checks,
)
from tests.unit.test_trading_calendar_observation_run import env_without_network
from tests.unit.test_trading_calendar_preview_catalog import DAY, SCOPE, make_entry, write_cache
from tests.unit.test_trading_calendar_preview_navigation import page

FS = "TRUSTED_LOCAL_POSIX"
NAV, HOURS_ID, RID, COLLECTION, OUTPUT = (c * 32 for c in "abcde")


def fixture(
    tmp_path,
    monkeypatch,
    *,
    entries=None,
    duplicate="",
    detail_rows=None,
    conflicts=(),
    future_names="铜",
    option_name="铜",
    raw_hours=None,
):
    root = tmp_path / "calendar"
    root.mkdir()
    cache = tmp_path / "cache"
    if entries is None:
        entries = [
            make_entry("cu2701", product_id="cu"),
            make_entry(
                "cu2701-C-100",
                product_id="cu",
                product_class="2",
                underlying_instrument_id="cu2701",
            ),
        ]
    write_cache(cache, entries, conflicts=conflicts)
    snapshot = read_local_catalog(cache_dir=str(cache), scope=SCOPE, trading_day=DAY, filesystem=FS)
    catalog = inbox.catalog_observation(
        snapshot, requested_day=DAY, scope=SCOPE, cache_dir=str(cache)
    )
    raw_nav = page(duplicate=duplicate)
    parsed_nav = parse_shfe_product_navigation(source_url=nav_observation._SOURCE, html=raw_nav)
    import bt_api_ctp.calendar_shfe_navigation_fetch as nav_fetch

    def nav_fake(**kwargs):
        result = collect_navigation(runner=lambda *args: ("HTTP_OK", parsed_nav))
        return replace(result, transport_assurance="SUPERVISED_DEFAULT_TLS_POLICY")

    monkeypatch.setattr(nav_fetch, "collect_navigation", nav_fake)
    observed = nav_observation.fetch_navigation(total_timeout=3, sensitive=(str(root),))
    assert observed["status"] == "CANDIDATE"
    _, _, merged = objects()
    if raw_hours is None:
        raw_hours = (
            HOURS.decode()
            .replace("合成甲期权", option_name)
            .replace("合成甲", future_names)
            .encode()
        )
    hours = parse_shfe_page(source_id=HOURS_URL, html=raw_hours)
    checks = source_checks()
    checks["hours"]["attempts"][0]["product_count"] = len(hours.products)
    for cid, kwargs in ((NAV, {"catalog": catalog, "navigation_fetch": observed}), (HOURS_ID, {})):
        assert (
            inbox.retain_candidate(
                root=str(root),
                candidate_id=cid,
                merged=merged,
                hours_page=hours,
                source_checks=checks,
                filesystem=FS,
                sensitive_values=(str(root),),
                **kwargs,
            ).status
            == "COMMITTED"
        )
    monkeypatch.setattr(
        collection.runs,
        "show_once",
        lambda **kw: {
            "intent": "VISIBLE_UNAUTHENTICATED",
            "record": "VISIBLE_UNAUTHENTICATED",
            "navigation_requested": True,
            "candidate_id": NAV,
            "observation": {
                "schema": collection.runs._RESULT_SCHEMA_V3,
                "candidate_id": NAV,
                "candidate_state": "COMMITTED",
            },
        },
    )

    def supervisor(args):
        state = detail._state(NAV, args["paths"])
        state["candidate_payload_sha256"] = args["expected_candidate_payload_sha256"]
        state["status"] = "OBSERVED_UNVERIFIED"
        for index, path in enumerate(args["paths"]):
            option = "/option/" in path
            symbol = (
                "看涨期权：CU-合约月份-C-行权价格；看跌期权：CU-合约月份-P-行权价格"
                if option
                else "CU"
            )
            rows = [
                {
                    "ContractSymbol": symbol,
                    "ListingExchange": "上海期货交易所",
                    "data_standard": "2023-03-29",
                    "TradingHours": "上午9:00-11:30",
                }
            ]
            if detail_rows:
                rows = detail_rows(path, rows)
            if rows is None:
                state["items"][index] = detail._item(path, "UNKNOWN", "FETCH_FAILED")
                state["items"][index]["transport_status"] = "TLS_ERROR"
                continue
            body = (
                "<script>let "
                + ("pageList_o" if option else "pageList")
                + " = "
                + json.dumps(rows, ensure_ascii=False)
                + ";</script>"
            ).encode()
            parsed = parse_shfe_product_page(source_url="https://www.shfe.com.cn" + path, html=body)
            result = collect_detail_page(
                path=path, runner=lambda *args, page=parsed: ("HTTP_OK", page)
            )
            state["items"][index] = detail._provider_item(
                path,
                replace(result, transport_assurance="SUPERVISED_DEFAULT_TLS_POLICY"),
                str(root),
            )
        if any(item["status"] != "CANDIDATE" for item in state["items"]):
            state["status"] = "PARTIAL"
        return state

    monkeypatch.setattr(detail, "_supervise", supervisor)
    event = collection.collect_once(
        root=str(root),
        run_id=RID,
        filesystem=FS,
        budget_seconds=60,
        per_request_seconds=3,
        collection_id=COLLECTION,
        emit=lambda _: None,
    )
    assert event["status"] in {"OBSERVED_UNVERIFIED", "PARTIAL"}, repr(event)
    return root


def build(root):
    return association.build_once(
        root=str(root),
        navigation_candidate_id=NAV,
        hours_candidate_id=HOURS_ID,
        collection_id=COLLECTION,
        candidate_id=OUTPUT,
        filesystem=FS,
        emit=lambda _: None,
    )


def show(root):
    return association.show(root=str(root), candidate_id=OUTPUT, filesystem=FS)


def test_real_parsers_collection_catalog_and_no_network(tmp_path, monkeypatch):
    root = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(socket.socket, "connect", lambda *a: pytest.fail("network"))
    assert build(root)["status"] == "COMMITTED"
    candidate = show(root)["candidate"]
    assert [
        (r["kind"], r["venue"], r["product"], r["contracts"]) for r in candidate["results"]
    ] == [("future", "SHFE", "cu", ["cu2701"]), ("option", "SHFE", "cu", ["cu2701-C-100"])]
    assert all(r["effective_date"] == "UNKNOWN" for r in candidate["results"])
    assert candidate["association"] == candidate["coverage"] == "NOT_PROVEN"
    assert (
        candidate["publication"] == "NOT_AUTHORIZED" and candidate["last_success"] == "NOT_UPDATED"
    )
    assert not (root / "CURRENT.json").exists()
    assert build(root)["status"] == "NOT_COMMITTED"
    assert show(root)["candidate"] == candidate
    path = root / "inbox" / f"candidate-{NAV}.json"
    data = json.loads(path.read_bytes())
    data["payload"]["identity_mapping"] = "FORGED"
    path.write_text(json.dumps(data))
    assert show(root)["status"] == "UNKNOWN"


def test_duplicate_name_across_paths_does_not_infer_venue(tmp_path, monkeypatch):
    root = fixture(
        tmp_path,
        monkeypatch,
        duplicate='<li class="isActive"><a href="/products/futures/metal/bc_f/">铜</a></li>',
        entries=[
            make_entry("cu2701", product_id="cu"),
            make_entry("bc2701", exchange_id="INE", product_id="bc"),
        ],
    )
    assert build(root)["status"] == "COMMITTED"
    rows = show(root)["candidate"]["results"]
    assert [r["reason"] for r in rows if r["kind"] == "future"] == [
        "DETAIL_IDENTITY_MULTIPLE_PAGES"
    ] * 2


def test_same_identity_different_names_and_hours_is_conflict(tmp_path, monkeypatch):
    base = HOURS.decode()
    first = base.replace("期货：合成甲<br>期权：合成甲期权", "期货：铜<br>期权：铜")
    first_row = first[first.index("<tr>") : first.index("</tr>") + 5]
    second_row = first_row.replace("期货：铜<br>期权：铜", "期货：国际铜").replace(
        "13:30-15:00", "13:30-14:30"
    )
    root = fixture(
        tmp_path,
        monkeypatch,
        duplicate='<li class="isActive"><a href="/products/futures/metal/bc_f/">国际铜</a></li>',
        raw_hours=first.replace(first_row, first_row + second_row).encode(),
    )
    assert build(root)["status"] == "COMMITTED"
    rows = show(root)["candidate"]["results"]
    assert [row["reason"] for row in rows[:2]] == ["DETAIL_IDENTITY_MULTIPLE_PAGES"] * 2
    assert all(row["status"] == "UNKNOWN" for row in rows[:2])
    assert rows[2]["status"] == "CANDIDATE"


def test_explicit_ine_and_shfe_and_option_name_mismatch(tmp_path, monkeypatch):
    def identities(path, rows):
        if path.endswith("bc_f/"):
            return [{**rows[0], "ContractSymbol": "CU", "ListingExchange": "上海国际能源交易中心"}]
        return rows

    root = fixture(
        tmp_path,
        monkeypatch,
        duplicate='<li class="isActive"><a href="/products/futures/metal/bc_f/">国际铜</a></li>',
        entries=[
            make_entry("cu2701", product_id="cu"),
            make_entry("bc2701", exchange_id="INE", product_id="cu"),
        ],
        future_names="铜、国际铜",
        option_name="铜期权",
        detail_rows=identities,
    )
    assert build(root)["status"] == "COMMITTED"
    rows = show(root)["candidate"]["results"]
    assert [(row["venue"], row["product"]) for row in rows[:2]] == [("SHFE", "cu"), ("INE", "cu")]
    assert rows[2]["reason"] == "HOURS_NAME_MISSING"


def test_history_conflict_and_missing_detail(tmp_path, monkeypatch):
    def history(path, rows):
        return rows + [{**rows[0], "ListingExchange": "上海国际能源交易中心"}]

    root = fixture(tmp_path, monkeypatch, detail_rows=history)
    assert build(root)["status"] == "COMMITTED"
    rows = show(root)["candidate"]["results"]
    assert rows[0]["reason"] == "DETAIL_HISTORY_CONFLICT"
    assert all(row["status"] == "UNKNOWN" for row in rows)


def test_catalog_conflict_and_missing(tmp_path, monkeypatch):
    root = fixture(tmp_path, monkeypatch, conflicts=("unknown2701",))
    assert build(root)["status"] == "COMMITTED"
    assert all(
        r["reason"] == "CATALOG_CONFLICT_OR_SKIPPED" for r in show(root)["candidate"]["results"]
    )


def test_missing_detail_and_catalog_match_remain_unknown(tmp_path, monkeypatch):
    root = fixture(
        tmp_path,
        monkeypatch,
        entries=[make_entry("other2701", product_id="other")],
        detail_rows=lambda path, rows: None if "/option/" in path else rows,
    )
    assert build(root)["status"] == "COMMITTED"
    rows = show(root)["candidate"]["results"]
    assert rows[0]["reason"] == "CATALOG_MATCH_MISSING"
    assert rows[1]["reason"] == "DETAIL_UNKNOWN"


def test_collection_tampering_invalidates_candidate(tmp_path, monkeypatch):
    root = fixture(tmp_path, monkeypatch)
    assert build(root)["status"] == "COMMITTED"
    path = root / "detail-collections" / f"collection-{COLLECTION}.batch-000.json"
    data = json.loads(path.read_bytes())
    data["body"]["index"] = 2
    path.write_text(json.dumps(data))
    assert show(root)["status"] == "UNKNOWN"


def test_contract_limit_is_unknown_and_never_truncated(tmp_path, monkeypatch):
    entries = [make_entry(f"cu{i}", product_id="cu") for i in range(3)]
    root = fixture(tmp_path, monkeypatch, entries=entries)
    monkeypatch.setattr(association, "_CONTRACT_LIMIT", 2)
    assert build(root)["status"] == "COMMITTED"
    row = show(root)["candidate"]["results"][0]
    assert row["reason"] == "CONTRACT_LIMIT" and row["contracts"] is None


def test_mismatched_collection_id_or_digest_rejected(tmp_path, monkeypatch):
    root = fixture(tmp_path, monkeypatch)
    original = collection._show_all
    monkeypatch.setattr(
        collection,
        "_show_all",
        lambda **kw: ({**original(**kw)[0], "candidate_payload_sha256": "f" * 64}, []),
    )
    assert build(root)["status"] == "NOT_COMMITTED"
    assert not (root / "association-review-candidates").exists()


@pytest.mark.parametrize("retry_result", ["missing", "failed"])
def test_unknown_or_failed_retry_does_not_reuse_earlier_success(
    tmp_path, monkeypatch, retry_result
):
    root = fixture(tmp_path, monkeypatch)
    nav, hours, summary, batches = association._inputs(str(root), NAV, HOURS_ID, COLLECTION, FS)
    batches = deepcopy(batches)
    report = deepcopy(batches[0]["result"])
    report["items"] = [report["items"][0]]
    if retry_result == "failed":
        report["items"][0]["status"] = "UNKNOWN"
    else:
        report = None
    batches[0]["retry_attempts"] = [{"path_index": 0, "attempt": 1, "result": report}]
    result = association._derive(nav, hours, summary, batches, OUTPUT, COLLECTION)
    assert result["results"][0]["status"] == "UNKNOWN"
    assert result["results"][0]["contracts"] is None


def test_association_directory_replacement_during_show_is_unknown(tmp_path, monkeypatch):
    root = fixture(tmp_path, monkeypatch)
    assert build(root)["status"] == "COMMITTED"
    original = association._derive

    def replace_directory(*args):
        value = original(*args)
        directory = root / "association-review-candidates"
        directory.rename(root / "displaced-candidates")
        directory.mkdir(mode=0o700)
        return value

    monkeypatch.setattr(association, "_derive", replace_directory)
    assert show(root)["status"] == "UNKNOWN"
    assert not (root / "CURRENT.json").exists()


def cli(root, command, candidate_id=OUTPUT):
    args = [
        sys.executable,
        "-m",
        "bt_api_py.trading_calendar_association_candidate",
        command,
        "--calendar-root",
        str(root),
        "--candidate-id",
        candidate_id,
        "--trusted-local-posix",
    ]
    if command == "build":
        args += [
            "--navigation-candidate-id",
            NAV,
            "--hours-candidate-id",
            HOURS_ID,
            "--collection-id",
            COLLECTION,
        ]
    return args


def run_cli(args, env):
    return subprocess.run(args, capture_output=True, text=True, env=env, timeout=20, check=False)


@pytest.mark.parametrize("phase", ["parse", "valid"])
def test_cli_preflight_interrupt_is_redacted_and_does_not_write(tmp_path, phase):
    root = tmp_path / "calendar"
    root.mkdir()
    injection = (
        "import argparse\n"
        "def interrupted(*a, **kw): raise KeyboardInterrupt('private /private/path')\n"
        "argparse.ArgumentParser.parse_args = interrupted\n"
        if phase == "parse"
        else "from bt_api_py import trading_calendar_preview_reports as reports\n"
        "def interrupted(*a, **kw): raise KeyboardInterrupt('private /private/path')\n"
        "reports._supported = interrupted\n"
    )
    env = env_without_network(tmp_path, extra=injection)
    result = run_cli(cli(root, "build"), env)
    assert result.returncode == 130
    assert json.loads(result.stdout) == {"status": "NOT_COMMITTED", "reason": "INTERRUPTED"}
    assert not result.stderr and str(root) not in result.stdout
    assert list(root.iterdir()) == []


def test_cli_sigint_in_preflight_is_redacted_and_does_not_write(tmp_path):
    root = tmp_path / "calendar"
    root.mkdir()
    ready = tmp_path / "valid-ready"
    env = env_without_network(
        tmp_path,
        extra=f"""
import pathlib, time
from bt_api_py import trading_calendar_preview_reports as reports
def wait_in_valid():
    pathlib.Path({str(ready)!r}).touch()
    while True: time.sleep(0.01)
reports._supported = wait_in_valid
""",
    )
    process = subprocess.Popen(
        cli(root, "build"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        os.kill(process.pid, signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 130
        assert json.loads(stdout) == {"status": "NOT_COMMITTED", "reason": "INTERRUPTED"}
        assert not stderr and str(root) not in stdout
        assert list(root.iterdir()) == []
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_cli_help_remains_successful(tmp_path):
    env = env_without_network(tmp_path)
    result = run_cli(
        [sys.executable, "-m", "bt_api_py.trading_calendar_association_candidate", "--help"], env
    )
    assert result.returncode == 0 and "usage:" in result.stdout and not result.stderr


def test_cli_build_show_duplicate_and_errors_are_offline_and_redacted(tmp_path, monkeypatch):
    root = fixture(tmp_path, monkeypatch)
    env = env_without_network(tmp_path)
    built = run_cli(cli(root, "build"), env)
    assert built.returncode == 10 and not built.stderr
    lines = [json.loads(line) for line in built.stdout.splitlines()]
    assert lines == [
        {"event": "ASSOCIATION_CANDIDATE_ID_ALLOCATED", "candidate_id": OUTPUT},
        {"status": "COMMITTED", "reason": "NONE", "candidate_id": OUTPUT},
    ]
    shown = run_cli(cli(root, "show"), env)
    assert shown.returncode == 10 and not shown.stderr
    candidate = json.loads(shown.stdout)["candidate"]
    assert candidate["source_authentication"] == candidate["association"] == "NOT_PROVEN"
    assert candidate["effective_date"] == "UNKNOWN"
    assert candidate["publication"] == "NOT_AUTHORIZED"
    assert all(row["effective_date"] == "UNKNOWN" for row in candidate["results"])
    duplicate = run_cli(cli(root, "build"), env)
    assert duplicate.returncode == 2
    assert json.loads(duplicate.stdout.splitlines()[-1])["status"] == "NOT_COMMITTED"
    assert json.loads(run_cli(cli(root, "show"), env).stdout)["candidate"] == candidate
    absent = run_cli(cli(root, "show", "f" * 32), env)
    assert absent.returncode == 25 and json.loads(absent.stdout)["status"] == "UNKNOWN"
    bad = run_cli(cli(root, "build", "../sensitive"), env)
    assert bad.returncode == 2
    assert json.loads(bad.stdout) == {"status": "NOT_COMMITTED", "reason": "INVALID_ARGUMENTS"}
    invalid = cli(root, "build")
    invalid[invalid.index(NAV)] = "../private-input"
    bad_input = run_cli(invalid, env)
    assert bad_input.returncode == 2 and len(bad_input.stdout.splitlines()) == 1
    missing = cli(root, "build", "f" * 32)
    missing[missing.index(COLLECTION)] = "f" * 32
    missing_input = run_cli(missing, env)
    assert missing_input.returncode == 2
    assert json.loads(missing_input.stdout.splitlines()[-1])["reason"] == "INPUT_UNAVAILABLE"
    for output in (built, shown, duplicate, absent, bad, bad_input, missing_input):
        assert str(root) not in output.stdout + output.stderr
        assert "private-input" not in output.stdout + output.stderr
        assert "sensitive" not in output.stdout + output.stderr
    assert not (root / "CURRENT.json").exists()


def test_cli_hides_raw_write_exception(tmp_path, monkeypatch):
    root = fixture(tmp_path, monkeypatch)
    env = env_without_network(
        tmp_path,
        extra="""
from bt_api_py import trading_calendar_detail_collection as collection
def fail(*args): raise OSError("secret raw exception /private/secret")
collection._write = fail
""",
    )
    result = run_cli(cli(root, "build"), env)
    assert result.returncode == 25
    assert json.loads(result.stdout.splitlines()[-1])["status"] == "COMMIT_UNKNOWN"
    assert not result.stderr
    assert "secret" not in result.stdout and str(root) not in result.stdout


def test_cli_flushes_allocated_id_before_write(tmp_path, monkeypatch):
    root = fixture(tmp_path, monkeypatch)
    started, release = tmp_path / "started", tmp_path / "release"
    env = env_without_network(
        tmp_path,
        extra=f"""
import pathlib, time
from bt_api_py import trading_calendar_detail_collection as collection
original = collection._write
def gated(*args):
    pathlib.Path({str(started)!r}).touch()
    while not pathlib.Path({str(release)!r}).exists(): time.sleep(0.01)
    return original(*args)
collection._write = gated
""",
    )
    process = subprocess.Popen(
        cli(root, "build"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
    )
    try:
        assert select.select([process.stdout], [], [], 10)[0]
        assert json.loads(process.stdout.readline())["candidate_id"] == OUTPUT
        deadline = time.monotonic() + 10
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert started.exists()
        assert not (root / "association-review-candidates" / f"candidate-{OUTPUT}.json").exists()
        release.touch()
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 10 and not stderr
        assert json.loads(stdout)["status"] == "COMMITTED"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


@pytest.mark.parametrize("after_write", [False, True])
def test_cli_interrupted_write_reports_unknown_without_retry(tmp_path, monkeypatch, after_write):
    root = fixture(tmp_path, monkeypatch)
    marker = tmp_path / "write-calls"
    env = env_without_network(
        tmp_path,
        extra=f"""
import pathlib
from bt_api_py import trading_calendar_detail_collection as collection
original = collection._write
def interrupted(*args):
    with pathlib.Path({str(marker)!r}).open("a") as calls: calls.write("1")
    if {after_write!r}: original(*args)
    raise KeyboardInterrupt("private exception")
collection._write = interrupted
""",
    )
    result = run_cli(cli(root, "build"), env)
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert result.returncode == 25 and lines[0]["candidate_id"] == OUTPUT
    assert lines[1] == {"status": "COMMIT_UNKNOWN", "reason": "INTERRUPTED", "candidate_id": OUTPUT}
    assert marker.read_text() == "1"
    assert "private exception" not in result.stdout + result.stderr
    assert str(root) not in result.stdout + result.stderr
    shown = run_cli(cli(root, "show"), env)
    assert json.loads(shown.stdout)["status"] == (
        "VISIBLE_UNAUTHENTICATED" if after_write else "UNKNOWN"
    )
    assert not (root / "CURRENT.json").exists()
